"""Host-side MinKNOW discovery helpers for the BMS host agent.

This module runs in the host-agent process, not inside the BMS API container.
It must never fabricate devices: every returned position comes from MinKNOW's
``flow_cell_positions()`` call, and unavailable states return an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable, Iterable

DEFAULT_MINKNOW_HOST = "localhost"
DEFAULT_MINKNOW_MANAGER_PORT = 9502

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
    return getattr(obj, name, default)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def infer_ont_device_type(*, position_name: str | None, product_code: str | None, description: Any = None) -> str | None:
    haystack = " ".join(str(part).lower() for part in (position_name, product_code, description) if part is not None)
    if "mk1d" in haystack or "minion_mk1d" in haystack:
        return "mk1d"
    if "mk1b" in haystack or "minion_mk1b" in haystack or "minion" in haystack:
        return "mk1b"
    return None


def normalize_flow_cell_info(flow_cell_info: Any) -> dict[str, Any]:
    return {
        "present": bool(_safe_get(flow_cell_info, "has_flow_cell", False)),
        "flow_cell_id": _string_or_none(_safe_get(flow_cell_info, "flow_cell_id")),
        "user_specified_flow_cell_id": _string_or_none(_safe_get(flow_cell_info, "user_specified_flow_cell_id")),
        "product_code": _string_or_none(_safe_get(flow_cell_info, "product_code")),
        "user_specified_product_code": _string_or_none(_safe_get(flow_cell_info, "user_specified_product_code")),
        "sample_rate": _safe_get(flow_cell_info, "sample_rate"),
    }


def normalize_position(position: Any) -> dict[str, Any]:
    position_name = _string_or_none(_safe_get(position, "name"))
    state = _string_or_none(_safe_get(position, "state"))
    running = bool(_safe_get(position, "running", False))
    description = _safe_get(position, "description")
    rpc_ports = _safe_get(description, "rpc_ports")
    secure_port = _safe_get(rpc_ports, "secure")

    flow_cell: dict[str, Any] = {"present": False}
    connection_error = None
    try:
        connection = position.connect()
        flow_cell = normalize_flow_cell_info(connection.device.get_flow_cell_info())
    except Exception as exc:  # noqa: BLE001 - MinKNOW/grpc exceptions vary by version
        connection_error = str(exc)

    product_code = flow_cell.get("user_specified_product_code") or flow_cell.get("product_code")
    return {
        "position": position_name,
        "device_type": infer_ont_device_type(
            position_name=position_name,
            product_code=product_code,
            description=description,
        ),
        "state": state,
        "running": running,
        "available_for_run": bool((not running) and flow_cell.get("present") and not connection_error),
        "flow_cell": flow_cell,
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


def protocol_options(
    position: str,
    *,
    kit: str | None = None,
    basecalling_enabled: bool = True,
    status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    status = status or discover_status()
    devices = status.get("live_devices") if isinstance(status, dict) else []
    for device in devices or []:
        if str(device.get("position") or "") == str(position):
            output_dirs = ((status.get("minknow") or {}).get("output_directories") or {}) if isinstance(status, dict) else {}
            return build_protocol_options_preflight(
                position=device,
                kit=kit,
                basecalling_enabled=basecalling_enabled,
                output_directories=output_dirs,
            )
    return None


def build_manager(config: MinknowHostConfig):
    from minknow_api.manager import Manager  # type: ignore[import-not-found]

    return Manager(
        host=config.host,
        port=config.port,
        developer_api_token=config.api_token,
        client_certificate_chain=config.client_cert_chain,
        client_private_key=config.client_key,
    )


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
        return {
            "implementation_status": MINKNOW_STATUS_CONFIGURED,
            "minknow": minknow,
            "live_devices": [normalize_position(position) for position in positions],
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
