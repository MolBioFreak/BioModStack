from __future__ import annotations

from types import SimpleNamespace
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from routers import workflow_adapter
from workflow_adapter_app import app as workflow_adapter_app


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(workflow_adapter.router, prefix="/api")
    return TestClient(app)


def enable_tailnet_identity(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS", "testclient")
    monkeypatch.setenv("BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS", "operator@example.com")
    return {"Tailscale-User-Login": "operator@example.com"}


def test_workflow_adapter_start_runtime_target_invokes_host_service(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(
        workflow_adapter,
        "start_runtime_target",
        lambda target=None, skip_api_wait=False, skip_workflow_adapter_wait=False: started.append(
            (target or "missing", skip_api_wait, skip_workflow_adapter_wait)
        ),
        raising=False,
    )

    with build_client() as client:
        response = client.post("/api/workflow-adapter/runtime/start-target", json={"target": "dev"})

    assert response.status_code == 200
    assert response.json() == {"target": "dev", "control_mode": "host-adapter"}
    assert started == [("dev", True, True)]


def test_workflow_adapter_start_runtime_target_accepts_query_param(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(
        workflow_adapter,
        "start_runtime_target",
        lambda target=None, skip_api_wait=False, skip_workflow_adapter_wait=False: started.append(
            (target or "missing", skip_api_wait, skip_workflow_adapter_wait)
        ),
        raising=False,
    )

    with build_client() as client:
        response = client.post("/api/workflow-adapter/runtime/start-target", params={"target": "both"})

    assert response.status_code == 200
    assert response.json() == {"target": "both", "control_mode": "host-adapter"}
    assert started == [("both", True, True)]


def test_workflow_adapter_selects_tailnet_environment_on_host(monkeypatch) -> None:
    selected: list[str] = []
    monkeypatch.setattr(
        workflow_adapter,
        "select_tailnet_environment",
        lambda environment: selected.append(environment) or {
            "selected_environment": environment,
            "serve_root_proxy": "http://127.0.0.1:5173",
        },
    )

    headers = enable_tailnet_identity(monkeypatch)
    with build_client() as client:
        response = client.post(
            "/api/workflow-adapter/tailnet-environment/select",
            headers=headers,
            json={"environment": "development"},
        )

    assert response.status_code == 200
    assert response.json()["selected_environment"] == "development"
    assert selected == ["development"]


def test_host_adapter_exposes_pathless_tailscale_proxy_endpoints(monkeypatch) -> None:
    selected: list[str] = []
    monkeypatch.setattr(
        workflow_adapter,
        "select_tailnet_environment",
        lambda environment: selected.append(environment) or {
            "selected_environment": environment,
            "serve_root_proxy": "http://127.0.0.1:18081",
        },
    )
    headers = enable_tailnet_identity(monkeypatch)
    with TestClient(workflow_adapter_app) as client:
        response = client.post(
            "/select", headers=headers, json={"environment": "production"}
        )
    assert response.status_code == 200
    assert response.json()["selected_environment"] == "production"
    assert selected == ["production"]


def test_tailnet_forwarded_nested_routes_are_not_exposed(monkeypatch) -> None:
    invoked: list[str] = []
    monkeypatch.setattr(
        workflow_adapter,
        "restart_runtime_target",
        lambda target=None: invoked.append(target or "missing"),
        raising=False,
    )
    headers = enable_tailnet_identity(monkeypatch)
    with TestClient(workflow_adapter_app) as client:
        response = client.post(
            "/api/workflow-adapter/runtime/restart",
            headers=headers,
            json={"target": "prod"},
        )
    assert response.status_code == 404
    assert invoked == []

    with TestClient(workflow_adapter_app, base_url="https://compute-node.taileb3a90.ts.net") as client:
        missing_identity = client.post(
            "/api/workflow-adapter/runtime/restart",
            json={"target": "prod"},
        )
    assert missing_identity.status_code == 404
    assert invoked == []


def test_host_adapter_disables_uvicorn_forwarded_client_rewrite() -> None:
    launcher = (REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh").read_text()
    assert "--no-proxy-headers" in launcher


def test_workflow_adapter_tailnet_control_rejects_missing_identity(monkeypatch) -> None:
    enable_tailnet_identity(monkeypatch)
    with build_client() as client:
        response = client.post(
            "/api/workflow-adapter/tailnet-environment/select",
            json={"environment": "production"},
        )
    assert response.status_code == 401


def test_workflow_adapter_tailnet_control_fails_closed_without_operator_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS", "testclient")
    monkeypatch.delenv("BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS", raising=False)
    with build_client() as client:
        response = client.post(
            "/api/workflow-adapter/tailnet-environment/select",
            headers={"Tailscale-User-Login": "operator@example.com"},
            json={"environment": "production"},
        )
    assert response.status_code == 503


def test_workflow_adapter_tailnet_control_rejects_nonallowed_member(monkeypatch) -> None:
    enable_tailnet_identity(monkeypatch)
    with build_client() as client:
        response = client.post(
            "/api/workflow-adapter/tailnet-environment/select",
            headers={"Tailscale-User-Login": "other@example.com"},
            json={"environment": "production"},
        )
    assert response.status_code == 403


def test_workflow_adapter_allows_only_cordova_local_origin_preflight() -> None:
    client = TestClient(workflow_adapter_app)
    response = client.options(
        "/api/workflow-adapter/tailnet-environment/select",
        headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://localhost"

    rejected = client.options(
        "/api/workflow-adapter/tailnet-environment/select",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_workflow_adapter_tailnet_environment_rejects_nonlocal_clients() -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="172.17.0.2"))
    payload = workflow_adapter.WorkflowAdapterTailnetEnvironmentRequest(environment="production")

    with pytest.raises(workflow_adapter.HTTPException) as exc_info:
        import asyncio

        asyncio.run(workflow_adapter.workflow_adapter_select_tailnet_environment(request, payload))

    assert exc_info.value.status_code == 403


def test_workflow_adapter_runtime_control_rejects_nonlocal_clients() -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="172.17.0.2"))

    with pytest.raises(workflow_adapter.HTTPException) as exc_info:
        workflow_adapter._require_local_adapter_request(request)  # noqa: SLF001 - local-only guard is the unit under test.

    assert exc_info.value.status_code == 403
    assert "local-only" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_workflow_adapter_launch_rejects_nonlocal_clients() -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="172.17.0.2"))
    payload = workflow_adapter.WorkflowAdapterLaunchRequest(
        job_id="job-1",
        model_id="nanopore",
        mode="basecall_dna",
        params={},
        output_dir="/tmp/job-1",
    )
    with pytest.raises(workflow_adapter.HTTPException) as exc_info:
        await workflow_adapter.workflow_adapter_launch(payload, request)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 403
