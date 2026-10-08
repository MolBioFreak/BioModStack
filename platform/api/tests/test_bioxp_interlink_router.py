from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
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
    monkeypatch.setattr(bioxp, "_GLOBAL_LINKAGE_URL", None, raising=False)
    return profile_path


def test_saved_interlink_profile_is_inactive_and_state_read_does_not_probe(monkeypatch, tmp_path: Path) -> None:
    profile_path = reset_interlink(monkeypatch, tmp_path)
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "bms.bioxp_interlink_profile.v1",
                "robot_api_url": "http://robot:8123",
                "robot_ssh_host": "robot",
                "connection_mode": "direct_http",
                "display_name": "BioXP3200",
                "auto_connect_on_launch": False,
            }
        ),
        encoding="utf-8",
    )
    proxy_calls: list[tuple[str, str]] = []

    async def fail_proxy_request(method: str, path: str, **_: object) -> dict:
        proxy_calls.append((method, path))
        raise AssertionError("GET /interlink/state must not call the robot unless probe=true")

    monkeypatch.setattr(bioxp, "proxy_request", fail_proxy_request)

    with build_client() as client:
        response = client.get("/api/bioxp/interlink/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["component"] == "bioxp-interlink"
    assert payload["configured"] is True
    assert payload["active"] is False
    assert payload["reachable"] is None
    assert payload["hardware_connected"] is None
    assert payload["robot_api_url"] == "http://robot:8123"
    assert "inactive" in payload["runtime_note"].lower()
    assert proxy_calls == []
    assert bioxp._GLOBAL_LINKAGE_URL is None


def test_save_settings_persists_profile_without_activating_or_polling(monkeypatch, tmp_path: Path) -> None:
    profile_path = reset_interlink(monkeypatch, tmp_path)
    proxy_calls: list[tuple[str, str]] = []

    async def fail_proxy_request(method: str, path: str, **_: object) -> dict:
        proxy_calls.append((method, path))
        raise AssertionError("saving settings must not probe the robot")

    monkeypatch.setattr(bioxp, "proxy_request", fail_proxy_request)

    with build_client() as client:
        response = client.put(
            "/api/bioxp/interlink/settings",
            json={
                "robot_api_url": "robot:8123/",
                "robot_ssh_host": "robot",
                "connection_mode": "direct_http",
                "display_name": "BioXP3200",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["active"] is False
    assert payload["reachable"] is None
    assert payload["robot_api_url"] == "http://robot:8123"
    assert proxy_calls == []
    assert bioxp._GLOBAL_LINKAGE_URL is None

    persisted = json.loads(profile_path.read_text(encoding="utf-8"))
    assert persisted["robot_api_url"] == "http://robot:8123"
    assert persisted["auto_connect_on_launch"] is False
    assert "sudo_password" not in persisted


def test_connect_activates_current_session_and_runs_one_passive_status_probe(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    calls: list[tuple[str, str, float | None]] = []

    async def fake_proxy_request(method: str, path: str, timeout: float = 65.0, **_: object) -> dict:
        calls.append((method, path, timeout))
        assert method == "GET"
        assert path == "/status"
        return {
            "status": "ok",
            "hardware_connected": True,
            "maintenance_state": {
                "usb_owner": "robot-local",
                "motion_blocked": False,
                "recovery_required": False,
            },
        }

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy_request)

    with build_client() as client:
        save_response = client.put(
            "/api/bioxp/interlink/settings",
            json={"robot_api_url": "robot:8123", "robot_ssh_host": "robot"},
        )
        connect_response = client.post("/api/bioxp/interlink/connect")

    assert save_response.status_code == 200
    assert connect_response.status_code == 200
    payload = connect_response.json()
    assert payload["configured"] is True
    assert payload["active"] is True
    assert payload["reachable"] is True
    assert payload["hardware_connected"] is True
    assert payload["maintenance_state"]["motion_blocked"] is False
    assert calls == [("GET", "/status", 18.0)]
    assert bioxp._GLOBAL_LINKAGE_URL == "http://robot:8123"


def test_connect_uses_targeted_power_when_status_probe_is_conservative(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    calls: list[tuple[str, str, float | None]] = []

    async def fake_proxy_request(method: str, path: str, timeout: float = 65.0, **_: object) -> dict:
        calls.append((method, path, timeout))
        assert method == "GET"
        if path == "/status":
            return {
                "status": "degraded",
                "runtime_available": True,
                "hardware_connected": False,
                "startup_error": None,
                "status_error": None,
            }
        if path == "/motion/power/status":
            return {
                "hardware_connected": True,
                "motion_arm": {"armed": False, "reason": "startup"},
            }
        raise AssertionError(path)

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy_request)

    with build_client() as client:
        connect_response = client.post(
            "/api/bioxp/interlink/connect",
            json={"robot_api_url": "robot:8123", "robot_ssh_host": "robot"},
        )

    assert connect_response.status_code == 200
    payload = connect_response.json()
    assert payload["active"] is True
    assert payload["reachable"] is True
    assert payload["hardware_connected"] is True
    assert payload["last_status"]["hardware_connected"] is True
    assert payload["last_status"]["hardware_connected_inferred_via"] == "/motion/power/status"
    assert calls == [("GET", "/status", 18.0), ("GET", "/motion/power/status", 5.0)]


def test_stale_successful_interlink_probe_does_not_expose_live_reachable(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    bioxp_interlink.save_profile({"robot_api_url": "http://robot:8123", "robot_ssh_host": "robot"})
    monkeypatch.setattr(bioxp, "_GLOBAL_LINKAGE_URL", "http://robot:8123", raising=False)
    bioxp_interlink.activate_session("http://robot:8123")
    stale_probe_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    bioxp_interlink._SESSION.update(  # noqa: SLF001 - regression covers exposed state contract
        {
            "reachable": True,
            "hardware_connected": True,
            "last_probe_at": stale_probe_at,
            "last_status": {"status": "ok", "hardware_connected": True},
            "last_error": None,
        }
    )
    proxy_calls: list[tuple[str, str]] = []

    async def fail_proxy_request(method: str, path: str, **_: object) -> dict:
        proxy_calls.append((method, path))
        raise AssertionError("passive stale-state read must not probe the robot")

    monkeypatch.setattr(bioxp, "proxy_request", fail_proxy_request)

    with build_client() as client:
        response = client.get("/api/bioxp/interlink/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is True
    assert payload["reachable"] is None
    assert payload["hardware_connected"] is None
    assert payload["last_probe_reachable"] is True
    assert payload["last_probe_hardware_connected"] is True
    assert payload["probe_fresh"] is False
    assert payload["probe_stale"] is True
    assert "stale" in payload["runtime_note"].lower()
    assert proxy_calls == []


def test_disconnect_deactivates_session_but_keeps_saved_profile(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    monkeypatch.setattr(bioxp, "_GLOBAL_LINKAGE_URL", "http://robot:8123", raising=False)
    bioxp_interlink.save_profile({"robot_api_url": "http://robot:8123", "robot_ssh_host": "robot"})
    bioxp_interlink.activate_session("http://robot:8123")

    with build_client() as client:
        response = client.post("/api/bioxp/interlink/disconnect")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["active"] is False
    assert payload["robot_api_url"] == "http://robot:8123"
    assert bioxp_interlink.load_profile()["robot_api_url"] == "http://robot:8123"
    assert bioxp._GLOBAL_LINKAGE_URL is None


def test_interlink_mutations_reject_non_local_admin_clients(monkeypatch, tmp_path: Path) -> None:
    reset_interlink(monkeypatch, tmp_path)
    bioxp_interlink.save_profile({"robot_api_url": "http://robot:8123", "robot_ssh_host": "robot"})
    monkeypatch.setattr(bioxp, "_GLOBAL_LINKAGE_URL", "http://robot:8123", raising=False)
    bioxp_interlink.activate_session("http://robot:8123")

    with build_client(host="203.0.113.9") as client:
        responses = [
            client.put("/api/bioxp/interlink/settings", json={"robot_api_url": "robot:8123"}),
            client.delete("/api/bioxp/interlink/settings"),
            client.post("/api/bioxp/interlink/connect"),
            client.post("/api/bioxp/interlink/disconnect"),
            client.post("/api/bioxp/interlink/diagnostics"),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]
    assert bioxp._GLOBAL_LINKAGE_URL == "http://robot:8123"