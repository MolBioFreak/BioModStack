"""Job readback: the public response projection of one Job."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List, Dict, Any, Literal
from pathlib import Path
from database import Job, Design, FrustraMPNNResult, ConformationalMappingRequest
from services.job_stage_progress import project_execution_stages
from paths import get_data_root, to_allowed_relative
from schemas import JobResponse, JobStatus
from schemas import BinderNavigationJob
from services.binder_navigation_jobs import navigation_columns, navigation_job
from services.stage_review import REVIEWABLE_STAGES, infer_antibody_stage_state, refresh_gate_payload
from services.job_requests import _coerce_positive_int, _dedupe_preserve_order, _to_bool


def _resolve_requested_design_count(job: Job) -> Optional[int]:
    params = job.params if isinstance(job.params, dict) else {}
    for key in (
        "rfantibody_num_designs",
        "rfd_num_designs",
        "num_designs",
        "num_samples",
    ):
        if key in params:
            count = _coerce_positive_int(params.get(key))
            if count is not None:
                return count
    return None


def resolve_output_dir(output_dir: str) -> Optional[Path]:
    if not output_dir:
        return None
    output_path = Path(output_dir)
    if output_path.is_absolute():
        return output_path
    return get_data_root() / output_dir


def _review_candidate_count(job: Job) -> Optional[int]:
    if not job.awaiting_input:
        return None
    stage = str(job.awaiting_stage or "").strip().lower()
    if stage not in REVIEWABLE_STAGES:
        return None
    payload = refresh_gate_payload(job.awaiting_payload or {}, job.output_dir)
    if stage in {"post_fampnn", "post_boltzgen", "post_ppiflow_generator", "post_caliby"}:
        filtered_count = payload.get("filtered_candidate_count")
        if isinstance(filtered_count, int) and filtered_count > 0:
            return filtered_count
    candidate_count = payload.get("candidate_count")
    if isinstance(candidate_count, int) and candidate_count >= 0:
        return candidate_count
    return None


def _is_meaningful_param_value(value: object) -> bool:
    """Treat empty/sentinel strings as unset."""
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized not in {"", "null", "none", "undefined", "n/a", "na"}
    return bool(value)


def _saved_review_filter_entries(job: Optional[Job]) -> List[Dict[str, Any]]:
    if job is None:
        return []

    saved_entries = getattr(job, "saved_selection_sets", None)
    if isinstance(saved_entries, list):
        return list(saved_entries)

    if isinstance(job.awaiting_payload, dict):
        raw_sets = job.awaiting_payload.get("review_filter_sets")
        if isinstance(raw_sets, list):
            return list(raw_sets)

    return []


def _serialized_saved_review_filter_sets(job: Optional[Job]) -> Optional[List[Dict[str, Any]]]:
    entries = _saved_review_filter_entries(job)
    return entries or None


def _to_stage_output_path(path: Path) -> str:
    try:
        return to_allowed_relative(path)
    except Exception:
        return str(path)


def _stage_output_exists(output_path: Optional[Path], output_value: str) -> bool:
    if not output_value or not isinstance(output_value, str):
        return False

    raw = Path(output_value)
    candidates: List[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(get_data_root() / output_value)
        if output_path is not None:
            candidates.append(output_path / output_value)
            try:
                output_rel = to_allowed_relative(output_path)
                if output_value.startswith(f"{output_rel}/"):
                    suffix = output_value[len(output_rel) + 1 :]
                    candidates.append(output_path / suffix)
            except Exception:
                pass

    for candidate in candidates:
        try:
            if candidate.exists():
                return True
        except Exception:
            continue
    return False


def _sanitize_nanopore_stage_outputs(
    stage_outputs: Dict[str, List[str]],
    output_dir: Optional[str],
) -> Dict[str, List[str]]:
    output_path = resolve_output_dir(output_dir or "")
    cleaned: Dict[str, List[str]] = {}
    for stage, outputs in (stage_outputs or {}).items():
        if not isinstance(outputs, list):
            continue
        filtered = [
            value
            for value in outputs
            if isinstance(value, str) and _stage_output_exists(output_path, value)
        ]
        deduped = _dedupe_preserve_order(filtered)
        if deduped:
            cleaned[stage] = deduped
    return cleaned


def _resolve_nanopore_fastq_qc_mode(params: Optional[dict]) -> tuple[bool, bool]:
    """Return (enabled, use_legacy_multimer_stages)."""
    if not isinstance(params, dict):
        return False, False

    run_fastq_qc = params.get("run_fastq_qc")
    if isinstance(run_fastq_qc, bool):
        return run_fastq_qc, False

    # Legacy persisted jobs may carry run_multimer_qc. New requests use run_fastq_qc only.
    run_multimer_qc = params.get("run_multimer_qc")
    if isinstance(run_multimer_qc, bool):
        return run_multimer_qc, True

    return False, False


def _resolve_nanopore_bam_realign(params: Optional[dict]) -> bool:
    if not isinstance(params, dict):
        return False
    return _to_bool(params.get("bam_force_realign"))


NANOPORE_STAGE_RESPONSE_MODES = frozenset(
    {
        "basecall_dna",
        "basecall_rna",
        "plasmid_qc",
        "construct_screening",
        "methylation_analysis",
        "nanopore_methylation",
        "fastq_qc",
        "clone_validation",
    }
)


def _uses_nanopore_stage_response(job: Job) -> bool:
    return job.model_id == "nanopore" and job.mode in NANOPORE_STAGE_RESPONSE_MODES


def _planned_nanopore_stages(params: Optional[dict], *, mode: Optional[str] = None) -> List[str]:
    np_params = params if isinstance(params, dict) else {}
    display_stages: List[str] = []
    has_pod5 = _is_meaningful_param_value(np_params.get("pod5_dir"))
    has_bam = _is_meaningful_param_value(np_params.get("bam_path"))
    has_fastq = _is_meaningful_param_value(np_params.get("fastq_path"))
    has_reference = _is_meaningful_param_value(np_params.get("reference_fasta"))
    if mode in {"basecall_dna", "basecall_rna"}:
        if not has_pod5:
            return []
        display_stages.append("dorado_basecall")
        has_barcode_kit = _is_meaningful_param_value(np_params.get("barcode_kit"))
        if mode == "basecall_dna" and has_barcode_kit:
            display_stages.append("dorado_demux")
        elif has_reference:
            display_stages.append("dorado_align")
        if (mode == "basecall_rna" and has_reference and np_params.get("run_modkit") is True
                and str(np_params.get("modified_bases") or "none") != "none"):
            display_stages.append("modkit")
        return display_stages
    fastq_qc_enabled, legacy_multimer_mode = _resolve_nanopore_fastq_qc_mode(np_params)
    bam_force_realign = _resolve_nanopore_bam_realign(np_params)

    if has_pod5:
        display_stages.append("dorado_basecall")
    if has_pod5 and has_reference:
        display_stages.append("dorado_align")
    if has_bam and has_reference:
        display_stages.append("dorado_align" if bam_force_realign else "bam_prepare")
    # Reference-free assembly aligns the reads to the assembled consensus afterwards.
    assembly_only = np_params.get("run_assembly") is True and not has_reference
    if not assembly_only and ((has_bam and not has_reference) or (has_pod5 and not has_reference)):
        display_stages.append("bam_prepare")
    if has_fastq and has_reference:
        display_stages.append("fastq_align")
    if fastq_qc_enabled and has_fastq and has_reference and not legacy_multimer_mode:
        display_stages.append("fastq_qc")
    if np_params.get("run_modkit") is not False and (has_pod5 or has_bam):
        display_stages.append("modkit")
    if fastq_qc_enabled and legacy_multimer_mode and has_fastq:
        display_stages.append("multimer_qc")
    if fastq_qc_enabled and legacy_multimer_mode and has_fastq and has_reference:
        display_stages.append("dimer_analysis")
    if np_params.get("run_assembly") is True and (has_pod5 or has_bam or has_fastq):
        display_stages.append("wf_clone_validation")
        if assembly_only:
            display_stages.append("dorado_align")

    return _dedupe_preserve_order(display_stages)


def _infer_nanopore_stage_outputs(
    output_dir: Optional[str],
    params: Optional[dict] = None,
    *,
    mode: Optional[str] = None,
) -> Dict[str, List[str]]:
    output_path = resolve_output_dir(output_dir or "")
    if not output_path or not output_path.exists():
        return {}

    expected = {
        "dorado_basecall": [
            "basecall/calls.bam",
            "basecall/basecall.log",
            "basecall/sequencing_summary.tsv",
        ],
        "dorado_align": [
            "align/aligned.bam",
            "align/aligned.bam.bai",
            "align/reference.fasta",
            "align/reference.fasta.fai",
            "align/align.log",
        ],
        "dorado_demux": [
            "demux/demux_manifest.json",
            "demux/per_barcode_units.json",
            "demux/demux/units",
        ],
        "bam_prepare": [
            "align/aligned.bam",
            "align/aligned.bam.bai",
            "align/bam_prepare.log",
            "align/reference.fasta",
            "align/reference.fasta.fai",
            "align/reference_prepare.log",
        ],
        "fastq_align": [
            "align/aligned.bam",
            "align/aligned.bam.bai",
            "align/reference.fasta",
            "align/reference.fasta.fai",
            "align/fastq_align.log",
        ],
        "fastq_qc": [
            "fastq_qc/read_lengths.tsv",
            "fastq_qc/fastq_qc_summary.tsv",
            "fastq_qc/fastq_alignment_stats.tsv",
            "fastq_qc/fastq_coverage.tsv",
            "fastq_qc/igv_coverage_depth.bedgraph",
            "fastq_qc/igv_position_gradient.bedgraph",
            "fastq_qc/igv_gc_content.bedgraph",
            "fastq_qc/igv_gc_zscore.bedgraph",
            "fastq_qc/igv_split_read_density.bedgraph",
            "fastq_qc/igv_softclip_density.bedgraph",
            "fastq_qc/igv_junction_hotspots.bed",
            "fastq_qc/igv_report_sites.bed",
            "fastq_qc/igv_report_sites.tsv",
            "fastq_qc/igv_track_config.json",
            "fastq_qc/igv_report.html",
            "fastq_qc/igv_report.log",
            "fastq_qc/fastq_consensus.fasta",
            "fastq_qc/fastq_consensus.fasta.fai",
            "fastq_qc/fastq_consensus.log",
            "fastq_qc/fastq_qc.log",
        ],
        "modkit": [
            "methylation/methylation.bed",
            "methylation/pileup.log",
            "methylation/modkit_summary.tsv",
            "methylation/summary.log",
        ],
        "multimer_qc": [
            "multimer_qc/read_lengths.tsv",
            "multimer_qc/multimer_summary.tsv",
            "multimer_qc/multimer_candidates.tsv",
            "multimer_qc/multimer_qc.log",
        ],
        "dimer_analysis": [
            "multimer_qc/dimer_candidates.fastq",
            "multimer_qc/dimer_candidates.fasta",
            "multimer_qc/dimer_read_lengths.tsv",
            "multimer_qc/dimer_analysis_summary.tsv",
            "multimer_qc/dimer_analysis.log",
            "multimer_qc/dimer_breakpoint_call.tsv",
            "multimer_qc/dimer_evidence_by_position.tsv",
            "multimer_qc/dimer_read_events.tsv",
            "multimer_qc/dimer_breakpoint_sequences.tsv",
            "multimer_qc/dimer_secondary_anomalies.tsv",
            "multimer_qc/dimer_secondary_summary.tsv",
            "multimer_qc/dimer_diagnostics.tar.gz",
            "multimer_qc/dimer_candidates.aligned.bam",
            "multimer_qc/dimer_candidates.aligned.bam.bai",
            "multimer_qc/dimer_reference.fasta",
            "multimer_qc/dimer_reference.fasta.fai",
            "multimer_qc/dominant_dimer_consensus.fasta",
            "multimer_qc/dominant_dimer_consensus.log",
            "multimer_qc/dominant_dimer_consensus_metadata.tsv",
            # Legacy fallback artifacts (older runs)
            "multimer_qc/dimer_breakpoint_screen.tsv",
            "multimer_qc/dimer_junction_clusters.tsv",
            "multimer_qc/dimer_junction_events.tsv",
            "multimer_qc/dimer_read_junctions.tsv",
            "multimer_qc/dimer_read_ledger.tsv",
            "multimer_qc/dimer_single_ref_split_profile.tsv",
        ],
        "wf_clone_validation": [
            "assembly/wf_clone.log",
            "assembly/runtime_provenance.json",
            "assembly/wf_clone_out",
            "assembly/wf_clone_out/wf-clone-validation-report.html",
            "assembly/wf_clone_out/sample_status.txt",
            "assembly/adapter/adapter_manifest.json",
            "verification/qc_manifest.json",
            "verification/verification_summary.tsv",
        ],
    }

    allowed_stages = set(expected.keys())
    if isinstance(params, dict):
        has_pod5 = _is_meaningful_param_value(params.get("pod5_dir"))
        has_bam = _is_meaningful_param_value(params.get("bam_path"))
        has_fastq = _is_meaningful_param_value(params.get("fastq_path"))
        has_reference = _is_meaningful_param_value(params.get("reference_fasta"))
        fastq_qc_enabled, legacy_multimer_mode = _resolve_nanopore_fastq_qc_mode(params)
        bam_force_realign = _resolve_nanopore_bam_realign(params)

        if mode in {"basecall_dna", "basecall_rna"}:
            allowed_stages = set(_planned_nanopore_stages(params, mode=mode))
        else:
            allowed_stages = set()
            if has_pod5:
                allowed_stages.add("dorado_basecall")
            if has_pod5 and has_reference:
                allowed_stages.add("dorado_align")
            if has_bam and has_reference and bam_force_realign:
                allowed_stages.add("dorado_align")
            if has_bam:
                allowed_stages.add("bam_prepare")
            if has_pod5 and not has_reference:
                allowed_stages.add("bam_prepare")
            if has_fastq and has_reference:
                allowed_stages.add("fastq_align")
            if fastq_qc_enabled and has_fastq and has_reference and not legacy_multimer_mode:
                allowed_stages.add("fastq_qc")
            if params.get("run_modkit") is not False and (has_pod5 or has_bam):
                allowed_stages.add("modkit")
            if fastq_qc_enabled and legacy_multimer_mode and has_fastq:
                allowed_stages.add("multimer_qc")
            if fastq_qc_enabled and legacy_multimer_mode and has_fastq and has_reference:
                allowed_stages.add("dimer_analysis")
            if params.get("run_assembly") is True and (has_pod5 or has_bam or has_fastq):
                allowed_stages.add("wf_clone_validation")
                if not has_reference:
                    allowed_stages.add("dorado_align")

    inferred: Dict[str, List[str]] = {}
    for stage, rel_paths in expected.items():
        if stage not in allowed_stages:
            continue
        found: List[str] = []
        for rel in rel_paths:
            p = output_path / rel
            if p.exists():
                found.append(_to_stage_output_path(p))
        if found:
            inferred[stage] = _dedupe_preserve_order(found)
    return inferred


def _resolve_stage_state_for_response(job: Job) -> tuple[List[str], Dict[str, List[str]]]:
    completed = _dedupe_preserve_order(list(job.completed_stages or []))
    stage_outputs = dict(job.stage_outputs or {})

    if _uses_nanopore_stage_response(job):
        stage_outputs = _sanitize_nanopore_stage_outputs(stage_outputs, job.output_dir)
        inferred_outputs = _infer_nanopore_stage_outputs(job.output_dir, job.params, mode=job.mode)
        for stage, outputs in inferred_outputs.items():
            existing = stage_outputs.get(stage)
            if isinstance(existing, list):
                merged = _dedupe_preserve_order([*existing, *outputs])
            else:
                merged = outputs
            stage_outputs[stage] = merged

    _inferred_completed, stage_outputs = infer_antibody_stage_state(job, list(completed), stage_outputs)

    return completed, stage_outputs


def _rfd3_public_json(value: Any, *, field: str | None = None) -> Any:
    """Project stored RFD3 provenance without exposing absolute host paths."""

    if isinstance(value, dict):
        return {key: _rfd3_public_json(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_rfd3_public_json(item, field=field) for item in value]
    field_name = str(field or "").lower()
    is_path_field = field_name in {
        "input",
        "input_structure",
        "input_pdb",
        "input_cif",
        "plr_input_pdb",
        "rfd3_request_path",
        "path",
        "paths",
        "file",
        "files",
        "dir",
        "directory",
        "directories",
        "filepath",
        "filepaths",
        "dirname",
        "dirnames",
        "storage_path",
    } or field_name.endswith(
        (
            "_path",
            "_paths",
            "_file",
            "_files",
            "_dir",
            "_directory",
            "_directories",
            "_filepath",
            "_filepaths",
            "_dirname",
            "_dirnames",
        )
    )
    if isinstance(value, str) and is_path_field:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate.name
    return value


def _is_native_rfd3_job(job: Any) -> bool:
    return str(job.model_id or "").strip().lower() == "protein_local_redesign"


def _public_job_output_dir(job: Any) -> str | None:
    if _is_native_rfd3_job(job):
        return None
    return job.output_dir


def _public_job_params(job: Any) -> dict[str, Any]:
    from services.execution_ownership import strip_execution_metadata
    from services.resource_usage_evidence import strip_resource_execution_metadata

    params = strip_execution_metadata(strip_resource_execution_metadata(job.params))
    if _is_native_rfd3_job(job):
        return _rfd3_public_json(params)
    return params


async def read_job(
    session: AsyncSession,
    job_id: str,
    projection: Optional[Literal["binder_navigation"]] = None,
) -> JobResponse | BinderNavigationJob | None:
    """Read one Job as its public response; None when it does not exist."""
    if projection == "binder_navigation":
        row = (await session.execute(select(*navigation_columns()).where(Job.id == job_id))).mappings().one_or_none()
        if row is None:
            return None
        return navigation_job(row)
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        return None

    # Get design count
    design_count_query = select(func.count(Design.id)).where(Design.job_id == job.id)
    design_count = (await session.execute(design_count_query)).scalar()
    if (design_count or 0) == 0:
        child_design_count_query = (
            select(func.count(Design.id))
            .select_from(Job)
            .join(Design, Design.job_id == Job.id)
            .where(Job.parent_job_id == job.id)
        )
        child_design_count = (await session.execute(child_design_count_query)).scalar()
        if child_design_count:
            design_count = child_design_count
    review_count = _review_candidate_count(job)
    if (design_count or 0) == 0 and review_count is not None:
        design_count = review_count
    completed_stages, stage_outputs = _resolve_stage_state_for_response(job)
    native_result_count = 0
    if (design_count or 0) == 0 and job.status in {JobStatus.COMPLETED.value, JobStatus.AWAITING_INPUT.value}:
        from services.rfd4_proteina_results import native_result_counts
        native_result_count = (await native_result_counts(session, [job.id])).get(job.id, 0)
    frustrampnn_result_count = int((await session.execute(
        select(func.count(FrustraMPNNResult.invocation_id)).where(FrustraMPNNResult.parent_job_id == job.id)
    )).scalar_one())
    conformational_mapping_request_id = None
    if job.model_id in {"conformational_mapping", "confornets_experimental"}:
        conformational_mapping_request_id = await session.scalar(
            select(ConformationalMappingRequest.request_id).where(
                ConformationalMappingRequest.job_id == job.id,
            )
        )

    return JobResponse(
        id=job.id,
        name=job.name,
        status=job.status,
        model_id=job.model_id,
        mode=job.mode,
        params=_public_job_params(job),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        output_dir=_public_job_output_dir(job),
        error_message=job.error_message,
        design_count=design_count or 0,
        requested_design_count=_resolve_requested_design_count(job),
        batch_id=job.batch_id,
        batch_name=job.batch_name,
        parent_job_id=job.parent_job_id,
        child_stage=job.child_stage,
        lineage_root_job_id=job.lineage_root_job_id,
        stage_family=job.stage_family,
        stage_mode=job.stage_mode,
        source_stage_job_id=job.source_stage_job_id,
        source_stage_family=job.source_stage_family,
        source_stage_mode=job.source_stage_mode,
        source_selection_manifest_path=job.source_selection_manifest_path,
        source_selection_count=job.source_selection_count,
        selected_input_artifact_class=job.selected_input_artifact_class,
        selected_input_schema_version=job.selected_input_schema_version,
        selection_source_type=job.selection_source_type,
        selection_source_job_id=job.selection_source_job_id,
        selection_dataset_name=job.selection_dataset_name,
        selected_loop_scope=job.selected_loop_scope,
        provenance=job.provenance,
        saved_selection_sets=_serialized_saved_review_filter_sets(job),
        pinned_gpu=job.pinned_gpu,
        execution_target_id=job.execution_target_id,
        execution_source_revision=job.execution_source_revision,
        execution_source_tree=job.execution_source_tree,
        execution_bundle_sha256=job.execution_bundle_sha256,
        remote_attempt_id=job.remote_attempt_id,
        remote_state=job.remote_state,
        execution_stages=project_execution_stages(job),
        current_stage=job.current_stage,
        completed_stages=completed_stages,
        stage_outputs=stage_outputs,
        awaiting_input=job.awaiting_input,
        awaiting_stage=job.awaiting_stage,
        awaiting_payload=job.awaiting_payload,
        decision_history=job.decision_history,
        frustrampnn_result_count=frustrampnn_result_count,
        native_result_count=native_result_count,
        frustrampnn_reopen_destination=(
            {"surface": "frustrampnn-workbench", "params": {"job_id": job.id}}
            if frustrampnn_result_count else None
        ),
        conformational_mapping_request_id=conformational_mapping_request_id,
    )
