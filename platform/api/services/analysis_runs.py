from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AnalysisRun, Design, Job
from paths import get_analysis_cache_dir, resolve_allowed_path, to_allowed_relative
from services.analysis_registry import (
    AnalysisDefinition,
    _design_supports_job_analysis,
    build_analysis_input_signature,
    get_analysis_definition,
)
from services.result_contracts import resolve_result_contract, validate_design_analysis_request
from services import stats_toolkit_client
from services.stats_toolkit_client import (
    STATS_TOOLKIT_UNAVAILABLE_REASON,
    StatsToolkitUnavailableError,
)


ACTIVE_ANALYSIS_STATUSES = {"queued", "running"}
REUSABLE_ANALYSIS_STATUSES = {"queued", "running", "completed"}
TERMINAL_ANALYSIS_STATUSES = {"completed", "failed", "cancelled", "stale"}


def stable_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _artifact_dir_for_run(run: AnalysisRun) -> Path:
    return (
        get_analysis_cache_dir()
        / str(run.subject_kind)
        / str(run.subject_id)
        / str(run.analysis_type)
        / str(run.cache_key)
        / str(run.id)
    )


def build_artifact_manifest_for_run(run: AnalysisRun) -> dict[str, Any]:
    artifact_dir = _artifact_dir_for_run(run)
    paths = {
        "cache_dir": to_allowed_relative(artifact_dir),
        "summary_json": to_allowed_relative(artifact_dir / "summary.json"),
        "result_json": to_allowed_relative(artifact_dir / "result.json"),
        "stdout_log": to_allowed_relative(artifact_dir / "stdout.log"),
        "stderr_log": to_allowed_relative(artifact_dir / "stderr.log"),
    }
    return paths


def normalize_analysis_params(analysis_type: str, raw_params: Optional[dict[str, Any]]) -> tuple[AnalysisDefinition, dict[str, Any]]:
    definition = get_analysis_definition(analysis_type)
    if definition is None:
        raise ValueError(f"Unsupported analysis type: {analysis_type}")
    return definition, definition.normalize_params(raw_params)


def _cache_key_for_subject(
    *,
    subject_kind: str,
    subject_id: str,
    analysis_type: str,
    params: dict[str, Any],
    input_signature: str,
    code_version: str,
) -> str:
    return stable_json_hash({
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "analysis_type": analysis_type,
        "params": params,
        "input_signature": input_signature,
        "code_version": code_version,
    })


def _preferred_run(runs: list[AnalysisRun]) -> AnalysisRun | None:
    if not runs:
        return None
    for status in ("running", "queued", "completed", "failed", "cancelled", "stale"):
        for run in runs:
            if run.status == status:
                return run
    return runs[0]


def extract_stats_run(run: AnalysisRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    summary = load_analysis_summary(run)
    if not isinstance(summary, dict):
        return None
    stats_run = summary.get("stats_run")
    if isinstance(stats_run, dict) and stats_run.get("run_id"):
        return dict(stats_run)
    return None


def is_stats_toolkit_unavailable_run(run: AnalysisRun | None) -> bool:
    if run is None or run.status != "failed":
        return False
    if str(run.error_message or "").strip().startswith(STATS_TOOLKIT_UNAVAILABLE_REASON):
        return True
    summary = run.summary_json if isinstance(run.summary_json, dict) else {}
    return summary.get("reason") == STATS_TOOLKIT_UNAVAILABLE_REASON


def _normalize_scope_tuple(params_json: dict[str, Any] | None) -> tuple[bool, tuple[str, ...]]:
    params = params_json if isinstance(params_json, dict) else {}
    include_children = bool(params.get("include_children", True))
    raw_ids = params.get("design_ids") or []
    design_ids = tuple(sorted({str(item).strip() for item in raw_ids if str(item).strip()}))
    return include_children, design_ids


def _order_superseding_runs(runs: list[AnalysisRun]) -> list[AnalysisRun]:
    """Order runs newest-first, ensuring superseding runs always precede superseded ones."""
    superseded_ids = {str(r.supersedes_run_id) for r in runs if r.supersedes_run_id}
    return sorted(
        runs,
        key=lambda r: (
            0 if str(r.id) in superseded_ids else 1,
            r.queued_at or datetime.min,
            r.completed_at or datetime.min,
            str(r.id),
        ),
        reverse=True,
    )


async def get_matching_analysis_run(
    session: AsyncSession,
    *,
    subject: Any,
    subject_kind: str,
    subject_id: str,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
) -> tuple[Optional[AnalysisRun], AnalysisDefinition, dict[str, Any], str]:
    definition, params = normalize_analysis_params(analysis_type, raw_params)
    if definition.subject_kind != subject_kind:
        raise ValueError(f"Analysis type {definition.analysis_type} is not valid for {subject_kind} subjects")
    input_signature = await build_analysis_input_signature(definition, subject, params, session)
    cache_key = _cache_key_for_subject(
        subject_kind=subject_kind,
        subject_id=str(subject_id),
        analysis_type=definition.analysis_type,
        params=params,
        input_signature=input_signature,
        code_version=definition.version,
    )
    result = await session.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.subject_kind == subject_kind,
            AnalysisRun.subject_id == str(subject_id),
            AnalysisRun.analysis_type == definition.analysis_type,
            AnalysisRun.cache_key == cache_key,
        )
        .order_by(AnalysisRun.queued_at.desc())
    )
    runs = list(result.scalars().all())
    return _preferred_run(runs), definition, params, cache_key


async def get_matching_design_analysis_run(
    session: AsyncSession,
    design: Design,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
) -> tuple[Optional[AnalysisRun], AnalysisDefinition, dict[str, Any], str]:
    return await get_matching_analysis_run(
        session,
        subject=design,
        subject_kind="design",
        subject_id=str(design.id),
        analysis_type=analysis_type,
        raw_params=raw_params,
    )


async def get_matching_job_analysis_run(
    session: AsyncSession,
    job: Job,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
) -> tuple[Optional[AnalysisRun], AnalysisDefinition, dict[str, Any], str]:
    return await get_matching_analysis_run(
        session,
        subject=job,
        subject_kind="job",
        subject_id=str(job.id),
        analysis_type=analysis_type,
        raw_params=raw_params,
    )


async def get_matching_selection_analysis_run(
    session: AsyncSession,
    selection_signature: str,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
) -> tuple[Optional[AnalysisRun], AnalysisDefinition, dict[str, Any], str]:
    return await get_matching_analysis_run(
        session,
        subject={"selection_signature": str(selection_signature)},
        subject_kind="selection",
        subject_id=str(selection_signature),
        analysis_type=analysis_type,
        raw_params=raw_params,
    )


async def get_latest_job_analysis_run_for_scope(
    session: AsyncSession,
    job: Job,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
    *,
    explicit_option_keys: frozenset[str] | set[str] = frozenset(),
) -> tuple[Optional[AnalysisRun], AnalysisDefinition, dict[str, Any], str, bool]:
    """Read-path lookup supporting superseding options (R08) and stale fallback (R07)."""
    definition, normalized_params = normalize_analysis_params(analysis_type, raw_params)
    if definition.subject_kind != "job":
        raise ValueError(f"Analysis type {definition.analysis_type} is not valid for job subjects")
    current_input_signature = await build_analysis_input_signature(
        definition, job, normalized_params, session
    )
    current_cache_key = _cache_key_for_subject(
        subject_kind="job",
        subject_id=str(job.id),
        analysis_type=definition.analysis_type,
        params=normalized_params,
        input_signature=current_input_signature,
        code_version=definition.version,
    )

    if not definition.toolkit_backed:
        result = await session.execute(
            select(AnalysisRun)
            .where(
                AnalysisRun.subject_kind == "job",
                AnalysisRun.subject_id == str(job.id),
                AnalysisRun.analysis_type == definition.analysis_type,
                AnalysisRun.cache_key == current_cache_key,
            )
            .order_by(AnalysisRun.queued_at.desc())
        )
        return _preferred_run(list(result.scalars().all())), definition, normalized_params, current_cache_key, False

    target_scope = _normalize_scope_tuple(normalized_params)
    result = await session.execute(
        select(AnalysisRun)
        .where(
            AnalysisRun.subject_kind == "job",
            AnalysisRun.subject_id == str(job.id),
            AnalysisRun.analysis_type == definition.analysis_type,
        )
        .order_by(AnalysisRun.queued_at.desc())
    )
    scoped_runs = [
        row
        for row in result.scalars().all()
        if _normalize_scope_tuple(row.params_json) == target_scope
    ]
    if explicit_option_keys:
        scoped_runs = [
            row
            for row in scoped_runs
            if all(
                (row.params_json or {}).get(key) == normalized_params.get(key)
                for key in explicit_option_keys
            )
        ]
    if not scoped_runs:
        return None, definition, normalized_params, current_cache_key, False

    ordered = _order_superseding_runs(scoped_runs)
    newest = ordered[0]
    if newest.status in ACTIVE_ANALYSIS_STATUSES:
        return newest, definition, newest.params_json or normalized_params, current_cache_key, False
    if newest.status == "completed":
        is_stale = newest.input_signature != current_input_signature
        return newest, definition, newest.params_json or normalized_params, current_cache_key, is_stale
    if newest.status == "failed":
        return newest, definition, newest.params_json or normalized_params, current_cache_key, False
    for candidate in ordered:
        if candidate.status == "completed":
            is_stale = candidate.input_signature != current_input_signature
            return candidate, definition, candidate.params_json or normalized_params, current_cache_key, is_stale
    return newest, definition, newest.params_json or normalized_params, current_cache_key, False


async def request_analysis(
    session: AsyncSession,
    *,
    subject: Any,
    subject_kind: str,
    subject_id: str,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
    force_refresh: bool = False,
    requested_by: str = "ui",
) -> tuple[AnalysisRun, bool]:
    existing, definition, params, cache_key = await get_matching_analysis_run(
        session,
        subject=subject,
        subject_kind=subject_kind,
        subject_id=subject_id,
        analysis_type=analysis_type,
        raw_params=raw_params,
    )
    now = datetime.utcnow()

    if existing is not None and not force_refresh and existing.status in REUSABLE_ANALYSIS_STATUSES:
        existing.last_accessed_at = now
        if existing.status == "completed":
            existing.reuse_count = int(existing.reuse_count or 0) + 1
        await session.commit()
        return existing, True

    supersedes_id = existing.id if existing is not None else None
    if supersedes_id is None and definition.toolkit_backed and subject_kind == "job":
        target_scope = _normalize_scope_tuple(params)
        prior_result = await session.execute(
            select(AnalysisRun)
            .where(
                AnalysisRun.subject_kind == subject_kind,
                AnalysisRun.subject_id == str(subject_id),
                AnalysisRun.analysis_type == definition.analysis_type,
            )
            .order_by(AnalysisRun.queued_at.desc())
        )
        prior_scoped = [
            row
            for row in prior_result.scalars().all()
            if _normalize_scope_tuple(row.params_json) == target_scope
        ]
        if prior_scoped:
            supersedes_id = _order_superseding_runs(prior_scoped)[0].id

    input_signature = await build_analysis_input_signature(definition, subject, params, session)
    params_hash = stable_json_hash(params)
    run = AnalysisRun(
        id=str(uuid.uuid4()),
        subject_kind=subject_kind,
        subject_id=str(subject_id),
        analysis_type=definition.analysis_type,
        status="queued",
        resource_class=definition.resource_class,
        params_json=params,
        params_hash=params_hash,
        input_signature=input_signature,
        code_version=definition.version,
        cache_key=cache_key,
        requested_by=requested_by,
        queued_at=now,
        last_accessed_at=now,
        supersedes_run_id=supersedes_id,
    )
    run.artifact_manifest = build_artifact_manifest_for_run(run)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run, False


async def validate_owned_design_analysis_request(design: Design, analysis_type: str, session: AsyncSession) -> str | None:
    from services.analysis_registry import scientific_contract_revision
    if (analysis_type in {"pae_matrix", "chain_metrics", "ipsae_interface"}
            and await scientific_contract_revision(design, session) == 1):
        # Native publication owns these artifacts, not legacy Design path slots.
        # The native signature/compute owner verifies bytes and preserves absent
        # mappings as unavailable evidence without inventing legacy artifacts.
        return None
    return validate_design_analysis_request(design, analysis_type)


async def request_design_analysis(
    session: AsyncSession,
    design: Design,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
    *,
    force_refresh: bool = False,
    requested_by: str = "ui",
) -> tuple[AnalysisRun, bool]:
    contract_error = await validate_owned_design_analysis_request(design, analysis_type, session)
    if contract_error:
        raise ValueError(contract_error)
    return await request_analysis(
        session,
        subject=design,
        subject_kind="design",
        subject_id=str(design.id),
        analysis_type=analysis_type,
        raw_params=raw_params,
        force_refresh=force_refresh,
        requested_by=requested_by,
    )


async def validate_job_analysis_request(
    session: AsyncSession,
    job: Job,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]],
) -> None:
    definition = get_analysis_definition(analysis_type)
    if definition is None or definition.subject_kind != "job":
        raise ValueError(f"Unknown job analysis type: {analysis_type}")
    params = definition.normalize_params(raw_params)
    job_ids = [str(job.id)]
    if bool(params.get("include_children", True)):
        child_result = await session.execute(select(Job.id).where(Job.parent_job_id == str(job.id)))
        job_ids.extend(str(row[0]) for row in child_result.all())
    query = select(Design).where(Design.job_id.in_(job_ids))
    design_ids = params.get("design_ids") or []
    if design_ids:
        query = query.where(Design.id.in_(list(design_ids)))
    design_result = await session.execute(query)
    scoped_designs = list(design_result.scalars().all())
    recognized_designs = [
        design for design in scoped_designs
        if resolve_result_contract(
            review_profile_id=getattr(design, "review_profile_id", None),
        ).analysis_contract_id not in {None, "unsupported_legacy"}
    ]
    if not recognized_designs:
        raise ValueError("job analysis has no designs with an authoritative review profile")
    eligible_designs = [
        design for design in recognized_designs
        if _design_supports_job_analysis(design, analysis_type)
    ]
    if not eligible_designs:
        raise ValueError("job analysis has no review-compatible designs in scope")


async def request_job_analysis(
    session: AsyncSession,
    job: Job,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
    *,
    force_refresh: bool = False,
    requested_by: str = "ui",
) -> tuple[AnalysisRun, bool]:
    await validate_job_analysis_request(session, job, analysis_type, raw_params)
    return await request_analysis(
        session,
        subject=job,
        subject_kind="job",
        subject_id=str(job.id),
        analysis_type=analysis_type,
        raw_params=raw_params,
        force_refresh=force_refresh,
        requested_by=requested_by,
    )


async def request_selection_analysis(
    session: AsyncSession,
    selection_signature: str,
    analysis_type: str,
    raw_params: Optional[dict[str, Any]] = None,
    *,
    force_refresh: bool = False,
    requested_by: str = "ui",
) -> tuple[AnalysisRun, bool]:
    definition = get_analysis_definition(analysis_type)
    if definition is None or definition.subject_kind != "selection":
        raise ValueError(f"Unknown selection analysis type: {analysis_type}")
    return await request_analysis(
        session,
        subject={"selection_signature": str(selection_signature)},
        subject_kind="selection",
        subject_id=str(selection_signature),
        analysis_type=analysis_type,
        raw_params=raw_params,
        force_refresh=force_refresh,
        requested_by=requested_by,
    )


def _resolve_artifact_path(run: AnalysisRun, key: str) -> Path | None:
    manifest = run.artifact_manifest if isinstance(run.artifact_manifest, dict) else {}
    rel_path = manifest.get(key)
    if not rel_path:
        return None
    try:
        return resolve_allowed_path(str(rel_path))
    except Exception:
        return None


def _distribution_from_toolkit_column(col: dict[str, Any]) -> dict[str, Any]:
    hist = col.get("histogram") if isinstance(col.get("histogram"), dict) else {}
    return {
        "min": float(col["min"]),
        "max": float(col["max"]),
        "avg": float(col["mean"]),
        "median": float(col["median"]),
        "std_dev": float(col["std"]) if col.get("std") is not None else None,
        "q1": float(col["q1"]) if col.get("q1") is not None else None,
        "q3": float(col["q3"]) if col.get("q3") is not None else None,
        "iqr": float(col["iqr"]) if col.get("iqr") is not None else None,
        "count": int(col["count"]),
        "histogram_bins": [float(x) for x in (hist.get("edges") or [])],
        "histogram_counts": [int(x) for x in (hist.get("counts") or [])],
    }


def _assemble_toolkit_analysis_result(run: AnalysisRun, raw_outputs: Any) -> Any:
    if not isinstance(raw_outputs, dict):
        return raw_outputs
    from services.analysis_registry import (
        JOB_CORRELATION_MATRIX_ANALYSIS,
        JOB_METRIC_SUMMARY_ANALYSIS,
        SELECTION_BATCH_SUMMARY_ANALYSIS,
        SELECTION_METRIC_SUMMARY_ANALYSIS,
    )
    from services.scientific_analytics import MetricState, assemble_cohort_from_metadata, metric_state

    summary_outer = load_analysis_summary(run)
    summary_inner = summary_outer.get("summary") if isinstance(summary_outer, dict) and isinstance(summary_outer.get("summary"), dict) else (summary_outer if isinstance(summary_outer, dict) else {})
    stats_run = extract_stats_run(run)

    if run.analysis_type in {JOB_METRIC_SUMMARY_ANALYSIS, SELECTION_METRIC_SUMMARY_ANALYSIS}:
        raw_cols = raw_outputs.get("columns") if isinstance(raw_outputs.get("columns"), dict) else {}
        metrics = {
            key: _distribution_from_toolkit_column(col)
            for key, col in raw_cols.items()
            if not str(key).startswith("cohort::") and isinstance(col, dict) and col.get("count")
        }
        cohorts = [
            assemble_cohort_from_metadata(cmeta, summary_columns=raw_cols).model_dump(mode="json")
            for cmeta in (summary_inner.get("cohort_metadata") or [])
            if isinstance(cmeta, dict)
        ]
        return {
            **raw_outputs,
            "metrics": metrics,
            "scientific_cohorts": cohorts,
            "stats_run": stats_run,
        }

    if run.analysis_type == JOB_CORRELATION_MATRIX_ANALYSIS:
        metrics = list(summary_inner.get("metrics") or raw_outputs.get("variables") or [])
        m = len(metrics)
        raw_corr = raw_outputs.get("correlation_matrix") or []
        matrix = [
            [
                1.0 if i == j else (
                    round(float(raw_corr[i][j]), 4)
                    if i < len(raw_corr) and j < len(raw_corr[i]) and raw_corr[i][j] is not None
                    else 0.0
                )
                for j in range(m)
            ]
            for i in range(m)
        ]
        sample_sizes = summary_inner.get("sample_sizes") or [[int(raw_outputs.get("n") or 0)] * m for _ in range(m)]
        corr_lookup: dict[tuple[str, str], MetricState] = {}
        for item in (raw_outputs.get("pairs") or []):
            if isinstance(item, dict) and item.get("var_a") and item.get("var_b"):
                stat = item.get("statistic")
                corr_lookup[(str(item["var_a"]), str(item["var_b"]))] = (
                    metric_state(float(stat))
                    if stat is not None
                    else MetricState(state="unavailable", value=None, reason_code="constant_metric")
                )
        cohort_scope = summary_inner.get("cohort_correlation_scope") or {}
        cohorts = []
        for cmeta in (summary_inner.get("cohort_metadata") or []):
            if not isinstance(cmeta, dict):
                continue
            use_lookup = corr_lookup if cohort_scope.get("cohort_key") == cmeta.get("cohort_key") else None
            cohorts.append(
                assemble_cohort_from_metadata(
                    cmeta,
                    summary_columns=None,
                    correlation_lookup=use_lookup,
                ).model_dump(mode="json")
            )
        return {
            **raw_outputs,
            "job_id": str(summary_inner.get("job_id") or run.subject_id),
            "metrics": metrics,
            "matrix": matrix,
            "sample_sizes": sample_sizes,
            "p_value_matrix": raw_outputs.get("p_value_matrix"),
            "adjusted_p_value_matrix": raw_outputs.get("adjusted_p_value_matrix"),
            "pairs": raw_outputs.get("pairs"),
            "method": raw_outputs.get("method") or summary_inner.get("method"),
            "p_adjust_method": raw_outputs.get("p_adjust_method") or summary_inner.get("p_adjust_method"),
            "scientific_cohorts": cohorts,
            "stats_backend": "stats_toolkit",
            "stats_tool_id": "inference.correlation_matrix",
            "stats_method_version": raw_outputs.get("method_version"),
            "stats_run": stats_run,
        }

    if run.analysis_type == SELECTION_BATCH_SUMMARY_ANALYSIS:
        raw_cols = raw_outputs.get("columns") if isinstance(raw_outputs.get("columns"), dict) else {}
        job_ids = list(summary_inner.get("job_ids") or [])
        metrics_summary: dict[str, dict[str, float]] = {}
        for jid in job_ids:
            prefix = f"job::{jid}::"
            for key, col in raw_cols.items():
                if not str(key).startswith(prefix):
                    continue
                rest = str(key)[len(prefix):]
                if rest.startswith("cohort::"):
                    continue
                if isinstance(col, dict) and col.get("mean") is not None:
                    metrics_summary.setdefault(rest, {})[jid] = float(col["mean"])
        cohorts = []
        cmeta_by_job = summary_inner.get("cohort_metadata_by_job") or {}
        for jid in job_ids:
            for cmeta in (cmeta_by_job.get(jid) or []):
                if isinstance(cmeta, dict):
                    cohorts.append(
                        assemble_cohort_from_metadata(
                            cmeta,
                            summary_columns=raw_cols,
                            column_prefix=f"job::{jid}::",
                        ).model_dump(mode="json")
                    )
        return {
            **raw_outputs,
            "job_ids": job_ids,
            "metrics_summary": metrics_summary,
            "common_metrics": sorted(metrics_summary.keys()),
            "scientific_cohorts": cohorts,
            "stats_run": stats_run,
        }

    if run.analysis_type == "md_convergence_diagnostics":
        from services.md.results import read_persisted_md_convergence_diagnostics

        return read_persisted_md_convergence_diagnostics(summary, primary_outputs=raw_outputs)

    if run.analysis_type == "confornets_mds":
        from services.conformational_mapping.confornets_mds import format_confornets_mds_coordinates

        sample_ids = summary.get("sample_ids") if isinstance(summary.get("sample_ids"), list) else None
        coords = format_confornets_mds_coordinates(raw_outputs.get("mds_coordinates"), sample_ids=sample_ids)
        return {
            **raw_outputs,
            "status": "available",
            "stats_backend": "stats_toolkit",
            "stats_tool_id": "multivariate.distance_clustering",
            "stats_run": stats_run,
            "sample_ids": sample_ids or [c["sample_id"] for c in coords],
            "coordinates": coords,
            "source_pairwise_rmsd_sha256": summary.get("source_pairwise_rmsd_sha256"),
        }

    return raw_outputs


def load_analysis_result(run: AnalysisRun) -> Any:
    stats_run = extract_stats_run(run)
    if stats_run is not None:
        raw_outputs = stats_toolkit_client.get_stats_toolkit_client().read_run_outputs(str(stats_run["run_id"]))
        return _assemble_toolkit_analysis_result(run, raw_outputs)
    if run.result_inline_json is not None:
        return run.result_inline_json
    result_path = _resolve_artifact_path(run, "result_json")
    if result_path is None or not result_path.exists():
        return None
    try:
        return json.loads(result_path.read_text())
    except Exception:
        return None


def load_analysis_summary(run: AnalysisRun) -> dict[str, Any] | None:
    if isinstance(run.summary_json, dict):
        return run.summary_json
    summary_path = _resolve_artifact_path(run, "summary_json")
    if summary_path is None or not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text())
    except Exception:
        return None


def serialize_analysis_run(
    run: AnalysisRun | None,
    *,
    analysis_type: str,
    subject_kind: str,
    subject_id: str,
    params: Optional[dict[str, Any]] = None,
    cache_hit: bool = False,
    include_result: bool = True,
    stale: bool = False,
) -> dict[str, Any]:
    if run is None:
        return {
            "run_id": None,
            "analysis_type": analysis_type,
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "status": "missing",
            "resource_class": None,
            "params": params or {},
            "cache_hit": cache_hit,
            "summary": None,
            "result": None,
            "stats_run": None,
            "stale": False,
            "supersedes_run_id": None,
            "reason": None,
            "error_message": None,
            "artifacts": {},
            "queued_at": None,
            "started_at": None,
            "completed_at": None,
            "last_accessed_at": None,
        }

    summary = load_analysis_summary(run)
    stats_run = extract_stats_run(run)
    status = run.status
    reason: str | None = None
    error_message = run.error_message
    result_payload: Any = None

    if is_stats_toolkit_unavailable_run(run):
        status = "unavailable"
        reason = STATS_TOOLKIT_UNAVAILABLE_REASON
    elif include_result and run.status == "completed":
        try:
            result_payload = load_analysis_result(run)
        except StatsToolkitUnavailableError as exc:
            status = "unavailable"
            reason = STATS_TOOLKIT_UNAVAILABLE_REASON
            error_message = str(exc)
            result_payload = None

    return {
        "run_id": run.id,
        "analysis_type": run.analysis_type,
        "subject_kind": run.subject_kind,
        "subject_id": run.subject_id,
        "status": status,
        "resource_class": run.resource_class,
        "params": run.params_json or {},
        "cache_hit": cache_hit,
        "summary": summary,
        "result": result_payload,
        "stats_run": stats_run,
        "stale": bool(stale),
        "supersedes_run_id": run.supersedes_run_id,
        "reason": reason,
        "error_message": error_message,
        "artifacts": run.artifact_manifest or {},
        "queued_at": run.queued_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "last_accessed_at": run.last_accessed_at,
    }


async def get_analysis_run_by_id(session: AsyncSession, run_id: str) -> AnalysisRun | None:
    result = await session.execute(select(AnalysisRun).where(AnalysisRun.id == run_id))
    return result.scalar_one_or_none()
