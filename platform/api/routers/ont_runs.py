"""ONT instrument-run API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services import ont_run_control

router = APIRouter()


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


@router.post("/runs/{run_id}/stop")
async def ont_stop_instrument_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return ont_run_control.stop_instrument_run(run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ONT instrument run: {run_id}") from exc
