"""ONT device-control API boundary.

These endpoints report hardware-control capability/status only. They do not
pretend that live MK1B/MK1D devices exist when MinKNOW integration is not
configured.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

from mobile_apk_auth import require_mk1d_reconnect_local_bms_web
from services import ont_device_control

router = APIRouter()


class Mk1dReconnectRequest(BaseModel):
    """The only accepted reconnect command; arbitrary body fields are forbidden."""

    model_config = ConfigDict(extra="forbid", strict=True)
    confirm_reconnect: StrictBool

    @field_validator("confirm_reconnect")
    @classmethod
    def _require_explicit_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("confirm_reconnect must be true")
        return value

@router.get("/devices/status")
async def ont_device_status() -> dict[str, object]:
    """Return truthful ONT device-control status for this runtime."""
    return ont_device_control.get_device_control_status()


@router.post(
    "/devices/reconnect",
    status_code=202,
    dependencies=[Depends(require_mk1d_reconnect_local_bms_web)],
)
async def reconnect_ont_mk1d(
    confirmation: Mk1dReconnectRequest,
) -> dict[str, object]:
    """Run the fixed recovery transaction for the trusted local BMS operator."""
    # Accessing the literal after Pydantic validation prevents accidental future
    # widening of the body model into a command/configuration transport.
    assert confirmation.confirm_reconnect is True
    try:
        return await asyncio.to_thread(ont_device_control.reconnect_mk1d)
    except ont_device_control.ReconnectHelperUnavailable as exc:
        raise HTTPException(status_code=503, detail="Reconnect helper unavailable/not installed") from exc
    except ont_device_control.ReconnectHelperProtocolError as exc:
        raise HTTPException(status_code=502, detail="Reconnect helper did not return a valid receipt") from exc
