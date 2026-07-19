from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from routers import system  # noqa: E402


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    return TestClient(app)


def _local_request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


def test_runtime_state_endpoint_returns_runtime_descriptor(monkeypatch) -> None:
    descriptor = {"runtime_mode": "container", "paths": {"data_root": "/srv/biomodstack"}}
    monkeypatch.setattr(system, "runtime_descriptor", lambda project_root=None, runtime_mode=None: descriptor, raising=False)

    with build_client() as client:
        response = client.get("/api/system/runtime-state", params={"runtime": "container"})

    assert response.status_code == 200
    assert response.json() == descriptor


def test_runtime_start_endpoint_invokes_service_layer(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(system, "start_all", lambda project_root=None, runtime_mode=None: started.append(runtime_mode or "dev"), raising=False)
    monkeypatch.setattr(
        system,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {"runtime_mode": runtime_mode or "dev", "runtime_active": True},
        raising=False,
    )

    with build_client() as client:
        response = client.post("/api/system/runtime/start", params={"runtime": "container"})

    assert response.status_code == 200
    assert started == ["container"]
    assert response.json()["runtime_mode"] == "container"
    assert response.json()["runtime_active"] is True


def test_runtime_start_api_endpoint_invokes_api_only_service_layer(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(system, "start_api", lambda project_root=None, runtime_mode=None: started.append(runtime_mode or "dev"), raising=False)
    monkeypatch.setattr(
        system,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {"runtime_mode": runtime_mode or "dev", "runtime_active": True},
        raising=False,
    )

    response = asyncio.run(system.start_runtime_api(_local_request(), runtime="container"))
    assert started == ["container"]
    assert response["runtime_mode"] == "container"


def test_runtime_state_endpoint_defaults_to_container_when_runtime_omitted(monkeypatch) -> None:
    seen: list[str] = []

    def fake_runtime_descriptor(project_root=None, runtime_mode=None):
        seen.append(runtime_mode or "missing")
        return {"runtime_mode": runtime_mode, "runtime_active": False}

    monkeypatch.delenv("BMS_RUNTIME_MODE", raising=False)
    monkeypatch.setattr(system, "runtime_descriptor", fake_runtime_descriptor, raising=False)

    with build_client() as client:
        response = client.get("/api/system/runtime-state")

    assert response.status_code == 200
    assert seen == ["container"]
    assert response.json()["runtime_mode"] == "container"


def test_runtime_state_marks_current_api_ready_and_derives_container_status(monkeypatch) -> None:
    descriptor = {
        "runtime_mode": "container",
        "runtime_active": False,
        "health": {"adapter_ready": True, "api_ready": False, "frontend_ready": True},
        "services": [
            {"name": system.WORKFLOW_ADAPTER_SERVICE, "active": False},
            {"name": system.CORE_RUNTIME_SERVICE, "active": False},
        ],
    }
    monkeypatch.setattr(system, "runtime_descriptor", lambda project_root=None, runtime_mode=None: descriptor, raising=False)

    with build_client() as client:
        response = client.get("/api/system/runtime-state", params={"runtime": "container"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"]["api_ready"] is True
    assert payload["runtime_active"] is True
    assert payload["runtime_ready"] is True
    assert payload["services"] == [
        {"name": system.WORKFLOW_ADAPTER_SERVICE, "active": True, "systemd_active": False, "active_source": "http-health"},
        {"name": system.CORE_RUNTIME_SERVICE, "active": True, "systemd_active": False, "active_source": "http-health"},
    ]


def test_runtime_state_recomputes_dev_active_after_marking_current_api_ready(monkeypatch) -> None:
    descriptor = {
        "runtime_mode": "dev",
        "runtime_active": False,
        "runtime_ready": False,
        "health": {"api_ready": False, "frontend_ready": True},
        "services": [
            {"name": "biomodstack-frontend.service", "active": True},
        ],
    }
    monkeypatch.setattr(system, "runtime_descriptor", lambda project_root=None, runtime_mode=None: descriptor, raising=False)

    with build_client() as client:
        response = client.get("/api/system/runtime-state", params={"runtime": "dev"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"] == {"api_ready": True, "frontend_ready": True}
    assert payload["runtime_ready"] is True
    assert payload["runtime_active"] is True
    assert payload["services"] == [
        {
            "name": "biomodstack-frontend.service",
            "active": True,
            "systemd_active": True,
            "active_source": "http-health",
        },
    ]


def test_runtime_state_does_not_keep_container_active_when_http_health_fails(monkeypatch) -> None:
    descriptor = {
        "runtime_mode": "container",
        "runtime_active": True,
        "runtime_ready": False,
        "health": {"adapter_ready": True, "api_ready": True, "frontend_ready": False},
        "services": [
            {"name": system.WORKFLOW_ADAPTER_SERVICE, "active": True},
            {"name": system.CORE_RUNTIME_SERVICE, "active": True},
        ],
    }
    monkeypatch.setattr(system, "runtime_descriptor", lambda project_root=None, runtime_mode=None: descriptor, raising=False)

    with build_client() as client:
        response = client.get("/api/system/runtime-state", params={"runtime": "container"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_active"] is False
    assert payload["runtime_ready"] is False
    assert payload["services"] == [
        {"name": system.WORKFLOW_ADAPTER_SERVICE, "active": True, "systemd_active": True, "active_source": "http-health"},
        {"name": system.CORE_RUNTIME_SERVICE, "active": False, "systemd_active": True, "active_source": "http-health-failed"},
    ]


def test_install_profile_put_persists_and_returns_snapshot(monkeypatch) -> None:
    saved_payloads: list[dict[str, object]] = []

    def fake_save_install_profile(payload: dict[str, object]) -> dict[str, object]:
        saved_payloads.append(payload)
        return {"data_root": "/srv/biomodstack"}

    snapshot = {
        "profile_path": "/home/christian/.config/biomodstack/install_profile.json",
        "compat_env_path": "/home/christian/.biomodstack/env.sh",
        "core_runtime_env_path": "/home/christian/.config/biomodstack/core-runtime.env",
        "profile": {"data_root": "/srv/biomodstack"},
        "resolved": {"data_root": "/srv/biomodstack", "db_path": "/srv/biomodstack/biomodstack.db"},
    }
    monkeypatch.setattr(system, "save_install_profile", fake_save_install_profile, raising=False)
    monkeypatch.setattr(system, "install_profile_snapshot", lambda profile=None: snapshot, raising=False)

    with build_client() as client:
        response = client.put("/api/system/install-profile", json={"data_root": "/srv/biomodstack"})

    assert response.status_code == 200
    assert saved_payloads == [{"data_root": "/srv/biomodstack"}]
    assert response.json() == snapshot


def test_install_profile_put_accepts_supported_feature_flags(monkeypatch) -> None:
    saved_payloads: list[dict[str, object]] = []

    def fake_save_install_profile(payload: dict[str, object]) -> dict[str, object]:
        saved_payloads.append(payload)
        return {"features": {"bioxp": False}}

    snapshot = {
        "profile": {"features": {"bioxp": False}},
        "resolved": {"features": {"bioxp": False}},
    }
    monkeypatch.setattr(system, "save_install_profile", fake_save_install_profile, raising=False)
    monkeypatch.setattr(system, "install_profile_snapshot", lambda profile=None: snapshot, raising=False)

    with build_client() as client:
        response = client.put(
            "/api/system/install-profile",
            json={"features": {"bioxp": False}},
        )

    assert response.status_code == 200
    assert saved_payloads == [{"features": {"bioxp": False}}]
    assert response.json()["resolved"]["features"]["bioxp"] is False


def test_system_features_endpoint_returns_resolved_addon_flags(monkeypatch) -> None:
    snapshot = {
        "profile": {"features": {"bioxp": False}},
        "resolved": {"features": {"bioxp": False}},
    }
    monkeypatch.setattr(system, "install_profile_snapshot", lambda profile=None: snapshot, raising=False)

    with build_client() as client:
        response = client.get("/api/system/features")

    assert response.status_code == 200
    assert response.json() == {
        "features": {"bioxp": False},
        "configured_features": {"bioxp": False},
        "dev_features": {"bioxp": True},
    }


def test_system_features_put_merges_feature_flags_into_install_profile(monkeypatch) -> None:
    saved_payloads: list[dict[str, object]] = []

    current_snapshot = {
        "profile": {"data_root": "/srv/biomodstack", "features": {"bioxp": True}},
        "resolved": {"features": {"bioxp": True}},
    }
    updated_snapshot = {
        "profile": {"data_root": "/srv/biomodstack", "features": {"bioxp": False}},
        "resolved": {"features": {"bioxp": False}},
    }

    def fake_save_install_profile(payload: dict[str, object]) -> dict[str, object]:
        saved_payloads.append(payload)
        return payload

    def fake_install_profile_snapshot(profile=None):
        return updated_snapshot if profile is not None else current_snapshot

    monkeypatch.setattr(system, "save_install_profile", fake_save_install_profile, raising=False)
    monkeypatch.setattr(system, "install_profile_snapshot", fake_install_profile_snapshot, raising=False)

    with build_client() as client:
        response = client.put("/api/system/features", json={"features": {"bioxp": False}})

    assert response.status_code == 200
    assert saved_payloads == [
        {"data_root": "/srv/biomodstack", "features": {"bioxp": False}},
    ]
    assert response.json() == {
        "features": {"bioxp": False},
        "configured_features": {"bioxp": False},
        "dev_features": {"bioxp": True},
    }


def test_runtime_ports_endpoint_persists_dev_and_prod_ports(monkeypatch) -> None:
    saved: list[tuple[int | None, int | None]] = []

    monkeypatch.setattr(
        system,
        "save_runtime_port_settings",
        lambda dev_web_host_port=None, prod_web_host_port=None: saved.append((dev_web_host_port, prod_web_host_port))
        or {
            "dev_web_host_port": dev_web_host_port,
            "prod_web_host_port": prod_web_host_port,
            "dev_url": f"http://127.0.0.1:{dev_web_host_port}/",
            "prod_url": f"http://127.0.0.1:{prod_web_host_port}/bms/",
        },
        raising=False,
    )

    with build_client() as client:
        response = client.put("/api/system/runtime-ports", json={"dev_web_host_port": 5179, "prod_web_host_port": 19090})

    assert response.status_code == 200
    assert saved == [(5179, 19090)]
    assert response.json()["dev_url"] == "http://127.0.0.1:5179/"
    assert response.json()["prod_url"] == "http://127.0.0.1:19090/bms/"


def test_start_runtime_target_endpoint_invokes_service_layer(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(system, "start_runtime_target", lambda target=None: started.append(target or "missing"), raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/start-target", params={"target": "both"})

    assert response.status_code == 200
    assert started == ["both"]
    assert response.json()["target"] == "both"


def test_runtime_stop_api_endpoint_invokes_api_only_service_layer(monkeypatch) -> None:
    stopped: list[str] = []
    monkeypatch.setattr(system, "stop_api", lambda project_root=None, runtime_mode=None: stopped.append(runtime_mode or "dev"), raising=False)
    monkeypatch.setattr(
        system,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {"runtime_mode": runtime_mode or "dev", "runtime_active": False},
        raising=False,
    )

    response = asyncio.run(system.stop_runtime_api(_local_request(), runtime="dev"))
    assert stopped == ["dev"]
    assert response["runtime_mode"] == "dev"


def test_start_runtime_target_endpoint_accepts_json_body_from_web_ui(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(system, "start_runtime_target", lambda target=None: started.append(target or "missing"), raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/start-target", json={"target": "dev"})

    assert response.status_code == 200
    assert started == ["dev"]
    assert response.json()["target"] == "dev"


def test_start_runtime_target_endpoint_proxies_from_core_runtime_to_host_adapter(monkeypatch) -> None:
    adapter_calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fail_local_start(target=None):
        raise AssertionError("container API must not call host systemctl directly")

    def fake_adapter_request(method: str, path: str, payload: dict[str, object] | None = None):
        adapter_calls.append((method, path, payload))
        return {"target": payload["target"] if payload else "missing", "control_mode": "host-adapter"}

    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")
    monkeypatch.setattr(system, "start_runtime_target", fail_local_start, raising=False)
    monkeypatch.setattr(system, "request_via_workflow_adapter", fake_adapter_request, raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/start-target", json={"target": "both"})

    assert response.status_code == 200
    assert response.json() == {"target": "both", "control_mode": "host-adapter"}
    assert adapter_calls == [
        ("POST", "/api/workflow-adapter/runtime/start-target", {"target": "both"}),
    ]


def test_runtime_start_endpoint_proxies_from_core_runtime_to_existing_start_target_route(monkeypatch) -> None:
    adapter_calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fail_local_start(**_kwargs):
        raise AssertionError("container API must not call host systemctl directly")

    def fake_adapter_request(method: str, path: str, payload: dict[str, object] | None = None):
        adapter_calls.append((method, path, payload))
        return {"target": payload["target"] if payload else "missing", "control_mode": "host-adapter"}

    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")
    monkeypatch.setattr(system, "start_all", fail_local_start, raising=False)
    monkeypatch.setattr(system, "request_via_workflow_adapter", fake_adapter_request, raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/start", params={"runtime": "container"})

    assert response.status_code == 200
    assert response.json() == {"target": "prod", "control_mode": "host-adapter"}
    assert adapter_calls == [
        ("POST", "/api/workflow-adapter/runtime/start-target", {"target": "prod"}),
    ]


def test_runtime_restart_endpoint_proxies_from_core_runtime_to_host_adapter(monkeypatch) -> None:
    adapter_calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fail_local_restart(**_kwargs):
        raise AssertionError("container API must not call host systemctl directly")

    def fake_adapter_request(method: str, path: str, payload: dict[str, object] | None = None):
        adapter_calls.append((method, path, payload))
        return {"runtime_mode": payload["runtime"] if payload else "missing", "action": "restart", "control_mode": "host-adapter"}

    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")
    monkeypatch.setattr(system, "restart_all", fail_local_restart, raising=False)
    monkeypatch.setattr(system, "request_via_workflow_adapter", fake_adapter_request, raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/restart", params={"runtime": "dev"})

    assert response.status_code == 200
    assert response.json() == {"runtime_mode": "dev", "action": "restart", "control_mode": "host-adapter"}
    assert adapter_calls == [
        ("POST", "/api/workflow-adapter/runtime/restart", {"runtime": "dev"}),
    ]


def test_runtime_action_proxy_marks_current_api_ready(monkeypatch) -> None:
    def fake_adapter_request(method: str, path: str, payload: dict[str, object] | None = None):
        return {
            "runtime_mode": "dev",
            "runtime_active": False,
            "runtime_ready": False,
            "health": {"api_ready": False, "frontend_ready": True},
            "services": [{"name": "biomodstack-frontend.service", "active": True}],
            "action": "stop",
            "control_mode": "host-adapter",
        }

    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")
    monkeypatch.setattr(system, "request_via_workflow_adapter", fake_adapter_request, raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/stop", params={"runtime": "dev"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"] == {"api_ready": True, "frontend_ready": True}
    assert payload["runtime_ready"] is True
    assert payload["runtime_active"] is True
    assert payload["control_mode"] == "host-adapter"


def test_runtime_state_uses_host_adapter_descriptor_when_available(monkeypatch) -> None:
    adapter_calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fail_local_descriptor(**_kwargs):
        raise AssertionError("container API must not use container-local systemctl for hosted status")

    def fake_adapter_request(method: str, path: str, payload: dict[str, object] | None = None):
        adapter_calls.append((method, path, payload))
        return {
            "runtime_mode": "dev",
            "runtime_active": True,
            "runtime_ready": True,
            "health": {"api_ready": False, "frontend_ready": True},
            "services": [{"name": "biomodstack-frontend.service", "active": True}],
            "control_mode": "host-adapter",
        }

    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")
    monkeypatch.setattr(system, "runtime_descriptor", fail_local_descriptor, raising=False)
    monkeypatch.setattr(system, "request_via_workflow_adapter", fake_adapter_request, raising=False)

    with build_client() as client:
        response = client.get("/api/system/runtime-state", params={"runtime": "dev"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["control_mode"] == "host-adapter"
    assert payload["runtime_active"] is True
    assert payload["health"]["api_ready"] is True
    assert adapter_calls == [("GET", "/api/workflow-adapter/runtime/state?runtime=dev", None)]


def test_start_runtime_target_endpoint_service_errors_remain_500(monkeypatch) -> None:
    monkeypatch.delenv("BMS_CORE_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)

    def fail_local_start(target=None):
        raise system.ServiceManagerError("host service manager rejected start")

    monkeypatch.setattr(system, "start_runtime_target", fail_local_start, raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/start-target", json={"target": "dev"})

    assert response.status_code == 500
    assert response.json()["detail"] == "host service manager rejected start"


def test_start_runtime_target_endpoint_adapter_runtime_errors_are_bad_gateway(monkeypatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")

    def fail_adapter_request(method: str, path: str, payload: dict[str, object] | None = None):
        raise RuntimeError("workflow adapter unavailable")

    monkeypatch.setattr(system, "request_via_workflow_adapter", fail_adapter_request, raising=False)

    with build_client() as client:
        response = client.post("/api/system/runtime/start-target", json={"target": "dev"})

    assert response.status_code == 502
    assert response.json()["detail"] == "workflow adapter unavailable"
