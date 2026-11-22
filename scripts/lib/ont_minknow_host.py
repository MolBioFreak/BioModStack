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
import shutil
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


def _basecall_model_catalog(config: Any) -> list[dict[str, Any]]:
    """The current simplex models of one basecall configuration, each with its modification models."""
    catalog: list[dict[str, Any]] = []
    for simplex in _safe_get(config, "simplex_models", []) or []:
        name = _string_or_none(_safe_get(simplex, "name"))
        variant = (_string_or_none(_safe_get(simplex, "variant")) or "").casefold()
        if not name or not variant or _safe_get(simplex, "outdated", False) is True:
            continue
        if any(item["variant"] == variant for item in catalog):
            continue
        modified = [
            {"name": str(_safe_get(model, "name")), "variant": str(_safe_get(model, "variant")),
             "canonical_base": _string_or_none(_safe_get(model, "canonical_base")),
             "context": _string_or_none(_safe_get(model, "context"))}
            for model in _safe_get(simplex, "modified_models", []) or []
            if _string_or_none(_safe_get(model, "name")) and _string_or_none(_safe_get(model, "variant"))
            and _safe_get(model, "outdated", False) is not True
        ]
        catalog.append({"name": name, "variant": variant,
                        "min_qscore": _safe_get(simplex, "default_q_score_cutoff"), "modified_models": modified})
    return catalog


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
                    # SDK releases return either the identifier or ProtocolInfo;
                    # the installed SDK's return annotation does not match its object result.
                    protocol_id = _string_or_none(found if isinstance(found, str) else _safe_get(found, "identifier"))
                    if protocol_id and basecalling_enabled:
                        sample_rate = (device.get("flow_cell") or {}).get("sample_rate")
                        configs = manager.find_basecall_configurations(product, kit, sample_rate)
                        if not configs:
                            # An idle device reports the rate it last ran at; the protocol sets its own.
                            # Use the rate the kit's basecall configuration names when there is exactly one.
                            configs = manager.find_basecall_configurations(product, kit)
                            rates = {_safe_get(config, "sampling_rate") for config in configs}
                            if len(rates) == 1:
                                sample_rate = rates.pop()
                        _config, simplex = tools.find_default_simplex_model(connection, kit, sample_rate, configs)
                        basecalling_options = {
                            "simplex_models": [simplex.name],
                            "min_qscore": _safe_get(simplex, "default_q_score_cutoff"),
                        }
                        models = _basecall_model_catalog(_config)
                        if models:
                            basecalling_options["models"] = models
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
    modified_models: list[str] | None = None
    if payload.get("basecalling_enabled") is True:
        simplex_model = _string_or_none(payload.get("simplex_model"))
        if not simplex_model:
            return rejected(400, "Basecalling needs a simplex model")
        # A barcoding kit is demultiplexed by MinKNOW as the run is basecalled.
        kits = [str(kit) for kit in payload.get("barcoding_kits") or [] if str(kit).strip()]
        barcoding = tools.BarcodingArgs(kits=kits, trim_barcodes=False,
                                        barcodes_both_ends=payload.get("barcodes_both_ends") is True) if kits else None
        modified_models = [str(name) for name in payload.get("modified_models") or [] if str(name).strip()] or None
        basecalling = tools.BasecallingArgs(
            simplex_model=simplex_model, modified_models=modified_models, stereo_model=None,
            barcoding=barcoding, alignment=None, min_qscore=payload.get("min_qscore"),
        )
    read_until = None
    if payload.get("adaptive_sampling"):
        try:
            read_until = _adaptive_sampling_args(tools, request_id, payload["adaptive_sampling"])
        except (ValueError, OSError) as exc:
            return rejected(400, f"Adaptive sampling could not be set up: {exc}"[:500])
    output = tools.OutputArgs(reads_per_file=None, batch_duration=None)
    # Sample names per barcode, so MinKNOW labels its own output with them.
    barcode_info = [tools.BarcodeUserData(barcode_name=str(item["barcode"]), alias=str(item["alias"]))
                    for item in payload.get("barcode_samples") or []] or None
    try:
        run_id = tools.start_protocol(
            connection,
            identifier=protocol_id,
            sample_id=_string_or_none(payload.get("sample_id")) or "",
            experiment_group=_string_or_none(payload.get("experiment_group")) or "",
            barcode_info=barcode_info,
            basecalling=basecalling,
            pod5_arguments=output,
            fastq_arguments=output if basecalling else None,
            # Modification calls travel in BAM tags, so a run that calls them also writes BAM.
            **({"bam_arguments": output} if modified_models else {}),
            # MinKNOW runs adaptive sampling itself; BMS only hands it the mode and files.
            **({"read_until": read_until} if read_until is not None else {}),
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


_YIELD_FIELDS = (
    "read_count", "basecalled_pass_read_count", "basecalled_fail_read_count",
    "basecalled_pass_bases", "basecalled_fail_bases", "estimated_selected_bases",
)
_SEQUENCING_STATES = frozenset({"strand", "adapter"})
_OPEN_PORE_STATES = frozenset({"pore"})


def _yield_counts(summary: Any) -> dict[str, int]:
    return {field: int(_safe_get(summary, field, 0) or 0) for field in _YIELD_FIELDS}


def _stream_deadline(error: Exception) -> bool:
    code = getattr(error, "code", None)
    return getattr(code() if callable(code) else None, "name", "") in {"DEADLINE_EXCEEDED", "CANCELLED"}


def _first_stream_messages(stream: Any, *, limit: int = 64, errors: list[Exception] | None = None) -> list[Any]:
    """Messages MinKNOW sends before a live stream goes quiet; the deadline ending the wait is expected."""
    messages: list[Any] = []
    try:
        for message in stream:
            messages.append(message)
            if len(messages) >= limit:
                break
    except Exception as error:  # noqa: BLE001 - a live acquisition keeps the stream open until the timeout
        if errors is not None and not _stream_deadline(error):
            errors.append(error)
    finally:
        cancel = getattr(stream, "cancel", None)
        if callable(cancel):
            cancel()
    return messages


def _acquisition_barcodes(connection: Any, acquisition_id: str, statistics: Any) -> dict[str, dict[str, Any]]:
    """Latest per-barcode output of one acquisition, keyed by barcode name."""
    stream = connection.statistics.stream_acquisition_output(
        acquisition_run_id=acquisition_id, split=statistics.AcquisitionOutputSplit(barcode_name=True), _timeout=3.0)
    barcodes: dict[str, dict[str, Any]] = {}
    errors: list[Exception] = []
    messages = _first_stream_messages(stream, errors=errors)
    if errors and not messages:
        raise errors[0]
    for message in messages:
        for group in _safe_get(message, "snapshots", []) or []:
            keys = list(_safe_get(group, "filtering", []) or [])
            name = _string_or_none(_safe_get(keys[0], "barcode_name")) if keys else None
            snapshots = list(_safe_get(group, "snapshots", []) or [])
            if not name or not snapshots:
                continue
            latest = max(snapshots, key=lambda item: int(_safe_get(item, "seconds", 0) or 0))
            seconds = int(_safe_get(latest, "seconds", 0) or 0)
            if name in barcodes and barcodes[name]["seconds"] > seconds:
                continue
            barcodes[name] = {"barcode": name, "alias": _string_or_none(_safe_get(keys[0], "barcode_alias")),
                              "seconds": seconds, **_yield_counts(_safe_get(latest, "yield_summary"))}
    return barcodes


def _acquisition_channels(connection: Any, acquisition_id: str, statistics: Any) -> dict[str, Any] | None:
    """Share of channel time sequencing, open and otherwise in the latest full minute of one acquisition."""
    stream = connection.statistics.stream_duty_time(
        acquisition_run_id=acquisition_id, data_selection=statistics.DataSelection(step=60), _timeout=3.0)
    messages = _first_stream_messages(stream, limit=1)
    if not messages:
        return None
    ranges = list(_safe_get(messages[0], "bucket_ranges", []) or [])
    states = {str(name): list(_safe_get(data, "state_times", []) or [])
              for name, data in dict(_safe_get(messages[0], "channel_states", {}) or {}).items()}
    # The newest bucket is still filling, so the one before it is the latest full minute.
    index = len(ranges) - 2 if len(ranges) > 1 else len(ranges) - 1
    if index < 0:
        return None
    times = {name: int(values[index]) for name, values in states.items() if len(values) > index}
    total = sum(times.values())
    if total <= 0:
        return None

    def share(names: frozenset[str]) -> float:
        return round(sum(times.get(name, 0) for name in names) / total, 4)

    return {
        "minute_start_seconds": int(_safe_get(ranges[index], "start", 0) or 0),
        "sequencing_fraction": share(_SEQUENCING_STATES),
        "open_pore_fraction": share(_OPEN_PORE_STATES),
        "other_fraction": round(1 - share(_SEQUENCING_STATES | _OPEN_PORE_STATES), 4),
    }


_RECOVERING_STATES = frozenset({"unavailable", "unblocking", "active_feedback"})
_ACTIVITY_GROUPS = ("sequencing", "pore", "recovering", "inactive", "unclassified")
_CHART_POINTS = 240


def _activity_group(name: str) -> str:
    if name in _SEQUENCING_STATES:
        return "sequencing"
    if name in _OPEN_PORE_STATES:
        return "pore"
    if name in _RECOVERING_STATES:
        return "recovering"
    if name.startswith(("unclassified", "pending")):
        return "unclassified"
    return "inactive"


def _chart_step(latest: dict[str, Any]) -> int:
    """Seconds per graph point: one minute, widened so a long run stays near 120 points."""
    import time  # noqa: PLC0415
    started = int(latest.get("started_at") or 0)
    ended = int(latest.get("ended_at") or 0) or int(time.time())
    minutes = max(1, (ended - started) // 60) if started else 1
    return 60 * max(1, -(-minutes // 120))


def _pore_activity(connection: Any, acquisition_id: str, statistics: Any, step: int) -> list[dict[str, Any]]:
    """Share of channel time per state group for each time bucket, as MinKNOW's pore activity plot."""
    stream = connection.statistics.stream_duty_time(
        acquisition_run_id=acquisition_id, data_selection=statistics.DataSelection(step=step), _timeout=3.0)
    messages = _first_stream_messages(stream, limit=1)
    if not messages:
        return []
    ranges = list(_safe_get(messages[0], "bucket_ranges", []) or [])
    states = {str(name): list(_safe_get(data, "state_times", []) or [])
              for name, data in dict(_safe_get(messages[0], "channel_states", {}) or {}).items()}
    rows: list[dict[str, Any]] = []
    for index, bucket in enumerate(ranges):
        groups = dict.fromkeys(_ACTIVITY_GROUPS, 0)
        for name, values in states.items():
            if len(values) > index:
                groups[_activity_group(name)] += int(values[index])
        total = sum(groups.values())
        if total > 0:
            rows.append({"start_seconds": int(_safe_get(bucket, "start", 0) or 0),
                         **{group: round(value / total, 4) for group, value in groups.items()}})
    return rows[-_CHART_POINTS:]


def _cumulative_output(connection: Any, acquisition_id: str, statistics: Any, step: int) -> list[dict[str, Any]]:
    """Reads and bases so far at each time point, for the cumulative output plot."""
    stream = connection.statistics.stream_acquisition_output(
        acquisition_run_id=acquisition_id, data_selection=statistics.DataSelection(step=step), _timeout=3.0)
    points: dict[int, dict[str, Any]] = {}
    for message in _first_stream_messages(stream):
        for group in _safe_get(message, "snapshots", []) or []:
            for snapshot in _safe_get(group, "snapshots", []) or []:
                seconds = int(_safe_get(snapshot, "seconds", 0) or 0)
                points[seconds] = {"seconds": seconds, **_yield_counts(_safe_get(snapshot, "yield_summary"))}
    return [points[key] for key in sorted(points)][-_CHART_POINTS:]


def _read_lengths(connection: Any, acquisition_id: str, statistics: Any) -> dict[str, Any] | None:
    """Estimated bases per read-length bin with the N50, trimmed of the far tail and at most 80 bars."""
    stream = connection.statistics.stream_read_length_histogram(
        acquisition_run_id=acquisition_id, read_length_type=statistics.EstimatedBases,
        bucket_value_type=statistics.ReadLengths, _timeout=3.0)
    messages = _first_stream_messages(stream, limit=1)
    data = list(_safe_get(messages[0], "histogram_data", []) or []) if messages else []
    if not data:
        return None
    ranges = list(_safe_get(messages[0], "bucket_ranges", []) or [])
    values = [int(value) for value in _safe_get(data[0], "bucket_values", []) or []][:len(ranges)]
    total = sum(values)
    if total <= 0:
        return None
    running, cut = 0, len(values)
    for index, value in enumerate(values):
        running += value
        if running >= 0.995 * total:
            cut = index + 1
            break
    size = max(1, -(-cut // 80))
    rejected = _rejected_lengths(connection, acquisition_id, statistics, ranges, len(values))
    buckets = []
    for start in range(0, cut, size):
        end = min(start + size, cut)
        bucket = {"start": int(_safe_get(ranges[start], "start", 0) or 0),
                  "end": int(_safe_get(ranges[end - 1], "end", 0) or 0), "value": sum(values[start:end])}
        if rejected is not None:
            bucket["rejected"] = sum(rejected[start:end])
        buckets.append(bucket)
    return {"n50": float(_safe_get(data[0], "n50", 0) or 0), "buckets": buckets}


def _acquisition_charts(connection: Any, latest: dict[str, Any], statistics: Any) -> dict[str, Any]:
    """Series for the run graphs. Each is read on its own, so one missing series leaves the others."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    acquisition_id, step = latest["acquisition_id"], _chart_step(latest)

    def attempt(read: Any, default: Any) -> Any:
        try:
            return read()
        except Exception:  # noqa: BLE001 - a series MinKNOW cannot give yet shows as empty
            return default

    with ThreadPoolExecutor(max_workers=3) as pool:
        activity = pool.submit(attempt, lambda: _pore_activity(connection, acquisition_id, statistics, step), [])
        cumulative = pool.submit(attempt, lambda: _cumulative_output(connection, acquisition_id, statistics, step), [])
        lengths = pool.submit(attempt, lambda: _read_lengths(connection, acquisition_id, statistics), None)
        return {"pore_activity": activity.result(), "cumulative": cumulative.result(), "read_lengths": lengths.result()}


_ADAPTIVE_MODES = frozenset({"enrich", "deplete", "barcode_balance"})
_ADAPTIVE_REJECTION_REASON = 7  # ReadEndReason.DataServiceUnblockMuxChange: ejected by adaptive sampling


def _adaptive_sampling_dir() -> Path:
    """Where MinKNOW, which cannot read a home directory, is given the reference and target files."""
    return Path(os.environ.get("BMS_ONT_ADAPTIVE_SAMPLING_DIR") or "/var/lib/minknow/data/bms_adaptive_sampling")


def _adaptive_sampling_args(tools: Any, request_id: str, adaptive: Any) -> Any:
    """MinKNOW's own adaptive sampling arguments, with the files copied to where MinKNOW can read them."""
    mode = str((adaptive or {}).get("mode") or "") if isinstance(adaptive, dict) else ""
    if mode not in _ADAPTIVE_MODES:
        raise ValueError("adaptive sampling mode must be enrich, deplete or barcode_balance")
    reference = bed = None
    if mode != "barcode_balance":
        source = _string_or_none(adaptive.get("reference_path"))
        if not source:
            raise ValueError("enrich and deplete need a reference FASTA")
        folder = _adaptive_sampling_dir() / request_id
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o755)
        suffix = Path(source).suffix.lower()
        reference = folder / f"reference{suffix if suffix in {'.fasta', '.fa', '.fna', '.mmi'} else '.fasta'}"
        shutil.copyfile(source, reference)
        reference.chmod(0o644)
        bed_source = _string_or_none(adaptive.get("bed_path"))
        if bed_source:
            bed = folder / "targets.bed"
            shutil.copyfile(bed_source, bed)
            bed.chmod(0o644)
    return tools.ReadUntilArgs(filter_type=mode, reference_files=[str(reference)] if reference else None,
                               bed_file=str(bed) if bed else None, first_channel=None, last_channel=None)


def _rejected_lengths(connection: Any, acquisition_id: str, statistics: Any, ranges: list[Any], count: int) -> list[int] | None:
    """Bases per read-length bin from strands adaptive sampling ejected, when MinKNOW gives them on the same bins."""
    try:
        stream = connection.statistics.stream_read_length_histogram(
            acquisition_run_id=acquisition_id, read_length_type=statistics.EstimatedBases,
            bucket_value_type=statistics.ReadLengths,
            split=statistics.ReadLengthHistogramSplit(read_end_reason=True), _timeout=3.0)
        messages = _first_stream_messages(stream, limit=1)
    except Exception:  # noqa: BLE001 - the plain histogram stays useful without the split
        return None
    if not messages:
        return None

    def edges(items: list[Any]) -> list[tuple[int, int]]:
        return [(int(_safe_get(item, "start", 0) or 0), int(_safe_get(item, "end", 0) or 0)) for item in items]

    if edges(list(_safe_get(messages[0], "bucket_ranges", []) or [])) != edges(ranges):
        return None
    for data in _safe_get(messages[0], "histogram_data", []) or []:
        keys = list(_safe_get(data, "filtering", []) or [])
        if keys and _safe_get(keys[0], "read_end_reason") == _ADAPTIVE_REJECTION_REASON:
            values = [int(value) for value in _safe_get(data, "bucket_values", []) or []][:count]
            return values + [0] * (count - len(values))
    return None


def _statistics_module():
    from minknow_api import statistics_pb2  # noqa: PLC0415 - only present in the MinKNOW runtime
    return statistics_pb2


def run_live_stats(minknow_run_id: str) -> dict[str, Any]:
    """Read-only live numbers for one run: reads, basecalled yield, per-barcode output and channel use."""
    requested = str(minknow_run_id or "").strip()
    result: dict[str, Any] = {"minknow_run_id": requested, "available": False, "fake_or_demo_devices": False}
    status = discover_status()
    if not requested or status.get("implementation_status") != MINKNOW_STATUS_CONFIGURED:
        return result
    statistics = _statistics_module()
    for device in status.get("live_devices") or []:
        position_name = _string_or_none(device.get("position"))
        if not position_name:
            continue
        try:
            _manager, connection = _connect_position(position_name)
            info = connection.protocol.get_run_info(run_id=requested)
        except Exception:  # noqa: BLE001 - unknown run ids and offline positions both raise
            continue
        if _string_or_none(_safe_get(info, "run_id")) != requested:
            continue
        acquisition_ids = [str(item) for item in (_safe_get(info, "acquisition_run_ids", []) or [])]
        totals = dict.fromkeys(_YIELD_FIELDS, 0)
        barcodes: dict[str, dict[str, Any]] = {}
        latest: dict[str, Any] | None = None
        barcodes_available = True
        for acquisition_id in acquisition_ids:
            try:
                acquisition = connection.acquisition.get_acquisition_info(run_id=acquisition_id)
            except Exception:  # noqa: BLE001
                continue
            summary = _safe_get(acquisition, "yield_summary")
            counts = _yield_counts(summary)
            for field in _YIELD_FIELDS:
                totals[field] += counts[field]
            config = _safe_get(acquisition, "config_summary")
            started = int(_safe_get(_safe_get(acquisition, "start_time"), "seconds", 0) or 0)
            ended = int(_safe_get(_safe_get(acquisition, "end_time"), "seconds", 0) or 0)
            latest = {"acquisition_id": acquisition_id, "started_at": started or None, "ended_at": ended or None,
                      "basecalling_enabled": bool(_safe_get(config, "basecalling_enabled", False)),
                      "channel_count": int(_safe_get(config, "channel_count", 0) or 0) or None,
                      "fraction_basecalled": float(_safe_get(summary, "fraction_basecalled", 0) or 0),
                      "fraction_skipped": float(_safe_get(summary, "fraction_skipped", 0) or 0)}
            try:
                for name, item in _acquisition_barcodes(connection, acquisition_id, statistics).items():
                    seen = barcodes.setdefault(name, {"barcode": name, "alias": item["alias"], **dict.fromkeys(_YIELD_FIELDS, 0)})
                    seen["alias"] = seen["alias"] or item["alias"]
                    for field in _YIELD_FIELDS:
                        seen[field] += item[field]
            except Exception:  # noqa: BLE001 - totals stay useful when the per-barcode stream is unavailable
                barcodes_available = False
        channels = None
        if latest is not None:
            try:
                channels = _acquisition_channels(connection, latest["acquisition_id"], statistics)
            except Exception:  # noqa: BLE001
                channels = None
        charts = _acquisition_charts(connection, latest, statistics) if latest is not None else {}
        raw_state = _safe_get(info, "state")
        result.update({
            "available": latest is not None,
            "position": position_name,
            "status": _protocol_state_to_status(_enum_name(info, "state", raw_state) or _string_or_none(raw_state) or ""),
            "acquisition_count": len(acquisition_ids),
            "latest_acquisition": latest,
            "totals": totals,
            "barcodes": sorted(barcodes.values(), key=lambda item: item["barcode"]),
            "barcodes_available": barcodes_available,
            "channels": channels,
            "charts": charts,
        })
        return result
    return result


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
