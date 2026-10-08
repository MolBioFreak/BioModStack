"""ONT instrument-run preflight/control service.

This service deliberately separates live acquisition readiness from existing
file-analysis jobs. It only reports start readiness when real host-agent/MinKNOW
state supports it; it does not fabricate protocol options or device positions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from services.host_agent_client import get_ont_position, get_ont_status, request_host_agent

_ONT_RUN_STORE: dict[str, dict[str, Any]] = {}


def reset_ont_run_store() -> None:
    _ONT_RUN_STORE.clear()


def _new_run_id() -> str:
    return f"ont-run-{uuid4().hex}"


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




def begin_position_hardware_check(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not bool(payload.get("confirm_hardware_check")):
        raise ValueError("confirm_hardware_check=true is required before starting a MinKNOW hardware check")
    host_payload = request_host_agent("POST", f"/ont/positions/{position}/hardware-check", payload)
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object hardware-check payload: {host_payload!r}")
    return host_payload

def refresh_position_state(position: str) -> dict[str, Any]:
    host_payload = request_host_agent("POST", f"/ont/positions/{position}/refresh", {"confirm_refresh": True})
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object refresh payload: {host_payload!r}")
    return host_payload


def restart_position(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not bool(payload.get("confirm_restart")):
        raise ValueError("confirm_restart=true is required before requesting an ONT instrument restart")
    host_payload = request_host_agent("POST", f"/ont/positions/{position}/restart", payload)
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object restart payload: {host_payload!r}")
    return host_payload

def start_instrument_run(position: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start an ONT instrument run through the host-agent and record its BMS ID."""
    if not bool(payload.get("confirm_start")):
        raise ValueError("confirm_start=true is required before starting a MinKNOW run")
    host_payload = request_host_agent("POST", f"/ont/positions/{position}/start", payload)
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object start payload: {host_payload!r}")
    minknow_run_id = str(host_payload.get("minknow_run_id") or host_payload.get("run_id") or "").strip()
    if not minknow_run_id:
        raise RuntimeError("host-agent start response did not include minknow_run_id")
    run_id = _new_run_id()
    record = {
        "id": run_id,
        "minknow_run_id": minknow_run_id,
        "position": host_payload.get("position") or position,
        "status": host_payload.get("status") or "starting",
        "sample_id": payload.get("sample_id"),
        "experiment_group": payload.get("experiment_group"),
        "kit": payload.get("kit"),
        "output_directories": host_payload.get("output_directories") or {},
        "last_minknow_payload": host_payload,
        "fake_or_demo_devices": False,
    }
    _ONT_RUN_STORE[run_id] = record
    return dict(record)


def get_instrument_run(run_id: str) -> dict[str, Any] | None:
    record = _ONT_RUN_STORE.get(run_id)
    return dict(record) if record else None


def _existing_output_files(raw_files: Any) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {"fastq": [], "pod5": [], "bam": []}
    if not isinstance(raw_files, dict):
        return normalized
    for kind in normalized:
        values = raw_files.get(kind) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            path = Path(str(value)).expanduser()
            if path.is_file():
                normalized[kind].append(str(path))
    return normalized


def refresh_instrument_run_status(run_id: str) -> dict[str, Any]:
    record = _ONT_RUN_STORE.get(run_id)
    if record is None:
        raise KeyError(run_id)
    host_payload = request_host_agent("GET", f"/ont/runs/{record['minknow_run_id']}")
    if not isinstance(host_payload, dict):
        raise RuntimeError(f"host-agent returned non-object run status payload: {host_payload!r}")
    output_files = _existing_output_files(host_payload.get("output_files"))
    refreshed = {
        **record,
        "status": host_payload.get("status") or record.get("status") or "unknown",
        "output_files": output_files,
        "handoff_ready": bool(output_files["fastq"] or output_files["bam"]),
        "last_minknow_payload": host_payload,
        "fake_or_demo_devices": False,
    }
    _ONT_RUN_STORE[run_id] = refreshed
    return dict(refreshed)


def build_plasmid_qc_handoff(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    record = _ONT_RUN_STORE.get(run_id)
    if record is None:
        raise KeyError(run_id)
    reference = Path(str(payload.get("reference_fasta") or "")).expanduser()
    if not reference.is_file():
        raise ValueError("reference_fasta must point to a readable file")
    output_files = record.get("output_files") if isinstance(record.get("output_files"), dict) else {}
    fastq_files = output_files.get("fastq") or []
    if not fastq_files:
        raise ValueError("instrument run has no readable FASTQ output for plasmid QC handoff")
    return {
        "model_id": "nanopore",
        "mode": "plasmid_qc",
        "params": {
            "ont_workflow_id": "ont_plasmid_qc",
            "fastq_path": fastq_files[0],
            "reference_fasta": str(reference),
            "run_fastq_qc": True,
            "run_modkit": False,
            "modified_bases": "none",
            "fastq_minimap2_preset": "map-ont",
            "source_instrument_run_id": run_id,
            "source_minknow_run_id": record.get("minknow_run_id"),
        },
        "fake_or_demo_devices": False,
    }


def stop_instrument_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not bool(payload.get("confirm_stop")):
        raise ValueError("confirm_stop=true is required before stopping a MinKNOW run")
    record = _ONT_RUN_STORE.get(run_id)
    if record is None:
        raise KeyError(run_id)
    host_payload = request_host_agent("POST", f"/ont/runs/{record['minknow_run_id']}/stop", {"confirm_stop": True})
    status = host_payload.get("status", "stopped") if isinstance(host_payload, dict) else "stopped"
    record = {**record, "status": status, "last_minknow_payload": host_payload}
    _ONT_RUN_STORE[run_id] = record
    return dict(record)


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
