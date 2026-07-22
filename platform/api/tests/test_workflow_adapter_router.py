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


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(workflow_adapter.router, prefix="/api")
    return TestClient(app)


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
