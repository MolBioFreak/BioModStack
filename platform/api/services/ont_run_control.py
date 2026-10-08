"""ONT instrument-run preflight/control service.

This service deliberately separates live acquisition readiness from existing
file-analysis jobs. It only reports start readiness when real host-agent/MinKNOW
state supports it; it does not fabricate protocol options or device positions.
"""

from __future__ import annotations

from typing import Any

from services.host_agent_client import get_ont_position, get_ont_status, request_host_agent


def _flowcell_present(position: dict[str, Any]) -> bool:
    flow_cell = position.get("flow_cell") if isinstance(position, dict) else None
    return bool(isinstance(flow_cell, dict) and flow_cell.get("present"))


def build_start_preflight(
    *,
    position: dict[str, Any],
    kit: str | None,
    basecalling_enabled: bool = True,
    basecalling_options: dict[str, Any] | None = None,
    output_directories: dict[str, Any] | None = None,
    protocol_id: str | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    if not _flowcell_present(position):
        blockers.append("flowcell_absent")
    if bool(position.get("running")):
        blockers.append("position_already_running")
    if not str(kit or "").strip():
        blockers.append("kit_missing")
    if basecalling_enabled and not (basecalling_options or {}).get("simplex_models"):
        blockers.append("basecalling_model_missing")
    if not output_directories:
        blockers.append("output_directory_missing")

    return {
        "position": position.get("position"),
        "can_start": not blockers,
        "blockers": blockers,
        "protocol_id": protocol_id,
        "kit": kit,
        "basecalling_enabled": bool(basecalling_enabled),
        "basecalling_options": basecalling_options or {},
        "output_directories": output_directories or {},
        "flow_cell": position.get("flow_cell") or {"present": False},
        "fake_or_demo_devices": False,
    }


def get_position_protocol_options(
    position: str,
    *,
    kit: str | None = None,
    basecalling_enabled: bool = True,
) -> dict[str, Any]:
    """Return host-agent protocol options/preflight for one ONT position."""
    host_payload = request_host_agent(
        "GET",
        f"/ont/positions/{position}/protocol-options",
        query={"kit": kit, "basecalling_enabled": "1" if basecalling_enabled else "0"},
    )
    if isinstance(host_payload, dict) and "can_start" in host_payload:
        return host_payload

    # Backward-compatible fallback for host agents that expose only position
    # discovery. This is still truthful: it never invents protocol IDs/models.
    position_payload = get_ont_position(position)
    position_detail = position_payload.get("position") if isinstance(position_payload.get("position"), dict) else position_payload
    if not isinstance(position_detail, dict):
        position_detail = {"position": position, "flow_cell": {"present": False}}
    status = get_ont_status()
    output_directories = ((status.get("minknow") or {}).get("output_directories") or {}) if isinstance(status, dict) else {}
    return build_start_preflight(
        position=position_detail,
        kit=kit,
        basecalling_enabled=basecalling_enabled,
        output_directories=output_directories,
    )
