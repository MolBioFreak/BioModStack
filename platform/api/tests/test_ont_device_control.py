from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path


from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import ont_devices  # noqa: E402
from services import ont_device_control  # noqa: E402
from services.ont_device_control import get_device_control_status
from services.mk1d_reconnect import request_mk1d_reconnect  # noqa: E402


def test_public_mk1d_device_requires_literal_host_booleans() -> None:
    projected = ont_device_control._public_mk1d_device(
        {
            "position": "X1",
            "device_type": "mk1d",
            "running": "false",
            "available_for_run": 1,
            "flow_cell": {"present": "true"},
        }
    )

    assert projected is not None
    assert projected["running"] is False
    assert projected["available_for_run"] is False
    assert projected["flow_cell"]["present"] is False


def test_ont_device_router_exposes_truthful_not_configured_status() -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = TestClient(app)

    response = client.get("/api/ont/devices/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["implementation_status"] == "not_configured"
    assert payload["live_devices"] == []
    assert payload["fake_or_demo_devices"] is False
    assert payload["owner"] == "bms_service_api"


def test_device_status_filters_non_mk1d_and_redacts_raw_minknow_details(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ONT_MINKNOW_ENABLED", "1")
    monkeypatch.setattr(
        ont_device_control,
        "discover_minknow_devices",
        lambda: {
            "implementation_status": "configured",
            "minknow": {"host": "PRIVATE-HOST", "output_directories": {"reads": "/private/output"}},
            "live_devices": [
                {
                    "position": "Mk1D safe label",
                    "device_type": "mk1d",
                    "available_for_run": True,
                    "flow_cell": {"present": True, "flow_cell_id": "PRIVATE-FLOWCELL", "product_code": "PRIVATE-PRODUCT"},
                    "output_directories": {"reads": "/private/output"},
                    "rpc_ports": {"secure": 9502},
                    "current_protocol": {"protocol_id": "PRIVATE-PROTOCOL"},
                    "protocol_runs": [{"raw": "PRIVATE-HISTORY"}],
                },
                {"position": "Mk1B must not escape", "device_type": "mk1b", "flow_cell": {"present": True}},
            ],
            "fake_or_demo_devices": False,
        },
    )

    payload = get_device_control_status()

    assert payload["live_devices"] == [{
        "position": "Mk1D safe label",
        "device_type": "mk1d",
        "state": None,
        "running": False,
        "available_for_run": True,
        "flow_cell": {"present": True},
        "fake_or_demo_device": False,
    }]
    rendered = str(payload)
    for secret in ("PRIVATE-HOST", "PRIVATE-FLOWCELL", "PRIVATE-PRODUCT", "/private/output", "PRIVATE-PROTOCOL", "PRIVATE-HISTORY", "Mk1B must not escape"):
        assert secret not in rendered


def test_manual_mk1d_reconnect_local_proxy_accepts_only_strict_confirmation(monkeypatch) -> None:
    from routers import ont_devices

    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    monkeypatch.setenv("BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET", "local-bms-web-secret")
    monkeypatch.setattr(ont_devices.ont_device_control, "reconnect_mk1d", lambda: {"connected": False})
    trusted_local = TestClient(app, client=("127.0.0.1", 50000), headers={"X-BMS-MK1D-Reconnect-Proxy-Secret": "local-bms-web-secret"})

    assert trusted_local.post("/api/ont/devices/reconnect").status_code == 422
    assert trusted_local.post("/api/ont/devices/reconnect", json={"confirm_reconnect": False}).status_code == 422
    assert trusted_local.post("/api/ont/devices/reconnect", json={"confirm_reconnect": 1}).status_code == 422
    assert trusted_local.post("/api/ont/devices/reconnect", json={"confirm_reconnect": "true"}).status_code == 422
    assert trusted_local.post("/api/ont/devices/reconnect", json={"confirm_reconnect": True, "command": "forbidden"}).status_code == 422
    assert trusted_local.post("/api/ont/devices/reconnect", json={"confirm_reconnect": True}).status_code == 202


def test_manual_mk1d_reconnect_local_proxy_denies_direct_and_forged_forwarding_headers(monkeypatch) -> None:
    from routers import ont_devices

    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    monkeypatch.setenv("BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET", "local-bms-web-secret")
    monkeypatch.setattr(ont_devices.ont_device_control, "reconnect_mk1d", lambda: {"connected": False})
    direct = TestClient(app, client=("127.0.0.1", 50000), headers={
        "Tailscale-User-Login": "forged@example.com",
        "X-Forwarded-For": "127.0.0.1",
        "X-Forwarded-Host": "forge.ts.net",
        "X-BMS-MK1D-Reconnect-Proxy-Secret": "forged",
    })
    remote = TestClient(app, client=("100.64.0.4", 50000), headers={"X-BMS-MK1D-Reconnect-Proxy-Secret": "local-bms-web-secret"})

    assert direct.post("/api/ont/devices/reconnect", json={"confirm_reconnect": True}).status_code == 401
    assert remote.post("/api/ont/devices/reconnect", json={"confirm_reconnect": True}).status_code == 401


def test_reconnect_socket_uses_native_primary_group_permission_shape(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "mk1d-reconnect.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chown(path, os.geteuid(), os.getegid())
    os.chmod(path, 0o660)
    listener.listen(1)

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            # Connection admission selects the only fixed recovery transaction;
            # request data is intentionally neither sent nor interpreted.
            connection.sendall(b'{"schema":"bms.mk1d-reconnect-receipt.v1","receipt_id":"test","status":"completed","minknow":"already_active","host_agent_recreate":"requested","host_agent_health":"verified"}')

    worker = threading.Thread(target=serve)
    worker.start()
    monkeypatch.setenv("BMS_MK1D_RECONNECT_SOCKET", str(path))
    try:
        receipt = request_mk1d_reconnect()
    finally:
        worker.join(timeout=2)
        listener.close()
    assert path.stat().st_gid == os.getegid()
    assert path.stat().st_mode & 0o777 == 0o660
    assert receipt["status"] == "completed"
