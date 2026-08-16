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
from services import nextflow


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(workflow_adapter.router, prefix="/api")
    return TestClient(app)


@pytest.fixture(autouse=True)
def explicit_development_adapter_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", "development")
    monkeypatch.setenv("BMS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BMS_DB_PATH", str(tmp_path / "state" / "biomodstack.db"))
    monkeypatch.setenv("BMS_WORK", str(tmp_path / "state" / "work"))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(tmp_path / "state" / "results"))


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


def test_workflow_adapter_start_runtime_target_rejects_cross_lane_query_param() -> None:
    with build_client() as client:
        response = client.post("/api/workflow-adapter/runtime/start-target", params={"target": "both"})

    assert response.status_code == 409
    assert "not owned by adapter lane 'development'" in response.json()["detail"]


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


def test_runner_environment_owns_development_stage_callback_url(monkeypatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "http://127.0.0.1:65535")
    identity = workflow_adapter.adapter_identity_from_environment()

    environment = workflow_adapter._runner_environment(  # noqa: SLF001 - execution boundary under test.
        identity=identity,
        unit_name="biomodstack-development-job-test-attempt-1.service",
        owner_nonce="owner-nonce",
    )

    assert environment["API_BASE_URL"] == "http://127.0.0.1:18002"


def test_adapter_persists_scheduler_gpu_before_detached_runner() -> None:
    job = SimpleNamespace(params={"num_designs": 1}, pinned_gpu=2, assigned_gpu=None)

    params = workflow_adapter._bind_scheduler_gpu_assignment(  # noqa: SLF001 - authority handoff under test.
        job,
        dict(job.params),
        {"num_designs": 1, "gpu_id": 2},
    )

    assert params == {"num_designs": 1, "gpu_id": 2}
    assert job.params == params
    assert job.assigned_gpu == 2


def test_adapter_rejects_scheduler_gpu_that_conflicts_with_pin() -> None:
    job = SimpleNamespace(params={}, pinned_gpu=2, assigned_gpu=None)

    with pytest.raises(workflow_adapter.HTTPException) as exc_info:
        workflow_adapter._bind_scheduler_gpu_assignment(  # noqa: SLF001 - authority handoff under test.
            job,
            {},
            {"gpu_id": 0},
        )

    assert exc_info.value.status_code == 409
    assert "pinned_gpu=2" in str(exc_info.value.detail)
    assert job.params == {}
    assert job.assigned_gpu is None


@pytest.mark.parametrize("invalid_gpu_id", [True, -1, 2.0, 2.7, "-1", "+2", "2.7", "gpu2"])
def test_adapter_rejects_noncanonical_gpu_identifiers(invalid_gpu_id: object) -> None:
    job = SimpleNamespace(params={}, pinned_gpu=None, assigned_gpu=None)

    with pytest.raises(workflow_adapter.HTTPException) as exc_info:
        workflow_adapter._bind_scheduler_gpu_assignment(  # noqa: SLF001 - authority handoff under test.
            job,
            {},
            {"gpu_id": invalid_gpu_id},
        )

    assert exc_info.value.status_code == 409
    assert job.params == {}
    assert job.assigned_gpu is None


@pytest.mark.parametrize("field", ["pinned_gpu", "assigned_gpu"])
@pytest.mark.parametrize("invalid_gpu_id", [True, -1, 2.0, 2.7, "-1", "+2", "2.7", "gpu2"])
def test_adapter_rejects_noncanonical_persisted_gpu_authority(
    field: str,
    invalid_gpu_id: object,
) -> None:
    values = {"params": {}, "pinned_gpu": None, "assigned_gpu": None}
    values[field] = invalid_gpu_id
    job = SimpleNamespace(**values)

    with pytest.raises(workflow_adapter.HTTPException) as exc_info:
        workflow_adapter._bind_scheduler_gpu_assignment(  # noqa: SLF001
            job,
            {},
            {"gpu_id": 2},
        )

    assert exc_info.value.status_code == 409
    assert job.params == {}


def test_detached_native_rfd3_runner_uses_durable_gpu_assignment() -> None:
    job = SimpleNamespace(assigned_gpu=2, pinned_gpu=2)

    assert nextflow._resolve_launch_gpu_id(job, {}, "protein_local_redesign") == 2  # noqa: SLF001


def test_native_rfd3_runner_rejects_missing_gpu_authority() -> None:
    job = SimpleNamespace(assigned_gpu=None, pinned_gpu=None)

    with pytest.raises(RuntimeError, match="no authoritative scheduler GPU assignment"):
        nextflow._resolve_launch_gpu_id(job, {}, "protein_local_redesign")  # noqa: SLF001


@pytest.mark.parametrize("invalid_gpu_id", [True, -1, 2.0, 2.7, "-1", "+2", "2.7", "gpu2"])
def test_native_rfd3_runner_rejects_noncanonical_gpu_identifiers(invalid_gpu_id: object) -> None:
    job = SimpleNamespace(assigned_gpu=None, pinned_gpu=None)

    with pytest.raises(RuntimeError, match="invalid scheduler GPU assignment"):
        nextflow._resolve_launch_gpu_id(  # noqa: SLF001 - detached authority handoff under test.
            job,
            {"gpu_id": invalid_gpu_id},
            "protein_local_redesign",
        )


@pytest.mark.parametrize("field", ["pinned_gpu", "assigned_gpu"])
@pytest.mark.parametrize("invalid_gpu_id", [True, -1, 2.0, 2.7, "-1", "+2", "2.7", "gpu2"])
def test_native_rfd3_runner_rejects_noncanonical_persisted_gpu_authority(
    field: str,
    invalid_gpu_id: object,
) -> None:
    values: dict[str, object] = {"pinned_gpu": 2, "assigned_gpu": 2}
    values[field] = invalid_gpu_id
    job = SimpleNamespace(**values)

    with pytest.raises(RuntimeError, match="invalid scheduler GPU assignment"):
        nextflow._resolve_launch_gpu_id(job, {"gpu_id": 2}, "protein_local_redesign")  # noqa: SLF001
