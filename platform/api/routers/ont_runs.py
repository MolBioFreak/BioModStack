"""ONT instrument-run and ONT/NGS analysis submission API endpoints."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from services.molbio_ngs_receipts import consume_molbio_ngs_receipt
from services.ngs_comparison_panels import consume_comparison_panel_receipt, materialize_comparison_launch
from paths import get_allowed_roots, resolve_allowed_path
from schemas import JobCreate, JobResponse
from services import alignment_access, ont_run_control, ont_submission_trust
from services.ont_barcode_units import load_barcode_unit, load_barcode_units
from services.ont_ngs_contract import (
    get_ont_workflow_spec,
    normalize_ont_launch_params,
    normalized_fasta_sequence_sha256,
    resolve_ont_workflow_alias,
)

router = APIRouter()
barcode_router = APIRouter()

# Canonical API workflow IDs and model-registry modes are distinct contracts.
# Keep this mapping explicit; prefix stripping does not work for wf_clone_validation.
ONT_WORKFLOW_MODEL_MODES: dict[str, str] = {
    "ont_basecall_dna": "basecall_dna",
    "ont_basecall_rna": "basecall_rna",
    "ont_plasmid_qc": "plasmid_qc",
    "ont_construct_screening": "construct_screening",
    "ont_methylation_analysis": "methylation_analysis",
    "ont_fastq_qc": "fastq_qc",
    "wf_clone_validation": "clone_validation",
}

ONT_PRIMARY_INPUT_KEYS: dict[str, str] = {
    "pod5": "pod5_dir",
    "bam": "bam_path",
    "fastq": "fastq_path",
}

ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS = ont_submission_trust.ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS
ONT_SERVER_CONTROLLED_RUNTIME_PARAMS = ont_submission_trust.ONT_SERVER_CONTROLLED_RUNTIME_PARAMS

# Browser-submitted handoff tuning is a separate, tiny allowlist below; primary
# input, output/reference paths, and source provenance never enter it.

ONT_INSTRUMENT_HANDOFF_SAFE_TUNING_PARAMS = frozenset(
    {
        "igv_report_max_sites",
        "enable_rotating_reference_frames",
        "rotation_scan_step_bp",
        "single_ref_split_min_mapq",
        "single_ref_split_min_segment_bp",
        "single_ref_split_max_query_gap_bp",
    }
)

ONT_REFERENCE_REQUIRED_WORKFLOWS = frozenset(
    {
        "ont_plasmid_qc",
        "ont_construct_screening",
        "ont_methylation_analysis",
        "ont_fastq_qc",
        "wf_clone_validation",
    }
)

ONT_COMPARISON_PANEL_WORKFLOWS = frozenset({"ont_plasmid_qc", "ont_construct_screening", "ont_fastq_qc"})


def _validate_comparison_panel_launch(workflow_id: str, expected_receipt_id: str, panel_receipt_id: str) -> None:
    """Keep comparison attribution out of vendor clone and non-QC routes."""
    if panel_receipt_id and not expected_receipt_id:
        raise ValueError("comparison attribution requires both expected-construct and approved-panel receipts")
    if panel_receipt_id and workflow_id not in ONT_COMPARISON_PANEL_WORKFLOWS:
        raise ValueError("approved comparison panels are only available for generic plasmid QC, construct screening, or FASTQ QC")


def _instrument_handoff_tuning_params(raw_params: Any) -> dict[str, Any]:
    """Validate the tiny browser-facing tuning contract without pass-through keys."""
    if raw_params is None:
        return {}
    if not isinstance(raw_params, dict):
        raise ValueError("instrument handoff params must be an object")
    if set(raw_params) - ONT_INSTRUMENT_HANDOFF_SAFE_TUNING_PARAMS:
        raise ValueError("instrument handoff params contain unsupported fields")

    normalized: dict[str, Any] = {}
    integer_limits = {
        "igv_report_max_sites": (1, 10_000),
        "rotation_scan_step_bp": (1, 10_000),
        "single_ref_split_min_mapq": (0, 60),
        "single_ref_split_min_segment_bp": (1, 1_000_000),
        "single_ref_split_max_query_gap_bp": (0, 1_000_000),
    }
    for key, (lower, upper) in integer_limits.items():
        if key not in raw_params:
            continue
        value = raw_params[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError("instrument handoff tuning value is outside the allowed integer range")
        normalized[key] = value
    if "enable_rotating_reference_frames" in raw_params:
        value = raw_params["enable_rotating_reference_frames"]
        if not isinstance(value, bool):
            raise ValueError("enable_rotating_reference_frames must be boolean")
        normalized["enable_rotating_reference_frames"] = value
    return normalized


class OntNgsSubmitRequest(BaseModel):
    """Request body for submitting an ONT/NGS analysis workflow."""

    name: str | None = Field(default=None, description="Optional job name. Defaults to the workflow display name.")
    params: dict[str, Any] = Field(default_factory=dict)
    pinned_gpu: int | None = Field(default=None)
    source_instrument_run_id: str | None = Field(default=None)


class OntRunIntentRequest(BaseModel):
    """Bounded browser input: only server-issued option handles and metadata."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    option_id: str = Field(min_length=1, max_length=80)
    option_receipt_id: str = Field(min_length=1, max_length=80)
    sample_id: str | None = Field(default=None, max_length=255)
    experiment_group: str | None = Field(default=None, max_length=255)


class OntIntentStartRequest(BaseModel):
    """Confirmation for a persisted intent; no protocol/path/model fields exist here."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    confirm_start: StrictBool
    intent_generation: int = Field(ge=1)


class OntBarcodeUnitSubmitRequest(BaseModel):
    """Exact per-barcode BAM resubmission; arbitrary parameter overrides are forbidden."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    target_workflow: str = Field(pattern="^(ont_plasmid_qc|ont_construct_screening)$")
    reference_fasta: str = Field(min_length=1)
    name: str | None = None
    pinned_gpu: int | None = None


async def _authorized_barcode_source(job_id: str, request: Request, session: AsyncSession) -> tuple[Job, Path, str]:
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.model_id != "nanopore":
        raise HTTPException(status_code=404, detail="Nanopore source job not found")
    if job.mode != "basecall_dna":
        raise HTTPException(status_code=422, detail="Source job is not a DNA barcode-basecalling job")
    if str((job.params or {}).get("barcode_kit") or "").strip() != "SQK-RBK114-96":
        raise HTTPException(status_code=422, detail="Source job is not bound to the locked barcode kit")
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Source barcode job is not complete")
    completed_stages = list(job.completed_stages or [])
    stage_outputs = dict(job.stage_outputs or {})
    demux_outputs = stage_outputs.get("dorado_demux")
    if "dorado_demux" not in completed_stages or not isinstance(demux_outputs, list) or not any(
        str(value).replace("\\", "/").endswith("/demux/demux_manifest.json") for value in demux_outputs
    ):
        raise HTTPException(status_code=409, detail="Source job has no recorded terminal dorado_demux product")
    terminal = (job.provenance or {}).get("ont_dorado_terminal_products")
    products = terminal.get("products") if isinstance(terminal, dict) else None
    required_products = {
        "demux_manifest",
        "barcode_units_manifest",
        "dorado_preflight",
        "dorado_runtime_provenance",
    }
    product_digests = {
        key: str(value.get("sha256") or "")
        for key, value in products.items()
        if isinstance(products, dict) and isinstance(value, dict)
    } if isinstance(products, dict) else {}
    manifest_sha256 = product_digests.get("demux_manifest", "")
    if (
        not isinstance(terminal, dict)
        or terminal.get("schema") != "biomodstack.ont_dorado_terminal_products.v1"
        or not required_products <= set(product_digests)
        or any(not re.fullmatch(r"[0-9a-f]{64}", product_digests[key]) for key in required_products)
    ):
        raise HTTPException(status_code=409, detail="Source job terminal Dorado product anchor is missing or malformed")
    if not alignment_access.request_is_authorized(request, job.id, job.provenance):
        raise HTTPException(status_code=403, detail="alignment access denied")
    output_dir = Path(str(job.output_dir or "")).expanduser()
    if not output_dir.is_dir():
        raise HTTPException(status_code=409, detail="authoritative source result root is unavailable")
    return job, output_dir, manifest_sha256


async def _authorized_barcode_unit(job_id: str, unit_id: str, request: Request, session: AsyncSession) -> tuple[Job, dict[str, Any]]:
    job, output_dir, manifest_sha256 = await _authorized_barcode_source(job_id, request, session)
    try:
        unit = load_barcode_unit(
            output_dir / "demux" / "demux_manifest.json",
            output_dir,
            unit_id,
            expected_manifest_sha256=manifest_sha256,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job, unit


def _mode_for_ont_workflow(workflow_id: str) -> str:
    canonical = resolve_ont_workflow_alias(workflow_id)
    return ONT_WORKFLOW_MODEL_MODES[canonical]


def _validate_ont_input_contract(canonical_id: str, params: dict[str, Any]) -> tuple[str, str]:
    """Validate one truthful primary input and return its mode/path."""
    spec = get_ont_workflow_spec(canonical_id)
    selected = [
        (input_mode, key, str(params[key]).strip())
        for input_mode, key in ONT_PRIMARY_INPUT_KEYS.items()
        if params.get(key) is not None and str(params[key]).strip()
    ]
    if len(selected) != 1:
        keys = ", ".join(ONT_PRIMARY_INPUT_KEYS.values())
        raise ValueError(f"exactly one primary ONT input is required ({keys}); found {len(selected)}")

    input_mode, input_key, input_path = selected[0]
    if input_mode not in spec.input_modes:
        raise ValueError(
            f"workflow {canonical_id!r} does not accept {input_mode!r} input via {input_key!r}; "
            f"accepted modes: {', '.join(spec.input_modes)}"
        )

    if canonical_id in ONT_REFERENCE_REQUIRED_WORKFLOWS and not str(params.get("reference_fasta") or "").strip():
        raise ValueError(f"workflow {canonical_id!r} requires reference_fasta for construct verification")

    if input_mode == "fastq" and bool(params.get("run_modkit")):
        raise ValueError("modkit requires a BAM with meaningful MM/ML tags or POD5 basecalled with modified bases")

    return input_mode, input_path


def _confine_submitted_path(value: Any, label: str, *, directory: bool, allow_results: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is empty")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        lexical = candidate.absolute()
    else:
        lexical = resolve_allowed_path(raw)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} must be an existing allowed path") from exc
    matched_root: Path | None = None
    relative: Path | None = None
    roots = get_allowed_roots()
    result_keys = tuple(key for key in ("bms_results", "results") if key in roots)
    if not allow_results:
        for key in result_keys:
            try:
                resolved.relative_to(roots[key].resolve())
            except ValueError:
                continue
            raise ValueError(f"{label} points into protected job results and requires source authorization")
    root_keys = ("inputs", "uploads", "downloads", "data") + (result_keys if allow_results else ())
    for root in (roots[key] for key in root_keys if key in roots):
        root_resolved = root.resolve()
        try:
            relative = lexical.relative_to(root_resolved)
            resolved.relative_to(root_resolved)
        except ValueError:
            continue
        matched_root = root_resolved
        break
    if matched_root is None or relative is None:
        raise ValueError(f"{label} must be confined beneath an allowed data root")
    cursor = matched_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} may not traverse symlinks")
    if directory and not resolved.is_dir():
        raise ValueError(f"{label} must be an existing directory")
    if not directory and not resolved.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    return str(resolved)


def _safe_ont_job_name(value: Any, fallback: str) -> str:
    name = str(value or fallback).strip()
    if not name or len(name) > 128 or ".." in name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", name):
        raise ValueError("ONT job name must be 1-128 safe filename characters without traversal components")
    return name


def _job_create_for_ont_submit(
    workflow_id: str,
    request: OntNgsSubmitRequest,
    *,
    trusted_server_params: frozenset[str] = frozenset(),
    trusted_result_paths: frozenset[str] = frozenset(),
) -> JobCreate:
    submitted_params = dict(request.params)
    if any(key in submitted_params for key in ("comparison_panel_snapshot", "comparison_panel_min_mapq", "ngs_comparison_panel_receipt_id")):
        raise ValueError(
            "comparison-panel paths are not accepted from ordinary NGS submissions; use a server-staged operator receipt"
        )
    reserved_params = ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ONT_SERVER_CONTROLLED_RUNTIME_PARAMS
    submitted_server_controlled = sorted(
        reserved_params.intersection(submitted_params) - trusted_server_params
    )
    if submitted_server_controlled:
        raise ValueError(
            "server-controlled ONT provenance/runtime parameters cannot be supplied by a generic submit request: "
            + ", ".join(submitted_server_controlled)
        )

    canonical_id = resolve_ont_workflow_alias(workflow_id)
    spec = get_ont_workflow_spec(canonical_id)
    params = normalize_ont_launch_params(canonical_id, submitted_params)
    path_contract = {
        "pod5_dir": True,
        "bam_path": False,
        "fastq_path": False,
        "reference_fasta": False,
        "wf_clone_primers": False,
        "wf_clone_insert_reference": False,
        "wf_clone_host_reference": False,
        "wf_clone_regions_bedfile": False,
        "sample_sheet": False,
        "duplex_pairs": False,
    }
    for key, is_directory in path_contract.items():
        if str(params.get(key) or "").strip():
            params[key] = _confine_submitted_path(
                params[key], key, directory=is_directory, allow_results=key in trusted_result_paths
            )
    input_mode, input_path = _validate_ont_input_contract(canonical_id, params)
    reference_raw = str(params.get("reference_fasta") or "").strip()
    if reference_raw:
        reference_path = Path(reference_raw).expanduser()
        if reference_path.is_file():
            params["reference_sequence_sha256"] = normalized_fasta_sequence_sha256(reference_path)
    model_mode = _mode_for_ont_workflow(canonical_id)
    params["ont_request_workflow_id"] = workflow_id
    params["ont_workflow_id"] = canonical_id
    params["ont_model_mode"] = model_mode
    params["ont_input_mode"] = input_mode
    params["ont_input_provenance"] = {
        "mode": input_mode,
        "path": input_path,
        "source": "submitted_path",
    }
    if request.source_instrument_run_id:
        params["source_instrument_run_id"] = request.source_instrument_run_id

    return JobCreate(
        name=_safe_ont_job_name(request.name, f"{spec.display_name} analysis"),
        model_id="nanopore",
        mode=model_mode,
        params=params,
        pinned_gpu=request.pinned_gpu,
    )


async def _create_pipeline_job(
    job: JobCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    response: Response,
    request: Request,
) -> JobResponse:
    # Import lazily so importing the ONT router does not import the entire jobs
    # router stack unless a launch request actually reaches this endpoint.
    from routers.jobs import create_job  # noqa: PLC0415

    token, token_digest = alignment_access.issue_alignment_access_token()
    trust_token = ont_submission_trust.begin_trusted_ont_job_creation(token_digest)
    try:
        created = await create_job(job, background_tasks, session)
    finally:
        ont_submission_trust.end_trusted_ont_job_creation(trust_token)
    alignment_access.set_alignment_access_cookie(created.id, token, response, request)
    return created


@router.get("/positions/{position}/protocol-options")
async def ont_position_protocol_options(position: str) -> dict[str, Any]:
    """Issue expiring opaque server-owned protocol options for one live position."""
    return await ont_run_control.issue_position_protocol_catalog(position)


@router.post("/positions/{position}/run-intents")
async def ont_create_run_intent(position: str, payload: OntRunIntentRequest) -> dict[str, Any]:
    try:
        return await ont_run_control.create_run_intent(position, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/runs/{run_id}/start")
async def ont_validate_and_start_intent(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Re-read the position before a future start; actual MinKNOW start remains disabled."""
    try:
        confirmed = OntIntentStartRequest.model_validate(payload)
    except ValidationError as exc:
        # Do not reflect rejected body values: callers may have supplied host
        # protocol/path identifiers that are intentionally browser-opaque.
        raise HTTPException(status_code=422, detail="invalid opaque intent confirmation") from exc
    try:
        return await ont_run_control.validate_armed_intent_start(run_id, confirmed.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail="MinKNOW protocol start remains disabled pending separately authorized supervised commissioning.",
        ) from exc


@router.post("/positions/{position}/hardware-check")
async def ont_begin_position_hardware_check(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Retired physical diagnostic activation; commissioning requires separate supervision."""
    del position, payload
    raise HTTPException(
        status_code=501,
        detail="Mk1D hardware-check activation is disabled pending separately authorized supervised commissioning.",
    )


@router.post("/positions/{position}/refresh")
async def ont_refresh_position_state(position: str) -> dict[str, Any]:
    """Re-read MinKNOW state for a position without power-cycling the instrument."""
    try:
        return ont_run_control.refresh_position_state(position)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT position: {position}") from exc


@router.post("/positions/{position}/restart")
async def ont_restart_position(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Expose explicit restart contract; host-agent refuses until live semantics are validated."""
    try:
        return ont_run_control.restart_position(position, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/positions/{position}/start")
async def ont_start_instrument_run(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Retired raw start surface; starts must be bound to a persisted opaque intent."""
    del position, payload
    raise HTTPException(
        status_code=410,
        detail="raw ONT start is retired; create a run intent from an opaque protocol receipt and use /runs/{id}/start",
    )


@router.get("/runs/{run_id}")
async def ont_get_instrument_run(run_id: str) -> dict[str, Any]:
    """Read the durable BMS ledger without contacting MinKNOW or mutating it."""
    record = await ont_run_control.get_instrument_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}")
    return record


@router.post("/runs/{run_id}/reconcile")
async def ont_reconcile_instrument_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile a durable run from the bounded host-agent status snapshot only."""
    if payload:
        raise HTTPException(status_code=422, detail="instrument reconciliation accepts no browser-controlled fields")
    try:
        return await ont_run_control.reconcile_instrument_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/handoff/plasmid-qc")
async def ont_handoff_plasmid_qc(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Retired because a handoff descriptor contains server-only file paths.

    The submit endpoint below still builds this descriptor internally and passes
    it directly to the job-creation boundary; it must never serialize it back to
    the browser.
    """
    del run_id, payload
    raise HTTPException(
        status_code=410,
        detail="raw ONT handoff descriptors are server-only; submit through /runs/{id}/handoff/plasmid-qc/submit",
    )


@router.post("/ngs/{workflow_id}/submit", response_model=JobResponse, status_code=201)
async def ont_submit_ngs_workflow(
    workflow_id: str,
    request: OntNgsSubmitRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Submit a canonical ONT/NGS Nextflow analysis job.

    This is the typed ONT product-family launch seam. It normalizes workflow
    aliases/defaults through the ONT registry, then delegates to the canonical
    job creation path so queueing, runtime policy, validation, and orchestrator
    behavior remain identical to other BioModStack jobs.
    """
    try:
        submitted = dict(request.params)
        if str(submitted.get("molbio_sequence_id") or "").strip():
            raise ValueError("molbio_sequence_id is not accepted at submission; submit a server-issued molbio_ngs_receipt_id")
        receipt_id = str(submitted.pop("molbio_ngs_receipt_id", "") or "").strip()
        panel_receipt_id = str(submitted.pop("ngs_comparison_panel_receipt_id", "") or "").strip()
        canonical_id = resolve_ont_workflow_alias(workflow_id)
        _validate_comparison_panel_launch(canonical_id, receipt_id, panel_receipt_id)
        receipt = None
        panel_receipt = None
        if receipt_id:
            receipt = await consume_molbio_ngs_receipt(session, receipt_id=receipt_id)
            submitted["reference_fasta"] = receipt.reference_snapshot_path
        if panel_receipt_id:
            panel_receipt = await consume_comparison_panel_receipt(
                session, receipt_id=panel_receipt_id, expected_receipt_id=receipt_id
            )
            staged = materialize_comparison_launch(
                expected_fasta=receipt.reference_snapshot_path,
                expected_sha256=receipt.reference_snapshot_sha256,
                panel_receipt=panel_receipt,
            )
            submitted["reference_fasta"] = staged["reference_fasta"]
        job = _job_create_for_ont_submit(workflow_id, request.model_copy(update={"params": submitted}))
        if receipt is not None:
            job.params["molbio_revision_binding"] = {
                "sequence_id": receipt.sequence_id,
                "revision_id": receipt.revision_id,
                "revision_sha256": receipt.revision_sha256,
                "reference_snapshot_sha256": receipt.reference_snapshot_sha256,
                "receipt_id": receipt.id,
                "binding_source": "server_issued_one_time_receipt",
            }
            receipt.consumed_at = datetime.utcnow()
            # Flush the one-time state before queueing so a competing submit
            # cannot observe an unused receipt while this request is creating a job.
            await session.flush()
        if panel_receipt is not None:
            panel_receipt.consumed_at = datetime.utcnow()
            job.params["comparison_panel_snapshot"] = staged["comparison_panel_snapshot"]
            job.params["comparison_panel_binding"] = {
                "panel_id": panel_receipt.panel_id,
                "panel_version": panel_receipt.panel_version,
                "panel_snapshot_sha256": panel_receipt.panel_snapshot_sha256,
                "receipt_id": panel_receipt.id,
                "task_input_root": staged["input_root"],
                "binding_source": "server_approved_panel_receipt",
            }
            await session.flush()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        created = await _create_pipeline_job(job, background_tasks, session, response, http_request)
    except Exception:
        # The receipt pair is a launch transaction: a queueing failure must not
        # strand either one-time receipt as consumed.
        if receipt is not None:
            receipt.consumed_at = None
        if panel_receipt is not None:
            panel_receipt.consumed_at = None
        await session.commit()
        raise
    if receipt is not None:
        receipt.consumed_job_id = created.id
    if panel_receipt is not None:
        panel_receipt.consumed_job_id = created.id
    if receipt is not None or panel_receipt is not None:
        await session.commit()
    return created


@barcode_router.get("/{job_id}/barcode-units")
async def ont_list_barcode_units(
    job_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List every digest-verified unit from a completed authorized source job."""
    _, output_dir, manifest_sha256 = await _authorized_barcode_source(job_id, http_request, session)
    try:
        units = load_barcode_units(
            output_dir / "demux" / "demux_manifest.json",
            output_dir,
            expected_manifest_sha256=manifest_sha256,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id, "units": units}


@barcode_router.get("/{job_id}/barcode-units/{unit_id}")
async def ont_get_barcode_unit(
    job_id: str,
    unit_id: str,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return one exact digest-bound barcode unit after source-job authorization."""
    _, unit = await _authorized_barcode_unit(job_id, unit_id, http_request, session)
    return unit


@barcode_router.post("/{job_id}/barcode-units/{unit_id}/submit", response_model=JobResponse, status_code=201)
async def ont_submit_barcode_unit(
    job_id: str,
    unit_id: str,
    request: OntBarcodeUnitSubmitRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Submit one isolated barcode BAM with exact source lineage and no override map."""
    source_job, unit = await _authorized_barcode_unit(job_id, unit_id, http_request, session)
    trusted = {
        "bam_source_sha256": unit["bam_sha256"],
        "source_ont_job_id": source_job.id,
        "source_barcode_unit": unit["unit_id"],
        "source_barcode_manifest_sha256": unit["manifest_sha256"],
    }
    submit = OntNgsSubmitRequest(
        name=request.name or f"{source_job.name} {unit_id}",
        params={
            "bam_path": unit["bam_path"],
            "reference_fasta": request.reference_fasta,
            "bam_force_realign": True,
            **trusted,
        },
        pinned_gpu=request.pinned_gpu,
    )
    try:
        job = _job_create_for_ont_submit(
            request.target_workflow,
            submit,
            trusted_server_params=frozenset(trusted),
            trusted_result_paths=frozenset({"bam_path"}),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _create_pipeline_job(job, background_tasks, session, response, http_request)


@router.post("/runs/{run_id}/handoff/plasmid-qc/submit", response_model=JobResponse, status_code=201)
async def ont_submit_plasmid_qc_from_run(
    run_id: str,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    http_request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Submit generic plasmid QC from a durable artifact and a one-time MolBio receipt."""
    allowed_fields = {"name", "params", "pinned_gpu", "molbio_ngs_receipt_id"}
    if not isinstance(payload, dict) or set(payload) - allowed_fields:
        raise HTTPException(status_code=422, detail="instrument handoff accepts only a name, tuning params, pinned_gpu, and molbio_ngs_receipt_id")
    name = payload.get("name")
    if name is not None and (not isinstance(name, str) or len(name.strip()) > 255):
        raise HTTPException(status_code=422, detail="instrument handoff name must be a bounded string")
    pinned_gpu = payload.get("pinned_gpu")
    if pinned_gpu is not None and (isinstance(pinned_gpu, bool) or not isinstance(pinned_gpu, int) or pinned_gpu < 0):
        raise HTTPException(status_code=422, detail="instrument handoff pinned_gpu must be a non-negative integer")
    try:
        extra_params = _instrument_handoff_tuning_params(payload.get("params"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    receipt_id = str(payload.get("molbio_ngs_receipt_id") or "").strip()
    if not receipt_id:
        raise HTTPException(status_code=422, detail="instrument handoff requires a server-issued molbio_ngs_receipt_id")
    try:
        receipt = await consume_molbio_ngs_receipt(session, receipt_id=receipt_id)
        handoff = await ont_run_control.build_plasmid_qc_handoff(
            run_id,
            {"reference_fasta": receipt.reference_snapshot_path},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc

    submit_request = OntNgsSubmitRequest(
        name=(name or "ONT plasmid QC").strip(),
        params={**extra_params, **handoff["params"]},
        pinned_gpu=pinned_gpu,
        source_instrument_run_id=run_id,
    )
    try:
        job = _job_create_for_ont_submit(
            "ont_plasmid_qc",
            submit_request,
            trusted_server_params=frozenset(handoff["params"]).intersection(
                ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ONT_SERVER_CONTROLLED_RUNTIME_PARAMS
            ),
            trusted_result_paths=frozenset({"fastq_path"}),
        )
        receipt.consumed_at = datetime.utcnow()
        await session.flush()
        created = await _create_pipeline_job(job, background_tasks, session, response, http_request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        receipt.consumed_at = None
        await session.commit()
        raise
    receipt.consumed_job_id = created.id
    await session.commit()
    return created


@router.post("/runs/{run_id}/stop")
async def ont_stop_instrument_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    del run_id, payload
    raise HTTPException(
        status_code=410,
        detail="Browser-initiated ONT physical stop is retired; use the separately supervised instrument-control lane.",
    )
