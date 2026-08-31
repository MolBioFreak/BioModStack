from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job, JobArtifact, MdEvent, MdReplicaRun, MdRun
from routers import jobs, molecular_dynamics
from routers.molecular_dynamics import FailedLaunchDeleteCommand, LifecycleCommand
from services.md.read_model import md_run_snapshot
from services.md.state import create_md_run, create_replica_attempt


def _contract() -> dict:
    return {
        "schema": "bms.md.job.v2", "engine": "gromacs", "replicas": 1,
        "input": {"structure": "protein.pdb", "structure_sha256": "a" * 64, "nested": {"scientific": "keep"}},
        "chemistry": {"profile_id": "amber", "profile_sha256": "b" * 64, "assurance": "curated", "family": "amber", "custom_scientific_field": "keep"},
        "engine_runtime": {"server": "only"},
    }


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as value:
        yield value
    await engine.dispose()


async def _failed_parent(session, job_id: str, *, output_dir: str | None = None,
                         normalized_request: dict | None = None) -> tuple[Job, MdRun]:
    job = Job(id=job_id, name="failed-md", status="failed", queue_status="failed", model_id="molecular_dynamics", mode="simulate", params={}, output_dir=output_dir)
    session.add(job); await session.flush()
    run = await create_md_run(session, job=job, normalized_request=normalized_request or _contract())
    run.phase = "failed"
    await session.flush()
    return job, run


def test_sanitize_materialized_v2_contract_is_deep_non_mutating() -> None:
    source = _contract()
    sanitized = molecular_dynamics.sanitize_materialized_v2_contract(source)
    assert sanitized is not source
    assert "engine_runtime" not in sanitized
    assert "structure_sha256" not in sanitized["input"]
    assert sanitized["input"]["nested"] == {"scientific": "keep"}
    assert "assurance" not in sanitized["chemistry"]
    assert sanitized["chemistry"]["custom_scientific_field"] == "keep"
    assert source["engine_runtime"] == {"server": "only"}
    assert source["input"]["structure_sha256"] == "a" * 64
    assert source["chemistry"]["assurance"] == "curated"
    sanitized["input"]["nested"]["scientific"] = "changed"
    assert source["input"]["nested"]["scientific"] == "keep"


@pytest.mark.asyncio
async def test_md_logs_are_job_owned_and_never_fall_back_to_global(session, tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "code"; root.mkdir(); (root / ".nextflow.log").write_text("global secret")
    monkeypatch.setattr(jobs, "CODE_ROOT", root)
    no_log = Job(id="md-no-log", name="MD", status="failed", model_id="molecular_dynamics", mode="simulate", params={})
    owned_dir = tmp_path / "owned"; owned_dir.mkdir(); (owned_dir / "nextflow.log").write_text("owned launch log")
    owned = Job(id="md-owned-log", name="MD", status="failed", model_id="molecular_dynamics", mode="simulate", params={}, output_dir=str(owned_dir))
    session.add_all([no_log, owned]); await session.commit()
    assert (await jobs.get_job_logs(no_log.id, session=session))["nextflow_log"] is None
    payload = await jobs.get_job_logs(owned.id, session=session)
    assert payload["nextflow_log"] == "owned launch log"
    assert payload["nextflow_log_source"] == "job_output"


@pytest.mark.asyncio
async def test_remote_logs_never_fall_back_to_local_nextflow_diagnostics(session, tmp_path: Path, monkeypatch) -> None:
    code_root = tmp_path / "code"
    code_root.mkdir()
    (code_root / ".nextflow.log").write_text("host-global-sentinel", encoding="utf-8")
    local_work = tmp_path / "work"
    local_task = local_work / "aa" / "bbbbbb"
    local_task.mkdir(parents=True)
    (local_task / ".command.log").write_text("host-task-sentinel", encoding="utf-8")
    monkeypatch.setattr(jobs, "CODE_ROOT", code_root)
    monkeypatch.setattr(jobs, "get_work_dir", lambda: local_work)

    output = tmp_path / "remote-output"
    returned = output / "_remote"
    returned.mkdir(parents=True)
    (returned / "nextflow.log").write_text("remote-nextflow-log", encoding="utf-8")
    (returned / "supervisor.log").write_text("remote-supervisor-log", encoding="utf-8")
    remote = Job(
        id="remote-log-job",
        name="remote",
        status="failed",
        queue_status="failed",
        model_id="boltz2",
        mode="predict",
        params={},
        output_dir=str(output),
        execution_target_id="vast:123",
    )
    pending = Job(
        id="remote-pending-log-job",
        name="remote pending",
        status="running",
        queue_status="running",
        model_id="boltz2",
        mode="predict",
        params={},
        output_dir=str(tmp_path / "pending-output"),
        execution_target_id="vast:123",
    )
    session.add_all([remote, pending])
    await session.commit()

    payload = await jobs.get_job_logs(remote.id, session=session)
    assert payload["nextflow_log_source"] == "remote_returned"
    assert payload["nextflow_log"] == "remote-nextflow-log"
    assert payload["command_log"] == "remote-supervisor-log"
    assert "host-global-sentinel" not in str(payload)
    assert "host-task-sentinel" not in str(payload)
    pending_payload = await jobs.get_job_logs(pending.id, session=session)
    assert pending_payload["nextflow_log_source"] == "remote_pending"
    assert pending_payload["nextflow_log"] is None
    assert pending_payload["command_log"] is None


@pytest.mark.asyncio
async def test_failed_parent_snapshot_only_offers_relaunch_before_any_replica(session) -> None:
    parent, _ = await _failed_parent(session, "failed-empty")
    snapshot = await md_run_snapshot(session, parent.id)
    assert {"view_logs", "reorchestrate", "delete_failed_launch"}.issubset(snapshot["allowed_actions"])
    assert snapshot["action_explanations"]["resume_dynamics"]
    _, historical_run = await _failed_parent(session, "failed-history")
    historical_run.phase = "replicas_running"
    await session.flush()
    replica, _ = await create_replica_attempt(session, job_id="failed-history", replica_index=0, attempt=0, engine="gromacs", execution_plan_sha256="c" * 64, compatibility_key="d" * 64)
    replica.state = "failed"; replica.active = False; historical_run.phase = "failed"; await session.flush()
    historical = await md_run_snapshot(session, "failed-history")
    assert "reorchestrate" not in historical["allowed_actions"]
    assert "delete_failed_launch" not in historical["allowed_actions"]


@pytest.mark.asyncio
async def test_reorchestrate_is_versioned_idempotent_and_preserves_lineage(session, tmp_path: Path, monkeypatch) -> None:
    results = tmp_path / "results"; results.mkdir()
    old_output = results / "failed-source"; inputs = old_output / "inputs"; inputs.mkdir(parents=True)
    snapshot = inputs / "structure.pdb"; snapshot.write_bytes(b"immutable-structure")
    contract = _contract(); contract["input"].update({
        "structure": str(snapshot),
        "structure_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "structure_bytes": snapshot.stat().st_size,
    })
    monkeypatch.setattr(molecular_dynamics, "get_results_dir", lambda: results)
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results)
    parent, run = await _failed_parent(
        session, "failed-source", output_dir=str(old_output), normalized_request=contract,
    )
    parent.lineage_root_job_id = "lineage-root"; parent.stage_family = "md"; parent.stage_mode = "simulate"
    captured = {}
    async def fake_create_job(job_data, _background, db, *, _preallocated_job_id, _commit, _md_output_creation, _md_input_resolver):
        captured["params"] = job_data.params
        assert _md_input_resolver(str(snapshot)) == str(snapshot.resolve())
        output = tmp_path / _preallocated_job_id; output.mkdir()
        _md_output_creation.update({"path": output, "created": True})
        created = Job(id=_preallocated_job_id, name=job_data.name, status="queued", queue_status="queued", model_id="molecular_dynamics", mode="simulate", params=job_data.params, output_dir=str(output), lineage_root_job_id=job_data.params["lineage_root_job_id"], source_stage_job_id=job_data.params["source_stage_job_id"])
        db.add(created); await db.flush()
        await create_md_run(db, job=created, normalized_request=contract)
        return SimpleNamespace(id=created.id)
    monkeypatch.setattr(jobs, "create_job", fake_create_job)
    command = LifecycleCommand(expected_state_version=run.state_version, idempotency_key="replay-key")
    first = await molecular_dynamics.reorchestrate_failed_md_run(parent.id, command, session)
    expected = str(molecular_dynamics.uuid.uuid5(molecular_dynamics.uuid.NAMESPACE_URL, f"bms-md-reorchestrate:{parent.id}:replay-key"))
    assert first["new_job_id"] == expected and first["replayed"] is False
    assert captured["params"]["lineage_root_job_id"] == "lineage-root"
    assert captured["params"]["source_stage_job_id"] == parent.id
    assert (await session.get(Job, expected)).provenance["reorchestrated_from_job_id"] == parent.id
    replay = await molecular_dynamics.reorchestrate_failed_md_run(parent.id, command, session)
    assert replay["replayed"] is True and replay["new_job_id"] == expected
    assert await session.scalar(select(MdEvent).where(MdEvent.idempotency_key == "replay-key"))


@pytest.mark.asyncio
async def test_delete_failed_launch_requires_empty_terminal_canonical_root(session, tmp_path: Path, monkeypatch) -> None:
    results = tmp_path / "Development" / "bms_results"; results.mkdir(parents=True)
    monkeypatch.setattr(molecular_dynamics, "get_results_dir", lambda: results)
    root = results / "delete-me"; root.mkdir(); (root / "nextflow.log").write_text("failed")
    parent, run = await _failed_parent(session, "delete-me", output_dir=str(root))
    receipt = await molecular_dynamics.delete_failed_md_launch(parent.id, FailedLaunchDeleteCommand(expected_state_version=run.state_version), session)
    assert receipt["deleted"] is True and not root.exists() and await session.get(Job, parent.id) is None
    blocked, blocked_run = await _failed_parent(session, "blocked", output_dir=str(results / "blocked"))
    session.add(JobArtifact(id="artifact", owner_job_id=blocked.id, logical_path="x", storage_path="x", sha256="e" * 64, bytes=1, media_type="text/plain", provenance={}))
    await session.flush()
    with pytest.raises(HTTPException, match="MD_PRE_REPLICA_TERMINAL_REQUIRED"):
        await molecular_dynamics.delete_failed_md_launch(blocked.id, FailedLaunchDeleteCommand(expected_state_version=blocked_run.state_version), session)
