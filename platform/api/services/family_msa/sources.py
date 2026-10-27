"""Read exact Project Dataset resource occurrences and supported owner sequences.

Unknown resources remain visible; missing producer evidence is reported rather
than converted into an execution rule. No payload is inferred from labels.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlencode

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentDatasetRevisionMember,
    ExperimentExternalEntityReceipt,
    ExperimentRevision,
)
from experiment_services import NotFound, ValidationFailure, canonical_json, sha256_text


async def _dataset(session: AsyncSession, project_id: str, dataset_id: str, revision_id: str):
    head = await session.get(ExperimentAggregateHead, dataset_id)
    revision = await session.get(ExperimentRevision, revision_id)
    if (head is None or head.aggregate_kind != "dataset" or head.workspace_id != project_id
            or revision is None or revision.subject_id != dataset_id):
        raise NotFound("Exact Dataset revision not found in Project")
    return revision


def _render(row: ExperimentDatasetRevisionMember, receipt: ExperimentExternalEntityReceipt | None) -> dict[str, Any]:
    value = json.loads(row.value_json)
    return {
        "ordinal": row.ordinal,
        "role": row.role,
        "member_identity": row.semantic_identity,
        "member_sha256": row.content_sha256,
        "media_type": row.media_type,
        "value": value,
        "receipt": None if receipt is None else {
            "receipt_id": receipt.id,
            "entity_kind": receipt.entity_kind,
            "entity_id": receipt.entity_id,
            "adapter_id": receipt.verification_authority,
            "revision": receipt.generation_or_revision,
            "content_digest": receipt.content_digest,
            "availability": receipt.availability,
        },
    }


def _statement(project_id: str, revision_id: str):
    member = ExperimentDatasetRevisionMember
    receipt = ExperimentExternalEntityReceipt
    return (select(member, receipt)
            .outerjoin(receipt, (receipt.id == member.semantic_identity) & (receipt.workspace_id == project_id))
            .where(member.revision_id == revision_id)
            .order_by(member.ordinal))


async def _ancestry(session, dataset_id, revision, row):
    """Validate each claimed link against immutable revision and member evidence.

    This establishes consistency, not who authored a declaration: the generic
    Dataset writer can create an indistinguishable, fully consistent wrapper.
    """
    seen = set()
    depth = 0
    path = []
    try:
        while True:
            key = (revision.resource_id, row.ordinal)
            if key in seen:
                raise ValueError("ancestry_cycle")
            seen.add(key)
            path.append(revision.resource_id)
            payload = json.loads(revision.canonical_payload)
            if (canonical_json(payload) != revision.canonical_payload or
                    sha256_text(revision.canonical_payload) != revision.payload_sha256):
                raise ValueError("invalid_revision_hash")
            members = payload.get("members")
            if not isinstance(members, list) or row.ordinal >= len(members):
                raise ValueError("member_not_in_revision")
            item = members[row.ordinal]
            if not isinstance(item, dict):
                raise ValueError("invalid_member")
            value = item.get("value", item)
            value_json = canonical_json(value)
            if (row.value_json != value_json or row.role != str(item.get("role") or "member") or
                    row.semantic_identity != str(item.get("identity") or f"member:{row.ordinal}") or
                    row.content_sha256 != str(item.get("content_sha256") or sha256_text(value_json)) or
                    row.media_type != item.get("media_type")):
                raise ValueError("member_projection_mismatch")
            if not isinstance(value, dict) or value.get("schema") != "bms.dataset-derived-member.v1":
                return {"status": "resolved", "basis": "validated_declaration_chain", "depth": depth, "_path": path,
                        "occurrence": {"dataset_id": dataset_id, "revision_id": revision.resource_id,
                                       "revision_sha256": revision.payload_sha256, "ordinal": row.ordinal,
                                       "member_sha256": row.content_sha256}}
            origin = value.get("origin")
            if (set(value) != {"schema", "source_value", "origin"} or not isinstance(origin, dict) or
                    set(origin) != {"revision_id", "revision_sha256", "ordinal", "member_sha256"} or
                    not isinstance(origin["revision_id"], str) or type(origin["ordinal"]) is not int or origin["ordinal"] < 0):
                raise ValueError("invalid_origin_declaration")
            parent = await session.get(ExperimentRevision, origin["revision_id"])
            if (parent is None or parent.subject_id != dataset_id or parent.revision_number >= revision.revision_number or
                    parent.payload_sha256 != origin["revision_sha256"]):
                raise ValueError("origin_revision_mismatch")
            source = (await session.execute(select(ExperimentDatasetRevisionMember).where(
                ExperimentDatasetRevisionMember.revision_id == parent.resource_id,
                ExperimentDatasetRevisionMember.ordinal == origin["ordinal"]))).scalar_one_or_none()
            if (source is None or source.content_sha256 != origin["member_sha256"] or
                    source.role != row.role or source.semantic_identity != row.semantic_identity or
                    source.media_type != row.media_type or canonical_json(value["source_value"]) != source.value_json):
                raise ValueError("origin_member_mismatch")
            revision, row = parent, source
            depth += 1
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return {"status": "unknown", "reason": str(exc)}


async def _render_with_ancestry(session, dataset_id, revision, rows):
    # Check the complete roster at each traversed revision, not only the page
    # or selected subset. Two descendants of one root cannot be paired safely.
    cache = {}
    async def roster(rev):
        if rev.resource_id not in cache:
            members = (await session.execute(select(ExperimentDatasetRevisionMember).where(
                ExperimentDatasetRevisionMember.revision_id == rev.resource_id))).scalars().all()
            values = {m.ordinal: await _ancestry(session, dataset_id, rev, m) for m in members}
            counts = {}
            for value in values.values():
                if value["status"] == "resolved":
                    key = canonical_json(value["occurrence"])
                    counts[key] = counts.get(key, 0) + 1
            cache[rev.resource_id] = (values, counts)
        return cache[rev.resource_id]
    values, _ = await roster(revision)
    result = []
    for row, receipt in rows:
        item = _render(row, receipt)
        ancestry = dict(values[row.ordinal])
        if ancestry["status"] == "resolved":
            key = canonical_json(ancestry["occurrence"])
            for revision_id in ancestry.pop("_path"):
                ancestor_revision = await session.get(ExperimentRevision, revision_id)
                _, counts = await roster(ancestor_revision)
                if counts.get(key, 0) > 1:
                    ancestry = {"status": "unknown", "reason": "repeated_origin_ambiguous"}
                    break
        item["ancestry"] = ancestry
        result.append(item)
    return result


async def browse_dataset_resources(session: AsyncSession, *, project_id: str, dataset_id: str,
                                   revision_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Page all pinned members, including unknown and non-Design receipts."""
    if offset < 0 or not 1 <= limit <= 1000:
        raise ValidationFailure("invalid page")
    revision = await _dataset(session, project_id, dataset_id, revision_id)
    statement = _statement(project_id, revision_id)
    total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = (await session.execute(statement.offset(offset).limit(limit))).all()
    return {"schema": "bms.family-msa.dataset-resources.v1", "project_id": project_id,
            "dataset_id": dataset_id, "revision_id": revision_id,
            "revision_sha256": revision.payload_sha256, "items": await _render_with_ancestry(session, dataset_id, revision, rows),
            "offset": offset, "limit": limit, "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None}


async def freeze_dataset_resources(session: AsyncSession, *, project_id: str, dataset_id: str,
                                   revision_id: str, ordinals: list[int] | None = None) -> dict[str, Any]:
    """Freeze resource occurrences in revision order; None means all members.

    This returns a value to be persisted by the calling owner, not a database
    write. It deliberately does not assert producer bytes remain available.
    """
    revision = await _dataset(session, project_id, dataset_id, revision_id)
    statement = _statement(project_id, revision_id)
    if ordinals is not None:
        if len(ordinals) != len(set(ordinals)) or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in ordinals):
            raise ValidationFailure("invalid member ordinals")
        statement = statement.where(ExperimentDatasetRevisionMember.ordinal.in_(ordinals))
    rows = (await session.execute(statement)).all()
    if ordinals is not None and len(rows) != len(ordinals):
        raise NotFound("Dataset member ordinal not found in exact revision")
    return {"schema": "bms.family-msa.dataset-resource-selection.v1", "project_id": project_id,
            "dataset_id": dataset_id, "revision_id": revision_id,
            "revision_sha256": revision.payload_sha256, "members": await _render_with_ancestry(session, dataset_id, revision, rows)}


async def resolve_molecular_member(project_session: AsyncSession, core_session: AsyncSession,
                                   molbio_session: AsyncSession, *, project_id: str,
                                   dataset_id: str, revision_id: str, ordinal: int,
                                   start: int | None = None, end: int | None = None,
                                   adapter=None) -> dict[str, Any]:
    """Read one exact nucleotide revision through its owner; coordinates are 0-based half-open.

    This is an optional read, not an admission check. The returned occurrence
    remains distinct even when multiple Dataset members reference the same receipt.
    The caller owns persistence of selections and must not infer it from this read.
    """
    from molbio_models import MolecularRevision
    from services.global_experiments.adapters import AdapterError, registry
    from services.molbio_ngs_receipts import _snapshot_sequence

    selection = await freeze_dataset_resources(project_session, project_id=project_id,
        dataset_id=dataset_id, revision_id=revision_id, ordinals=[ordinal])
    member = selection["members"][0]
    receipt = member["receipt"]
    result = {"schema": "bms.family-msa.resolved-member.v1", "project_id": project_id,
              "dataset_id": dataset_id, "revision_id": revision_id,
              "revision_sha256": selection["revision_sha256"], "ordinal": ordinal,
              "member_identity": member["member_identity"], "member_sha256": member["member_sha256"]}
    supported = {"bms.molbio.member-molecular-revision.adapter.v1", "bms.molbio.revision-reference.adapter.v1",
                 "bms.molbio.construct-reference.adapter.v1"}
    if receipt is None or receipt["adapter_id"] not in supported:
        return {**result, "status": "unavailable", "reason": "unsupported_or_missing_receipt"}
    try:
        identity = parse_qs(receipt["entity_id"], strict_parsing=True, keep_blank_values=True)
        if set(identity) != {"sequence_id", "revision_id"} or any(len(v) != 1 or not v[0] for v in identity.values()):
            raise ValueError("invalid molecular identity")
        owner = adapter or registry.get(receipt["adapter_id"])
        verification_id = receipt["entity_id"]
        if receipt["adapter_id"] == "bms.molbio.member-molecular-revision.adapter.v1":
            head = await project_session.get(ExperimentAggregateHead, dataset_id)
            verification_id += "&" + urlencode({"domain_experiment_id": head.parent_id})
        fresh = await owner.verify(core_session, verification_id)
        persisted = await project_session.get(ExperimentExternalEntityReceipt, receipt["receipt_id"])
        acknowledgement = json.loads(persisted.acknowledgement_json or "{}")
        fields = ("store_id", "entity_kind", "entity_id", "entity_revision_id", "content_digest", "contract_digest", "verifier_id", "reopen_uri")
        if any(fresh.get(k) != acknowledgement.get(k) for k in fields) or (
            persisted.store_id, persisted.entity_kind, persisted.entity_id, persisted.generation_or_revision, persisted.content_digest
        ) != (fresh.get("store_id"), fresh.get("entity_kind"), fresh.get("entity_id"), str(fresh.get("entity_revision_id")), fresh.get("content_digest")):
            raise ValueError("receipt differs from producer authority")
        revision = await molbio_session.get(MolecularRevision, identity["revision_id"][0])
        if revision is None or revision.document_id != identity["sequence_id"][0]:
            raise ValueError("molecular revision missing")
        sequence = _snapshot_sequence(revision)
        if revision.content_sha256 != receipt["content_digest"] or len(sequence) != revision.content_length:
            raise ValueError("molecular sequence differs from receipt")
    except (AdapterError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {**result, "status": "unavailable", "reason": str(exc)}
    first = 0 if start is None else start
    last = len(sequence) if end is None else end
    if type(first) is not int or type(last) is not int or first < 0 or last <= first or last > len(sequence):
        raise ValidationFailure("invalid sequence region")
    return {**result, "status": "resolved", "source_kind": "molecular_revision",
            "source_identity": receipt["entity_id"], "source_digest": revision.content_sha256,
            "molecule": str((revision.snapshot or {}).get("sequence_type") or "dna").lower(),
            "label": (revision.snapshot or {}).get("name"), "start": first, "end": last,
            "source_positions": list(range(first, last)),
            "sequence": sequence[first:last]}


async def resolve_design_member(project_session, core_session, *, project_id, dataset_id,
                                revision_id, ordinal, chain=None, start=None, end=None):
    """Read a Design occurrence's observed protein residues from verified structure bytes."""
    import hashlib
    import io
    from pathlib import Path
    from Bio.PDB import PDBParser, MMCIFParser, Polypeptide
    from database import Design
    from services.global_experiments.adapters import AdapterError, registry
    from paths import resolve_runtime_data_path

    selection = await freeze_dataset_resources(project_session, project_id=project_id, dataset_id=dataset_id, revision_id=revision_id, ordinals=[ordinal])
    member = selection['members'][0]
    receipt = member['receipt']
    result = {'schema': 'bms.family-msa.resolved-member.v1', 'project_id': project_id, 'dataset_id': dataset_id, 'revision_id': revision_id, 'revision_sha256': selection['revision_sha256'], 'ordinal': ordinal, 'member_identity': member['member_identity'], 'member_sha256': member['member_sha256']}
    if not receipt or receipt['adapter_id'] != 'bms.core.protein-result-reference.adapter.v1':
        return {**result, 'status': 'unavailable', 'reason': 'unsupported_or_missing_receipt'}
    try:
        fresh = await registry.get(receipt['adapter_id']).verify(core_session, receipt['entity_id'])
        persisted = await project_session.get(ExperimentExternalEntityReceipt, receipt['receipt_id'])
        acknowledgement = json.loads(persisted.acknowledgement_json or '{}')
        fields = ('store_id', 'entity_kind', 'entity_id', 'entity_revision_id', 'content_digest', 'contract_digest', 'verifier_id', 'reopen_uri')
        if any(fresh.get(k) != acknowledgement.get(k) for k in fields) or (persisted.store_id, persisted.entity_kind, persisted.entity_id, persisted.generation_or_revision, persisted.content_digest) != (fresh.get('store_id'), fresh.get('entity_kind'), fresh.get('entity_id'), str(fresh.get('entity_revision_id')), fresh.get('content_digest')):
            raise ValueError('receipt differs from producer authority')
        design = await core_session.get(Design, receipt['entity_id'])
        path = Path(resolve_runtime_data_path(design.pdb_path))
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt['content_digest']:
            raise ValueError('structure differs from receipt')
        parser = MMCIFParser(QUIET=True) if path.suffix.lower() in ('.cif', '.mmcif') else PDBParser(QUIET=True)
        structure = parser.get_structure('design', io.StringIO(raw.decode('utf-8')))
        model = next(structure.get_models())
        is_mmcif = path.suffix.lower() in ('.cif', '.mmcif')
        # Bio.PDB synthesizes a serial when the input has no model number.
        has_model_number = ('_atom_site.pdbx_PDB_model_num' in parser._mmcif_dict if is_mmcif
                            else any(line.startswith(b'MODEL ') for line in raw.splitlines()))
        model_serial = model.serial_num if has_model_number else None
        sequences = {}
        residue_maps = {}
        for item in model:
            residues = [r for r in item if Polypeptide.is_aa(r, standard=False)]
            sequence = ''.join(Polypeptide.protein_letters_3to1_extended.get(r.resname.upper(), 'X') for r in residues)
            if sequence:
                sequences[item.id] = sequence
                residue_maps[item.id] = [
                    {'observed_index': i, 'chain_id': item.id,
                     'model_index': model.id, 'model_serial': model_serial,
                     'sequence_number': r.id[1], 'insertion_code': r.id[2].strip(),
                     'hetero_flag': r.id[0].strip(), 'residue_name': r.resname}
                    for i, r in enumerate(residues)
                ]
        if not sequences:
            raise ValueError('no observed protein chains')
    except (AdapterError, OSError, ValueError, KeyError, TypeError, StopIteration, UnicodeError) as exc:
        return {**result, 'status': 'unavailable', 'reason': str(exc)}
    common = {**result, 'source_kind': 'design_chain', 'source_identity': receipt['entity_id'], 'source_digest': receipt['content_digest'], 'molecule': 'protein', 'chains': [{'chain_id': k, 'length': len(v)} for k, v in sequences.items()]}
    if chain is None:
        return {**common, 'status': 'choose_chain'}
    if chain not in sequences:
        return {**common, 'status': 'unavailable', 'reason': 'chain not found'}
    sequence = sequences[chain]
    first = 0 if start is None else start
    last = len(sequence) if end is None else end
    if type(first) is not int or type(last) is not int or first < 0 or last <= first or last > len(sequence):
        raise ValidationFailure('invalid sequence region')
    return {**common, 'status': 'resolved', 'chain': chain, 'label': f'{design.name} chain {chain}', 'start': first, 'end': last, 'source_positions': list(range(first, last)), 'sequence': sequence[first:last],
            'structure_residue_map': {'schema': 'bms.family-msa.structure-residue-map.v1',
                'source_identity': receipt['entity_id'], 'source_digest': receipt['content_digest'],
                'format': 'mmcif' if path.suffix.lower() in ('.cif', '.mmcif') else 'pdb',
                'numbering': 'Bio.PDB author residue ids', 'model_selection': 'first parsed model',
                'entries': residue_maps[chain][first:last]}}
