"""Retain explicitly selected restriction-cloning materials in native molecular history.

Suggestions are read-only. Prepare creates reusable children; the existing ligation
Save owns the final construct. No planner or scientific calculation runs on reopen.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from molbio_models import (MolecularDocument, MolecularOperation, MolecularOperationInput, MolecularOperationOutput,
                           MolecularRevision, NucleotideSequence)
from services.molbio_authority import SERVER_OWNED_ACTOR
from services.molbio_persistence import (
    _record_inline_sequence_input, add_operation_edges, begin_immediate_molbio_write,
    canonical_request_fingerprint, create_operation, record_sequence_revision,
)
from .physical_ends import read_physical_ends, with_unphosphorylated_blunt_ends, write_physical_ends
from .restriction_cloning import selected_restriction_cloning
from .types import AssemblyError, FragmentEnd

KIND = "restriction_cloning_preparation"


async def resolve_cloning_sources(session, body: dict[str, Any]):
    resolved, revisions = dict(body), {}
    parents, source_notes = {}, []
    for role in ("backbone", "insert"):
        revision_id = body.get(f"{role}_revision_id")
        revision = await session.get(MolecularRevision, revision_id) if revision_id else None
        if revision_id and revision is None:
            raise HTTPException(404, f"{role.title()} molecular revision was not found")
        if revision is not None:
            snapshot = revision.snapshot
            resolved.update({f"{role}_sequence": snapshot["sequence"],
                             f"{role}_features": snapshot.get("features") or [],
                             f"{role}_topology": "circular" if snapshot.get("is_circular") else "linear"})
        elif not resolved.get(f"{role}_sequence"):
            raise AssemblyError(f"Supply {role}_sequence or {role}_revision_id")
        if revision is None and body.get(f"{role}_source_document_id"):
            document = await session.get(MolecularDocument, body[f"{role}_source_document_id"])
            if document is not None and document.current_revision_id:
                parents[role] = document.current_revision_id
            else:
                source_notes.append(f"{role.title()} document head could not be linked; the supplied inline DNA is still used.")
        revisions[role] = revision
    resolved["_source_parent_revisions"], resolved["_source_notes"] = parents, source_notes
    return resolved, revisions


def _row(name, sequence, features, *, circular=False, primers=None):
    return NucleotideSequence(id=str(uuid.uuid4()), name=name, description=None, sequence=sequence,
        sequence_type="dna", molecule_strandedness="double", molecule_orientation="not_applicable",
        is_circular=circular, length=len(sequence), features=features, primers=primers or [],
        analysis_tracks={}, operation=KIND, operation_params={}, version=1,
        gc_content=round(100 * (sequence.count("G") + sequence.count("C")) / max(len(sequence), 1), 2))


async def read_preparation(session, operation):
    if operation is None or operation.operation_kind != KIND:
        raise HTTPException(404, "Restriction-cloning preparation was not found")
    rows = (await session.execute(select(MolecularOperationOutput, MolecularRevision).join(
        MolecularRevision, MolecularRevision.id == MolecularOperationOutput.revision_id).where(
        MolecularOperationOutput.operation_id == operation.id).order_by(MolecularOperationOutput.position))).all()
    fragments, outputs = [], []
    for edge, revision in rows:
        snapshot = revision.snapshot
        outputs.append({"document_id": revision.document_id, "revision_id": revision.id,
                        "name": snapshot["name"], "role": edge.role})
        if edge.role not in {"restriction_backbone", "restriction_insert"}:
            continue
        left, right = read_physical_ends(revision.provenance)
        fid = "vector" if edge.role == "restriction_backbone" else "insert"
        fragments.append({"id": fid, "name": snapshot["name"], "sequence": snapshot["sequence"],
            "orientation": operation.parameters["selection"]["insert_orientation"] if fid == "insert" else "forward",
            "left_end": asdict(left) if left is not None else None,
            "right_end": asdict(right) if right is not None else None, "features": snapshot.get("features") or [],
            "source_sequence_id": revision.document_id, "source_revision": revision.revision_number,
            "metadata": {"revision_id": revision.id, "retained_ends": True,
                         "end_enzymes": revision.provenance["end_enzymes"]}})
    source_edges = (await session.execute(select(MolecularOperationInput).where(
        MolecularOperationInput.operation_id == operation.id))).scalars().all()
    request = {**operation.parameters["source_controls"],
               **{f"{edge.role.removesuffix('_source')}_revision_id": edge.revision_id for edge in source_edges if edge.role.endswith("_source")},
               **{f"{edge.role.removesuffix('_parent')}_source_document_id": edge.snapshot["document_id"]
                  for edge in source_edges if edge.role.endswith("_parent")}}
    selection = {**operation.parameters["selection"], "parent_revisions": {
        edge.role.removesuffix("_parent"): edge.revision_id for edge in source_edges if edge.role.endswith("_parent")}}
    return {"operation_id": operation.id, "outputs": outputs, "request": request,
            "assembly_request": {"fragments": fragments, "circular": True, "new_name": operation.parameters["name"],
                                 "observe_enzymes": operation.parameters["observe_enzymes"]},
            "selection": selection, "notes": operation.warnings}


async def save_preparation(session, *, body, selection, name, idempotency_key):
    fingerprint = canonical_request_fingerprint({"kind": KIND, "request": body, "selection": selection, "name": name})
    await begin_immediate_molbio_write(session)
    if idempotency_key:
        existing = (await session.execute(select(MolecularOperation).where(
            MolecularOperation.idempotency_key == idempotency_key))).scalar_one_or_none()
        if existing is not None:
            if existing.operation_kind != KIND or existing.request_fingerprint != fingerprint:
                raise HTTPException(409, "Idempotency key is bound to another request")
            return await read_preparation(session, existing)
    resolved, sources = await resolve_cloning_sources(session, body)
    result = await run_in_threadpool(selected_restriction_cloning, resolved, selection)
    notes = list(selection.get("primer_notes") or [])
    parent_inputs = []
    for role, revision_id in (selection.get("parent_revisions") or resolved["_source_parent_revisions"]).items():
        parent = await session.get(MolecularRevision, revision_id)
        if parent is not None:
            parent_inputs.append((parent, f"{role}_parent", {"document_id": parent.document_id}))
        else:
            notes.append(f"{role.title()} parent revision is unavailable; the supplied inline DNA is still used.")
    notes.extend(resolved["_source_notes"])
    notes.append("Prepared materials are a sequence-based recipe, not evidence that the laboratory reactions were performed. Methylation sensitivity and enzyme activity are not evaluated.")
    operation = await create_operation(session, operation_kind=KIND,
        implementation="services.assembly.restriction_cloning.selected_restriction_cloning",
        parameters={"selection": selection, "name": name, "observe_enzymes": result["assembly_request"]["observe_enzymes"],
                    "source_controls": {key: value for key, value in resolved.items() if key not in {
                        "backbone_sequence", "insert_sequence", "backbone_features", "insert_features",
                        "backbone_revision_id", "insert_revision_id", "_source_parent_revisions", "_source_notes"}}},
        warnings=notes, provenance={"source": "api", "fixed_selection": True},
        idempotency_key=idempotency_key, request_fingerprint=fingerprint)
    for role in ("backbone", "insert"):
        if sources[role] is None:
            sources[role] = await _record_inline_sequence_input(session,
                _row(f"{name} original {role}", resolved[f"{role}_sequence"], resolved.get(f"{role}_features") or [],
                     circular=resolved.get(f"{role}_topology") == "circular"),
                operation=operation, provenance={"source_role": role}, created_by=SERVER_OWNED_ACTOR)
    outputs = []
    pcr = None
    if result.get("prepared_insert"):
        prepared = result["prepared_insert"]
        tails = result["primer_tails"]
        offset = len(tails["forward_primer_5prime_tail"])
        payload_length = len(prepared["sequence"]) - offset - len(tails["reverse_primer_5prime_tail"])
        primers = []
        for primer in selection.get("primers") or []:
            start = offset if primer["strand"] == 1 else offset + payload_length - primer["anneal_length"]
            primers.append({**primer, "name": "Restriction forward" if primer["strand"] == 1 else "Restriction reverse",
                            "start": start, "end": start + primer["anneal_length"],
                            "tail_sequence": primer["sequence"][:-primer["anneal_length"]]})
        pcr = await record_sequence_revision(session,
            _row(f"{name} site-tailed insert", prepared["sequence"], prepared["features"], primers=primers),
            change_kind="operation_intermediate", operation_id=operation.id,
            provenance=with_unphosphorylated_blunt_ends({"source_revision_id": sources["insert"].id,
                "preparation": "primer_tail", "primer_tails": tails}, "pcr_or_synthesis_recipe"))
        outputs.append((pcr, "prepared_insert", {"document_id": pcr.document_id}))
    for role, fragment in zip(("backbone", "insert"), result["assembly_request"]["fragments"]):
        provenance = write_physical_ends({"source_revision_id": sources[role].id,
            "end_enzymes": fragment["metadata"]["end_enzymes"],
            "source_segments": fragment["metadata"]["digest_fragment"]["source_segments"],
            "preparation": selection["kind"]}, FragmentEnd(**fragment["left_end"]), FragmentEnd(**fragment["right_end"]), "restriction_cloning")
        if role == "insert" and pcr is not None:
            provenance["parent_revision_id"] = pcr.id
        child = await record_sequence_revision(session,
            _row(f"{name} {role}", fragment["sequence"], fragment.get("features") or []),
            change_kind="operation_intermediate", operation_id=operation.id, provenance=provenance)
        outputs.append((child, f"restriction_{role}", {"document_id": child.document_id}))
    await add_operation_edges(session, operation,
        input_revisions=[(revision, f"{role}_source", {"document_id": revision.document_id}) for role, revision in sources.items()] + parent_inputs,
        output_revisions=outputs)
    await session.commit()
    return await read_preparation(session, operation)
