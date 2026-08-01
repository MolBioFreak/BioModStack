from __future__ import annotations

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
RECONNECT_ALLOWED_IDENTITY = "operator@example.com"
RECONNECT_PROXY_SECRET = "test-proxy-secret"


@pytest.fixture(autouse=True)
def _configure_mk1d_reconnect_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_MK1D_RECONNECT_TRUSTED_PROXY_HOSTS", "testclient")
    monkeypatch.setenv("BMS_MK1D_RECONNECT_ALLOWED_TAILSCALE_USERS", RECONNECT_ALLOWED_IDENTITY)
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", RECONNECT_PROXY_SECRET)


def reconnect_client(app: FastAPI, *, client: tuple[str, int] = ("testclient", 50000), identity: str = RECONNECT_ALLOWED_IDENTITY) -> TestClient:
    return TestClient(
        app,
        client=client,
        headers={
            "Tailscale-User-Login": identity,
            "X-BMS-CM-Proxy-Secret": RECONNECT_PROXY_SECRET,
        },
    )


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
        "configure_run",
        "start_run",
        "stop_run",
        "monitor_run",
        "handoff_outputs_to_analysis",
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
    assert payload["live_devices"] == [{"position": "X1", "device_type": "mk1d", "available_for_run": True}]
    assert payload["fake_or_demo_devices"] is False


def test_manual_mk1d_reconnect_returns_helper_receipt_and_only_claims_observed_status(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = reconnect_client(app)
    monkeypatch.setattr(
        ont_device_control,
        "request_mk1d_reconnect",
        lambda: {
            "schema": "bms.mk1d-reconnect-receipt.v1",
            "receipt_id": "mk1d-reconnect-test",
            "status": "completed",
            "minknow": "already_active",
            "host_agent_recreate": "requested",
            "host_agent_health": "verified",
        },
    )
    monkeypatch.setattr(
        ont_device_control,
        "get_device_control_status",
        lambda: {
            "implementation_status": "host_agent_unavailable",
            "live_devices": [],
            "fake_or_demo_devices": False,
        },
    )

    response = client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 202
    payload = response.json()
    assert payload["receipt"]["receipt_id"] == "mk1d-reconnect-test"
    assert payload["receipt"]["host_agent_recreate"] == "requested"
    assert payload["receipt"]["host_agent_health"] == "verified"
    assert payload["post_action_device_status"]["implementation_status"] == "host_agent_unavailable"
    assert payload["device_status_observed"] is False
    assert payload["connected"] is False


def test_manual_mk1d_reconnect_fails_closed_when_privileged_helper_is_not_installed(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = reconnect_client(app)

    class HelperUnavailable(RuntimeError):
        pass

    monkeypatch.setattr(ont_device_control, "ReconnectHelperUnavailable", HelperUnavailable)
    monkeypatch.setattr(
        ont_device_control,
        "request_mk1d_reconnect",
        lambda: (_ for _ in ()).throw(HelperUnavailable("internal path must not be disclosed")),
    )

    response = client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 503
    assert response.json() == {"detail": "Reconnect helper unavailable/not installed"}


def test_reconnect_socket_client_refuses_a_missing_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_MK1D_RECONNECT_SOCKET", str(tmp_path / "missing.sock"))

    with pytest.raises(ReconnectHelperUnavailable, match="unavailable/not installed"):
        request_mk1d_reconnect()


def test_reconnect_socket_client_accepts_only_the_fixed_public_receipt(monkeypatch, tmp_path: Path) -> None:
    socket_path = tmp_path / "mk1d-reconnect.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    received: list[bytes] = []

    def serve_once() -> None:
        connection, _ = listener.accept()
        with connection:
            received.append(connection.recv(128))
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

    assert not worker.is_alive()
    assert received == [b"RECONNECT\n"]
    assert receipt == {
        "schema": "bms.mk1d-reconnect-receipt.v1",
        "receipt_id": "mk1d-reconnect-test",
        "status": "completed",
        "minknow": "already_active",
        "host_agent_recreate": "requested",
        "host_agent_health": "verified",
    }



def test_concurrent_reconnect_socket_requests_expose_the_helper_busy_receipt(monkeypatch, tmp_path: Path) -> None:
    socket_path = tmp_path / "mk1d-reconnect.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(2)
    responses = (
        b'{"schema":"bms.mk1d-reconnect-receipt.v1","receipt_id":"mk1d-reconnect-test",'
        b'"status":"completed","minknow":"already_active",'
        b'"host_agent_recreate":"requested","host_agent_health":"verified"}',
        b'{"schema":"bms.mk1d-reconnect-receipt.v1","receipt_id":"mk1d-reconnect-busy",'
        b'"status":"busy","minknow":"not_attempted",'
        b'"host_agent_recreate":"not_attempted","host_agent_health":"not_checked"}',
    )

    def serve_two() -> None:
        for response in responses:
            connection, _ = listener.accept()
            with connection:
                connection.recv(128)
                connection.sendall(response)

    worker = threading.Thread(target=serve_two)
    worker.start()
    monkeypatch.setenv("BMS_MK1D_RECONNECT_SOCKET", str(socket_path))
    receipts: list[dict[str, str]] = []
    clients = [threading.Thread(target=lambda: receipts.append(request_mk1d_reconnect())) for _ in range(2)]
    for client in clients:
        client.start()
    for client in clients:
        client.join(timeout=2)
    worker.join(timeout=2)
    listener.close()

    assert all(not client.is_alive() for client in clients)
    assert not worker.is_alive()
    assert {receipt["status"] for receipt in receipts} == {"completed", "busy"}
    busy = next(receipt for receipt in receipts if receipt["status"] == "busy")
    assert busy == {
        "schema": "bms.mk1d-reconnect-receipt.v1",
        "receipt_id": "mk1d-reconnect-busy",
        "status": "busy",
        "minknow": "not_attempted",
        "host_agent_recreate": "not_attempted",
        "host_agent_health": "not_checked",
    }


def test_manual_mk1d_reconnect_requires_strict_confirmation_and_trusted_tailscale_operator(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    allowed_client = reconnect_client(app)
    direct_loopback_client = reconnect_client(app, client=("127.0.0.1", 50000))
    untrusted_proxy_client = reconnect_client(app, client=("100.64.0.7", 50000))
    denied_client = reconnect_client(app, identity="intruder@example.com")
    monkeypatch.setattr(ont_device_control, "reconnect_mk1d", lambda: {"connected": False})

    assert allowed_client.post("/api/ont/devices/reconnect").status_code == 422
    assert allowed_client.post("/api/ont/devices/reconnect", json={"confirm_reconnect": False}).status_code == 422
    assert allowed_client.post("/api/ont/devices/reconnect", json={"confirm_reconnect": True, "service": "anything"}).status_code == 422
    assert direct_loopback_client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 401
    assert untrusted_proxy_client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 401
    assert denied_client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 403
    assert allowed_client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION).status_code == 202


def test_manual_mk1d_reconnect_fails_closed_without_an_explicit_operator_policy(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = reconnect_client(app)
    monkeypatch.delenv("BMS_MK1D_RECONNECT_TRUSTED_PROXY_HOSTS")
    monkeypatch.delenv("BMS_MK1D_RECONNECT_ALLOWED_TAILSCALE_USERS")

    response = client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 503


def test_manual_mk1d_reconnect_rejects_wrong_trusted_proxy_secret(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = TestClient(
        app,
        client=("testclient", 50000),
        headers={
            "Tailscale-User-Login": RECONNECT_ALLOWED_IDENTITY,
            "X-BMS-CM-Proxy-Secret": "forged-secret",
        },
    )
    monkeypatch.setattr(ont_device_control, "reconnect_mk1d", lambda: {"connected": False})

    response = client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 401


def test_manual_mk1d_reconnect_requires_an_error_free_observed_mk1d(monkeypatch) -> None:
    monkeypatch.setattr(
        ont_device_control,
        "request_mk1d_reconnect",
        lambda: {
            "schema": "bms.mk1d-reconnect-receipt.v1",
            "receipt_id": "mk1d-reconnect-test",
            "status": "completed",
            "minknow": "already_active",
            "host_agent_recreate": "requested",
            "host_agent_health": "verified",
        },
    )
    for devices, expected in (
        ([], False),
        ([{"position": "X1", "device_type": "mk1d", "connection_error": "auth failed"}], False),
        ([{"position": "X1", "device_type": "mk1d", "connection_error": None}], True),
    ):
        monkeypatch.setattr(
            ont_device_control,
            "get_device_control_status",
            lambda devices=devices: {"implementation_status": "configured", "live_devices": devices},
        )
        assert ont_device_control.reconnect_mk1d()["connected"] is expected


def test_manual_mk1d_reconnect_preserves_fixed_busy_receipt(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_devices.router, prefix="/api/ont")
    client = reconnect_client(app)
    monkeypatch.setattr(
        ont_device_control,
        "request_mk1d_reconnect",
        lambda: {
            "schema": "bms.mk1d-reconnect-receipt.v1",
            "receipt_id": "mk1d-reconnect-busy",
            "status": "busy",
            "minknow": "not_attempted",
            "host_agent_recreate": "not_attempted",
            "host_agent_health": "not_checked",
        },
    )
    monkeypatch.setattr(ont_device_control, "get_device_control_status", lambda: {"implementation_status": "configured", "live_devices": []})

    response = client.post("/api/ont/devices/reconnect", json=RECONNECT_CONFIRMATION)

    assert response.status_code == 202
    assert response.json()["receipt"] == {
        "schema": "bms.mk1d-reconnect-receipt.v1",
        "receipt_id": "mk1d-reconnect-busy",
        "status": "busy",
        "minknow": "not_attempted",
        "host_agent_recreate": "not_attempted",
        "host_agent_health": "not_checked",
    }


def test_root_helper_artifacts_only_start_inactive_minknow_and_recreate_host_agent() -> None:
    helper = (REPO_ROOT / "config/mk1d-reconnect/bms-reconnect-mk1d.template").read_text(encoding="utf-8")
    socket_unit = (REPO_ROOT / "config/mk1d-reconnect/bms-reconnect-mk1d.socket.template").read_text(encoding="utf-8")
    service_unit = (REPO_ROOT / "config/mk1d-reconnect/bms-reconnect-mk1d@.service.template").read_text(encoding="utf-8")
    installer = (REPO_ROOT / "config/mk1d-reconnect/install-root-helper.sh").read_text(encoding="utf-8")

    assert "inactive|failed)" in helper
    assert "active)" in helper
    assert "systemctl start \"$MINKNOW_SERVICE\"" in helper
    assert "systemctl restart" not in helper
    assert "hardware-check" not in helper
    assert "start_protocol" not in helper
    assert "--no-deps --force-recreate bms-host-agent" in helper
    assert "--no-build" in helper
    assert "--env-file /dev/null" in helper
    assert "__BMS_RECOVERY_COMPOSE_FILE__" in helper
    assert "__BMS_COMPOSE_FILE__" not in helper
    assert "flock -n" in helper
    assert '"receipt_id":"mk1d-reconnect-busy"' in helper
    assert "host_agent_health" in helper
    assert "curl --fail --silent --show-error" in helper
    assert "bms-api" not in helper
    assert "/etc/biomodstack/mk1d-reconnect-compose.json" in installer
    assert "--format json" in installer
    assert "--env-file" in installer
    assert "os.replace" in installer
    assert "BMS_MK1D_RECOVERY_GID" in installer
    assert "Accept=yes" in socket_unit
    assert "SocketGroup=__BMS_RECOVERY_GROUP__" in socket_unit
    assert "User=root" in service_unit
    assert "ExecStart=/usr/local/sbin/bms-reconnect-mk1d" in service_unit
