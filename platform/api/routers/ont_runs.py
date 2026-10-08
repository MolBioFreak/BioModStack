"""ONT instrument-run API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

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
