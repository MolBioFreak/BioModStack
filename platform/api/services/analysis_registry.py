from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Sequence
import hashlib
import inspect
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from database import Design, Job
from paths import resolve_runtime_data_path
from services.aligned_error_utils import fingerprint_aligned_error_artifact
from services.result_contracts import resolve_result_contract


def _persisted_job_design_contract(design: Design):
    return resolve_result_contract(review_profile_id=design.review_profile_id)


def _design_supports_binder_metrics(design: Design) -> bool:
    capabilities = _persisted_job_design_contract(design).viewer_capabilities
    return "antibody_backbone_metrics" in capabilities or "complex_interface_metrics" in capabilities


def _design_supports_job_analysis(design: Design, analysis_type: str) -> bool:
    contract = _persisted_job_design_contract(design)
    if not contract.analysis_contract_id or contract.analysis_contract_id == "unsupported_legacy":
        return False
    if analysis_type in {JOB_AA_COMPOSITION_ANALYSIS, JOB_CDR_LOGO_PACK_ANALYSIS}:
        return "antibody_backbone_metrics" in contract.viewer_capabilities
    if analysis_type == JOB_CORRELATION_MATRIX_ANALYSIS:
        return _design_supports_binder_metrics(design)
    return False


STRUCTURE_SUMMARY_ANALYSIS = "structure_summary"
CONTACT_MAP_ANALYSIS = "contact_map"
CHAIN_METRICS_ANALYSIS = "chain_metrics"
FAMPNN_PSCE_PROFILE_ANALYSIS = "fampnn_psce_profile"
PAE_MATRIX_ANALYSIS = "pae_matrix"
IPSAE_INTERFACE_ANALYSIS = "ipsae_interface"
ANTIBODY_ANNOTATION_PACK_ANALYSIS = "antibody_annotation_pack"
JOB_CORRELATION_MATRIX_ANALYSIS = "job_correlation_matrix"
JOB_AA_COMPOSITION_ANALYSIS = "job_aa_composition"
JOB_CDR_LOGO_PACK_ANALYSIS = "job_cdr_logo_pack"

AnalysisParamsBuilder = Callable[[dict[str, Any] | None], dict[str, Any]]
InputSignatureBuilder = Callable[[Any, dict[str, Any], AsyncSession], str | Awaitable[str]]


@dataclass(frozen=True)
class AnalysisDefinition:
    analysis_type: str
    subject_kind: str
    version: str
    resource_class: str
    normalize_params: AnalysisParamsBuilder
    build_input_signature: InputSignatureBuilder


def _json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _structure_fingerprint(path_str: str | None) -> dict[str, Any]:
    path = Path(path_str or "").expanduser()
    resolved = resolve_runtime_data_path(path) if path.is_absolute() else path.resolve()
    if not resolved.exists():
        raise ValueError(f"Structure file not found: {resolved}")
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_design_id_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Sequence):
        raw_items = list(value)
    else:
        raw_items = []
    cleaned = sorted({str(item).strip() for item in raw_items if str(item).strip()})
    return cleaned


async def scientific_contract_revision(design: Design, session: AsyncSession) -> int | None:
    """Read launch authority, never a Design/user-supplied provenance claim."""
    from services.core_protein_scientific_contract import revision_for_job
    job = await session.scalar(
        select(Job).options(load_only(Job.id, Job.provenance)).where(Job.id == design.job_id)
    )
    return revision_for_job(job)


def unavailable_scientific_identity(design: Design, metric: str, reason: str) -> dict[str, Any]:
    return {
        "schema_name": "core_protein_viewer_metric", "schema_version": 1,
        "contract_revision": 1, "design_id": design.id, "design_name": design.name,
        "metric": metric, "status": "unavailable", "reason": reason,
        "document": None, "artifact_sha256": None, "producer_binding": None,
        "row_axis": None, "column_axis": None,
        "sampled_row_indices": None, "sampled_column_indices": None,
        "native_shape": None, "native_row_positions": None, "native_column_positions": None, "pae_matrix": None, "size": None,
    }


async def build_analysis_input_signature(
    definition: AnalysisDefinition,
    subject: Any,
    params: dict[str, Any],
    session: AsyncSession,
) -> str:
    if (definition.subject_kind == "design" and definition.analysis_type in {PAE_MATRIX_ANALYSIS, CHAIN_METRICS_ANALYSIS, IPSAE_INTERFACE_ANALYSIS}
            and await scientific_contract_revision(subject, session) == 1):
        from services.boltz_scientific_consumer import verified_boltz_design
        identity = {"core_protein_scientific_contract":1, "viewer_identity_adapter":2,
            "design_id":subject.id, "analysis_type":definition.analysis_type, "params":params}
        if definition.analysis_type == IPSAE_INTERFACE_ANALYSIS:
            identity.update(review_profile_id=subject.review_profile_id,
                review_role_map=subject.review_role_map,
                detected_antibody_chains=subject.detected_antibody_chains,
                detected_target_chain=subject.detected_target_chain,
                producer_record=subject.confidence_metrics)
        try:
            if not isinstance(getattr(subject, 'confidence_metrics', None), dict) or not subject.confidence_metrics.get('core_protein_scientific'):
                raise ValueError('missing_producer_native_axis_ledger')
            selected = await verified_boltz_design(subject, session)
        except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
            # This distinct namespace can only cache unavailable results. It
            # never falls back to legacy paths or reuses a prior healthy run.
            return _json_hash({**identity, "status":"unavailable"})
        return _json_hash({**identity, "status":"ok", "native":selected['native'],
            "artifacts":selected['artifacts'], "block":selected['block']})
    value = definition.build_input_signature(subject, params, session)
    if inspect.isawaitable(value):
        value = await value
    if definition.subject_kind == "design" and definition.analysis_type in {
        PAE_MATRIX_ANALYSIS, CHAIN_METRICS_ANALYSIS, CONTACT_MAP_ANALYSIS,
    } and await scientific_contract_revision(subject, session) == 1:
        return _json_hash({"legacy_signature": str(value), "core_protein_scientific_contract": 1,
                           "viewer_identity_adapter": 1, "candidate_id": subject.id})
    return str(value)


def normalize_structure_summary_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    return {}


def normalize_contact_map_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(raw or {})
    max_size = params.get("max_size", 300)
    try:
        max_size_value = int(max_size)
    except (TypeError, ValueError):
        max_size_value = 300
    max_size_value = max(50, min(max_size_value, 500))
    return {"max_size": max_size_value}


def normalize_chain_metrics_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    del raw
    return {}


def normalize_fampnn_psce_profile_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(raw or {})
    return {"ignore_cbeta": _normalize_bool(params.get("ignore_cbeta"), False)}


def normalize_pae_matrix_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(raw or {})
    max_size = params.get("max_size", 200)
    try:
        max_size_value = int(max_size)
    except (TypeError, ValueError):
        max_size_value = 200
    max_size_value = max(50, min(max_size_value, 500))
    return {"max_size": max_size_value}


def normalize_ipsae_interface_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(raw or {})
    try:
        pae_cutoff = float(params.get("pae_cutoff", 10.0))
    except (TypeError, ValueError):
        pae_cutoff = 10.0
    try:
        dist_cutoff = float(params.get("dist_cutoff", 10.0))
    except (TypeError, ValueError):
        dist_cutoff = 10.0
    pae_cutoff = max(0.0, min(pae_cutoff, 50.0))
    dist_cutoff = max(0.0, min(dist_cutoff, 50.0))
    return {"pae_cutoff": pae_cutoff, "dist_cutoff": dist_cutoff}


def normalize_antibody_annotation_pack_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    del raw
    return {}


def normalize_job_scope_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(raw or {})
    return {
        "include_children": _normalize_bool(params.get("include_children"), True),
        "design_ids": _normalize_design_id_list(params.get("design_ids")),
    }


def build_structure_summary_signature(design: Design, params: dict[str, Any], _session: AsyncSession) -> str:
    del params
    return _json_hash({
        "analysis_type": STRUCTURE_SUMMARY_ANALYSIS,
        "structure": _structure_fingerprint(design.pdb_path),
    })


def build_contact_map_signature(design: Design, params: dict[str, Any], _session: AsyncSession) -> str:
    return _json_hash({
        "analysis_type": CONTACT_MAP_ANALYSIS,
        "structure": _structure_fingerprint(design.pdb_path),
        "params": params,
    })


def build_chain_metrics_signature(design: Design, params: dict[str, Any], _session: AsyncSession) -> str:
    del params
    return _json_hash({
        "analysis_type": CHAIN_METRICS_ANALYSIS,
        "structure": _structure_fingerprint(design.pdb_path),
    })


def build_fampnn_psce_profile_signature(design: Design, params: dict[str, Any], _session: AsyncSession) -> str:
    return _json_hash({
        "analysis_type": FAMPNN_PSCE_PROFILE_ANALYSIS,
        "structure": _structure_fingerprint(design.pdb_path),
        "params": params,
    })


def _aligned_error_source_fingerprint(design: Design) -> dict[str, Any]:
    if not design.aligned_error_path or not design.aligned_error_format:
        raise ValueError(f"No raw aligned-error artifact is recorded for design {design.id}")
    return {
        "structure": _structure_fingerprint(design.pdb_path),
        "artifact": fingerprint_aligned_error_artifact(
            aligned_error_path=design.aligned_error_path,
            aligned_error_format=design.aligned_error_format,
            matrix_key=design.aligned_error_key,
        ),
    }


def build_pae_matrix_signature(design: Design, params: dict[str, Any], _session: AsyncSession) -> str:
    return _json_hash({
        "analysis_type": PAE_MATRIX_ANALYSIS,
        "source": _aligned_error_source_fingerprint(design),
        "params": params,
    })


def build_ipsae_interface_signature(design: Design, params: dict[str, Any], _session: AsyncSession) -> str:
    return _json_hash({
        "analysis_type": IPSAE_INTERFACE_ANALYSIS,
        "source": _aligned_error_source_fingerprint(design),
        "params": params,
        "review_profile_id": design.review_profile_id,
        "review_role_map": design.review_role_map if isinstance(design.review_role_map, dict) else {},
        "detected_antibody_chains": design.detected_antibody_chains,
        "detected_target_chain": design.detected_target_chain,
    })


def build_antibody_annotation_pack_signature(design: Design, params: dict[str, Any], _session: AsyncSession) -> str:
    del params
    return _json_hash({
        "analysis_type": ANTIBODY_ANNOTATION_PACK_ANALYSIS,
        "structure": _structure_fingerprint(design.pdb_path),
    })


async def _job_design_scope_rows(
    session: AsyncSession,
    job: Job,
    *,
    include_children: bool,
    design_ids: Sequence[str],
    columns: Sequence[Any],
) -> list[Design]:
    job_ids = [str(job.id)]
    if include_children:
        child_result = await session.execute(select(Job.id).where(Job.parent_job_id == str(job.id)))
        job_ids.extend(str(row[0]) for row in child_result.all())

    query = (
        select(Design)
        .options(load_only(*columns, Design.review_profile_id))
        .where(Design.job_id.in_(job_ids))
        .order_by(Design.id.asc())
    )
    if design_ids:
        query = query.where(Design.id.in_(list(design_ids)))
    result = await session.execute(query)
    return list(result.scalars().all())


async def correlation_scientific_scope(designs, session):
    """One canonical read path for cached computation and source-bound keys."""
    from services.scientific_analytics import owning_jobs, partition, persisted_projection
    from services.core_protein_scientific_contract import revision_for_job
    owners = await owning_jobs(session, designs)
    projections, sources = {}, []
    for design in designs:
        owner = owners.get(design.job_id)
        if owner is None or revision_for_job(owner) != 1:
            continue
        value = await persisted_projection(design, session)
        projections[design.id] = value
        sources.append({"id": design.id, "job_id": design.job_id, "revision": 1,
            "owner_model": owner.model_id, "owner_provenance": owner.provenance,
            "producer_record": design.confidence_metrics,
            "projection": {key: ({k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v
                for k, v in item.items()} if isinstance(item, dict) else item)
                for key, item in value.items()}})
    legacy, cohorts = await partition(designs, owners, session, projections=projections)
    return legacy, [cohort.model_dump(mode="json") for cohort in cohorts], sources


async def build_job_correlation_signature(job: Job, params: dict[str, Any], session: AsyncSession) -> str:
    designs = await _job_design_scope_rows(
        session,
        job,
        include_children=bool(params.get("include_children", True)),
        design_ids=params.get("design_ids") or [],
        columns=tuple(getattr(Design, column.key) for column in Design.__table__.columns),
    )
    designs = [design for design in designs if _design_supports_job_analysis(design, JOB_CORRELATION_MATRIX_ANALYSIS)]
    designs, cohorts, sources = await correlation_scientific_scope(designs, session)
    payload = {
        "analysis_type": JOB_CORRELATION_MATRIX_ANALYSIS,
        "job_id": str(job.id),
        "params": params,
        "designs": [
            {
                "id": design.id,
                "plddt_overall": design.plddt_overall,
                "plddt_binder": design.plddt_binder if _design_supports_binder_metrics(design) else None,
                "pae_overall": design.pae_overall,
                "pae_interaction": design.pae_interaction if _design_supports_binder_metrics(design) else None,
                "rmsd_binder": design.rmsd_binder if _design_supports_binder_metrics(design) else None,
                "rmsd_overall": design.rmsd_overall,
                "mpnn_score": design.mpnn_score,
                "conf_score": design.conf_score,
                "ptm": design.ptm,
                "rog": design.rog,
                "ligand_iptm": design.ligand_iptm if _design_supports_binder_metrics(design) else None,
                "affinity_score": design.affinity_score if _design_supports_binder_metrics(design) else None,
                "binder_probability": design.binder_probability if _design_supports_binder_metrics(design) else None,
            }
            for design in designs
        ],
    }
    if sources:
        payload.update(scientific_contract_revision=1, scientific_sources=sources, scientific_cohorts=cohorts)
    return _json_hash(payload)


async def build_job_aa_composition_signature(job: Job, params: dict[str, Any], session: AsyncSession) -> str:
    designs = await _job_design_scope_rows(
        session,
        job,
        include_children=bool(params.get("include_children", True)),
        design_ids=params.get("design_ids") or [],
        columns=(
            Design.id,
            Design.cdr_h1,
            Design.cdr_h2,
            Design.cdr_h3,
            Design.cdr_l1,
            Design.cdr_l2,
            Design.cdr_l3,
        ),
    )
    designs = [design for design in designs if _design_supports_job_analysis(design, JOB_AA_COMPOSITION_ANALYSIS)]
    payload = {
        "analysis_type": JOB_AA_COMPOSITION_ANALYSIS,
        "job_id": str(job.id),
        "params": params,
        "designs": [
            {
                "id": design.id,
                "cdr_h1": design.cdr_h1,
                "cdr_h2": design.cdr_h2,
                "cdr_h3": design.cdr_h3,
                "cdr_l1": design.cdr_l1,
                "cdr_l2": design.cdr_l2,
                "cdr_l3": design.cdr_l3,
            }
            for design in designs
        ],
    }
    return _json_hash(payload)


async def build_job_cdr_logo_signature(job: Job, params: dict[str, Any], session: AsyncSession) -> str:
    designs = await _job_design_scope_rows(
        session,
        job,
        include_children=bool(params.get("include_children", True)),
        design_ids=params.get("design_ids") or [],
        columns=(
            Design.id,
            Design.cdr_h1,
            Design.cdr_h2,
            Design.cdr_h3,
            Design.cdr_l1,
            Design.cdr_l2,
            Design.cdr_l3,
        ),
    )
    designs = [design for design in designs if _design_supports_job_analysis(design, JOB_CDR_LOGO_PACK_ANALYSIS)]
    payload = {
        "analysis_type": JOB_CDR_LOGO_PACK_ANALYSIS,
        "job_id": str(job.id),
        "params": params,
        "designs": [
            {
                "id": design.id,
                "cdr_h1": design.cdr_h1,
                "cdr_h2": design.cdr_h2,
                "cdr_h3": design.cdr_h3,
                "cdr_l1": design.cdr_l1,
                "cdr_l2": design.cdr_l2,
                "cdr_l3": design.cdr_l3,
            }
            for design in designs
        ],
    }
    return _json_hash(payload)


ANALYSIS_DEFINITIONS: Dict[str, AnalysisDefinition] = {
    STRUCTURE_SUMMARY_ANALYSIS: AnalysisDefinition(
        analysis_type=STRUCTURE_SUMMARY_ANALYSIS,
        subject_kind="design",
        version="2026-03-18-v1",
        resource_class="cpu_light",
        normalize_params=normalize_structure_summary_params,
        build_input_signature=build_structure_summary_signature,
    ),
    CONTACT_MAP_ANALYSIS: AnalysisDefinition(
        analysis_type=CONTACT_MAP_ANALYSIS,
        subject_kind="design",
        version="2026-03-18-v1",
        resource_class="cpu_heavy",
        normalize_params=normalize_contact_map_params,
        build_input_signature=build_contact_map_signature,
    ),
    CHAIN_METRICS_ANALYSIS: AnalysisDefinition(
        analysis_type=CHAIN_METRICS_ANALYSIS,
        subject_kind="design",
        version="2026-03-18-v1",
        resource_class="cpu_light",
        normalize_params=normalize_chain_metrics_params,
        build_input_signature=build_chain_metrics_signature,
    ),
    FAMPNN_PSCE_PROFILE_ANALYSIS: AnalysisDefinition(
        analysis_type=FAMPNN_PSCE_PROFILE_ANALYSIS,
        subject_kind="design",
        version="2026-03-23-v1",
        resource_class="cpu_light",
        normalize_params=normalize_fampnn_psce_profile_params,
        build_input_signature=build_fampnn_psce_profile_signature,
    ),
    PAE_MATRIX_ANALYSIS: AnalysisDefinition(
        analysis_type=PAE_MATRIX_ANALYSIS,
        subject_kind="design",
        version="2026-03-21-v2",
        resource_class="cpu_heavy",
        normalize_params=normalize_pae_matrix_params,
        build_input_signature=build_pae_matrix_signature,
    ),
    IPSAE_INTERFACE_ANALYSIS: AnalysisDefinition(
        analysis_type=IPSAE_INTERFACE_ANALYSIS,
        subject_kind="design",
        version="2026-03-21-v1",
        resource_class="cpu_light",
        normalize_params=normalize_ipsae_interface_params,
        build_input_signature=build_ipsae_interface_signature,
    ),
    ANTIBODY_ANNOTATION_PACK_ANALYSIS: AnalysisDefinition(
        analysis_type=ANTIBODY_ANNOTATION_PACK_ANALYSIS,
        subject_kind="design",
        version="2026-03-18-v1",
        resource_class="cpu_light",
        normalize_params=normalize_antibody_annotation_pack_params,
        build_input_signature=build_antibody_annotation_pack_signature,
    ),
    JOB_CORRELATION_MATRIX_ANALYSIS: AnalysisDefinition(
        analysis_type=JOB_CORRELATION_MATRIX_ANALYSIS,
        subject_kind="job",
        version="2026-03-18-v2",
        resource_class="cpu_heavy",
        normalize_params=normalize_job_scope_params,
        build_input_signature=build_job_correlation_signature,
    ),
    JOB_AA_COMPOSITION_ANALYSIS: AnalysisDefinition(
        analysis_type=JOB_AA_COMPOSITION_ANALYSIS,
        subject_kind="job",
        version="2026-03-18-v1",
        resource_class="cpu_heavy",
        normalize_params=normalize_job_scope_params,
        build_input_signature=build_job_aa_composition_signature,
    ),
    JOB_CDR_LOGO_PACK_ANALYSIS: AnalysisDefinition(
        analysis_type=JOB_CDR_LOGO_PACK_ANALYSIS,
        subject_kind="job",
        version="2026-03-18-v1",
        resource_class="cpu_heavy",
        normalize_params=normalize_job_scope_params,
        build_input_signature=build_job_cdr_logo_signature,
    ),
}


def get_analysis_definition(analysis_type: str) -> AnalysisDefinition | None:
    return ANALYSIS_DEFINITIONS.get(str(analysis_type or "").strip().lower())
