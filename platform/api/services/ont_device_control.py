"""Truthful ONT Mk1D/MinKNOW device-control boundary for BioModStack.

Live Mk1D/MinKNOW operation is owned by the BMS service/API layer.
Nextflow receives existing run outputs for reproducible analysis; it does not
own live device handles.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from services.host_agent_client import get_ont_status, host_agent_enabled
from services.ont_ngs_contract import ANALYSIS_OWNER, DEVICE_CONTROL_OWNER, get_ont_workflow_spec
from services.ont_minknow_client import discover_minknow_devices

DEVICE_CONTROL_STATUS_NOT_CONFIGURED = "not_configured"

SUPPORTED_DEVICE_TYPES = ({"id": "mk1d", "display_name": "ONT MinION Mk1D", "requires_minknow": True},)

ONT_DEVICE_CONTROL_CAPABILITIES: dict[str, Any] = {
    "owner": DEVICE_CONTROL_OWNER,
    "not_owned_by": ANALYSIS_OWNER,
    "supported_device_types": SUPPORTED_DEVICE_TYPES,
    "controls": [
        "discover_devices",
        "inspect_flowcell",
        "configure_run",
        "start_run",
        "stop_run",
        "monitor_run",
        "handoff_outputs_to_analysis",
    ],
    "analysis_handoff_inputs": ("pod5", "fastq", "bam"),
}


def get_device_control_status() -> dict[str, Any]:
    """Return current ONT device-control status without inventing devices.

    By default BMS reports the live-control boundary as not configured. When
    ``BMS_ONT_MINKNOW_ENABLED=1`` is set, this delegates discovery to the
    MinKNOW API adapter and returns normalized real device positions.
    """
    if os.getenv("BMS_ONT_MINKNOW_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        if host_agent_enabled():
            try:
                discovered = get_ont_status()
            except Exception as exc:  # noqa: BLE001 - host-agent/network failures must degrade truthfully
                discovered = {
                    "implementation_status": "host_agent_unavailable",
                    "live_devices": [],
                    "fake_or_demo_devices": False,
                    "message": f"BMS host-agent ONT status unavailable: {exc}",
                }
        else:
            discovered = discover_minknow_devices()
        return {
            "owner": DEVICE_CONTROL_OWNER,
            "analysis_owner": ANALYSIS_OWNER,
            "supported_device_types": list(SUPPORTED_DEVICE_TYPES),
            **discovered,
        }

    return {
        "owner": DEVICE_CONTROL_OWNER,
        "analysis_owner": ANALYSIS_OWNER,
        "implementation_status": DEVICE_CONTROL_STATUS_NOT_CONFIGURED,
        "supported_device_types": list(SUPPORTED_DEVICE_TYPES),
        "live_devices": [],
        "fake_or_demo_devices": False,
        "message": "MinKNOW/Mk1D live device control is a service/API boundary and is not configured in this runtime.",
    }


def build_analysis_handoff(*, workflow_id: str, run_output_dir: str | Path, primary_input_kind: str) -> dict[str, Any]:
    """Describe handoff from live-run outputs into a reproducible analysis workflow."""
    spec = get_ont_workflow_spec(workflow_id)
    output_path = Path(run_output_dir).expanduser()
    input_kind = str(primary_input_kind or "").strip().lower()
    if (
        input_kind not in ONT_DEVICE_CONTROL_CAPABILITIES["analysis_handoff_inputs"]
        or input_kind not in spec.input_modes
    ):
        raise ValueError(
            f"workflow {spec.workflow_id!r} does not accept ONT input kind: "
            f"{primary_input_kind!r}"
        )

    return {
        "workflow_id": spec.workflow_id,
        "analysis_owner": ANALYSIS_OWNER,
        "device_control_owner": DEVICE_CONTROL_OWNER,
        "requires_live_device": False,
        "primary_input_kind": input_kind,
        "run_output_dir": str(output_path),
        "manifest_contract": spec.manifest_schema,
    }
