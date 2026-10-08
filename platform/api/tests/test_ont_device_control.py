from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import ont_devices  # noqa: E402
from services import ont_device_control  # noqa: E402
from services.ont_device_control import (  # noqa: E402
    DEVICE_CONTROL_STATUS_NOT_CONFIGURED,
    ONT_DEVICE_CONTROL_CAPABILITIES,
    build_analysis_handoff,
    get_device_control_status,
)
from services.ont_ngs_contract import DEVICE_CONTROL_OWNER  # noqa: E402
from services.mk1d_reconnect import ReconnectHelperUnavailable, request_mk1d_reconnect  # noqa: E402


RECONNECT_CONFIRMATION = {"confirm_reconnect": True}
RECONNECT_PROXY_SECRET = "test-tailnet-proxy-secret"
RECONNECT_IDENTITY = "authorized@example.com"


@pytest.fixture(autouse=True)
def _configure_mk1d_reconnect_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_MK1D_RECONNECT_TRUSTED_PROXY_HOSTS", "127.0.0.1")
    monkeypatch.setenv("BMS_MK1D_RECONNECT_ALLOWED_TAILSCALE_USERS", RECONNECT_IDENTITY)
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", RECONNECT_PROXY_SECRET)


def reconnect_client(app: FastAPI, *, client: tuple[str, int] = ("127.0.0.1", 50000)) -> TestClient:
    return TestClient(
        app,
        client=client,
        headers={
            "Tailscale-User-Login": RECONNECT_IDENTITY,
            "X-BMS-CM-Proxy-Secret": RECONNECT_PROXY_SECRET,
        },
    )


def _completed_reconnect_receipt() -> dict[str, str]:
    return {
        "schema": "bms.mk1d-reconnect-receipt.v1",
        "receipt_id": "mk1d-reconnect-test",
        "status": "completed",
        "minknow": "already_active",
        "host_agent_recreate": "requested",
        "host_agent_health": "verified",
    }

def test_ont_device_control_contract_supports_live_mk1d_without_fake_devices() -> None:
    status = get_device_control_status()

    assert status["owner"] == DEVICE_CONTROL_OWNER
    assert status["implementation_status"] == DEVICE_CONTROL_STATUS_NOT_CONFIGURED
    assert status["live_devices"] == []
    assert {device["id"] for device in status["supported_device_types"]} == {"mk1d"}
    assert status["fake_or_demo_devices"] is False


def test_ont_device_control_capabilities_are_hardware_side_not_nextflow_side() -> None:
    assert ONT_DEVICE_CONTROL_CAPABILITIES["owner"] == "bms_service_api"
    assert ONT_DEVICE_CONTROL_CAPABILITIES["not_owned_by"] == "nextflow_analysis"
    assert ONT_DEVICE_CONTROL_CAPABILITIES["controls"] == [
        "discover_devices",
        "inspect_flowcell",
        "issue_opaque_run_intent",
        "monitor_run",
        "handoff_verified_outputs_to_analysis",
    ]


def test_analysis_handoff_requires_existing_run_outputs_not_live_device_handle(tmp_path: Path) -> None:
    run_dir = tmp_path / "ont_run_001"
    run_dir.mkdir()

    handoff = build_analysis_handoff(
        workflow_id="ont_methylation_analysis",
        run_output_dir=run_dir,
        primary_input_kind="pod5",
    )

    assert handoff["workflow_id"] == "ont_methylation_analysis"
    assert handoff["analysis_owner"] == "nextflow_analysis"
    assert handoff["device_control_owner"] == "bms_service_api"
    assert handoff["requires_live_device"] is False
    assert handoff["primary_input_kind"] == "pod5"
    assert handoff["run_output_dir"] == str(run_dir)


def test_analysis_handoff_rejects_fast5_and_workflow_incompatible_inputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "ont_run_legacy"
    run_dir.mkdir()

    assert "fast5" not in ONT_DEVICE_CONTROL_CAPABILITIES["analysis_handoff_inputs"]
    with pytest.raises(ValueError, match="does not accept ONT input kind"):
        build_analysis_handoff(
            workflow_id="ont_basecall_dna",
            run_output_dir=run_dir,
            primary_input_kind="fast5",
        )
    with pytest.raises(ValueError, match="does not accept ONT input kind"):
        build_analysis_handoff(
            workflow_id="ont_basecall_dna",
            run_output_dir=run_dir,
            primary_input_kind="bam",
        )


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


def test_ont_device_status_can_delegate_to_minknow_adapter_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("BMS_ONT_MINKNOW_ENABLED", "1")
    monkeypatch.setattr(
        ont_device_control,
        "discover_minknow_devices",
        lambda: {
            "implementation_status": "configured",
            "minknow": {"host": "localhost", "manager_port": 9502},
            "live_devices": [{"position": "X1", "device_type": "mk1d", "available_for_run": True}],
            "fake_or_demo_devices": False,
            "message": "fake adapter payload for unit test",
        },
    )

    payload = get_device_control_status()

    assert payload["owner"] == "bms_service_api"
    assert payload["analysis_owner"] == "nextflow_analysis"
    assert payload["implementation_status"] == "configured"
    assert payload["live_devices"] == [{
        "position": "X1",
        "device_type": "mk1d",
        "state": None,
        "running": False,
        "available_for_run": True,
        "flow_cell": {"present": False},
        "fake_or_demo_device": False,
    }]
    assert payload["fake_or_demo_devices"] is False


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


def test_manual_mk1d_reconnect_accepts_only_strict_confirmation_from_trusted_tailnet_proxy(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    trusted_proxy = reconnect_client(app)
    direct_api = TestClient(app, client=("127.0.0.1", 50000))
    non_loopback = reconnect_client(app, client=("100.64.0.7", 50000))
    monkeypatch.setattr(ont_device_control, "reconnect_mk1d", lambda: {"connected": False})

    assert trusted_proxy.post("/api/ont/devices/reconnect").status_code == 422
    assert trusted_proxy.post("/api/ont/devices/reconnect", json={"confirm_reconnect": False}).status_code == 422
    assert trusted_proxy.post("/api/ont/devices/reconnect", json={"confirm_reconnect": 1}).status_code == 422
    assert trusted_proxy.post("/api/ont/devices/reconnect", json={"confirm_reconnect": "true"}).status_code == 422
    assert trusted_proxy.post("/api/ont/devices/reconnect", json={"confirm_reconnect": True, "service": "anything"}).status_code == 422
    assert direct_api.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 401
    assert non_loopback.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 401
    assert trusted_proxy.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 202


def test_manual_mk1d_reconnect_denies_forged_tailnet_forwarding_headers(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    direct_api = TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={
            "Tailscale-User-Login": "forged@example.com",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Host": "forge.ts.net",
            "X-BMS-CM-Proxy-Secret": "forged-secret",
        },
    )
    monkeypatch.setattr(ont_device_control, "reconnect_mk1d", lambda: {"connected": False})

    assert direct_api.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 401


def test_manual_mk1d_reconnect_fails_closed_without_the_trusted_proxy_secret(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = reconnect_client(app)
    monkeypatch.delenv("BMS_CM_TRUSTED_PROXY_SECRET")

    assert client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 503


def test_manual_mk1d_reconnect_returns_helper_receipt_and_only_claims_observed_status(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    monkeypatch.setattr(ont_device_control, "request_mk1d_reconnect", _completed_reconnect_receipt)
    monkeypatch.setattr(
        ont_device_control,
        "get_device_control_status",
        lambda: {"implementation_status": "host_agent_unavailable", "live_devices": [], "fake_or_demo_devices": False},
    )

    response = reconnect_client(app).post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 202
    assert response.json()["receipt"] == _completed_reconnect_receipt()
    assert response.json()["connected"] is False


def test_manual_mk1d_reconnect_fails_closed_when_privileged_helper_is_not_installed(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    monkeypatch.setattr(
        ont_device_control,
        "request_mk1d_reconnect",
        lambda: (_ for _ in ()).throw(ReconnectHelperUnavailable("internal path must not be disclosed")),
    )

    response = reconnect_client(app).post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 503
    assert response.json() == {"detail": "Reconnect helper unavailable/not installed"}


def test_reconnect_socket_client_refuses_a_missing_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_MK1D_RECONNECT_SOCKET", str(tmp_path / "missing.sock"))

    with pytest.raises(ReconnectHelperUnavailable, match="unavailable/not installed"):
        request_mk1d_reconnect()


def test_reconnect_socket_client_uses_native_service_primary_group_permission_shape(monkeypatch, tmp_path: Path) -> None:
    socket_path = tmp_path / "mk1d-reconnect.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    os.chown(socket_path, os.geteuid(), os.getegid())
    os.chmod(socket_path, 0o660)
    listener.listen(1)

    def serve_once() -> None:
        connection, _ = listener.accept()
        with connection:
            assert connection.recv(128) == b"RECONNECT\n"
            connection.sendall(
                b'{"schema":"bms.mk1d-reconnect-receipt.v1","receipt_id":"mk1d-reconnect-test",'
                b'"status":"completed","minknow":"already_active",'
                b'"host_agent_recreate":"requested","host_agent_health":"verified"}'
            )

    worker = threading.Thread(target=serve_once)
    worker.start()
    monkeypatch.setenv("BMS_MK1D_RECONNECT_SOCKET", str(socket_path))
    try:
        receipt = request_mk1d_reconnect()
    finally:
        worker.join(timeout=2)
        listener.close()

    socket_stat = socket_path.stat()
    assert socket_stat.st_gid == os.getegid()
    assert socket_stat.st_mode & 0o777 == 0o660
    assert receipt == _completed_reconnect_receipt()


def test_manual_mk1d_reconnect_preserves_fixed_busy_receipt(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    busy = {
        "schema": "bms.mk1d-reconnect-receipt.v1",
        "receipt_id": "mk1d-reconnect-busy",
        "status": "busy",
        "minknow": "not_attempted",
        "host_agent_recreate": "not_attempted",
        "host_agent_health": "not_checked",
    }
    monkeypatch.setattr(ont_device_control, "request_mk1d_reconnect", lambda: busy)
    monkeypatch.setattr(ont_device_control, "get_device_control_status", lambda: {"implementation_status": "configured", "live_devices": []})

    response = reconnect_client(app).post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 202
    assert response.json()["receipt"] == busy
