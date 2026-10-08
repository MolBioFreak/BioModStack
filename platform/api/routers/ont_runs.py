"""ONT instrument-run and ONT/NGS analysis submission API endpoints."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from paths import get_allowed_roots, resolve_allowed_path
from schemas import JobCreate, JobResponse
from services import alignment_access, ont_run_control, ont_submission_trust
from services.host_agent_client import HostAgentRequestError
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

# Instrument handoff values are validated by ont_run_control and are authoritative.
# Untrusted request extras may add analysis tuning knobs, but must never add or
# replace primary inputs, output/reference paths, or source provenance.
ONT_HANDOFF_CONTROLLED_PARAMS = frozenset(
    {
        *ONT_PRIMARY_INPUT_KEYS.values(),
        "output_dir",
        "reference_fasta",
        "source_instrument_run_id",
        *ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS,
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


class OntNgsSubmitRequest(BaseModel):
    """Request body for submitting an ONT/NGS analysis workflow."""

    name: str | None = Field(default=None, description="Optional job name. Defaults to the workflow display name.")
    params: dict[str, Any] = Field(default_factory=dict)
    pinned_gpu: int | None = Field(default=None)
    source_instrument_run_id: str | None = Field(default=None)


class OntBarcodeUnitSubmitRequest(BaseModel):
    """Exact per-barcode BAM resubmission; arbitrary parameter overrides are forbidden."""

    model_config = ConfigDict(extra="forbid")

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
async def ont_position_protocol_options(
    position: str,
    kit: str | None = Query(default=None),
    basecalling_enabled: bool = Query(default=True),
) -> dict[str, Any]:
    """Return truthful preflight/protocol options for a live ONT position."""
    return ont_run_control.get_position_protocol_options(
        position,
        kit=kit,
        basecalling_enabled=basecalling_enabled,
    )


@router.post("/positions/{position}/hardware-check")
async def ont_begin_position_hardware_check(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start a guarded MinKNOW hardware diagnostic check for a position."""
    try:
        return ont_run_control.begin_position_hardware_check(position, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HostAgentRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


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
    """Start a real MinKNOW run through host-agent after explicit confirmation."""
    try:
        return ont_run_control.start_instrument_run(position, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def ont_get_instrument_run(run_id: str) -> dict[str, Any]:
    try:
        return ont_run_control.refresh_instrument_run_status(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc


@router.post("/runs/{run_id}/handoff/plasmid-qc")
async def ont_handoff_plasmid_qc(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return ont_run_control.build_plasmid_qc_handoff(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc


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
        job = _job_create_for_ont_submit(workflow_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await _create_pipeline_job(job, background_tasks, session, response, http_request)


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
    """Build the instrument-run plasmid-QC handoff and submit the Nextflow job."""
    try:
        handoff = ont_run_control.build_plasmid_qc_handoff(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc

    raw_extra_params = payload.get("params")
    raw_reserved_params = sorted(
        (ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ONT_SERVER_CONTROLLED_RUNTIME_PARAMS).intersection(
            raw_extra_params if isinstance(raw_extra_params, dict) else {}
        )
    )
    if raw_reserved_params:
        raise HTTPException(
            status_code=422,
            detail="server-controlled ONT provenance/runtime parameters cannot be supplied by instrument handoff: "
            + ", ".join(raw_reserved_params),
        )
    extra_params = {
        key: value
        for key, value in raw_extra_params.items()
        if key not in ONT_HANDOFF_CONTROLLED_PARAMS
    } if isinstance(raw_extra_params, dict) else {}
    submit_request = OntNgsSubmitRequest(
        name=str(payload.get("name") or "ONT plasmid QC").strip(),
        params={**extra_params, **handoff["params"]},
        pinned_gpu=payload.get("pinned_gpu"),
        source_instrument_run_id=run_id,
    )
    try:
        job = _job_create_for_ont_submit(
            "ont_plasmid_qc",
            submit_request,
            trusted_server_params=frozenset(handoff["params"]).intersection(
                ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ONT_SERVER_CONTROLLED_RUNTIME_PARAMS
            ),
        )
        return await _create_pipeline_job(job, background_tasks, session, response, http_request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/runs/{run_id}/stop")
async def ont_stop_instrument_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return ont_run_control.stop_instrument_run(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc
