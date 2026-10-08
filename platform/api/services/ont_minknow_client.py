"""MinKNOW API adapter for BioModStack ONT instrument discovery.

This module is intentionally a thin normalization layer around ONT's
``minknow_api`` package.  MinKNOW remains the hardware driver/control plane;
BioModStack consumes normalized device/run state and hands resulting files to
Nextflow analysis workflows.
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
class MinknowConnectionConfig:
    host: str = DEFAULT_MINKNOW_HOST
    port: int | None = DEFAULT_MINKNOW_MANAGER_PORT
    api_token: str | None = None
    client_cert_chain: bytes | None = None
    client_key: bytes | None = None

    @classmethod
    def from_env(cls) -> "MinknowConnectionConfig":
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
    """Best-effort Mk1B/Mk1D classification from MinKNOW metadata.

    MinKNOW positions are often named generically (for example ``X1``), so this
    function only classifies when metadata explicitly contains an expected token.
    Unknown is safer than pretending a device type.
    """
    haystack = " ".join(
        str(part).lower()
        for part in (position_name, product_code, description)
        if part is not None
    )
    if "mk1d" in haystack or "minion_mk1d" in haystack:
        return "mk1d"
    if "mk1b" in haystack or "minion_mk1b" in haystack or "minion" in haystack:
        return "mk1b"
    return None


def normalize_flow_cell_info(flow_cell_info: Any) -> dict[str, Any]:
    has_flow_cell = bool(_safe_get(flow_cell_info, "has_flow_cell", False))
    return {
        "present": has_flow_cell,
        "flow_cell_id": _string_or_none(_safe_get(flow_cell_info, "flow_cell_id")),
        "user_specified_flow_cell_id": _string_or_none(_safe_get(flow_cell_info, "user_specified_flow_cell_id")),
        "product_code": _string_or_none(_safe_get(flow_cell_info, "product_code")),
        "user_specified_product_code": _string_or_none(_safe_get(flow_cell_info, "user_specified_product_code")),
        "sample_rate": _safe_get(flow_cell_info, "sample_rate"),
    }


def normalize_position(position: Any) -> dict[str, Any]:
    """Normalize one MinKNOW flow-cell position to a BMS-safe device record."""
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
    except Exception as exc:  # noqa: BLE001 - preserve device discovery without overclaiming flowcell state
        connection_error = str(exc)

    product_code = flow_cell.get("user_specified_product_code") or flow_cell.get("product_code")
    device_type = infer_ont_device_type(
        position_name=position_name,
        product_code=product_code,
        description=description,
    )
    return {
        "position": position_name,
        "device_type": device_type,
        "state": state,
        "running": running,
        "available_for_run": bool((not running) and flow_cell.get("present") and not connection_error),
        "flow_cell": flow_cell,
        "rpc_ports": {"secure": secure_port} if secure_port is not None else {},
        "connection_error": connection_error,
    }


def build_manager(config: MinknowConnectionConfig):
    """Construct ONT's MinKNOW Manager client lazily.

    Importing here keeps BioModStack usable on machines where ``minknow_api`` is
    not installed yet.
    """
    from minknow_api.manager import Manager  # type: ignore[import-not-found]

    return Manager(
        host=config.host,
        port=config.port,
        developer_api_token=config.api_token,
        client_certificate_chain=config.client_cert_chain,
        client_private_key=config.client_key,
    )


def discover_minknow_devices(
    *,
    config: MinknowConnectionConfig | None = None,
    manager_factory: Callable[[MinknowConnectionConfig], Any] | None = None,
) -> dict[str, Any]:
    """Return normalized MinKNOW position/device status for BMS.

    The returned shape is safe for the BMS API/frontend: no fake devices, no raw
    client objects, and explicit status when the MinKNOW client is missing or the
    manager cannot be reached.
    """
    config = config or MinknowConnectionConfig.from_env()
    manager_factory = manager_factory or build_manager
    try:
        manager = manager_factory(config)
        positions: Iterable[Any] = manager.flow_cell_positions()
        devices = [normalize_position(position) for position in positions]
        return {
            "implementation_status": MINKNOW_STATUS_CONFIGURED,
            "minknow": {
                "host": config.host,
                "manager_port": config.port,
            },
            "live_devices": devices,
            "fake_or_demo_devices": False,
            "message": "MinKNOW API reachable; live_devices reflects manager.flow_cell_positions().",
        }
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("minknow_api"):
            return {
                "implementation_status": MINKNOW_STATUS_CLIENT_MISSING,
                "minknow": {"host": config.host, "manager_port": config.port},
                "live_devices": [],
                "fake_or_demo_devices": False,
                "message": "minknow_api is not installed in this runtime.",
            }
        raise
    except PermissionError as exc:
        return {
            "implementation_status": MINKNOW_STATUS_AUTH_ERROR,
            "minknow": {"host": config.host, "manager_port": config.port},
            "live_devices": [],
            "fake_or_demo_devices": False,
            "message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - manager connection errors vary by grpc/minknow_api version
        return {
            "implementation_status": MINKNOW_STATUS_UNREACHABLE,
            "minknow": {"host": config.host, "manager_port": config.port},
            "live_devices": [],
            "fake_or_demo_devices": False,
            "message": str(exc),
        }
