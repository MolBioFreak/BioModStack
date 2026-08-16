from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from schemas import (
    BoltzApiEstimateResponse,
    BoltzApiProviderStatusResponse,
    BoltzApiStructureRequest,
    BoltzApiSubmitRequest,
    JobResponse,
)
from services.boltz_api_jobs import (
    BoltzApiJobError,
    build_boltz_api_input,
    estimate_boltz_api_cost,
    get_cli_update_status,
    probe_provider_status,
    provider_capability_contract,
    queue_boltz_api_job,
)


router = APIRouter()


def _raise_http(exc: BoltzApiJobError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def _provider_input(payload: BoltzApiStructureRequest) -> dict:
    return build_boltz_api_input(
        sequence=payload.sequence,
        primary_chain_id=payload.primary_chain_id,
        complex_components=[component.model_dump(exclude_none=True) for component in payload.complex_components],
        num_samples=payload.num_samples,
        use_msa=payload.use_msa,
    )


@router.get("/status", response_model=BoltzApiProviderStatusResponse)
async def boltz_api_status() -> BoltzApiProviderStatusResponse:
    provider_status = await probe_provider_status()
    return BoltzApiProviderStatusResponse(**{
        **provider_status,
        "capabilities": provider_capability_contract(),
        "cli_update": await get_cli_update_status(),
    })


@router.post("/estimate", response_model=BoltzApiEstimateResponse)
async def estimate_boltz_api_job(payload: BoltzApiStructureRequest) -> BoltzApiEstimateResponse:
    try:
        provider_input = _provider_input(payload)
        estimate, fingerprint = await estimate_boltz_api_cost(model=payload.model, provider_input=provider_input)
    except BoltzApiJobError as exc:
        _raise_http(exc)
    return BoltzApiEstimateResponse(
        model=payload.model,
        provider_input=provider_input,
        estimate=estimate,
        estimate_fingerprint=fingerprint,
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_boltz_api_job(
    payload: BoltzApiSubmitRequest,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    try:
        provider_input = _provider_input(payload)
        job = await queue_boltz_api_job(
            session,
            name=payload.name,
            client_request_id=payload.client_request_id,
            model=payload.model,
            provider_input=provider_input,
            approved_estimate_fingerprint=payload.approved_estimate_fingerprint,
        )
    except BoltzApiJobError as exc:
        _raise_http(exc)
    return JobResponse.model_validate(job)
