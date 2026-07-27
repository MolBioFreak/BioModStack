from __future__ import annotations

from fastapi.testclient import TestClient

from routers import workflow_adapter
from workflow_adapter_app import app


def _enable_operator(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS", "testclient")
    monkeypatch.setenv("BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS", "operator@example.com")
    return {"Tailscale-User-Login": "operator@example.com"}


def test_tailnet_control_base_indexes_current_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        workflow_adapter,
        "current_tailnet_environment",
        lambda: {
            "selected_environment": "development",
            "serve_root_proxy": "http://127.0.0.1:5173",
        },
    )
    headers = _enable_operator(monkeypatch)
    with TestClient(app, base_url="https://compute-node.taileb3a90.ts.net") as client:
        response = client.get("/", headers=headers)
    assert response.status_code == 200
    assert response.json()["selected_environment"] == "development"


def test_tailnet_control_selects_only_allowed_environment(monkeypatch) -> None:
    selected: list[str] = []
    monkeypatch.setattr(
        workflow_adapter,
        "select_tailnet_environment",
        lambda environment: selected.append(environment) or {
            "selected_environment": environment,
            "serve_root_proxy": "http://127.0.0.1:18081",
        },
    )
    headers = _enable_operator(monkeypatch)
    with TestClient(app, base_url="https://compute-node.taileb3a90.ts.net") as client:
        response = client.post("/select", headers=headers, json={"environment": "production"})
        invalid = client.post("/select", headers=headers, json={"environment": "staging"})
    assert response.status_code == 200
    assert invalid.status_code == 422
    assert selected == ["production"]


def test_tailnet_control_requires_allowed_tailscale_identity(monkeypatch) -> None:
    monkeypatch.setenv("BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS", "testclient")
    monkeypatch.setenv("BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS", "operator@example.com")
    with TestClient(app, base_url="https://compute-node.taileb3a90.ts.net") as client:
        missing = client.get("/")
        forbidden = client.get("/", headers={"Tailscale-User-Login": "intruder@example.com"})
    assert missing.status_code == 401
    assert forbidden.status_code == 403


def test_tailnet_forwarded_control_surface_hides_nested_adapter_routes(monkeypatch) -> None:
    headers = _enable_operator(monkeypatch)
    with TestClient(app, base_url="https://compute-node.taileb3a90.ts.net") as client:
        response = client.post(
            "/api/workflow-adapter/runtime/restart",
            headers=headers,
            json={"runtime": "container"},
        )
    assert response.status_code == 404
