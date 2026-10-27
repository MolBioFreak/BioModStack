"""Host-side MinKNOW discovery helpers for the BMS host agent.

This module runs in the host-agent process, not inside the BMS API container.
It must never fabricate devices: every returned position comes from MinKNOW's
``flow_cell_positions()`` call, and unavailable states return an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from .ont_start_receipts import claim_start, finish_start

DEFAULT_MINKNOW_HOST = "localhost"
DEFAULT_MINKNOW_MANAGER_PORT = 9502
MAX_DISCOVERED_OUTPUT_FILES = 100000

MINKNOW_STATUS_CONFIGURED = "configured"
MINKNOW_STATUS_CLIENT_MISSING = "client_missing"
MINKNOW_STATUS_UNREACHABLE = "unreachable"
MINKNOW_STATUS_AUTH_ERROR = "auth_error"


@dataclass(frozen=True)
class MinknowHostConfig:
    host: str = DEFAULT_MINKNOW_HOST
    port: int | None = DEFAULT_MINKNOW_MANAGER_PORT
    api_token: str | None = None
    client_cert_chain: bytes | None = None
    client_key: bytes | None = None

    @classmethod
    def from_env(cls) -> "MinknowHostConfig":
        port_text = os.getenv("BMS_ONT_MINKNOW_PORT", str(DEFAULT_MINKNOW_MANAGER_PORT)).strip()
        try:
            port: int | None = int(port_text) if port_text else None
        except ValueError:
            port = DEFAULT_MINKNOW_MANAGER_PORT
        return cls(
            host=os.getenv("BMS_ONT_MINKNOW_HOST", DEFAULT_MINKNOW_HOST).strip() or DEFAULT_MINKNOW_HOST,
            port=port,
            api_token=os.getenv("BMS_ONT_MINKNOW_API_TOKEN") or None,
        )


def _safe_get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    try:
        return getattr(obj, name)
    except (AttributeError, TypeError):
        return default


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None




def _enum_name(obj: Any, field_name: str, value: Any) -> str | None:
    """Best-effort protobuf enum label for MinKNOW MessageWrapper fields."""
    if value is None:
        return None
    descriptor = _safe_get(obj, "DESCRIPTOR")
    try:
        field = descriptor.fields_by_name[field_name]
        enum_value = field.enum_type.values_by_number.get(int(value))
        return enum_value.name if enum_value is not None else None
    except Exception:  # noqa: BLE001 - descriptor shapes vary by protobuf runtime
        return None


def _message_firmware_versions(device_info: Any) -> list[dict[str, str]]:
    versions: list[dict[str, str]] = []
    for item in _safe_get(device_info, "firmware_version", []) or []:
        component = _string_or_none(_safe_get(item, "component"))
        version = _string_or_none(_safe_get(item, "version"))
        if component or version:
            versions.append({"component": component or "", "version": version or ""})
    return versions


def normalize_device_info(device_info: Any) -> dict[str, Any]:
    device_type = _safe_get(device_info, "device_type")
    return {
        "device_id": _string_or_none(_safe_get(device_info, "device_id")),
        "device_type": _enum_name(device_info, "device_type", device_type) or _string_or_none(device_type),
        "is_simulated": bool(_safe_get(device_info, "is_simulated", False)),
        "max_channel_count": _safe_get(device_info, "max_channel_count"),
        "max_wells_per_channel": _safe_get(device_info, "max_wells_per_channel"),
        "can_set_temperature": bool(_safe_get(device_info, "can_set_temperature", False)),
        "digitisation": _safe_get(device_info, "digitisation"),
        "firmware_version": _message_firmware_versions(device_info),
    }


def normalize_device_state(device_state: Any) -> dict[str, Any]:
    raw_state = _safe_get(device_state, "device_state")
    connector = _safe_get(device_state, "flow_cell_connector")
    return {
        "device_state": _enum_name(device_state, "device_state", raw_state) or _string_or_none(raw_state),
        "flow_cell_connector": _enum_name(device_state, "flow_cell_connector", connector) or _string_or_none(connector),
    }


def normalize_output_directories(output_dirs: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key in ("output", "reads", "log"):
        value = _string_or_none(_safe_get(output_dirs, key))
        if value:
            normalized[key] = value
    return normalized




def _message_repeated(obj: Any, name: str) -> list[Any]:
    values = _safe_get(obj, name, [])
    try:
        return list(values or [])
    except TypeError:
        return []


def normalize_protocol_run(run: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("run_id", "protocol_id", "identifier", "state", "phase", "sample_id", "experiment_group", "user_info"):
        value = _safe_get(run, key)
        if value is not None:
            result[key] = _enum_name(run, key, value) or _string_or_none(value) or value
    text = str(run)
    if "hardware" in text.lower() or "flowcell_check" in text.lower() or "platform_qc" in text.lower():
        result["hardware_check_like"] = True
    return result or {"raw": text[:1200]}


def normalize_protocol_runs(payload: Any) -> list[dict[str, Any]]:
    runs = _message_repeated(payload, "protocol_runs") or _message_repeated(payload, "runs")
    if runs:
        return [normalize_protocol_run(run) for run in runs]
    run_ids = _message_repeated(payload, "run_ids")
    return [{"run_id": _string_or_none(run_id) or str(run_id)} for run_id in run_ids]


def normalize_acquisition_run(run: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("run_id", "state", "status", "start_time", "end_time"):
        value = _safe_get(run, key)
        if value is not None:
            result[key] = _enum_name(run, key, value) or _string_or_none(value) or value
    return result or {"raw": str(run)[:1200]}


def normalize_acquisition_runs(payload: Any) -> list[dict[str, Any]]:
    runs = _message_repeated(payload, "acquisition_runs") or _message_repeated(payload, "runs")
    if runs:
        return [normalize_acquisition_run(run) for run in runs]
    run_ids = _message_repeated(payload, "run_ids")
    return [{"run_id": _string_or_none(run_id) or str(run_id)} for run_id in run_ids]

def normalize_current_protocol(protocol_run: Any) -> dict[str, Any] | None:
    if protocol_run is None:
        return None
    result: dict[str, Any] = {}
    for key in ("run_id", "protocol_id", "phase", "state", "sample_id", "experiment_group"):
        value = _safe_get(protocol_run, key)
        if value is not None:
            result[key] = _enum_name(protocol_run, key, value) or _string_or_none(value) or value
    protocol_text = str(result.get("protocol_id") or "").lower()
    raw_text = str(protocol_run).lower()
    if "hardware_check" in protocol_text or "hardware_validation" in protocol_text or "hardwarecheck" in raw_text:
        result["hardware_check_like"] = True
    return result or {"raw": str(protocol_run)}


def normalize_acquisition_status(status: Any) -> dict[str, Any]:
    raw = _safe_get(status, "status")
    return {"status": _enum_name(status, "status", raw) or _string_or_none(raw)}

def infer_ont_device_type(*, position_name: str | None, product_code: str | None, description: Any = None) -> str | None:
    haystack = " ".join(str(part).lower() for part in (position_name, product_code, description) if part is not None)
    if "mk1d" in haystack or "minion_mk1d" in haystack:
        return "mk1d"
    if "mk1b" in haystack or "minion_mk1b" in haystack or "minion" in haystack:
        return "mk1b"
    return None


def normalize_flow_cell_info(flow_cell_info: Any, *, sample_rate: Any = None) -> dict[str, Any]:
    return {
        "present": bool(_safe_get(flow_cell_info, "has_flow_cell", False)),
        "is_ctc": bool(_safe_get(flow_cell_info, "is_ctc", False)),
        "has_adapter": bool(_safe_get(flow_cell_info, "has_adapter", False)),
        "flow_cell_id": _string_or_none(_safe_get(flow_cell_info, "flow_cell_id")),
        "user_specified_flow_cell_id": _string_or_none(_safe_get(flow_cell_info, "user_specified_flow_cell_id")),
        "product_code": _string_or_none(_safe_get(flow_cell_info, "product_code")),
        "user_specified_product_code": _string_or_none(_safe_get(flow_cell_info, "user_specified_product_code")),
        "sample_rate": _safe_get(flow_cell_info, "sample_rate", sample_rate),
        "channel_count": _safe_get(flow_cell_info, "channel_count"),
        "wells_per_channel": _safe_get(flow_cell_info, "wells_per_channel"),
        "use_count": _safe_get(flow_cell_info, "use_count"),
        "use_count_limit": _safe_get(flow_cell_info, "use_count_limit"),
        "adapter_id": _string_or_none(_safe_get(flow_cell_info, "adapter_id")),
        "barcode_kit": _string_or_none(_safe_get(flow_cell_info, "barcode_kit")),
    }


def normalize_position(position: Any) -> dict[str, Any]:
    position_name = _string_or_none(_safe_get(position, "name"))
    state = _string_or_none(_safe_get(position, "state"))
    running = bool(_safe_get(position, "running", False))
    description = _safe_get(position, "description")
    rpc_ports = _safe_get(description, "rpc_ports")
    secure_port = _safe_get(rpc_ports, "secure")

    flow_cell: dict[str, Any] = {"present": False}
    device_info: dict[str, Any] = {}
    device_state: dict[str, Any] = {}
    output_directories: dict[str, str] = {}
    acquisition_status: dict[str, Any] = {}
    current_protocol: dict[str, Any] | None = None
    protocol_runs: list[dict[str, Any]] = []
    acquisition_runs: list[dict[str, Any]] = []
    connection_error = None
    try:
        connection = position.connect()
        sample_rate = _safe_get(connection.device.get_sample_rate(), "sample_rate")
        flow_cell = normalize_flow_cell_info(connection.device.get_flow_cell_info(), sample_rate=sample_rate)
        device_info = normalize_device_info(connection.device.get_device_info())
        device_state = normalize_device_state(connection.device.get_device_state())
        output_directories = normalize_output_directories(connection.instance.get_output_directories())
        acquisition_status = normalize_acquisition_status(connection.acquisition.current_status())
        try:
            current_protocol = normalize_current_protocol(connection.protocol.get_current_protocol_run())
        except Exception:  # noqa: BLE001 - no current protocol is a normal MinKNOW state
            current_protocol = None
        try:
            protocol_runs = normalize_protocol_runs(connection.protocol.list_protocol_runs())
        except Exception:  # noqa: BLE001 - history availability varies by MinKNOW version/state
            protocol_runs = []
        try:
            acquisition_runs = normalize_acquisition_runs(connection.acquisition.list_acquisition_runs())
        except Exception:  # noqa: BLE001 - history availability varies by MinKNOW version/state
            acquisition_runs = []
    except Exception as exc:  # noqa: BLE001 - MinKNOW/grpc exceptions vary by version
        connection_error = str(exc)

    # Manager.position.running means the position's MinKNOW service is up,
    # not that sequencing is active. Only positive READY + no current protocol
    # evidence makes an otherwise running position idle; unknown states block.
    acquisition_state = str(acquisition_status.get("status") or "").upper()
    if not connection_error and acquisition_state == "READY" and current_protocol is None:
        running = False
    state = str(acquisition_status.get("status") or state or "unknown")

    product_code = flow_cell.get("user_specified_product_code") or flow_cell.get("product_code")
    inferred_type = infer_ont_device_type(
        position_name=position_name,
        product_code=product_code,
        description=description,
    )
    if inferred_type is None and str(device_info.get("device_type") or "").upper().endswith("MK1D"):
        inferred_type = "mk1d"
    return {
        "position": position_name,
        "device_type": inferred_type,
        "state": state,
        "running": running,
        "protocol_state": _string_or_none(_safe_get(position, "protocol_state")),
        "available_for_run": bool((not running) and flow_cell.get("present") and not connection_error),
        "flow_cell": flow_cell,
        "device_info": device_info,
        "device_state": device_state,
        "output_directories": output_directories,
        "acquisition_status": acquisition_status,
        "current_protocol": current_protocol,
        "protocol_runs": protocol_runs,
        "acquisition_runs": acquisition_runs,
        "hardware_check_runs": ([current_protocol] if current_protocol and current_protocol.get("hardware_check_like") else []) + [run for run in protocol_runs if run.get("hardware_check_like")],
        "rpc_ports": {"secure": secure_port} if secure_port is not None else {},
        "connection_error": connection_error,
    }


def build_protocol_options_preflight(
    *,
    position: dict[str, Any],
    kit: str | None,
    basecalling_enabled: bool = True,
    output_directories: dict[str, Any] | None = None,
    protocol_id: str | None = None,
    basecalling_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    flow_cell = position.get("flow_cell") if isinstance(position, dict) else None
    if not (isinstance(flow_cell, dict) and flow_cell.get("present")):
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
        "device_type": position.get("device_type"),
        "kit": kit,
        "can_start": not blockers,
        "blockers": blockers,
        "protocol_id": protocol_id,
        "basecalling_enabled": bool(basecalling_enabled),
        "basecalling_options": basecalling_options or {},
        "output_directories": output_directories or {},
        "flow_cell": flow_cell or {"present": False},
        "fake_or_demo_devices": False,
    }


def _protocols_module():
    from minknow_api.tools import protocols  # type: ignore[import-not-found]

    return protocols


def _connect_position(position_name: str, *, config: MinknowHostConfig | None = None,
                      manager_factory: Callable[[MinknowHostConfig], Any] | None = None) -> tuple[Any, Any]:
    """Return (manager, position connection) for one named MinKNOW position."""
    config = config or MinknowHostConfig.from_env()
    manager = (manager_factory or build_manager)(config)
    for position in manager.flow_cell_positions():
        if str(_safe_get(position, "name") or "") == str(position_name):
            return manager, position.connect()
    raise LookupError(f"MinKNOW position {position_name} was not found")


def _product_code(connection: Any) -> str | None:
    info = connection.device.get_flow_cell_info()
    return (_string_or_none(_safe_get(info, "user_specified_product_code"))
            or _string_or_none(_safe_get(info, "product_code")))


def _sequencing_kits(connection: Any, product_code: str | None) -> list[str]:
    kits: set[str] = set()
    for protocol in _safe_get(connection.protocol.list_protocols(), "protocols", []) or []:
        tags = _safe_get(protocol, "tags", {}) or {}
        try:
            if tags["experiment type"].string_value != "sequencing":
                continue
            if product_code and tags["flow cell"].string_value != product_code:
                continue
            kit = tags["kit"].string_value
        except (KeyError, AttributeError, TypeError):
            continue
        if kit:
            kits.add(kit)
    return sorted(kits)


def protocol_options(
    position: str,
    *,
    kit: str | None = None,
    basecalling_enabled: bool = True,
    status: dict[str, Any] | None = None,
    manager_factory: Callable[[MinknowHostConfig], Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve the MinKNOW protocol and default basecall model for one kit."""
    status = status or discover_status()
    devices = status.get("live_devices") if isinstance(status, dict) else []
    for device in devices or []:
        if str(device.get("position") or "") != str(position):
            continue
        if str(device.get("device_type") or "").strip().lower() != "mk1d":
            return None
        output_dirs = device.get("output_directories") or ((status.get("minknow") or {}).get("output_directories") or {}) if isinstance(status, dict) else {}
        protocol_id: str | None = None
        basecalling_options: dict[str, Any] = {}
        available_kits: list[str] = []
        lookup_error: str | None = None
        if (device.get("flow_cell") or {}).get("present"):
            try:
                manager, connection = _connect_position(position, manager_factory=manager_factory)
                product = _product_code(connection)
                available_kits = _sequencing_kits(connection, product)
                if kit:
                    tools = _protocols_module()
                    found = tools.find_protocol(connection, product_code=product, kit=kit, experiment_type="sequencing")
                    # minknow_api.tools.protocols.find_protocol returns Optional[str].
                    protocol_id = _string_or_none(found) if isinstance(found, str) else None
                    if protocol_id and basecalling_enabled:
                        sample_rate = (device.get("flow_cell") or {}).get("sample_rate")
                        configs = manager.find_basecall_configurations(product, kit, sample_rate)
                        _config, simplex = tools.find_default_simplex_model(connection, kit, sample_rate, configs)
                        basecalling_options = {
                            "simplex_models": [simplex.name],
                            "min_qscore": _safe_get(simplex, "default_q_score_cutoff"),
                        }
            except Exception as exc:  # noqa: BLE001 - grpc/minknow failures vary by version
                lookup_error = str(exc)[:500]
        result = build_protocol_options_preflight(
            position=device,
            kit=kit,
            basecalling_enabled=basecalling_enabled,
            output_directories=output_dirs,
            protocol_id=protocol_id,
            basecalling_options=basecalling_options,
        )
        if kit and not protocol_id and "protocol_unavailable" not in result["blockers"]:
            result["blockers"].append("protocol_unavailable")
            result["can_start"] = False
        result["available_kits"] = available_kits
        if lookup_error:
            result["lookup_error"] = lookup_error
        return result
    return None


def start_protocol(position_name: str, payload: dict[str, Any], *,
                   manager_factory: Callable[[MinknowHostConfig], Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Start one sequencing protocol as described by BMS's sealed run intent."""
    if payload.get("confirm_start") is not True:
        return 400, {"detail": "confirm_start=true is required to start a MinKNOW run"}
    protocol_id = _string_or_none(payload.get("protocol_id"))
    if not protocol_id:
        return 400, {"detail": "protocol_id is required"}
    run_hours = payload.get("run_hours")
    if isinstance(run_hours, bool) or not isinstance(run_hours, (int, float)) or not 0 < run_hours <= 96:
        return 400, {"detail": "run_hours must be greater than 0 and at most 96"}
    try:
        previous = claim_start(position_name, payload)
    except ValueError as exc:
        return 400, {"detail": str(exc)}
    if previous is not None:
        return previous
    request_id = payload["start_request_id"]

    def rejected(code: int, detail: str):
        return finish_start(position_name, request_id, code, {"detail": detail, "start_outcome": "rejected"})

    try:
        _manager, connection = _connect_position(position_name, manager_factory=manager_factory)
    except LookupError as exc:
        return rejected(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        return rejected(503, f"MinKNOW is unreachable: {exc}"[:500])
    if not bool(_safe_get(connection.device.get_flow_cell_info(), "has_flow_cell", False)):
        return rejected(409, "No flow cell is inserted in this position")
    try:
        current = connection.protocol.get_current_protocol_run()
    except Exception:  # noqa: BLE001 - MinKNOW reports "no current protocol" as an error
        current = None
    if _string_or_none(_safe_get(current, "run_id")):
        return rejected(409, "A protocol is already running on this position")
    tools = _protocols_module()
    basecalling = None
    if payload.get("basecalling_enabled") is True:
        simplex_model = _string_or_none(payload.get("simplex_model"))
        if not simplex_model:
            return rejected(400, "Basecalling needs a simplex model")
        basecalling = tools.BasecallingArgs(
            simplex_model=simplex_model, modified_models=None, stereo_model=None,
            barcoding=None, alignment=None, min_qscore=payload.get("min_qscore"),
        )
    output = tools.OutputArgs(reads_per_file=None, batch_duration=None)
    try:
        run_id = tools.start_protocol(
            connection,
            identifier=protocol_id,
            sample_id=_string_or_none(payload.get("sample_id")) or "",
            experiment_group=_string_or_none(payload.get("experiment_group")) or "",
            barcode_info=None,
            basecalling=basecalling,
            pod5_arguments=output,
            fastq_arguments=output if basecalling else None,
            experiment_duration=float(run_hours),
        )
    except Exception as exc:  # noqa: BLE001
        return finish_start(position_name, request_id, 502, {
            "detail": f"MinKNOW start outcome is unknown: {exc}"[:500], "start_outcome": "unknown",
        })
    if not isinstance(run_id, str) or not run_id.strip():
        return finish_start(position_name, request_id, 502, {
            "detail": "MinKNOW returned no run ID; check MinKNOW before starting another run.", "start_outcome": "unknown",
        })
    return finish_start(position_name, request_id, 201, {
        "minknow_run_id": run_id, "start_outcome": "accepted", "fake_or_demo_devices": False,
    })


def build_manager(config: MinknowHostConfig):
    from minknow_api.manager import Manager  # type: ignore[import-not-found]

    return Manager(
        host=config.host,
        port=config.port,
        developer_api_token=config.api_token,
        client_certificate_chain=config.client_cert_chain,
        client_private_key=config.client_key,
    )




def begin_hardware_check(position_name: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    del payload
    return 501, {
        "detail": "Run hardware checks from MinKNOW; BMS shows the result the next time it reads the position.",
        "position": position_name,
        "fake_or_demo_devices": False,
    }

def refresh_position(position_name: str) -> tuple[int, dict[str, Any]]:
    status = discover_status()
    if status.get("implementation_status") != MINKNOW_STATUS_CONFIGURED:
        return 503, {
            "detail": "MinKNOW is not configured/reachable from the BMS host-agent",
            "implementation_status": status.get("implementation_status"),
            "fake_or_demo_devices": False,
        }
    for device in status.get("live_devices") or []:
        if str(device.get("position") or "") == str(position_name):
            return 200, {
                "action": "refresh",
                "detail": "Reopened the MinKNOW position connection and reread device/flow-cell state; this does not power-cycle the instrument.",
                "position": device,
                "fake_or_demo_devices": False,
            }
    return 404, {"detail": f"unknown ONT position: {position_name}", "fake_or_demo_devices": False}


def restart_position(position_name: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    del payload
    return 501, {
        "detail": "BMS can't power-cycle the Mk1D. Use Refresh or Reconnect Mk1D instead.",
        "position": position_name,
        "fake_or_demo_devices": False,
    }


def stop_protocol(minknow_run_id: str, payload: dict[str, Any], *,
                  manager_factory: Callable[[MinknowHostConfig], Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Stop one MinKNOW protocol run, keeping the data acquired so far."""
    if payload.get("confirm_stop") is not True:
        return 400, {"detail": "confirm_stop=true is required before stopping a MinKNOW run"}
    requested = _string_or_none(minknow_run_id)
    if not requested:
        return 400, {"detail": "MinKNOW run id is required"}
    config = MinknowHostConfig.from_env()
    try:
        manager = (manager_factory or build_manager)(config)
        positions = list(manager.flow_cell_positions())
    except Exception as exc:  # noqa: BLE001
        return 503, {"detail": f"MinKNOW is unreachable: {exc}"[:500]}
    for position in positions:
        try:
            connection = position.connect()
            current = connection.protocol.get_current_protocol_run()
        except Exception:  # noqa: BLE001 - idle positions report no current run
            continue
        if _string_or_none(_safe_get(current, "run_id")) != requested:
            continue
        try:
            connection.protocol.stop_protocol(protocol_run_id=requested)
        except Exception as exc:  # noqa: BLE001
            return 502, {"detail": f"MinKNOW refused the stop: {exc}"[:500]}
        return 202, {"minknow_run_id": requested, "accepted": True, "fake_or_demo_devices": False}
    return 409, {"detail": "That run is not currently running in MinKNOW", "minknow_run_id": requested}


_MINKNOW_RUN_FOLDER_RE = re.compile(r"^\d{8}_\d{4}_[^/]+_([0-9A-Za-z]{8})$")


def _run_folder_matches(part: str, minknow_run_id: str) -> bool:
    """Match MinKNOW's run folder to the bound protocol run identity.

    MinKNOW names run folders ``{date}_{time}_{device}_{flow_cell}_{short_id}``
    where ``short_id`` is the first eight characters of the protocol run ID;
    the full ID is never a path component under the default layout.
    """
    if part == minknow_run_id:
        return True
    match = _MINKNOW_RUN_FOLDER_RE.fullmatch(part)
    return bool(match) and len(minknow_run_id) >= 8 and match.group(1).lower() == minknow_run_id[:8].lower()


def _discover_run_outputs(output_directories: dict[str, Any], minknow_run_id: str) -> dict[str, list[str]]:
    """Discover bounded regular outputs below MinKNOW-issued roots without links."""
    result: dict[str, list[str]] = {"fastq": [], "pod5": [], "bam": []}
    roots: list[Path] = []
    for key in ("output", "reads"):
        raw = str(output_directories.get(key) or "").strip()
        if raw and os.path.isabs(raw):
            root = Path(os.path.abspath(raw))
            if root not in roots:
                roots.append(root)
    discovered = 0
    for root in roots:
        try:
            for directory, directory_names, file_names, directory_fd in os.fwalk(root, topdown=True, follow_symlinks=False):
                directory_names[:] = sorted(directory_names)
                for name in sorted(file_names):
                    if discovered >= MAX_DISCOVERED_OUTPUT_FILES:
                        return result
                    lowered = name.lower()
                    kind = (
                        "pod5" if lowered.endswith(".pod5") else
                        "bam" if lowered.endswith(".bam") else
                        "fastq" if lowered.endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz")) else
                        None
                    )
                    if kind is None:
                        continue
                    try:
                        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
                        try:
                            if not os.path.isfile(f"/proc/self/fd/{file_fd}"):
                                continue
                        finally:
                            os.close(file_fd)
                    except OSError:
                        continue
                    candidate = str(Path(directory) / name)
                    relative_parts = Path(candidate).relative_to(root).parts[:-1]
                    run_like_parts = [
                        part for part in relative_parts
                        if len(part) >= 20 or _MINKNOW_RUN_FOLDER_RE.fullmatch(part)
                    ]
                    if run_like_parts and not any(
                        _run_folder_matches(part, minknow_run_id) for part in relative_parts
                    ):
                        continue
                    result[kind].append(candidate)
                    discovered += 1
        except OSError:
            continue
    return result


def discover_status(
    *,
    config: MinknowHostConfig | None = None,
    manager_factory: Callable[[MinknowHostConfig], Any] | None = None,
) -> dict[str, Any]:
    config = config or MinknowHostConfig.from_env()
    manager_factory = manager_factory or build_manager
    minknow = {"host": config.host, "manager_port": config.port}
    try:
        manager = manager_factory(config)
        positions: Iterable[Any] = manager.flow_cell_positions()
        live_devices = [device for device in (normalize_position(position) for position in positions) if device.get("device_type") == "mk1d"]
        first_output_dirs = next((device.get("output_directories") for device in live_devices if device.get("output_directories")), {})
        return {
            "implementation_status": MINKNOW_STATUS_CONFIGURED,
            "minknow": {**minknow, "output_directories": first_output_dirs},
            "live_devices": live_devices,
            "fake_or_demo_devices": False,
            "message": "MinKNOW API reachable from BMS host-agent; live_devices reflects manager.flow_cell_positions().",
        }
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("minknow_api"):
            return {
                "implementation_status": MINKNOW_STATUS_CLIENT_MISSING,
                "minknow": minknow,
                "live_devices": [],
                "fake_or_demo_devices": False,
                "message": "minknow_api is not installed in the BMS host-agent Python environment.",
            }
        raise
    except PermissionError as exc:
        return {
            "implementation_status": MINKNOW_STATUS_AUTH_ERROR,
            "minknow": minknow,
            "live_devices": [],
            "fake_or_demo_devices": False,
            "message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - MinKNOW/grpc connection failures vary by version
        return {
            "implementation_status": MINKNOW_STATUS_UNREACHABLE,
            "minknow": minknow,
            "live_devices": [],
            "fake_or_demo_devices": False,
            "message": str(exc),
        }


_PROTOCOL_STATE_MAP = {
    "protocol_running": "active",
    "protocol_waiting_for_temperature": "active",
    "protocol_waiting_for_acquisition": "active",
    "protocol_waiting_for_resource": "active",
    "protocol_completed": "completed",
    "protocol_finished_unable_to_send_telemetry": "completed",
    "protocol_stopped_by_user": "stopped",
}


def _protocol_state_to_status(raw: str) -> str:
    state = raw.strip().lower()
    if state in _PROTOCOL_STATE_MAP:
        return _PROTOCOL_STATE_MAP[state]
    if state.startswith("protocol_finished_with_"):
        return "failed"
    return _LEGACY_STATES.get(state, "unknown")


_LEGACY_STATES = {
    "active": "active",
    "running": "active",
    "protocol_state_running": "active",
    "acquisition_running": "active",
    "acquisition_state_running": "active",
    "complete": "completed",
    "completed": "completed",
    "finished": "completed",
    "protocol_finished": "completed",
    "protocol_state_finished": "completed",
    "protocol_state_completed": "completed",
    "acquisition_finished": "completed",
    "acquisition_completed": "completed",
    "acquisition_state_finished": "completed",
    "acquisition_state_completed": "completed",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "protocol_error": "failed",
    "protocol_state_error": "failed",
    "acquisition_error": "failed",
    "acquisition_state_error": "failed",
    "stop": "stopped",
    "stopped": "stopped",
    "cancelled": "stopped",
    "canceled": "stopped",
    "aborted": "stopped",
    "protocol_stopped": "stopped",
    "protocol_state_stopped": "stopped",
    "acquisition_stopped": "stopped",
    "acquisition_state_stopped": "stopped",
}


def _run_info(position_name: str, minknow_run_id: str) -> dict[str, Any] | None:
    """Read MinKNOW's own record of one protocol run, or None if this position lacks it."""
    try:
        _manager, connection = _connect_position(position_name)
        info = connection.protocol.get_run_info(run_id=minknow_run_id)
    except Exception:  # noqa: BLE001 - unknown run ids and offline positions both raise
        return None
    if _string_or_none(_safe_get(info, "run_id")) != minknow_run_id:
        return None
    raw_state = _safe_get(info, "state")
    acquisitions = [str(item) for item in (_safe_get(info, "acquisition_run_ids", []) or [])]
    return {
        "state": _enum_name(info, "state", raw_state) or _string_or_none(raw_state) or "",
        "output_path": _string_or_none(_safe_get(info, "output_path")),
        "acquisition_id": acquisitions[-1] if acquisitions else None,
    }


def observe_run(minknow_run_id: str) -> dict[str, Any]:
    """Return a bounded status snapshot and discovered outputs for one bound run."""
    requested = str(minknow_run_id or "").strip()
    empty = {"fastq": [], "pod5": [], "bam": []}
    status = discover_status()
    if not requested or status.get("implementation_status") != MINKNOW_STATUS_CONFIGURED:
        return {"status": "unknown", "minknow_run_id": requested, "output_files": empty, "fake_or_demo_devices": False}
    for device in status.get("live_devices") or []:
        position_name = _string_or_none(device.get("position"))
        info = _run_info(position_name, requested) if position_name else None
        if info is not None:
            output_path = info["output_path"]
            output_files = (
                _discover_run_outputs({"output": output_path}, requested)
                if output_path else _discover_run_outputs(device.get("output_directories") or {}, requested)
            )
            return {
                "status": _protocol_state_to_status(info["state"]),
                "minknow_run_id": requested,
                "acquisition_id": info["acquisition_id"],
                "output_files": output_files,
                "fake_or_demo_devices": False,
            }
        current = device.get("current_protocol") if isinstance(device.get("current_protocol"), dict) else {}
        candidates = [current, *(device.get("protocol_runs") or []), *(device.get("acquisition_runs") or [])]
        matching = next((run for run in candidates if isinstance(run, dict) and str(run.get("run_id") or "").strip() == requested), None)
        if matching is None:
            continue
        raw_state = str(matching.get("state") or matching.get("status") or "")
        return {
            "status": _protocol_state_to_status(raw_state),
            "minknow_run_id": requested,
            "acquisition_id": str(matching.get("acquisition_id") or "").strip() or None,
            "output_files": _discover_run_outputs(device.get("output_directories") or {}, requested),
            "fake_or_demo_devices": False,
        }
    return {"status": "unknown", "minknow_run_id": requested, "output_files": empty, "fake_or_demo_devices": False}
