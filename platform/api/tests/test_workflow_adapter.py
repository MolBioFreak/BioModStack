from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import database
from routers import gpu
from services import gpu_orchestrator, nextflow, workflow_adapter
import workflow_adapter_app


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_workflow_adapter_disabled_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)
    monkeypatch.delenv("BMS_CORE_RUNTIME_MODE", raising=False)

    assert workflow_adapter.workflow_adapter_base_url() is None
    assert workflow_adapter.workflow_adapter_enabled() is False
    assert workflow_adapter.workflow_launch_mode() == "native"



def test_workflow_adapter_enabled_when_base_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", " http://127.0.0.1:8001/// ")

    assert workflow_adapter.workflow_adapter_base_url() == "http://127.0.0.1:8001"
    assert workflow_adapter.workflow_adapter_enabled() is True



def test_workflow_launch_mode_is_adapter_in_core_runtime_when_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    assert workflow_adapter.workflow_launch_mode() == "adapter"



def test_launch_request_posts_expected_payload_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"accepted": True, "job_id": "job-123", "launch_mode": "native-host"}, status=202)

    monkeypatch.setattr(workflow_adapter.urllib.request, "urlopen", fake_urlopen)

    response = workflow_adapter.launch_via_workflow_adapter(
        job_id="job-123",
        model_id="boltz2",
        mode="predict",
        params={"gpu_id": 1, "allow_retries": True},
        output_dir="/mnt/BioModStack/bms_results/job-123",
    )

    assert captured["url"] == "http://127.0.0.1:8001/api/workflow-adapter/launch"
    assert captured["method"] == "POST"
    assert captured["timeout"] == workflow_adapter.DEFAULT_ADAPTER_TIMEOUT_SECONDS
    assert captured["payload"] == {
        "job_id": "job-123",
        "model_id": "boltz2",
        "mode": "predict",
        "params": {"gpu_id": 1, "allow_retries": True},
        "output_dir": "/mnt/BioModStack/bms_results/job-123",
    }
    assert response == {"accepted": True, "job_id": "job-123", "launch_mode": "native-host"}



def test_launch_request_translates_container_paths_for_host_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("BMS_STATE_DIR", "/mnt/BioModStack")
    monkeypatch.setenv("BMS_CONTAINER_STATE_PATH", "/var/lib/biomodstack")
    monkeypatch.setenv("BMS_INPUTS_CONTAINER_PATH", "/var/lib/biomodstack/inputs")
    monkeypatch.setenv("BMS_DB_CONTAINER_PATH", "/var/lib/biomodstack/biomodstack.db")
    monkeypatch.setattr(
        workflow_adapter,
        "resolve_runtime_paths",
        lambda: {
            "data_root": "/var/lib/biomodstack",
            "inputs_dir": "/var/lib/biomodstack/inputs",
            "db_path": "/var/lib/biomodstack/biomodstack.db",
            "container_state_path": "/var/lib/biomodstack",
            "inputs_container_path": "/var/lib/biomodstack/inputs",
            "db_container_path": "/var/lib/biomodstack/biomodstack.db",
        },
    )

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=0):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"accepted": True, "job_id": "job-123", "launch_mode": "native-host"}, status=202)

    monkeypatch.setattr(workflow_adapter.urllib.request, "urlopen", fake_urlopen)

    workflow_adapter.launch_via_workflow_adapter(
        job_id="job-123",
        model_id="boltz_cp_experimental",
        mode="design",
        params={
            "input_path": "/var/lib/biomodstack/inputs/smoke/input.yaml",
            "db_path": "/var/lib/biomodstack/biomodstack.db",
            "nested": {
                "manifest": "/var/lib/biomodstack/bms_results/job-123/manifest.json",
            },
        },
        output_dir="/var/lib/biomodstack/bms_results/job-123",
    )

    assert captured["payload"] == {
        "job_id": "job-123",
        "model_id": "boltz_cp_experimental",
        "mode": "design",
        "params": {
            "input_path": "/mnt/BioModStack/inputs/smoke/input.yaml",
            "db_path": "/mnt/BioModStack/biomodstack.db",
            "nested": {
                "manifest": "/mnt/BioModStack/bms_results/job-123/manifest.json",
            },
        },
        "output_dir": "/mnt/BioModStack/bms_results/job-123",
    }



def test_cancel_request_posts_expected_payload_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse({"cancelled": True})

    monkeypatch.setattr(workflow_adapter.urllib.request, "urlopen", fake_urlopen)

    cancelled = workflow_adapter.cancel_via_workflow_adapter("run-123")

    assert cancelled is True
    assert captured["url"] == "http://127.0.0.1:8001/api/workflow-adapter/cancel"
    assert captured["method"] == "POST"
    assert captured["payload"] == {"nextflow_run_id": "run-123"}



def test_running_jobs_request_reads_adapter_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        return _FakeHTTPResponse({"running_jobs": {"job-1": 12345, "job-2": 0}})

    monkeypatch.setattr(workflow_adapter.urllib.request, "urlopen", fake_urlopen)

    running_jobs = workflow_adapter.get_adapter_running_jobs()

    assert running_jobs == {"job-1": 12345, "job-2": 0}
    assert captured["url"] == "http://127.0.0.1:8001/api/workflow-adapter/running-jobs"
    assert captured["method"] == "GET"


def test_generic_adapter_request_reads_json_from_gpu_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://127.0.0.1:8001")

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["payload"] = request.data
        return _FakeHTTPResponse({"gpus": [{"index": 0}], "gpu_error": None})

    monkeypatch.setattr(workflow_adapter.urllib.request, "urlopen", fake_urlopen)

    response = workflow_adapter.request_via_workflow_adapter("GET", "/api/gpu/status")

    assert captured["url"] == "http://127.0.0.1:8001/api/gpu/status"
    assert captured["method"] == "GET"
    assert captured["timeout"] == workflow_adapter.DEFAULT_ADAPTER_TIMEOUT_SECONDS
    assert captured["payload"] is None
    assert response == {"gpus": [{"index": 0}], "gpu_error": None}


def test_workflow_adapter_app_exposes_gpu_routes() -> None:
    routes = {getattr(route, "path", "") for route in workflow_adapter_app.app.routes}

    assert "/api/gpu/status" in routes
    assert "/api/gpu/power-control" in routes
    assert "/api/gpu/scheduler-config" in routes


def test_workflow_adapter_exposes_runtime_state_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import workflow_adapter as workflow_adapter_router

    monkeypatch.setattr(
        workflow_adapter_router,
        "runtime_descriptor",
        lambda runtime_mode=None: {"runtime_mode": runtime_mode, "runtime_active": True},
    )

    client = TestClient(workflow_adapter_app.app)
    response = client.get("/api/workflow-adapter/runtime/state", params={"runtime": "dev"})

    assert response.status_code == 200
    assert response.json() == {"runtime_mode": "dev", "runtime_active": True, "control_mode": "host-adapter"}


def test_workflow_adapter_runtime_state_marks_current_adapter_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import workflow_adapter as workflow_adapter_router

    monkeypatch.setattr(
        workflow_adapter_router,
        "runtime_descriptor",
        lambda runtime_mode=None: {
            "runtime_mode": "container",
            "runtime_active": False,
            "runtime_ready": False,
            "health": {"adapter_ready": False, "api_ready": True, "frontend_ready": True},
            "services": [
                {"name": workflow_adapter_router.WORKFLOW_ADAPTER_SERVICE, "active": False},
                {"name": workflow_adapter_router.CORE_RUNTIME_SERVICE, "active": True},
            ],
        },
    )

    client = TestClient(workflow_adapter_app.app)
    response = client.get("/api/workflow-adapter/runtime/state", params={"runtime": "container"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"] == {"adapter_ready": True, "api_ready": True, "frontend_ready": True}
    assert payload["runtime_ready"] is True
    assert payload["runtime_active"] is True
    assert payload["services"][0] == {
        "name": workflow_adapter_router.WORKFLOW_ADAPTER_SERVICE,
        "active": True,
        "active_source": "current-adapter-process",
    }
    assert payload["control_mode"] == "host-adapter"


def test_workflow_adapter_runtime_action_invokes_mode_scoped_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import workflow_adapter as workflow_adapter_router

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(workflow_adapter_router, "stop_all", lambda runtime_mode=None: calls.append(("stop", runtime_mode)))
    monkeypatch.setattr(
        workflow_adapter_router,
        "runtime_descriptor",
        lambda runtime_mode=None: {"runtime_mode": runtime_mode, "runtime_active": False},
    )

    client = TestClient(workflow_adapter_app.app)
    response = client.post("/api/workflow-adapter/runtime/stop", json={"runtime": "dev"})

    assert response.status_code == 200
    assert calls == [("stop", "dev")]
    assert response.json()["control_mode"] == "host-adapter"
    assert response.json()["action"] == "stop"


def test_workflow_adapter_container_restart_returns_before_self_killing_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from routers import workflow_adapter as workflow_adapter_router

    delayed: list[object] = []
    monkeypatch.setattr(workflow_adapter_router, "_run_delayed", lambda action: delayed.append(action))

    client = TestClient(workflow_adapter_app.app)
    response = client.post("/api/workflow-adapter/runtime/restart", json={"runtime": "container"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["background"] is True
    assert payload["action"] == "restart"
    assert payload["control_mode"] == "host-adapter"
    assert len(delayed) == 1


def test_nextflow_command_uses_authoritative_data_root_for_fresh_work_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / "repo-on-os-drive"
    nvme_root = tmp_path / "BMS-4TB-NVME"
    output_dir = nvme_root / "bms_results" / "job-123"
    project_root.mkdir()
    output_dir.mkdir(parents=True)
    monkeypatch.setattr(nextflow, "PROJECT_ROOT", project_root)
    monkeypatch.setenv("BMS_DATA", str(nvme_root))
    monkeypatch.setenv("BMS_WEIGHTS", str(nvme_root / "weights"))
    monkeypatch.setenv("BMS_COLABFOLD_DB", str(nvme_root / "colabfold_db"))
    monkeypatch.setenv("BMS_MSA_CACHE", str(nvme_root / "msa_cache"))
    monkeypatch.delenv("BMS_CONTAINER_DIR", raising=False)

    cmd = nextflow.build_nextflow_command(
        model_id="boltz_cp_experimental",
        mode="predict",
        params={},
        output_dir=str(output_dir),
        job_id="job-123",
    )

    assert "-w" in cmd
    work_dir = Path(cmd[cmd.index("-w") + 1])
    assert work_dir == nvme_root / "work"
    assert not str(work_dir).startswith(str(project_root))
    assert cmd[cmd.index("--out_dir") + 1] == str(output_dir)


@pytest.mark.asyncio
async def test_gpu_status_routes_to_adapter_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    captured: list[tuple[str, str, object | None]] = []
    expected = {
        "gpus": [{"index": 2, "name": "RTX 3090"}],
        "gpu_error": None,
        "cpu": {"name": "host"},
        "ram": {"total_gb": 125},
        "timestamp": "2026-04-20T14:00:00Z",
        "cpu_history": [1.0],
        "ram_history": [2.0],
    }

    def fake_request(method: str, path: str, payload: object | None = None):
        captured.append((method, path, payload))
        return expected

    monkeypatch.setattr(gpu, "request_via_workflow_adapter", fake_request, raising=False)
    monkeypatch.setattr(gpu, "get_cpu_stats", lambda: (_ for _ in ()).throw(AssertionError("host-local CPU stats should not be used in proxied core-runtime mode")))
    monkeypatch.setattr(gpu, "get_ram_stats", lambda: (_ for _ in ()).throw(AssertionError("host-local RAM stats should not be used in proxied core-runtime mode")))
    monkeypatch.setattr(gpu, "get_gpu_stats_with_error", lambda: (_ for _ in ()).throw(AssertionError("host-local GPU stats should not be used in proxied core-runtime mode")))

    response = await gpu.get_system_status()

    assert response == expected
    assert captured == [("GET", "/api/gpu/status", None)]


@pytest.mark.asyncio
async def test_power_control_routes_to_adapter_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    captured: list[tuple[str, str, object | None]] = []
    expected = {"success": True, "message": "GPU 2 set to 310W"}

    def fake_request(method: str, path: str, payload: object | None = None):
        captured.append((method, path, payload))
        return expected

    monkeypatch.setattr(gpu, "request_via_workflow_adapter", fake_request, raising=False)
    monkeypatch.setattr(gpu, "_set_power_control_sync", lambda _request: (_ for _ in ()).throw(AssertionError("host-local power control should not run in proxied core-runtime mode")))

    response = await gpu.set_power_control(gpu.PowerControlRequest(gpu_index=2, limit_watts=310))

    assert response == expected
    assert captured == [("POST", "/api/gpu/power-control", {"gpu_index": 2, "limit_watts": 310})]


@pytest.mark.asyncio
async def test_power_control_status_routes_to_adapter_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    captured: list[tuple[str, str, object | None]] = []
    expected = {"limits": {2: 310}, "eco_mode": False}

    def fake_request(method: str, path: str, payload: object | None = None):
        captured.append((method, path, payload))
        return expected

    monkeypatch.setattr(gpu, "request_via_workflow_adapter", fake_request, raising=False)
    monkeypatch.setattr(gpu, "_get_power_control_payload", lambda: (_ for _ in ()).throw(AssertionError("host-local power status should not run in proxied core-runtime mode")))

    response = await gpu.get_power_control()

    assert response == expected
    assert captured == [("GET", "/api/gpu/power-control", None)]


@pytest.mark.asyncio
async def test_scheduler_config_routes_to_adapter_in_core_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    captured: list[tuple[str, str, object | None]] = []
    expected = {"global": {"enabled": True}, "overrides": {"2": {"disabled": False}}}

    def fake_request(method: str, path: str, payload: object | None = None):
        captured.append((method, path, payload))
        return expected

    monkeypatch.setattr(gpu, "request_via_workflow_adapter", fake_request, raising=False)
    monkeypatch.setattr(gpu, "read_scheduler_config", lambda: (_ for _ in ()).throw(AssertionError("host-local scheduler config should not run in proxied core-runtime mode")))

    response = await gpu.get_scheduler_config()

    assert response == expected
    assert captured == [("GET", "/api/gpu/scheduler-config", None)]


class _FakeSelect:
    def where(self, *_args, **_kwargs):
        return self


class _FakeSessionResult:
    def __init__(self, job: SimpleNamespace | None) -> None:
        self._job = job

    def scalar_one_or_none(self) -> SimpleNamespace | None:
        return self._job


class _FakeAsyncSession:
    def __init__(self, job: SimpleNamespace | None) -> None:
        self.job = job
        self.commit_count = 0

    async def __aenter__(self) -> "_FakeAsyncSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, *_args, **_kwargs) -> _FakeSessionResult:
        return _FakeSessionResult(self.job)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, _obj: object) -> None:
        return None


class _FakeJobModel:
    id = "id"
    nextflow_run_id = "nextflow_run_id"


class _FakeRunningJobQueryModel:
    queue_status = "queue_status"


@pytest.mark.asyncio
async def test_cancel_nextflow_job_uses_adapter_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    adapter_calls: list[str] = []

    def fake_cancel_via_adapter(nextflow_run_id: str) -> bool:
        adapter_calls.append(nextflow_run_id)
        return True

    monkeypatch.setattr(nextflow, "cancel_via_workflow_adapter", fake_cancel_via_adapter, raising=False)
    monkeypatch.setattr(
        nextflow.os,
        "getpgid",
        lambda _pid: (_ for _ in ()).throw(AssertionError("adapter mode should not inspect host-local process groups")),
    )

    cancelled = await nextflow.cancel_nextflow_job("adapter-run-123")

    assert cancelled is True
    assert adapter_calls == ["adapter-run-123"]


def test_get_running_jobs_prefers_adapter_authoritative_state_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")
    monkeypatch.setattr(nextflow, "_running_processes", {"local-job": SimpleNamespace(pid=999, returncode=None)})
    monkeypatch.setattr(nextflow, "_launching_jobs", {"launching-job"})

    adapter_calls: list[str] = []

    def fake_running_jobs() -> dict[str, int]:
        adapter_calls.append("called")
        return {"adapter-job": 321, "launching-job": 0}

    monkeypatch.setattr(nextflow, "get_adapter_running_jobs", fake_running_jobs, raising=False)

    assert nextflow.get_running_jobs() == {"adapter-job": 321, "launching-job": 0}
    assert adapter_calls == ["called"]


@pytest.mark.asyncio
async def test_launch_nextflow_job_routes_to_adapter_before_local_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    job = SimpleNamespace(
        id="job-123",
        status="queued",
        started_at=None,
        params={},
        batch_id=None,
        current_stage=None,
        queue_status="queued",
        nextflow_run_id=None,
        completed_at=None,
        error_message=None,
        awaiting_input=False,
    )
    session = _FakeAsyncSession(job)

    monkeypatch.setattr(database, "async_session", lambda: session)
    monkeypatch.setattr(database, "Job", _FakeJobModel)
    monkeypatch.setattr(sqlalchemy, "select", lambda *_args, **_kwargs: _FakeSelect())

    async def fake_prepare(params: dict[str, object]):
        return params, []

    async def fake_dynamic_gpu(*_args, **_kwargs):
        return None

    adapter_calls: list[dict[str, object]] = []
    local_spawn_calls: list[tuple[object, ...]] = []

    def fake_adapter_launch(**payload):
        adapter_calls.append(payload)
        return {"accepted": True, "nextflow_run_id": "adapter-run-42"}

    async def fake_create_subprocess_exec(*args, **kwargs):
        local_spawn_calls.append(args)
        raise RuntimeError("adapter mode should not spawn a local nextflow process")

    monkeypatch.setattr(nextflow, "prepare_boltzgen_params_for_launch", fake_prepare)
    monkeypatch.setattr(nextflow, "_is_protenix_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(nextflow, "_resolve_dynamic_gpu_cpu_share", fake_dynamic_gpu)
    monkeypatch.setattr(nextflow, "launch_via_workflow_adapter", fake_adapter_launch, raising=False)
    monkeypatch.setattr(nextflow.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    output_dir = tmp_path / "job-123"

    await nextflow.launch_nextflow_job(
        job_id="job-123",
        model_id="boltz2",
        mode="predict",
        params={"gpu_id": 1, "allow_retries": True},
        output_dir=str(output_dir),
    )

    assert adapter_calls == [
        {
            "job_id": "job-123",
            "model_id": "boltz2",
            "mode": "predict",
            "params": {"gpu_id": 1, "allow_retries": True},
            "output_dir": str(output_dir),
        }
    ]
    assert local_spawn_calls == []
    assert job.nextflow_run_id == "adapter-run-42"
    assert job.status == "running"


@pytest.mark.asyncio
async def test_gpu_orchestrator_skips_host_process_scan_when_adapter_reports_running_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://host.docker.internal:8001")

    job = SimpleNamespace(
        id="job-123",
        name="adapter-job",
        status="running",
        queue_status="running",
        started_at=datetime.utcnow(),
        assigned_gpu=None,
        current_stage=None,
        stage_work_dir=None,
        output_dir=None,
        error_message=None,
    )

    class _FakeRunningRows:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self._rows = rows

        def all(self) -> list[SimpleNamespace]:
            return self._rows

    class _FakeRunningResult:
        def __init__(self, rows: list[SimpleNamespace]) -> None:
            self._rows = rows

        def scalars(self) -> _FakeRunningRows:
            return _FakeRunningRows(self._rows)

    class _FakeRunningSession:
        def __init__(self) -> None:
            self.commit_count = 0

        async def __aenter__(self) -> "_FakeRunningSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def execute(self, *_args, **_kwargs) -> _FakeRunningResult:
            return _FakeRunningResult([job])

        async def commit(self) -> None:
            self.commit_count += 1

    monkeypatch.setattr(database, "Job", _FakeRunningJobQueryModel)
    monkeypatch.setattr(database, "Design", object(), raising=False)
    monkeypatch.setattr(sqlalchemy, "select", lambda *_args, **_kwargs: _FakeSelect())
    monkeypatch.setattr(gpu_orchestrator, "_read_nextflow_history_statuses", lambda _job_ids: {})
    monkeypatch.setattr(nextflow, "get_running_jobs", lambda: {"job-123": 777})

    ps_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_ps_run(*args, **kwargs):
        ps_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(gpu_orchestrator.subprocess, "run", fake_ps_run)

    orchestrator = gpu_orchestrator.GPUOrchestrator(
        db_session_factory=lambda: _FakeRunningSession(),
        get_gpu_stats_fn=lambda: [],
        launch_nextflow_job_fn=lambda **_kwargs: None,
    )

    await orchestrator.check_job_completions()

    assert ps_calls == []
    assert job.status == "running"
    assert job.queue_status == "running"


def test_workflow_adapter_app_exposes_only_adapter_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_CORE_RUNTIME_MODE", raising=False)

    import workflow_adapter_app

    client = TestClient(workflow_adapter_app.app)

    health = client.get("/api/workflow-adapter/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    assert client.get("/api/health").status_code == 404


def test_workflow_adapter_launch_endpoint_schedules_detached_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)

    import workflow_adapter_app

    captured: list[dict[str, object]] = []

    def fake_detached_launch(**payload):
        captured.append(payload)
        return "task-handle"

    monkeypatch.setattr(nextflow, "launch_nextflow_job_detached", fake_detached_launch)

    client = TestClient(workflow_adapter_app.app)
    response = client.post(
        "/api/workflow-adapter/launch",
        json={
            "job_id": "job-123",
            "model_id": "boltz2",
            "mode": "predict",
            "params": {"gpu_id": 1},
            "output_dir": "/tmp/job-123",
        },
    )

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "job_id": "job-123",
        "nextflow_run_id": "job-123",
        "launch_mode": "native-host",
    }
    assert captured == [
        {
            "job_id": "job-123",
            "model_id": "boltz2",
            "mode": "predict",
            "params": {"gpu_id": 1},
            "output_dir": "/tmp/job-123",
            "allow_running_job": True,
        }
    ]


@pytest.mark.asyncio
async def test_launch_nextflow_job_allows_shared_db_prestarted_jobs_when_explicitly_permitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)

    job = SimpleNamespace(
        id="job-123",
        status="running",
        started_at=datetime.utcnow(),
        params={},
        batch_id=None,
        current_stage=None,
        queue_status="running",
        nextflow_run_id=None,
        completed_at=None,
        error_message=None,
        awaiting_input=False,
    )
    session = _FakeAsyncSession(job)

    monkeypatch.setattr(database, "async_session", lambda: session)
    monkeypatch.setattr(database, "Job", _FakeJobModel)
    monkeypatch.setattr(sqlalchemy, "select", lambda *_args, **_kwargs: _FakeSelect())

    launch_calls: list[tuple[str, dict[str, object], str]] = []

    async def fake_launch_msa_batch_job(job_id: str, params: dict[str, object], output_dir: str) -> None:
        launch_calls.append((job_id, params, output_dir))

    monkeypatch.setattr(nextflow, "launch_msa_batch_job", fake_launch_msa_batch_job)

    output_dir = tmp_path / "job-123"
    await nextflow.launch_nextflow_job(
        job_id="job-123",
        model_id="msa_batch",
        mode="predict",
        params={"gpu_id": 1},
        output_dir=str(output_dir),
        allow_running_job=True,
    )

    assert launch_calls == [("job-123", {"gpu_id": 1}, str(output_dir))]


def test_workflow_adapter_cancel_endpoint_resolves_job_handle_to_native_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)

    import workflow_adapter_app
    from routers import workflow_adapter as workflow_adapter_router

    job = SimpleNamespace(id="job-123", nextflow_run_id="12345")

    class _FakeHandleResult:
        def scalar_one_or_none(self) -> SimpleNamespace:
            return job

    class _FakeHandleSession:
        async def __aenter__(self) -> "_FakeHandleSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def execute(self, *_args, **_kwargs) -> _FakeHandleResult:
            return _FakeHandleResult()

    monkeypatch.setattr(database, "async_session", lambda: _FakeHandleSession())
    monkeypatch.setattr(database, "Job", _FakeJobModel)
    monkeypatch.setattr(sqlalchemy, "select", lambda *_args, **_kwargs: _FakeSelect())

    cancelled: list[str] = []

    async def fake_cancel(nextflow_run_id: str) -> bool:
        cancelled.append(nextflow_run_id)
        return True

    monkeypatch.setattr(nextflow, "cancel_nextflow_job", fake_cancel)
    monkeypatch.setattr(workflow_adapter_router, "cancel_nextflow_job", fake_cancel)

    client = TestClient(workflow_adapter_app.app)
    response = client.post(
        "/api/workflow-adapter/cancel",
        json={"nextflow_run_id": "job-123"},
    )

    assert response.status_code == 200
    assert response.json() == {"cancelled": True, "resolved_nextflow_run_id": "12345"}
    assert cancelled == ["12345"]


def test_workflow_adapter_running_jobs_endpoint_reports_nextflow_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)

    import workflow_adapter_app

    monkeypatch.setattr(nextflow, "get_running_jobs", lambda: {"job-1": 111, "job-2": 0})

    client = TestClient(workflow_adapter_app.app)
    response = client.get("/api/workflow-adapter/running-jobs")

    assert response.status_code == 200
    assert response.json() == {"running_jobs": {"job-1": 111, "job-2": 0}}
