"""Read-only Project Hub and Project Sequences aggregation service.

Reads stored rows across experiments.db, biomodstack.db, molbio_ngs.db, and
molbio.db without triggering jobs, recomputation, or domain auto-creation.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from database import Design, Job
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentRevision,
    ExperimentWorkflowSetupContext,
)
from molbio_models import (
    MolecularDocument,
    MolecularOperation,
    MolecularRevision,
    NucleotideSequence,
)
from molbio_ngs_models import (
    MolBioNGSDomainState,
    MolBioNGSDomainStateMember,
    MolBioNGSDomainStateRevision,
    MolBioNGSMemberReceipt,
    MolBioNGSReferenceResource,
    MolBioNGSReferenceRevision,
    MolBioNGSSample,
    MolBioNGSSampleRevision,
)

_CORE_STORES = {"core", "bms-core-development"}
_NGS_MODEL_PREFIXES = ("ont", "ngs", "dorado", "epi2me", "nextflow_ngs", "nanopore", "minknow")
_ALIGNMENT_MODEL_HINTS = ("align", "msa", "mmseqs", "mafft", "clustal", "muscle", "family_msa")
_ALIGNMENT_ENTITY_KINDS = {
    "family_msa_import",
    "family_alignment",
    "alignment_result",
    "alignment_session",
    "alignment_comparison",
    "sequence_alignment",
    "sequence_alignment_result",
}
_NGS_REF_ENTITY_KINDS = {"ngs_molbio_reference_revision", "ngs_reference_revision"}
_NGS_SAMPLE_ENTITY_KINDS = {"ngs_molbio_sample_revision", "ngs_sample_revision"}
_INTERNAL_BOOKKEEPING_KINDS = {
    "ngs_molbio_domain_state",
    "ngs_molbio_global_binding",
    "ngs_molbio_project_binding",
    "domain_state",
    "global_binding",
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _safe_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _normalize_scope(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return "global"
    raw = payload.get("project_scope")
    if raw == "ngs_molbio_local":
        return "ngs_molbio_local"
    return "global"


def resolve_hub_capabilities(request: Request | None) -> dict[str, bool]:
    """Derive hub section availability from runtime features and mounted routes."""
    if request is not None:
        override = getattr(request.app.state, "hub_capabilities_override", None)
        if isinstance(override, Mapping):
            return {
                "has_in_silico": bool(override.get("has_in_silico", True)),
                "has_molbio": bool(override.get("has_molbio", True)),
                "has_ngs_ont": bool(override.get("has_ngs_ont", True)),
                "has_stats": False,
            }

    from biomodstack_runtime_profile import resolve_install_features
    from molbio_ngs_database import get_molbio_ngs_startup_error
    from services.runtime_features import effective_runtime_features

    installed = resolve_install_features()
    mounted = effective_runtime_features(request, installed) if request is not None else installed
    return {
        "has_in_silico": bool(installed["protein_compute"] and mounted["protein_compute"]),
        "has_molbio": True,  # The molecular toolset is independent of optional NGS hardware.
        "has_ngs_ont": bool(installed["ngs_ont"] and mounted["ngs_ont"] and not get_molbio_ngs_startup_error()),
        "has_stats": False,
    }


def _designer_sequence_href(
    project_id: str,
    sequence_id: str,
    *,
    pinned_revision_id: str | None = None,
    experiment_id: str | None = None,
    domain_experiment_id: str | None = None,
    state_revision_id: str | None = None,
) -> str:
    params: dict[str, str] = {
        "project_id": project_id,
        "molbio_sequence_id": sequence_id,
    }
    if pinned_revision_id:
        params["molbio_revision_id"] = pinned_revision_id
        if experiment_id and domain_experiment_id and state_revision_id:
            params["workspace_id"] = project_id
            params["global_experiment_id"] = experiment_id
            params["domain_experiment_id"] = domain_experiment_id
            params["state_revision_id"] = state_revision_id
    return "/designer?" + urlencode(params)


def _calc_gc(seq_text: str | None) -> float | None:
    if not seq_text:
        return None
    s = seq_text.upper()
    total = len(s)
    if total == 0:
        return None
    gc = s.count("G") + s.count("C")
    return round(100.0 * gc / total, 2)


def _sequence_from_sources(
    seq_row: NucleotideSequence | None,
    doc_row: MolecularDocument | None,
    rev_row: MolecularRevision | None,
    *,
    project_id: str,
    experiment_id: str | None,
    domain_experiment_id: str,
    state_revision_id: str,
    receipt_id: str,
    is_current_state: bool,
) -> dict[str, Any] | None:
    snapshot = rev_row.snapshot if (rev_row is not None and isinstance(rev_row.snapshot, dict)) else {}
    sequence_id = (
        (seq_row.id if seq_row is not None else None)
        or (doc_row.id if doc_row is not None else None)
        or (rev_row.document_id if rev_row is not None else None)
        or snapshot.get("id")
    )
    if not sequence_id:
        return None

    is_head = bool(
        is_current_state
        and seq_row is not None
        and doc_row is not None
        and doc_row.deleted_at is None
        and (rev_row is None or doc_row.current_revision_id == rev_row.id)
    )

    if is_head and seq_row is not None:
        name = seq_row.name
        description = seq_row.description
        sequence_type = seq_row.sequence_type or "dna"
        strandedness = seq_row.molecule_strandedness or "unknown"
        orientation = seq_row.molecule_orientation or "unknown"
        is_circular = bool(seq_row.is_circular)
        length = int(seq_row.length or len(seq_row.sequence or ""))
        gc_content = seq_row.gc_content if seq_row.gc_content is not None else _calc_gc(seq_row.sequence)
        features = seq_row.features if isinstance(seq_row.features, list) else []
        primers = seq_row.primers if isinstance(seq_row.primers, list) else []
        organism = seq_row.organism
        accession = seq_row.accession
        source_file = seq_row.source_file
        parent_id = seq_row.parent_id
        operation = seq_row.operation
        version = int(seq_row.version or 1)
        created_at = _iso(seq_row.created_at)
        updated_at = _iso(seq_row.updated_at or seq_row.created_at)
    else:
        seq_text = str(snapshot.get("sequence") or (seq_row.sequence if seq_row else ""))
        name = str(snapshot.get("name") or (doc_row.name if doc_row else None) or (seq_row.name if seq_row else sequence_id))
        description = snapshot.get("description") if "description" in snapshot else (seq_row.description if seq_row else None)
        sequence_type = str(snapshot.get("sequence_type") or (seq_row.sequence_type if seq_row else "dna"))
        strandedness = str(snapshot.get("molecule_strandedness") or (seq_row.molecule_strandedness if seq_row else "unknown"))
        orientation = str(snapshot.get("molecule_orientation") or (seq_row.molecule_orientation if seq_row else "unknown"))
        is_circular = bool(snapshot.get("is_circular") if "is_circular" in snapshot else (seq_row.is_circular if seq_row else False))
        length = int(snapshot.get("length") or (rev_row.content_length if rev_row else 0) or len(seq_text))
        gc_val = snapshot.get("gc_content")
        gc_content = float(gc_val) if gc_val is not None else _calc_gc(seq_text)
        features = snapshot.get("features") if isinstance(snapshot.get("features"), list) else []
        primers = snapshot.get("primers") if isinstance(snapshot.get("primers"), list) else []
        organism = snapshot.get("organism")
        accession = snapshot.get("accession")
        source_file = snapshot.get("source_file")
        parent_id = snapshot.get("parent_id")
        operation = snapshot.get("operation")
        version = int(rev_row.revision_number if rev_row else (seq_row.version if seq_row else 1))
        created_at = _iso(rev_row.created_at if rev_row else (seq_row.created_at if seq_row else None))
        updated_at = created_at

    pinned_rev_id = None if is_head else (rev_row.id if rev_row else None)
    reopen_href = _designer_sequence_href(
        project_id,
        sequence_id,
        pinned_revision_id=pinned_rev_id,
        experiment_id=experiment_id,
        domain_experiment_id=domain_experiment_id,
        state_revision_id=state_revision_id,
    )
    return {
        "id": sequence_id,
        "name": name,
        "description": description,
        "sequence_type": sequence_type,
        "molecule_strandedness": strandedness,
        "molecule_orientation": orientation,
        "is_circular": is_circular,
        "length": length,
        "gc_content": gc_content,
        "feature_count": len(features),
        "primer_count": len(primers),
        "organism": organism,
        "accession": accession,
        "source_file": source_file,
        "parent_id": parent_id,
        "operation": operation,
        "version": version,
        "created_at": created_at,
        "updated_at": updated_at,
        "domain_experiment_id": domain_experiment_id,
        "experiment_id": experiment_id,
        "state_revision_id": state_revision_id,
        "receipt_id": receipt_id,
        "revision_id": rev_row.id if rev_row else None,
        "reopen_href": reopen_href,
        "is_head_editable": is_head,
        "pinned_revision_id": pinned_rev_id,
    }


async def _load_project_head_and_payload(
    session: AsyncSession,
    project_id: str,
) -> tuple[ExperimentAggregateHead, dict[str, Any]] | None:
    row = (
        await session.execute(
            select(ExperimentAggregateHead, ExperimentRevision.canonical_payload)
            .join(
                ExperimentRevision,
                ExperimentRevision.resource_id == ExperimentAggregateHead.current_revision_id,
            )
            .where(
                ExperimentAggregateHead.aggregate_id == project_id,
                ExperimentAggregateHead.aggregate_kind == "workspace",
            )
        )
    ).first()
    if row is None:
        return None
    return row[0], _safe_json(row[1])


async def get_project_sequences_payload(
    session: AsyncSession,
    native_session: AsyncSession | None,
    molbio_session: AsyncSession,
    *,
    project_id: str,
    domain_experiment_id: str | None = None,
    state_revision_id: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any] | None:
    """Return deduplicated sequences across a project's NGS/MolBio domains."""
    loaded = await _load_project_head_and_payload(session, project_id)
    if loaded is None:
        return None
    project_head, project_payload = loaded
    project_scope = _normalize_scope(project_payload)

    domain_rows = (
        await session.execute(
            select(ExperimentAggregateHead).where(
                ExperimentAggregateHead.workspace_id == project_id,
                ExperimentAggregateHead.aggregate_kind == "domain_experiment",
            )
            .order_by(ExperimentAggregateHead.updated_at.desc(), ExperimentAggregateHead.aggregate_id.desc())
        )
    ).scalars().all()

    if experiment_id:
        domain_rows = [d for d in domain_rows if d.parent_id == experiment_id]
    if domain_experiment_id:
        domain_rows = [d for d in domain_rows if d.aggregate_id == domain_experiment_id]

    domain_ids = [d.aggregate_id for d in domain_rows]
    domain_by_id = {d.aggregate_id: d for d in domain_rows}
    if not domain_ids or native_session is None:
        return {
            "project_id": project_id,
            "project_name": project_payload.get("name") or project_head.display_name,
            "project_scope": project_scope,
            "domains": [],
            "items": [],
        }

    states = (
        await native_session.execute(
            select(MolBioNGSDomainState).where(
                MolBioNGSDomainState.global_domain_experiment_id.in_(domain_ids)
            )
        )
    ).scalars().all()
    state_by_domain = {s.global_domain_experiment_id: s for s in states}

    domains_summary: list[dict[str, Any]] = []
    target_state_revs: list[tuple[str, str, bool]] = []  # (domain_id, state_rev_id, is_current)
    for dom in domain_rows:
        st = state_by_domain.get(dom.aggregate_id)
        if st is None:
            continue
        domains_summary.append(
            {
                "domain_experiment_id": dom.aggregate_id,
                "experiment_id": dom.parent_id,
                "name": dom.display_name,
                "current_state_revision_id": st.current_state_revision_id,
            }
        )
        if domain_experiment_id and state_revision_id:
            is_cur = state_revision_id == st.current_state_revision_id
            target_state_revs.append((dom.aggregate_id, state_revision_id, is_cur))
        elif st.current_state_revision_id:
            target_state_revs.append((dom.aggregate_id, st.current_state_revision_id, True))

    if not target_state_revs:
        return {
            "project_id": project_id,
            "project_name": project_payload.get("name") or project_head.display_name,
            "project_scope": project_scope,
            "domains": domains_summary,
            "items": [],
        }

    state_rev_meta = {srev: (dom_id, is_cur) for dom_id, srev, is_cur in target_state_revs}
    state_rev_ids = list(state_rev_meta.keys())

    member_rows = (
        await native_session.execute(
            select(MolBioNGSDomainStateMember, MolBioNGSMemberReceipt)
            .join(
                MolBioNGSMemberReceipt,
                MolBioNGSMemberReceipt.receipt_id == MolBioNGSDomainStateMember.receipt_id,
            )
            .where(
                MolBioNGSDomainStateMember.state_revision_id.in_(state_rev_ids),
                MolBioNGSMemberReceipt.entity_kind == "molecular_revision",
            )
            .order_by(MolBioNGSDomainStateMember.ordinal.asc(), MolBioNGSDomainStateMember.receipt_id.asc())
        )
    ).all()

    if not member_rows:
        return {
            "project_id": project_id,
            "project_name": project_payload.get("name") or project_head.display_name,
            "project_scope": project_scope,
            "domains": domains_summary,
            "items": [],
        }

    rev_ids = list({receipt.entity_id for _member, receipt in member_rows if receipt.entity_id})
    revs = (
        await molbio_session.execute(
            select(MolecularRevision).where(MolecularRevision.id.in_(rev_ids))
        )
    ).scalars().all()
    rev_by_id = {r.id: r for r in revs}

    doc_ids = list({r.document_id for r in revs if r.document_id})
    docs = (
        await molbio_session.execute(
            select(MolecularDocument).where(MolecularDocument.id.in_(doc_ids))
        )
    ).scalars().all() if doc_ids else []
    doc_by_id = {d.id: d for d in docs}

    seqs = (
        await molbio_session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id.in_(doc_ids))
        )
    ).scalars().all() if doc_ids else []
    seq_by_id = {s.id: s for s in seqs}

    seen_sequences: dict[str, dict[str, Any]] = {}
    ordered_items: list[dict[str, Any]] = []
    for member, receipt in member_rows:
        dom_id, is_cur = state_rev_meta[member.state_revision_id]
        dom_head = domain_by_id.get(dom_id)
        rev_row = rev_by_id.get(receipt.entity_id)
        doc_id = rev_row.document_id if rev_row else None
        doc_row = doc_by_id.get(doc_id) if doc_id else None
        seq_row = seq_by_id.get(doc_id) if doc_id else None
        item = _sequence_from_sources(
            seq_row,
            doc_row,
            rev_row,
            project_id=project_id,
            experiment_id=dom_head.parent_id if dom_head else None,
            domain_experiment_id=dom_id,
            state_revision_id=member.state_revision_id,
            receipt_id=receipt.receipt_id,
            is_current_state=is_cur,
        )
        if item is None:
            continue
        sid = item["id"]
        if sid in seen_sequences:
            # Prefer editable head over older revision when deduping across domains
            if item["is_head_editable"] and not seen_sequences[sid]["is_head_editable"]:
                idx = ordered_items.index(seen_sequences[sid])
                ordered_items[idx] = item
                seen_sequences[sid] = item
            continue
        seen_sequences[sid] = item
        ordered_items.append(item)

    return {
        "project_id": project_id,
        "project_name": project_payload.get("name") or project_head.display_name,
        "project_scope": project_scope,
        "domains": domains_summary,
        "items": ordered_items,
    }


def _classify_core_job(entity_kind: str, job: Job | None) -> str:
    if entity_kind in _ALIGNMENT_ENTITY_KINDS:
        return "alignment"
    model_id = (job.model_id or "").lower() if job is not None else ""
    mode = (job.mode or "").lower() if job is not None else ""
    if any(model_id.startswith(p) or mode.startswith(p) for p in _NGS_MODEL_PREFIXES):
        return "ngs_job"
    if any(h in model_id or h in mode for h in _ALIGNMENT_MODEL_HINTS):
        return "alignment"
    return "in_silico"


def _in_silico_open_href(entity_kind: str, entity_id: str, job: Job | None, ack: dict[str, Any], project_id: str) -> str:
    if entity_kind == "conformational_mapping_source":
        return f"/projects/{quote(project_id, safe='')}/conformational-sources/{quote(entity_id, safe='')}"
    reopen = ack.get("reopen_uri")
    if isinstance(reopen, str) and reopen.startswith("/") and not reopen.startswith("/projects/"):
        return reopen
    if entity_kind == "conformational_mapping_request":
        return f"/designs/{entity_id}" if job is not None else f"/jobs/{entity_id}"
    if job is not None:
        if (job.status or "").lower() == "completed":
            return f"/designs/{job.id}"
        return f"/jobs/{job.id}"
    return f"/jobs/{entity_id}"


def _format_seq_subtitle(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("sequence_type", "dna")).upper(),
        "Circular" if item.get("is_circular") else "Linear",
        f"{int(item.get('length') or 0):,} bp",
    ]
    gc = item.get("gc_content")
    if gc is not None:
        parts.append(f"{float(gc):.1f}% GC")
    fc = int(item.get("feature_count") or 0)
    if fc:
        parts.append(f"{fc} features" if fc != 1 else "1 feature")
    return " · ".join(parts)


async def get_projects_summary_counts(
    session: AsyncSession,
    native_session: AsyncSession | None,
    molbio_session: AsyncSession,
    project_ids: list[str],
) -> dict[str, dict[str, int]]:
    """Batched per-project 4-bucket counts for the /projects index table."""
    counts: dict[str, dict[str, int]] = {
        pid: {
            "in_silico": 0,
            "molecular_sequences": 0,
            "molecular_operations": 0,
            "ngs_ont_and_alignments": 0,
        }
        for pid in project_ids
    }
    if not project_ids:
        return counts

    # 1. Receipts in experiments.db (in_silico, molbio_operation, alignments, receipt-only refs/samples)
    receipt_rows = (
        await session.execute(
            select(
                ExperimentExternalEntityReceipt.workspace_id,
                ExperimentExternalEntityReceipt.store_id,
                ExperimentExternalEntityReceipt.entity_kind,
                ExperimentExternalEntityReceipt.entity_id,
            ).where(ExperimentExternalEntityReceipt.workspace_id.in_(project_ids))
        )
    ).all()

    in_silico_ids: dict[str, set[str]] = defaultdict(set)
    op_ids: dict[str, set[str]] = defaultdict(set)
    align_ids: dict[str, set[str]] = defaultdict(set)
    ref_ids_from_rcpt: dict[str, set[str]] = defaultdict(set)
    sample_ids_from_rcpt: dict[str, set[str]] = defaultdict(set)

    for wid, store_id, entity_kind, entity_id in receipt_rows:
        if entity_kind in _INTERNAL_BOOKKEEPING_KINDS:
            continue
        if entity_kind == "molbio_operation":
            op_ids[wid].add(entity_id)
        elif entity_kind in _ALIGNMENT_ENTITY_KINDS:
            align_ids[wid].add(entity_id)
        elif entity_kind in _NGS_REF_ENTITY_KINDS:
            ref_ids_from_rcpt[wid].add(entity_id)
        elif entity_kind in _NGS_SAMPLE_ENTITY_KINDS:
            sample_ids_from_rcpt[wid].add(entity_id)
        elif store_id in _CORE_STORES:
            in_silico_ids[wid].add(entity_id)

    # 2. Domain experiments -> molbio_ngs.db current sequences, operations, samples, references
    dom_rows = (
        await session.execute(
            select(ExperimentAggregateHead.aggregate_id, ExperimentAggregateHead.workspace_id).where(
                ExperimentAggregateHead.workspace_id.in_(project_ids),
                ExperimentAggregateHead.aggregate_kind == "domain_experiment",
            )
        )
    ).all()
    dom_to_ws = {did: wid for did, wid in dom_rows}
    all_dom_ids = list(dom_to_ws.keys())

    seq_ids: dict[str, set[str]] = defaultdict(set)
    sample_ids: dict[str, set[str]] = defaultdict(set)
    ref_ids: dict[str, set[str]] = defaultdict(set)

    if all_dom_ids and native_session is not None:
        states = (
            await native_session.execute(
                select(
                    MolBioNGSDomainState.global_domain_experiment_id,
                    MolBioNGSDomainState.current_state_revision_id,
                ).where(MolBioNGSDomainState.global_domain_experiment_id.in_(all_dom_ids))
            )
        ).all()
        srev_to_ws = {
            srev: dom_to_ws[did]
            for did, srev in states
            if srev and did in dom_to_ws
        }
        if srev_to_ws:
            mem_rows = (
                await native_session.execute(
                    select(
                        MolBioNGSDomainStateMember.state_revision_id,
                        MolBioNGSMemberReceipt.entity_kind,
                        MolBioNGSMemberReceipt.entity_id,
                    )
                    .join(
                        MolBioNGSMemberReceipt,
                        MolBioNGSMemberReceipt.receipt_id == MolBioNGSDomainStateMember.receipt_id,
                    )
                    .where(
                        MolBioNGSDomainStateMember.state_revision_id.in_(list(srev_to_ws.keys())),
                        MolBioNGSMemberReceipt.entity_kind.in_(("molecular_revision", "molecular_operation")),
                    )
                )
            ).all()
            mol_rev_ids = list({eid for _srev, ek, eid in mem_rows if ek == "molecular_revision"})
            rev_to_doc: dict[str, str] = {}
            if mol_rev_ids:
                rev_to_doc = dict(
                    (
                        await molbio_session.execute(
                            select(MolecularRevision.id, MolecularRevision.document_id).where(
                                MolecularRevision.id.in_(mol_rev_ids)
                            )
                        )
                    ).all()
                )
            for srev, ek, eid in mem_rows:
                wid = srev_to_ws[srev]
                if ek == "molecular_revision":
                    seq_ids[wid].add(rev_to_doc.get(eid, eid))
                elif ek == "molecular_operation":
                    op_ids[wid].add(eid)

        samp_rows = (
            await native_session.execute(
                select(MolBioNGSSample.global_domain_experiment_id, MolBioNGSSample.id).where(
                    MolBioNGSSample.global_domain_experiment_id.in_(all_dom_ids),
                    MolBioNGSSample.archived_at.is_(None),
                )
            )
        ).all()
        for did, sid in samp_rows:
            sample_ids[dom_to_ws[did]].add(sid)

        ref_rows = (
            await native_session.execute(
                select(
                    MolBioNGSReferenceResource.global_domain_experiment_id,
                    MolBioNGSReferenceResource.id,
                ).where(
                    MolBioNGSReferenceResource.global_domain_experiment_id.in_(all_dom_ids),
                    MolBioNGSReferenceResource.archived_at.is_(None),
                )
            )
        ).all()
        for did, rid in ref_rows:
            ref_ids[dom_to_ws[did]].add(rid)

    for pid in project_ids:
        # Avoid double counting sample/ref receipts when native table rows exist
        total_samples = len(sample_ids[pid]) if sample_ids[pid] else len(sample_ids_from_rcpt[pid])
        total_refs = len(ref_ids[pid]) if ref_ids[pid] else len(ref_ids_from_rcpt[pid])
        counts[pid] = {
            "in_silico": len(in_silico_ids[pid]),
            "molecular_sequences": len(seq_ids[pid]),
            "molecular_operations": len(op_ids[pid]),
            "ngs_ont_and_alignments": total_samples + total_refs + len(align_ids[pid]),
        }
    return counts


async def get_project_hub_payload(
    request: Request | None,
    session: AsyncSession,
    core_session: AsyncSession,
    native_session: AsyncSession | None,
    molbio_session: AsyncSession,
    *,
    project_id: str,
    experiment_id: str | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any] | None:
    """Compose the unified read-only Project Hub payload."""
    loaded = await _load_project_head_and_payload(session, project_id)
    if loaded is None:
        return None
    project_head, project_payload = loaded
    project_scope = _normalize_scope(project_payload)
    capabilities = resolve_hub_capabilities(request)
    if not capabilities["has_ngs_ont"]:
        native_session = None

    # Load experiments and domain experiments
    child_heads = (
        await session.execute(
            select(ExperimentAggregateHead).where(
                ExperimentAggregateHead.workspace_id == project_id,
                ExperimentAggregateHead.aggregate_kind.in_(("experiment", "domain_experiment")),
            )
            .order_by(ExperimentAggregateHead.created_at.asc(), ExperimentAggregateHead.aggregate_id.asc())
        )
    ).scalars().all()
    experiments = [h for h in child_heads if h.aggregate_kind == "experiment"]
    domains = [h for h in child_heads if h.aggregate_kind == "domain_experiment"]
    domain_to_exp = {d.aggregate_id: d.parent_id for d in domains}
    exp_ids = {e.aggregate_id for e in experiments}
    domain_ids = [d.aggregate_id for d in domains]

    # Map receipt resource_id -> experiment_id via lineage edges
    receipt_rows = (
        await session.execute(
            select(ExperimentExternalEntityReceipt)
            .where(ExperimentExternalEntityReceipt.workspace_id == project_id)
            .order_by(ExperimentExternalEntityReceipt.created_at.desc(), ExperimentExternalEntityReceipt.resource_id.desc())
        )
    ).scalars().all()

    receipt_res_ids = [r.resource_id for r in receipt_rows]
    receipt_to_exp: dict[str, str | None] = {}
    if receipt_res_ids:
        edge_rows = (
            await session.execute(
                select(
                    ExperimentLineageEdge.source_resource_id,
                    ExperimentLineageEdge.target_resource_id,
                ).where(ExperimentLineageEdge.target_resource_id.in_(receipt_res_ids))
            )
        ).all()
        for src, tgt in edge_rows:
            if src in domain_to_exp:
                receipt_to_exp[tgt] = domain_to_exp[src]
            elif src in exp_ids and tgt not in receipt_to_exp:
                receipt_to_exp[tgt] = src

    # Resolve core jobs + headline metrics in 2 batched queries
    core_job_ids = list({
        r.entity_id
        for r in receipt_rows
        if r.store_id in _CORE_STORES and r.entity_kind not in _INTERNAL_BOOKKEEPING_KINDS
    })
    jobs_by_id: dict[str, Job] = {}
    design_stats_by_job: dict[str, dict[str, Any]] = {}
    if core_job_ids:
        job_rows = (
            await core_session.execute(
                select(Job)
                .options(
                    load_only(
                        Job.id,
                        Job.name,
                        Job.model_id,
                        Job.mode,
                        Job.status,
                        Job.stage_family,
                        Job.stage_mode,
                        Job.created_at,
                        Job.completed_at,
                    )
                )
                .where(Job.id.in_(core_job_ids))
            )
        ).scalars().all()
        jobs_by_id = {j.id: j for j in job_rows}

        try:
            design_rows = (
                await core_session.execute(
                    select(
                        Design.job_id,
                        func.count(Design.id),
                        func.max(Design.iptm),
                        func.max(Design.ptm),
                        func.max(Design.plddt_mean),
                    )
                    .where(Design.job_id.in_(core_job_ids))
                    .group_by(Design.job_id)
                )
            ).all()
            for jid, cnt, max_iptm, max_ptm, max_plddt in design_rows:
                design_stats_by_job[jid] = {
                    "count": int(cnt or 0),
                    "iptm": float(max_iptm) if max_iptm is not None else None,
                    "ptm": float(max_ptm) if max_ptm is not None else None,
                    "plddt": float(max_plddt) if max_plddt is not None else None,
                }
        except Exception:
            design_stats_by_job = {}

    # Build sections: in_silico, ngs_ont_and_alignments, molecular_operations
    in_silico_items: list[dict[str, Any]] = []
    seen_core_jobs: set[str] = set()
    ngs_ont_items: list[dict[str, Any]] = []
    alignment_items: list[dict[str, Any]] = []
    seen_alignments: set[str] = set()
    op_receipt_meta: dict[str, tuple[str | None, str | None]] = {}  # op_id -> (exp_id, created_at)
    rcpt_sample_rows: list[tuple[ExperimentExternalEntityReceipt, str | None]] = []
    rcpt_ref_rows: list[tuple[ExperimentExternalEntityReceipt, str | None]] = []

    for r in receipt_rows:
        if r.entity_kind in _INTERNAL_BOOKKEEPING_KINDS:
            continue
        ack = _safe_json(r.acknowledgement_json)
        exp_id = receipt_to_exp.get(r.resource_id) or ack.get("global_experiment_id")
        if exp_id not in exp_ids:
            exp_id = receipt_to_exp.get(r.resource_id)

        if r.entity_kind == "molbio_operation":
            if r.entity_id not in op_receipt_meta:
                op_receipt_meta[r.entity_id] = (exp_id, _iso(r.created_at))
            continue
        if r.entity_kind in _NGS_SAMPLE_ENTITY_KINDS:
            rcpt_sample_rows.append((r, exp_id))
            continue
        if r.entity_kind in _NGS_REF_ENTITY_KINDS:
            rcpt_ref_rows.append((r, exp_id))
            continue
        if r.entity_kind in _ALIGNMENT_ENTITY_KINDS:
            if r.entity_id in seen_alignments:
                continue
            seen_alignments.add(r.entity_id)
            title = str(
                ack.get("title")
                or ack.get("name")
                or ack.get("display_name")
                or ack.get("query_name")
                or r.entity_id
            )
            reopen = ack.get("reopen_uri")
            open_href = (
                reopen
                if isinstance(reopen, str) and reopen.startswith("/") and not reopen.startswith("/projects/")
                else f"/alignments?project_id={project_id}"
            )
            badge = "Family MSA" if r.entity_kind == "family_msa_import" else "Alignment"
            subtitle_parts = []
            if ack.get("sequence_count"):
                subtitle_parts.append(f"{ack['sequence_count']} sequences")
            if ack.get("engine"):
                subtitle_parts.append(str(ack["engine"]))
            alignment_items.append(
                {
                    "id": r.entity_id,
                    "title": title,
                    "subtitle": " · ".join(subtitle_parts) or r.entity_kind.replace("_", " "),
                    "kind": "alignment",
                    "badges": [badge],
                    "status": str(ack.get("status") or "completed"),
                    "experiment_id": exp_id,
                    "updated_at": _iso(r.created_at),
                    "open_href": open_href,
                    "detach": None,
                }
            )
            continue

        if r.store_id in _CORE_STORES:
            if r.entity_id in seen_core_jobs:
                continue
            seen_core_jobs.add(r.entity_id)
            job = jobs_by_id.get(r.entity_id)
            bucket = _classify_core_job(r.entity_kind, job)
            title = (
                (job.name if job is not None and job.name else None)
                or ack.get("job_name")
                or ack.get("name")
                or ack.get("title")
                or r.entity_id
            )
            model_label = (
                (job.model_id if job is not None and job.model_id else None)
                or ack.get("model_id")
                or r.entity_kind
            )
            mode_label = (job.mode if job is not None and job.mode else None) or ack.get("mode")
            status_val = (job.status if job is not None and job.status else None) or ack.get("status") or "recorded"
            updated_val = _iso((job.completed_at or job.created_at) if job is not None else r.created_at)

            stats = design_stats_by_job.get(r.entity_id)
            sub_parts: list[str] = []
            if mode_label:
                sub_parts.append(str(mode_label).replace("_", " "))
            if stats:
                if stats["count"]:
                    sub_parts.append(f"{stats['count']} designs")
                if stats["iptm"] is not None:
                    sub_parts.append(f"ipTM {stats['iptm']:.2f}")
                elif stats["ptm"] is not None:
                    sub_parts.append(f"pTM {stats['ptm']:.2f}")
                elif stats["plddt"] is not None:
                    sub_parts.append(f"pLDDT {stats['plddt']:.1f}")

            item_obj = {
                "id": r.entity_id,
                "title": str(title),
                "subtitle": " · ".join(sub_parts),
                "kind": bucket,
                "badges": [str(model_label)] if model_label else [],
                "status": str(status_val),
                "experiment_id": exp_id,
                "updated_at": updated_val,
                "open_href": _in_silico_open_href(r.entity_kind, r.entity_id, job, ack, project_id),
                "detach": None,
            }
            if bucket == "alignment":
                alignment_items.append(item_obj)
            elif bucket == "ngs_job":
                ngs_ont_items.append(item_obj)
            else:
                in_silico_items.append(item_obj)

    # Molecular sequences (using the exact same server helper as /sequences)
    seq_payload = await get_project_sequences_payload(
        session,
        native_session,
        molbio_session,
        project_id=project_id,
    )
    raw_sequences = (seq_payload or {}).get("items", [])
    molecular_sequence_items: list[dict[str, Any]] = []
    for seq in raw_sequences:
        badges = [
            str(seq.get("sequence_type", "dna")).upper(),
            "Circular" if seq.get("is_circular") else "Linear",
            f"{int(seq.get('length') or 0):,} bp",
        ]
        molecular_sequence_items.append(
            {
                "id": seq["id"],
                "title": seq["name"],
                "subtitle": _format_seq_subtitle(seq),
                "kind": "molecular_sequence",
                "badges": badges,
                "status": "head" if seq.get("is_head_editable") else "pinned",
                "experiment_id": seq.get("experiment_id"),
                "updated_at": seq.get("updated_at") or seq.get("created_at"),
                "open_href": seq["reopen_href"],
                "detach": {
                    "method": "remove_molecular_member",
                    "target_id": seq["receipt_id"],
                    "domain_experiment_id": seq["domain_experiment_id"],
                    "expected_state_revision_id": seq["state_revision_id"],
                    "experiment_id": seq.get("experiment_id"),
                    "sequence_id": seq["id"],
                },
            }
        )

    # Native NGS samples, references, and molecular_operation members from molbio_ngs.db
    if domain_ids and native_session is not None:
        states = (
            await native_session.execute(
                select(MolBioNGSDomainState).where(
                    MolBioNGSDomainState.global_domain_experiment_id.in_(domain_ids)
                )
            )
        ).scalars().all()
        srev_to_dom = {
            s.current_state_revision_id: s.global_domain_experiment_id
            for s in states
            if s.current_state_revision_id
        }
        if srev_to_dom:
            op_members = (
                await native_session.execute(
                    select(
                        MolBioNGSDomainStateMember.state_revision_id,
                        MolBioNGSMemberReceipt.entity_id,
                        MolBioNGSMemberReceipt.created_at,
                    )
                    .join(
                        MolBioNGSMemberReceipt,
                        MolBioNGSMemberReceipt.receipt_id == MolBioNGSDomainStateMember.receipt_id,
                    )
                    .where(
                        MolBioNGSDomainStateMember.state_revision_id.in_(list(srev_to_dom.keys())),
                        MolBioNGSMemberReceipt.entity_kind == "molecular_operation",
                    )
                )
            ).all()
            for srev, op_id, created_at in op_members:
                if op_id not in op_receipt_meta:
                    dom_id = srev_to_dom[srev]
                    op_receipt_meta[op_id] = (domain_to_exp.get(dom_id), _iso(created_at))

        samples = (
            await native_session.execute(
                select(MolBioNGSSample)
                .where(
                    MolBioNGSSample.global_domain_experiment_id.in_(domain_ids),
                    MolBioNGSSample.archived_at.is_(None),
                )
                .order_by(MolBioNGSSample.updated_at.desc())
            )
        ).scalars().all()
        samp_rev_ids = [s.current_revision_id for s in samples if s.current_revision_id]
        samp_revs = (
            await native_session.execute(
                select(MolBioNGSSampleRevision).where(MolBioNGSSampleRevision.id.in_(samp_rev_ids))
            )
        ).scalars().all() if samp_rev_ids else []
        samp_rev_by_id = {sr.id: sr for sr in samp_revs}
        for s in samples:
            sr = samp_rev_by_id.get(s.current_revision_id) if s.current_revision_id else None
            spayload = _safe_json(sr.canonical_payload) if sr else {}
            dom_id = s.global_domain_experiment_id
            exp_id = domain_to_exp.get(dom_id)
            title = str(spayload.get("name") or spayload.get("sample_name") or spayload.get("label") or s.id)
            desc = str(spayload.get("description") or spayload.get("platform") or "")
            ngs_params = urlencode({
                k: v for k, v in (
                    ("workspace_id", project_id),
                    ("global_experiment_id", exp_id),
                    ("domain_experiment_id", dom_id),
                    ("sample_id", s.id),
                ) if v
            })
            ngs_ont_items.append(
                {
                    "id": s.id,
                    "title": title,
                    "subtitle": desc[:140],
                    "kind": "ngs_sample",
                    "badges": ["ONT sample"],
                    "status": "active",
                    "experiment_id": exp_id,
                    "updated_at": _iso(s.updated_at or s.created_at),
                    "open_href": f"/ngs?{ngs_params}",
                    "detach": None,
                }
            )

        refs = (
            await native_session.execute(
                select(MolBioNGSReferenceResource)
                .where(
                    MolBioNGSReferenceResource.global_domain_experiment_id.in_(domain_ids),
                    MolBioNGSReferenceResource.archived_at.is_(None),
                )
                .order_by(MolBioNGSReferenceResource.updated_at.desc())
            )
        ).scalars().all()
        ref_rev_ids = [r.current_revision_id for r in refs if r.current_revision_id]
        ref_revs = (
            await native_session.execute(
                select(MolBioNGSReferenceRevision).where(MolBioNGSReferenceRevision.id.in_(ref_rev_ids))
            )
        ).scalars().all() if ref_rev_ids else []
        ref_rev_by_id = {rr.id: rr for rr in ref_revs}
        for ref in refs:
            rr = ref_rev_by_id.get(ref.current_revision_id) if ref.current_revision_id else None
            rpayload = _safe_json(rr.canonical_payload) if rr else {}
            dom_id = ref.global_domain_experiment_id
            exp_id = domain_to_exp.get(dom_id)
            ngs_params = urlencode({
                k: v for k, v in (
                    ("workspace_id", project_id),
                    ("global_experiment_id", exp_id),
                    ("domain_experiment_id", dom_id),
                    ("reference_id", ref.id),
                ) if v
            })
            ngs_ont_items.append(
                {
                    "id": ref.id,
                    "title": ref.name or ref.id,
                    "subtitle": str(rpayload.get("description") or rpayload.get("format") or "Read-alignment reference")[:140],
                    "kind": "ngs_reference",
                    "badges": ["Reference"],
                    "status": "active",
                    "experiment_id": exp_id,
                    "updated_at": _iso(ref.updated_at or ref.created_at),
                    "open_href": f"/ngs?{ngs_params}",
                    "detach": None,
                }
            )

    # Fallback for receipt-only samples/references when no native rows exist
    if not any(i["kind"] == "ngs_sample" for i in ngs_ont_items) and rcpt_sample_rows:
        seen_s: set[str] = set()
        for r, exp_id in rcpt_sample_rows:
            if r.entity_id in seen_s:
                continue
            seen_s.add(r.entity_id)
            ack = _safe_json(r.acknowledgement_json)
            ngs_ont_items.append(
                {
                    "id": r.entity_id,
                    "title": str(ack.get("name") or ack.get("sample_name") or r.entity_id),
                    "subtitle": str(ack.get("description") or "ONT sample")[:140],
                    "kind": "ngs_sample",
                    "badges": ["ONT sample"],
                    "status": "active",
                    "experiment_id": exp_id,
                    "updated_at": _iso(r.created_at),
                    "open_href": f"/ngs?workspace_id={project_id}",
                    "detach": None,
                }
            )
    if not any(i["kind"] == "ngs_reference" for i in ngs_ont_items) and rcpt_ref_rows:
        seen_r: set[str] = set()
        for r, exp_id in rcpt_ref_rows:
            if r.entity_id in seen_r:
                continue
            seen_r.add(r.entity_id)
            ack = _safe_json(r.acknowledgement_json)
            ngs_ont_items.append(
                {
                    "id": r.entity_id,
                    "title": str(ack.get("name") or ack.get("reference_name") or r.entity_id),
                    "subtitle": str(ack.get("description") or "Reference revision")[:140],
                    "kind": "ngs_reference",
                    "badges": ["Reference"],
                    "status": "active",
                    "experiment_id": exp_id,
                    "updated_at": _iso(r.created_at),
                    "open_href": f"/ngs?workspace_id={project_id}",
                    "detach": None,
                }
            )

    # Resolve MolecularOperation details from molbio.db
    molecular_operation_items: list[dict[str, Any]] = []
    if op_receipt_meta:
        ops = (
            await molbio_session.execute(
                select(MolecularOperation)
                .where(MolecularOperation.id.in_(list(op_receipt_meta.keys())))
                .order_by(MolecularOperation.created_at.desc())
            )
        ).scalars().all()
        op_by_id = {op.id: op for op in ops}
        for op_id, (exp_id, rcpt_ts) in op_receipt_meta.items():
            op = op_by_id.get(op_id)
            op_kind = op.operation_kind if op is not None else "operation"
            params = op.parameters if (op is not None and isinstance(op.parameters, dict)) else {}
            title = str(
                params.get("name")
                or params.get("output_name")
                or op_kind.replace("_", " ").title()
            )
            molecular_operation_items.append(
                {
                    "id": op_id,
                    "title": title,
                    "subtitle": str(op.implementation if op else ""),
                    "kind": "molecular_operation",
                    "badges": [op_kind],
                    "status": op.status if op else "completed",
                    "experiment_id": exp_id,
                    "updated_at": _iso(op.created_at) if op else rcpt_ts,
                    "open_href": "/designer?" + urlencode({"project_id": project_id, "molbio_operation_id": op_id}),
                    "detach": None,
                }
            )

    # Compute per-experiment item counts across all sections before optional experiment_id filter
    exp_item_counts: dict[str, int] = defaultdict(int)
    all_items_unfiltered = (
        in_silico_items
        + molecular_sequence_items
        + molecular_operation_items
        + ngs_ont_items
        + alignment_items
    )
    for item in all_items_unfiltered:
        eid = item.get("experiment_id")
        if eid:
            exp_item_counts[eid] += 1

    experiments_list = [
        {
            "id": e.aggregate_id,
            "name": e.display_name,
            "status": e.lifecycle_state,
            "item_count": int(exp_item_counts.get(e.aggregate_id, 0)),
        }
        for e in experiments
    ]

    if experiment_id:
        in_silico_items = [i for i in in_silico_items if i.get("experiment_id") == experiment_id]
        molecular_sequence_items = [i for i in molecular_sequence_items if i.get("experiment_id") == experiment_id]
        molecular_operation_items = [i for i in molecular_operation_items if i.get("experiment_id") == experiment_id]
        ngs_ont_items = [i for i in ngs_ont_items if i.get("experiment_id") == experiment_id]
        alignment_items = [i for i in alignment_items if i.get("experiment_id") == experiment_id]

    ngs_ont_and_alignments = ngs_ont_items + alignment_items

    # Linked ngs_molbio_local projects for global projects
    linked_local_projects: list[dict[str, Any]] = []
    if project_scope == "global" and capabilities["has_in_silico"]:
        link_edges = (
            await session.execute(
                select(
                    ExperimentLineageEdge.source_resource_id,
                    ExperimentLineageEdge.target_resource_id,
                ).where(
                    or_(
                        ExperimentLineageEdge.target_resource_id == project_id,
                        ExperimentLineageEdge.source_resource_id == project_id,
                    ),
                    ExperimentLineageEdge.edge_key.like("ngs-molbio-project-link:%"),
                )
            )
        ).all()
        other_ids = list({
            (src if tgt == project_id else tgt)
            for src, tgt in link_edges
            if (src if tgt == project_id else tgt) != project_id
        })
        if other_ids:
            linked_rows = (
                await session.execute(
                    select(ExperimentAggregateHead, ExperimentRevision.canonical_payload)
                    .join(
                        ExperimentRevision,
                        ExperimentRevision.resource_id == ExperimentAggregateHead.current_revision_id,
                    )
                    .where(
                        ExperimentAggregateHead.aggregate_id.in_(other_ids),
                        ExperimentAggregateHead.aggregate_kind == "workspace",
                    )
                    .order_by(ExperimentAggregateHead.updated_at.desc())
                )
            ).all()
            local_heads = []
            for head, payload_text in linked_rows:
                pl = _safe_json(payload_text)
                if _normalize_scope(pl) == "ngs_molbio_local":
                    local_heads.append((head, pl))
            local_ids = [h.aggregate_id for h, _pl in local_heads]
            local_counts = await get_projects_summary_counts(session, native_session, molbio_session, local_ids)
            for head, pl in local_heads:
                linked_local_projects.append(
                    {
                        "id": head.aggregate_id,
                        "name": pl.get("name") or head.display_name,
                        "description": pl.get("research_objective") or pl.get("description") or "",
                        "lifecycle_state": head.lifecycle_state,
                        "updated_at": _iso(head.updated_at),
                        "counts": local_counts.get(head.aggregate_id, {}),
                    }
                )

    counts = {
        "in_silico": len(in_silico_items),
        "molecular_sequences": len(molecular_sequence_items),
        "molecular_operations": len(molecular_operation_items),
        "ngs_ont": len(ngs_ont_items),
        "alignments": len(alignment_items),
    }

    setups = (await session.execute(
        select(ExperimentWorkflowSetupContext.setup_context_id, ExperimentWorkflowSetupContext.lifecycle_state,
               ExperimentWorkflowSetupContext.capability_id, ExperimentAggregateHead.display_name)
        .join(ExperimentAggregateHead, ExperimentAggregateHead.aggregate_id == ExperimentWorkflowSetupContext.workflow_id)
        .where(ExperimentWorkflowSetupContext.project_id == project_id, ExperimentWorkflowSetupContext.deleted_at.is_(None))
        .order_by(ExperimentWorkflowSetupContext.updated_at.desc(), ExperimentWorkflowSetupContext.setup_context_id)
    )).all()
    buckets = {
        "in_silico": in_silico_items, "molecular_sequences": molecular_sequence_items,
        "molecular_operations": molecular_operation_items, "ngs_ont_and_alignments": ngs_ont_and_alignments,
    }
    # Advance each bucket by the same page width, with a deterministic tie breaker.
    if cursor is not None and (len(cursor) > 12 or not cursor.isascii() or not cursor.isdecimal()):
        raise HTTPException(422, detail="Invalid project hub cursor")
    offset = int(cursor or 0)
    end = offset + limit
    for items in buckets.values():
        items.sort(key=lambda row: (row.get("updated_at") or "", row["id"]), reverse=True)
    return {
        "next_cursor": str(end) if any(len(items) > end for items in buckets.values()) else None,
        "capabilities": capabilities,
        "project": {
            "id": project_head.aggregate_id,
            "name": project_payload.get("name") or project_head.display_name,
            "description": project_payload.get("research_objective") or project_payload.get("description") or "",
            "lifecycle_state": project_head.lifecycle_state,
            "project_scope": project_scope,
            "created_at": _iso(project_head.created_at),
            "updated_at": _iso(project_head.updated_at),
            "experiments": experiments_list,
            "workflow_setups": [{"id": sid, "state": state, "label": label or cap} for sid, state, cap, label in setups],
            "linked_local_projects": linked_local_projects,
        },
        "counts": counts,
        "sections": {name: items[offset:end] for name, items in buckets.items()},
    }
