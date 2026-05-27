"""ONT device-control API boundary.

These endpoints report hardware-control capability/status only. They do not
pretend that live MK1B/MK1D devices exist when MinKNOW integration is not
configured.
"""

from __future__ import annotations

from fastapi import APIRouter

from services.ont_device_control import get_device_control_status

router = APIRouter()


@router.get("/devices/status")
async def ont_device_status() -> dict[str, object]:
    """Return truthful ONT device-control status for this runtime."""
    return get_device_control_status()
