from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from routers import bioxp
from services import bioxp_interlink


def build_client(host: str = "testclient") -> TestClient:
    app = FastAPI()
    app.include_router(bioxp.router, prefix="/api/bioxp")
    return TestClient(app, client=(host, 50000))


def reset_interlink(monkeypatch, tmp_path: Path) -> Path:
    profile_path = tmp_path / "bioxp_interlink_profile.json"
    monkeypatch.setattr(bioxp_interlink, "PROFILE_PATH", profile_path, raising=False)
    bioxp_interlink.reset_session()
    bioxp_interlink.save_profile({"robot_api_url": "http://robot:8123", "robot_ssh_host": "robot"})
    monkeypatch.setattr(bioxp, "_GLOBAL_LINKAGE_URL", "http://robot:8123", raising=False)
    bioxp_interlink.activate_session("http://robot:8123")
    return profile_path


def test_runtime_reset_requires_exact_operator_ack(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)

    with build_client() as client:
        missing = client.post("/api/bioxp/interlink/runtime-reset", json={"reason": "test"})
        wrong = client.post(
            "/api/bioxp/interlink/runtime-reset",
            json={"operator_ack": "RESET", "reason": "test"},
        )

    assert missing.status_code == 400
    assert wrong.status_code == 400
    assert "RESET BIOXP RUNTIME" in missing.json()["detail"]
    assert "RESET BIOXP RUNTIME" in wrong.json()["detail"]


def test_robot_reboot_requires_exact_operator_ack(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)

    with build_client() as client:
        missing = client.post("/api/bioxp/interlink/robot-reboot", json={"reason": "test"})
        wrong = client.post(
            "/api/bioxp/interlink/robot-reboot",
            json={"operator_ack": "REBOOT", "reason": "test"},
        )

    assert missing.status_code == 400
    assert wrong.status_code == 400
    assert "REBOOT ROBOT" in missing.json()["detail"]
    assert "REBOOT ROBOT" in wrong.json()["detail"]


def test_lifecycle_actions_reject_non_local_admin_clients(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)

    with build_client(host="203.0.113.9") as client:
        reset_response = client.post(
            "/api/bioxp/interlink/runtime-reset",
            json={"operator_ack": "RESET BIOXP RUNTIME", "reason": "test"},
        )
        reboot_response = client.post(
            "/api/bioxp/interlink/robot-reboot",
            json={"operator_ack": "REBOOT ROBOT", "reason": "test"},
        )
        logs_response = client.post("/api/bioxp/interlink/logs", json={"tail": 120})

    assert reset_response.status_code == 403
    assert reboot_response.status_code == 403
    assert logs_response.status_code == 403


def test_logs_report_unsupported_instead_of_502_when_ssh_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    monkeypatch.setattr(bioxp_interlink.shutil, "which", lambda _: None, raising=False)

    with build_client() as client:
        response = client.post("/api/bioxp/interlink/logs", json={"tail": 120})

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "logs"
    assert payload["supported"] is False
    assert payload["command_result"]["returncode"] == 127
    assert any(note == "No robot command was executed." for note in payload["notes"])


def test_runtime_lifecycle_reports_unsupported_without_deactivating_when_ssh_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    monkeypatch.setattr(bioxp_interlink.shutil, "which", lambda _: None, raising=False)

    with build_client() as client:
        response = client.post(
            "/api/bioxp/interlink/runtime-reset",
            json={"operator_ack": "RESET BIOXP RUNTIME", "reason": "test"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "runtime-reset"
    assert payload["supported"] is False
    assert payload["active"] is True
    assert bioxp._GLOBAL_LINKAGE_URL == "http://robot:8123"


def test_runtime_reset_is_scoped_to_robot_local_systemd_and_redacts_password(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    commands: list[list[str]] = []
    proxy_paths: list[str] = []

    def fake_run_command(command: list[str], *, input_text: str | None = None, timeout: float = 90.0) -> dict:
        commands.append(command)
        assert input_text == "super-secret\n"
        return {"returncode": 0, "stdout": "runtime restarted", "stderr": ""}

    async def fake_proxy_request(method: str, path: str, **_: object) -> dict:
        proxy_paths.append(path)
        return {"status": "ok", "hardware_connected": False}

    monkeypatch.setattr(bioxp_interlink, "run_command", fake_run_command, raising=False)
    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy_request)

    with build_client() as client:
        response = client.post(
            "/api/bioxp/interlink/runtime-reset",
            json={
                "operator_ack": "RESET BIOXP RUNTIME",
                "reason": "test scoped runtime restart",
                "sudo_password": "super-secret",
                "watch_until_ready": False,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    serialized = str(payload)
    assert payload["action"] == "runtime-reset"
    assert payload["operator_ack"] == "RESET BIOXP RUNTIME"
    assert payload["sudo_password"] == "[REDACTED]"
    assert "super-secret" not in serialized
    assert commands == [["ssh", "-o", "BatchMode=yes", "robot", "sudo", "-S", "systemctl", "restart", "bioxp-api.service"]]
    command_text = " ".join(commands[0])
    for forbidden in ("killall", "pkill", "uvicorn", "usbreset", "strict_startup", "homing", "motion/arm", "recover_motion"):
        assert forbidden not in command_text
    assert proxy_paths == []


def test_robot_reboot_is_scoped_and_leaves_link_inactive(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], *, input_text: str | None = None, timeout: float = 90.0) -> dict:
        commands.append(command)
        return {"returncode": 0, "stdout": "reboot requested", "stderr": ""}

    monkeypatch.setattr(bioxp_interlink, "run_command", fake_run_command, raising=False)

    with build_client() as client:
        response = client.post(
            "/api/bioxp/interlink/robot-reboot",
            json={"operator_ack": "REBOOT ROBOT", "reason": "test scoped reboot"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "robot-reboot"
    assert payload["active"] is False
    assert bioxp._GLOBAL_LINKAGE_URL is None
    assert commands == [["ssh", "-o", "BatchMode=yes", "robot", "sudo", "-n", "reboot"]]
    command_text = " ".join(commands[0])
    for forbidden in ("killall", "pkill", "uvicorn", "usbreset", "strict_startup", "homing", "motion/arm", "recover_motion"):
        assert forbidden not in command_text
