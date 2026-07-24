from __future__ import annotations

import json
import os
import subprocess
import sys
import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
for candidate in (str(API_ROOT), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from database import Base, Job  # noqa: E402
from routers import jobs as jobs_router  # noqa: E402
from routers import md_results as md_results_router  # noqa: E402
from schemas import JobCreate  # noqa: E402
from services.gpu_orchestrator import GPUOrchestrator  # noqa: E402
from services.md.lifecycle import reconcile_md_analysis_parent  # noqa: E402
from scripts.bms_md import aggregate_children as aggregate_module  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_completed_replica(parent_root: Path, parent_id: str) -> tuple[Path, str]:
    replica_root = parent_root / "replicas" / "replica_0"
    replica_root.mkdir(parents=True)
    trajectory = replica_root / "production.xtc"
    trajectory.write_bytes(b"immutable trajectory\n")
    manifest = replica_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "bms.md.run.v1",
                "status": "completed",
                "job_id": parent_id,
                "replica_index": 0,
                "artifacts": {
                    "trajectory": {
                        "path": trajectory.name,
                        "bytes": trajectory.stat().st_size,
                        "sha256": _sha256(trajectory),
                    }
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    aggregate = parent_root / "manifest.json"
    aggregate.write_text(
        json.dumps(
            {
                "schema": "bms.md.aggregate.v1",
                "status": "completed",
                "job_id": parent_id,
                "replicas": [{"replica_index": 0}],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest, _sha256(manifest)


class _AcceptingRegistry:
    def reload(self) -> None:
        return None

    def validate_job_params(self, *_args: object, **_kwargs: object) -> list[str]:
        return []


@pytest.mark.asyncio
async def test_analysis_child_is_validated_persisted_cpu_only_and_launched_without_gpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    parent_id = "11111111-1111-1111-1111-111111111111"
    parent_root = tmp_path / "results" / "parent"
    parent_root.mkdir(parents=True)
    replica_manifest, replica_manifest_sha256 = _write_completed_replica(parent_root, parent_id)
    work_item = parent_root / "orchestration" / "analysis_work_items" / "replica_0.json"
    work_item.parent.mkdir(parents=True)
    work_item.write_text(
        json.dumps(
            {
                "schema": "bms.md.analysis-work-item.v1",
                "job_id": parent_id,
                "replica_index": 0,
                "manifest": str(replica_manifest),
                "manifest_sha256": replica_manifest_sha256,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    async with sessions() as session:
        session.add(
            Job(
                id=parent_id,
                name="MD parent",
                status="running",
                model_id="molecular_dynamics",
                mode="simulate",
                params={},
                output_dir=str(parent_root),
                queue_status="running",
                vram_estimate_mb=0,
            )
        )
        await session.commit()

    monkeypatch.setattr(jobs_router, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs_router, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs_router, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(jobs_router, "get_results_dir", lambda: tmp_path / "results")

    async with sessions() as session:
        response = await jobs_router.create_job(
            JobCreate(
                name="MD parent - analysis 1/1",
                model_id="molecular_dynamics",
                mode="analyze",
                params={
                    "md_analysis_work_item": str(work_item),
                    "md_analysis_sif_sha256": "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68",
                },
                parent_job_id=parent_id,
                batch_id=parent_id,
                batch_name="MD parent",
                child_stage="md_analysis",
            ),
            BackgroundTasks(),
            session,
        )
        child = await session.get(Job, response.id)
        assert child is not None
        assert child.vram_estimate_mb == 0
        assert child.pinned_gpu is None
        assert child.params["md_replica_manifest_sha256"] == replica_manifest_sha256

    launched: list[dict[str, object]] = []

    async def launch(**kwargs: object) -> None:
        launched.append(kwargs)

    orchestrator = GPUOrchestrator(sessions, lambda: [], launch)
    await orchestrator._process_cycle()

    assert len(launched) == 1
    assert launched[0]["mode"] == "analyze"
    assert "gpu_id" not in launched[0]["params"]
    async with sessions() as session:
        child = await session.get(Job, response.id)
        assert child is not None
        assert child.queue_status == "running"
        assert child.assigned_gpu is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_analysis_child_rejects_any_requested_gpu_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_router, "require_molecular_dynamics_feature", lambda _model_id: None)
    monkeypatch.setattr(jobs_router, "_raise_if_workflow_launches_disabled", lambda _action: None)
    monkeypatch.setattr(jobs_router, "get_registry", lambda: _AcceptingRegistry())
    with pytest.raises(HTTPException) as error:
        await jobs_router.create_job(
            JobCreate(
                name="invalid analysis",
                model_id="molecular_dynamics",
                mode="analyze",
                params={"md_analysis_work_item": str(tmp_path / "item.json")},
                pinned_gpu=0,
                parent_job_id="11111111-1111-1111-1111-111111111111",
                child_stage="md_analysis",
            ),
            BackgroundTasks(),
            object(),
        )

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "MD_ANALYSIS_GPU_FORBIDDEN"


@pytest.mark.asyncio
async def test_scheduler_rejects_legacy_analysis_row_with_gpu_assignment(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    child_id = "22222222-2222-2222-2222-222222222222"
    async with sessions() as session:
        session.add(
            Job(
                id=child_id,
                name="legacy GPU analysis",
                status="queued",
                model_id="molecular_dynamics",
                mode="analyze",
                params={"gpu_id": 0},
                output_dir=str(tmp_path / "analysis"),
                queue_status="queued",
                vram_estimate_mb=1024,
                pinned_gpu=0,
            )
        )
        await session.commit()

    launched: list[dict[str, object]] = []

    async def launch(**kwargs: object) -> None:
        launched.append(kwargs)

    gpu = SimpleNamespace(
        index=0,
        name="test-gpu",
        memory_used_mb=0,
        memory_total_mb=24_576,
        memory_free_mb=24_576,
        utilization=0,
        temperature=30,
        processes=[],
    )
    orchestrator = GPUOrchestrator(sessions, lambda: [gpu], launch)
    await orchestrator._process_cycle()

    assert launched == []
    async with sessions() as session:
        child = await session.get(Job, child_id)
        assert child is not None
        assert child.queue_status == "failed"
        assert child.assigned_gpu is None
        assert child.error_message == "MD_ANALYSIS_GPU_FORBIDDEN"
    await engine.dispose()


def test_replica_collection_is_idempotent_and_conflicting_replay_is_non_destructive(tmp_path: Path) -> None:
    from scripts.bms_md.aggregate_children import collect_children

    parent_id = "md-parent-immutable"
    child_root = tmp_path / "child"
    replica_manifest, _manifest_sha = _write_completed_replica(child_root, parent_id)
    status_path = tmp_path / "replica_children.json"
    status_path.write_text(
        json.dumps(
            {
                "total": 1,
                "completed": 1,
                "failed": 0,
                "cancelled": 0,
                "child_ids": ["replica-child-1"],
                "child_output_dirs": [str(child_root)],
            }
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "parent"

    collect_children(status_path, destination)
    accepted = destination / "replicas" / "replica_0" / "production.xtc"
    accepted_inode = accepted.stat().st_ino
    accepted_bytes = accepted.read_bytes()
    collect_children(status_path, destination)
    assert accepted.stat().st_ino == accepted_inode
    assert accepted.read_bytes() == accepted_bytes

    source_trajectory = replica_manifest.parent / "production.xtc"
    source_trajectory.write_bytes(b"conflicting dynamics\n")
    replica = json.loads(replica_manifest.read_text(encoding="utf-8"))
    replica["artifacts"]["trajectory"].update(
        bytes=source_trajectory.stat().st_size,
        sha256=_sha256(source_trajectory),
    )
    replica_manifest.write_text(json.dumps(replica, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as conflict:
        collect_children(status_path, destination)
    assert getattr(conflict.value, "code", None) == "MD_IMMUTABLE_COLLECTION_CONFLICT"
    assert accepted.read_bytes() == accepted_bytes
    assert accepted.stat().st_ino == accepted_inode


def test_analysis_spawn_retry_schedules_only_analysis_and_preserves_dynamics_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        spawn_module = importlib.import_module("scripts.bms_md.spawn_analysis")
    except ModuleNotFoundError:
        pytest.fail("durable MD analysis child spawner is not implemented")

    parent_id = "md-parent-retry"
    parent_root = tmp_path / "parent"
    replica_manifest, replica_manifest_sha256 = _write_completed_replica(parent_root, parent_id)
    dynamics_before = {
        path.relative_to(parent_root).as_posix(): _sha256(path)
        for path in sorted((parent_root / "replicas").rglob("*"))
        if path.is_file()
    }
    posted: list[dict[str, object]] = []

    class _Response:
        ok = True
        status_code = 201
        text = ""

        def __init__(self, child_id: str) -> None:
            self.child_id = child_id

        def json(self) -> dict[str, str]:
            return {"id": self.child_id, "name": "analysis", "status": "queued"}

    def post(_url: str, *, json: dict[str, object], timeout: int) -> _Response:
        assert timeout == 30
        posted.append(json)
        return _Response(f"analysis-attempt-{len(posted)}")

    monkeypatch.setattr(spawn_module.requests, "post", post)
    kwargs = {
        "parent_job_id": parent_id,
        "parent_name": "MD parent",
        "aggregate_manifest": parent_root / "manifest.json",
        "api_url": "http://api.invalid",
        "work_item_dir": parent_root / "orchestration" / "analysis_work_items",
        "runtime_sha256": "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68",
    }
    first = spawn_module.spawn_analysis(**kwargs)
    second = spawn_module.spawn_analysis(**kwargs)

    assert [payload["mode"] for payload in posted] == ["analyze", "analyze"]
    assert all(payload["child_stage"] == "md_analysis" for payload in posted)
    assert all(payload["params"]["md_replica_manifest_sha256"] == replica_manifest_sha256 for payload in posted)
    assert first["replica_manifest_set_sha256"] == second["replica_manifest_set_sha256"]
    assert not any(payload["mode"] == "replica" for payload in posted)
    dynamics_after = {
        path.relative_to(parent_root).as_posix(): _sha256(path)
        for path in sorted((parent_root / "replicas").rglob("*"))
        if path.is_file()
    }
    assert dynamics_after == dynamics_before
    assert _sha256(replica_manifest) == replica_manifest_sha256


def test_analysis_collection_is_atomic_idempotent_and_conflict_preserving(tmp_path: Path) -> None:
    try:
        collect_module = importlib.import_module("scripts.bms_md.collect_analysis")
    except ModuleNotFoundError:
        pytest.fail("durable MD analysis collector is not implemented")

    parent_id = "md-parent-analysis-collection"
    parent_root = tmp_path / "parent"
    replica_manifest, replica_manifest_sha256 = _write_completed_replica(parent_root, parent_id)
    child_root = tmp_path / "analysis-child" / "analysis"
    child_root.mkdir(parents=True)
    report = child_root / "md_analysis_replica_0.json"
    report.write_bytes(b'{"schema":"bms.md.analysis.v1","status":"completed"}\n')
    sidecar = child_root / "md_analysis_replica_0.artifacts.json"
    sidecar.write_text(
        json.dumps(
            {
                "schema": "bms.md.analysis-artifacts.v1",
                "status": "completed",
                "job_id": parent_id,
                "replica": 0,
                "input_manifest_sha256": replica_manifest_sha256,
                "artifacts": {
                    "report": {
                        "path": report.name,
                        "bytes": report.stat().st_size,
                        "sha256": _sha256(report),
                        "semantic_role": "md_analysis_report",
                    }
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    status_path = tmp_path / "analysis_children.json"
    status_path.write_text(
        json.dumps(
            {
                "total": 1,
                "completed": 1,
                "failed": 0,
                "cancelled": 0,
                "child_ids": ["analysis-child-1"],
                "child_output_dirs": [str(child_root.parent)],
            }
        ),
        encoding="utf-8",
    )

    collection = collect_module.collect_analysis(status_path, parent_root / "manifest.json", parent_root)
    accepted = parent_root / "analysis" / report.name
    accepted_inode = accepted.stat().st_ino
    accepted_bytes = accepted.read_bytes()
    replay = collect_module.collect_analysis(status_path, parent_root / "manifest.json", parent_root)
    assert replay == collection
    assert accepted.stat().st_ino == accepted_inode

    report.write_bytes(b'{"schema":"bms.md.analysis.v1","status":"failed"}\n')
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["artifacts"]["report"].update(bytes=report.stat().st_size, sha256=_sha256(report))
    sidecar.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as conflict:
        collect_module.collect_analysis(status_path, parent_root / "manifest.json", parent_root)
    assert getattr(conflict.value, "code", None) == "MD_IMMUTABLE_COLLECTION_CONFLICT"
    assert accepted.read_bytes() == accepted_bytes
    assert accepted.stat().st_ino == accepted_inode
    assert _sha256(replica_manifest) == replica_manifest_sha256

def test_partial_md_collection_publishes_manifest_before_parent_terminal_failure(tmp_path: Path) -> None:
    child_dir = tmp_path / "child-0" / "replicas" / "replica_0"
    child_dir.mkdir(parents=True)
    (child_dir / "production.xtc").write_bytes(b"trajectory")
    (child_dir / "production.cpt").write_bytes(b"checkpoint")
    (child_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "bms.md.run.v1",
                "status": "completed",
                "job_id": "md-parent-1",
                "replica_index": 0,
                "replica_seed": 20260717,
                "engine": "gromacs",
                "artifacts": {
                    "trajectory": {"path": "production.xtc"},
                    "checkpoint": {"path": "production.cpt"},
                },
            }
        ),
        encoding="utf-8",
    )
    child_status = tmp_path / "child_outputs.json"
    child_status.write_text(
        json.dumps(
            {
                "total": 2,
                "completed": 1,
                "failed": 1,
                "cancelled": 0,
                "child_ids": ["child-0", "child-1"],
                "child_output_dirs": [str(tmp_path / "child-0")],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "aggregate"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.bms_md.aggregate_children",
            "--child-status",
            str(child_status),
            "--output-dir",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial_failure"
    assert manifest["lineage"]["failed_children"] == 1
    assert (output_dir / "replicas" / "replica_0" / "production.xtc").is_file()


@pytest.mark.asyncio
async def test_md_lifecycle_projects_partial_analysis_without_overwriting_dynamics(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    parent_root = tmp_path / "parent"
    replica_root = parent_root / "replicas" / "replica_0"
    replica_root.mkdir(parents=True)
    replica_manifest = replica_root / "manifest.json"
    replica_manifest.write_text("{}\n", encoding="utf-8")
    (parent_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "bms.md.aggregate.v1",
                "status": "completed",
                "job_id": "parent",
                "replicas": [{"replica_index": 0}],
            }
        ),
        encoding="utf-8",
    )
    async with sessions() as session:
        session.add_all(
            [
                Job(id="parent", name="md", model_id="molecular_dynamics", mode="simulate", params={}, status="running", output_dir=str(parent_root)),
                Job(
                    id="analysis-0",
                    name="analysis",
                    model_id="molecular_dynamics",
                    mode="analyze",
                    params={"md_replica_index": 0},
                    status="failed",
                    parent_job_id="parent",
                    child_stage="md_analysis",
                    output_dir=str(tmp_path / "analysis-0"),
                ),
            ]
        )
        await session.commit()
        state = await reconcile_md_analysis_parent("parent", session)
        await session.commit()
        parent = await session.get(Job, "parent")
        assert state["status"] == "partial_failure"
        assert parent is not None and parent.status == "failed"
        assert parent.provenance["md"]["dynamics_state"] == "completed"
        assert parent.provenance["md"]["analysis_state"] == "failed"
        assert parent.provenance["md"]["result_state"] == "partial"
        assert parent.provenance["md"]["analysis_child_ids"] == ["analysis-0"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_superseded_active_analysis_blocks_lifecycle_and_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'active-overlap.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    parent_id = "77777777-7777-7777-7777-777777777777"
    parent_root = tmp_path / "active-overlap-parent"
    parent_root.mkdir()
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    _manifest, manifest_sha256 = _write_completed_replica(parent_root, parent_id)
    manifest_set_sha256 = hashlib.sha256(json.dumps([(0, manifest_sha256)], separators=(",", ":")).encode()).hexdigest()
    aggregate_sha256 = hashlib.sha256((parent_root / "manifest.json").read_bytes()).hexdigest()
    async with sessions() as session:
        session.add_all([
            Job(id=parent_id, name="md", model_id="molecular_dynamics", mode="simulate", params={}, status="running", output_dir=str(parent_root), provenance={"md": {"dynamics_state": "completed", "analysis_state": "failed", "aggregate_manifest_sha256": aggregate_sha256, "replica_manifest_set_sha256": manifest_set_sha256}}),
            Job(id="analysis-old-running", name="old", model_id="molecular_dynamics", mode="analyze", child_stage="md_analysis", parent_job_id=parent_id, params={"md_replica_index": 0}, status="running", queue_status="running"),
            Job(id="analysis-new-failed", name="new", model_id="molecular_dynamics", mode="analyze", child_stage="md_analysis", parent_job_id=parent_id, params={"md_replica_index": 0}, status="failed", queue_status="failed"),
        ])
        await session.commit()
        state = await reconcile_md_analysis_parent(parent_id, session)
        assert state["status"] == "waiting"
        parent = await session.get(Job, parent_id)
        assert parent is not None and parent.status == "running"
        with pytest.raises(HTTPException) as exc_info:
            await md_results_router.retry_md_analysis(parent_id, session)
        assert exc_info.value.detail == {"code": "MD_ANALYSIS_RETRY_ACTIVE", "message": "An MD analysis retry is already active"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_endpoint_schedules_cpu_analysis_only_and_preserves_replica_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    parent_id = "33333333-3333-3333-3333-333333333333"
    parent_root = tmp_path / "parent"
    parent_root.mkdir()
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    replica_manifest, replica_sha256 = _write_completed_replica(parent_root, parent_id)
    captured: list[JobCreate] = []

    async def fake_create_job(job_data: JobCreate, _background: BackgroundTasks, _session: AsyncSession):
        captured.append(job_data)
        return type("Created", (), {"id": "analysis-retry-0"})()

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    async with sessions() as session:
        accepted_set_sha256 = hashlib.sha256(
            json.dumps([(0, replica_sha256)], separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        aggregate_sha256 = hashlib.sha256((parent_root / "manifest.json").read_bytes()).hexdigest()
        session.add(
            Job(
                id=parent_id,
                name="md",
                model_id="molecular_dynamics",
                mode="simulate",
                params={},
                provenance={"md": {"dynamics_state": "completed", "analysis_state": "failed", "aggregate_manifest_sha256": aggregate_sha256, "replica_manifest_set_sha256": accepted_set_sha256}},
                status="failed",
                output_dir=str(parent_root),
            )
        )
        await session.commit()
        response = await md_results_router.retry_md_analysis(parent_id, session)
        parent = await session.get(Job, parent_id)
        assert response["created_child_ids"] == ["analysis-retry-0"]
        assert len(captured) == 1
        retry_job = captured[0]
        assert retry_job.mode == "analyze"
        assert retry_job.child_stage == "md_analysis"
        assert retry_job.parent_job_id == parent_id
        assert retry_job.pinned_gpu is None
        assert retry_job.params["md_replica_manifest_sha256"] == replica_sha256
        assert _sha256(replica_manifest) == replica_sha256
        assert parent is not None and parent.provenance["md"]["dynamics_state"] == "completed"
        assert parent.provenance["md"]["analysis_state"] == "retrying"
        provenance = dict(parent.provenance)
        md = dict(provenance["md"])
        md.pop("aggregate_manifest_sha256")
        md["analysis_state"] = "failed"
        provenance["md"] = md
        parent.provenance = provenance
        parent.status = "failed"
        parent.queue_status = "failed"
        await session.commit()
        eligibility = await md_results_router.get_md_analysis(parent_id, session)
        assert eligibility["retry"]["reason"] == "dynamics_generation_changed"
        with pytest.raises(HTTPException) as missing_generation:
            await md_results_router.retry_md_analysis(parent_id, session)
        assert isinstance(missing_generation.value.detail, dict)
        assert missing_generation.value.detail["code"] == "MD_DYNAMICS_GENERATION_CHANGED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_rejects_a_changed_dynamics_generation_without_creating_a_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'changed-retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    parent_id = "44444444-4444-4444-4444-444444444444"
    parent_root = tmp_path / "changed-parent"
    parent_root.mkdir()
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    manifest_path, accepted_manifest_sha256 = _write_completed_replica(parent_root, parent_id)
    accepted_set_sha256 = hashlib.sha256(
        json.dumps([(0, accepted_manifest_sha256)], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    aggregate_sha256 = hashlib.sha256((parent_root / "manifest.json").read_bytes()).hexdigest()
    trajectory = parent_root / "replicas" / "replica_0" / "production.xtc"
    trajectory.write_bytes(b"changed dynamics\n")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["trajectory"]["bytes"] = trajectory.stat().st_size
    manifest["artifacts"]["trajectory"]["sha256"] = _sha256(trajectory)
    if "representative_structure" in manifest["artifacts"]:
        manifest["artifacts"]["representative_structure"]["source_trajectory_sha256"] = _sha256(trajectory)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    aggregate_path = parent_root / "manifest.json"
    changed_aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    changed_aggregate["replicas"][0]["manifest_sha256"] = _sha256(manifest_path)
    aggregate_path.write_text(json.dumps(changed_aggregate, sort_keys=True) + "\n", encoding="utf-8")
    created: list[JobCreate] = []

    async def fake_create_job(job_data: JobCreate, _background: BackgroundTasks, _session: AsyncSession):
        created.append(job_data)
        return SimpleNamespace(id="must-not-exist")

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    async with sessions() as session:
        session.add_all(
            [
                Job(
                    id=parent_id, name="md", model_id="molecular_dynamics", mode="simulate", params={},
                    provenance={"md": {"dynamics_state": "completed", "analysis_state": "failed", "aggregate_manifest_sha256": aggregate_sha256, "replica_manifest_set_sha256": accepted_set_sha256}},
                    status="failed", output_dir=str(parent_root),
                ),
                Job(
                    id="55555555-5555-5555-5555-555555555555", name="analysis", model_id="molecular_dynamics",
                    mode="analyze", child_stage="md_analysis", parent_job_id=parent_id, status="failed",
                    params={"md_replica_index": 0, "md_replica_manifest_sha256": accepted_manifest_sha256, "md_replica_manifest_set_sha256": accepted_set_sha256},
                ),
            ]
        )
        await session.commit()
        eligibility = await md_results_router.get_md_analysis(parent_id, session)
        assert eligibility["retry"] == {
            "eligible": False,
            "active": False,
            "reason": "dynamics_generation_changed",
        }
        with pytest.raises(HTTPException) as exc_info:
            await md_results_router.retry_md_analysis(parent_id, session)
        assert isinstance(exc_info.value.detail, dict)
        assert exc_info.value.detail["code"] == "MD_DYNAMICS_GENERATION_CHANGED"
        assert created == []
        assert (await session.get(Job, parent_id)).status == "failed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_child_creation_failure_restores_a_truthful_recoverable_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failed-scheduling.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    parent_id = "66666666-6666-6666-6666-666666666666"
    parent_root = tmp_path / "failed-scheduling-parent"
    parent_root.mkdir()
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    _manifest, manifest_sha256 = _write_completed_replica(parent_root, parent_id)
    manifest_set_sha256 = hashlib.sha256(
        json.dumps([(0, manifest_sha256)], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    aggregate_sha256 = hashlib.sha256((parent_root / "manifest.json").read_bytes()).hexdigest()

    async def fail_create_job(_job_data: JobCreate, _background: BackgroundTasks, _session: AsyncSession):
        raise RuntimeError("injected scheduling failure")

    monkeypatch.setattr(jobs_router, "create_job", fail_create_job)
    async with sessions() as session:
        session.add(
            Job(
                id=parent_id, name="md", model_id="molecular_dynamics", mode="simulate", params={},
                provenance={"md": {"dynamics_state": "completed", "analysis_state": "failed", "aggregate_manifest_sha256": aggregate_sha256, "replica_manifest_set_sha256": manifest_set_sha256}},
                status="failed", queue_status="failed", output_dir=str(parent_root),
            )
        )
        await session.commit()
        with pytest.raises(HTTPException) as exc_info:
            await md_results_router.retry_md_analysis(parent_id, session)
        assert exc_info.value.status_code == 500
        session.expire_all()
        parent = await session.get(Job, parent_id)
        assert parent is not None and parent.status == "failed" and parent.queue_status == "failed"
        assert parent.provenance["md"]["dynamics_state"] == "completed"
        assert parent.provenance["md"]["analysis_state"] == "failed"
        assert parent.error_message == "MD_ANALYSIS_RETRY_SCHEDULING_FAILED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_partial_multi_replica_retry_admission_cancels_created_children_and_remains_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'partial-retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    parent_id = "88888888-8888-8888-8888-888888888888"
    parent_root = tmp_path / "partial-retry-parent"
    parent_root.mkdir()
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path))
    manifest0, digest0 = _write_completed_replica(parent_root, parent_id)
    replica1 = parent_root / "replicas" / "replica_1"
    replica1.mkdir()
    trajectory1 = replica1 / "production.xtc"
    trajectory1.write_bytes(b"immutable trajectory replica 1\n")
    manifest1 = replica1 / "manifest.json"
    manifest1.write_text(json.dumps({"schema": "bms.md.run.v1", "status": "completed", "job_id": parent_id, "replica_index": 1, "artifacts": {"trajectory": {"path": trajectory1.name, "bytes": trajectory1.stat().st_size, "sha256": _sha256(trajectory1)}}}, sort_keys=True) + "\n")
    digest1 = _sha256(manifest1)
    aggregate_path = parent_root / "manifest.json"
    aggregate = json.loads(aggregate_path.read_text())
    aggregate["replicas"] = [{"replica_index": 0}, {"replica_index": 1}]
    aggregate_path.write_text(json.dumps(aggregate, sort_keys=True) + "\n")
    manifest_set = hashlib.sha256(json.dumps([(0, digest0), (1, digest1)], separators=(",", ":")).encode()).hexdigest()
    aggregate_sha = _sha256(aggregate_path)
    calls = 0

    async def create_then_fail(job_data: JobCreate, _background: BackgroundTasks, session: AsyncSession):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second-lane failure")
        child = Job(
            id="99999999-9999-9999-9999-999999999999", name=job_data.name,
            model_id=job_data.model_id, mode=job_data.mode, child_stage=job_data.child_stage,
            parent_job_id=job_data.parent_job_id, params=job_data.params,
            status="queued", queue_status="queued",
        )
        session.add(child)
        await session.commit()
        return child

    monkeypatch.setattr(jobs_router, "create_job", create_then_fail)
    async with sessions() as session:
        session.add(Job(id=parent_id, name="md", model_id="molecular_dynamics", mode="simulate", params={}, status="failed", queue_status="failed", output_dir=str(parent_root), provenance={"md": {"dynamics_state": "completed", "analysis_state": "failed", "aggregate_manifest_sha256": aggregate_sha, "replica_manifest_set_sha256": manifest_set}}))
        await session.commit()
        with pytest.raises(HTTPException) as first_failure:
            await md_results_router.retry_md_analysis(parent_id, session)
        assert first_failure.value.status_code == 500
        partial = await session.get(Job, "99999999-9999-9999-9999-999999999999")
        assert partial is not None and partial.status == "cancelled" and partial.queue_status == "cancelled"

        retry_ids = iter(["retry-lane-0", "retry-lane-1"])
        async def replayable_create(_job_data: JobCreate, _background: BackgroundTasks, _session: AsyncSession):
            return SimpleNamespace(id=next(retry_ids))
        monkeypatch.setattr(jobs_router, "create_job", replayable_create)
        response = await md_results_router.retry_md_analysis(parent_id, session)
        assert response["created_child_ids"] == ["retry-lane-0", "retry-lane-1"]
        assert _sha256(manifest0) == digest0
        assert _sha256(manifest1) == digest1
    await engine.dispose()


def test_immutable_publication_copies_the_verified_descriptor_during_source_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    attacker = tmp_path / "attacker.bin"
    destination = tmp_path / "accepted.bin"
    trusted = b"trusted-analysis-generation"
    source.write_bytes(trusted)
    attacker.write_bytes(b"replacement-generation")
    expected = hashlib.sha256(trusted).hexdigest()
    real_read = aggregate_module.os.read
    swapped = False

    def replace_after_open(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        data = real_read(descriptor, count)
        if not swapped:
            swapped = True
            attacker.replace(source)
        return data

    monkeypatch.setattr(aggregate_module.os, "read", replace_after_open)
    aggregate_module.publish_file_immutable(
        source,
        destination,
        expected_size=len(trusted),
        expected_sha256=expected,
    )
    assert source.read_bytes() == b"replacement-generation"
    assert destination.read_bytes() == trusted
