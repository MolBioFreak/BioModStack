"""ONT instrument-run and ONT/NGS analysis submission API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from schemas import JobCreate, JobResponse
from services import alignment_access, ont_run_control, ont_submission_trust
from services.host_agent_client import HostAgentRequestError
from services.ont_ngs_contract import (
    get_ont_workflow_spec,
    normalize_ont_launch_params,
    normalized_fasta_sequence_sha256,
    resolve_ont_workflow_alias,
)

router = APIRouter()

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


def _job_create_for_ont_submit(
    workflow_id: str,
    request: OntNgsSubmitRequest,
    *,
    trusted_server_params: frozenset[str] = frozenset(),
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
        name=(request.name or f"{spec.display_name} analysis").strip(),
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
