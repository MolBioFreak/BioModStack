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
    """Normalize one MinKNOW position to the browser-safe discovery contract.

    The adapter may inspect flow-cell identifiers and connection metadata while
    talking to MinKNOW, but those values are host-only inputs to device
    classification. Discovery callers need only a position label, Mk1D
    eligibility, and whether a flow cell is present. In particular, never put
    flow-cell IDs, product/model identifiers, RPC ports, exception text, or
    client objects in this return value.
    """
    position_name = _string_or_none(_safe_get(position, "name"))
    state = _string_or_none(_safe_get(position, "state"))
    running = bool(_safe_get(position, "running", False))
    description = _safe_get(position, "description")

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
        "flow_cell": {"present": bool(flow_cell.get("present"))},
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
    """Return browser-safe MinKNOW position/device discovery status for BMS.

    This projection deliberately excludes fake devices, raw client objects, host
    addresses, flow-cell IDs, RPC information, and exception text. Server-side
    protocol preflight uses the host-agent control seam rather than this
    discovery response when it needs sensitive MinKNOW details.
    """
    config = config or MinknowConnectionConfig.from_env()
    manager_factory = manager_factory or build_manager
    try:
        manager = manager_factory(config)
        positions: Iterable[Any] = manager.flow_cell_positions()
        devices = [normalize_position(position) for position in positions]
        return {
            "implementation_status": MINKNOW_STATUS_CONFIGURED,
            "live_devices": devices,
            "fake_or_demo_devices": False,
            "message": "MinKNOW API reachable; live_devices reflects manager.flow_cell_positions().",
        }
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("minknow_api"):
            return {
                "implementation_status": MINKNOW_STATUS_CLIENT_MISSING,
                "live_devices": [],
                "fake_or_demo_devices": False,
                "message": "minknow_api is not installed in this runtime.",
            }
        raise
    except PermissionError:
        return {
            "implementation_status": MINKNOW_STATUS_AUTH_ERROR,
            "live_devices": [],
            "fake_or_demo_devices": False,
            "message": "MinKNOW authentication is unavailable.",
        }
    except Exception:  # noqa: BLE001 - manager connection errors vary by grpc/minknow_api version
        return {
            "implementation_status": MINKNOW_STATUS_UNREACHABLE,
            "live_devices": [],
            "fake_or_demo_devices": False,
            "message": "MinKNOW discovery is unavailable.",
        }
