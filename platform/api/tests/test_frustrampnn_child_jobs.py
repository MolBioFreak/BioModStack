from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from routers.frustrampnn import AnalyzeDesignsRequest, ReanalyzeRequest, router
from services import nextflow as nextflow_service
from services.frustrampnn import jobs as child_jobs


def _pdb() -> bytes:
    lines: list[str] = []
    for serial, (atom, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1):
        atom_field = f" {atom:<3}"
        lines.append(
            f"ATOM  {serial:5d} {atom_field} GLY A   1    {serial:8.3f}{serial + 1:8.3f}"
            f"{serial + 2:8.3f}{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


@pytest_asyncio.fixture
async def child_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(child_jobs, "get_results_dir", lambda: results)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'children.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions, results
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_child_creation_commits_immutable_authority_and_builds_scheduler_handoff(
    child_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, results = child_db
    selection = child_jobs.upload_selection(
        filename="candidate.pdb",
        payload=_pdb(),
        expected_sha256=hashlib.sha256(_pdb()).hexdigest(),
    )
    async with sessions() as session:
        child = await child_jobs.create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
        )
        child_id = child.id

    async with sessions() as session:
        persisted = await session.get(Job, child_id)
        assert persisted is not None
        assert persisted.status == persisted.queue_status == "queued"
        assert persisted.sequence_length == 1
        assert persisted.vram_estimate_mb is not None
        assert persisted.vram_estimate_mb > 0
        assert persisted.output_dir == persisted.child_output_dir
        root = Path(persisted.output_dir)
        assert root.parent == results
        envelope = persisted.params[child_jobs.ENVELOPE_KEY]
        assert envelope["execution_owner_job_id"] == child_id
        assert set(persisted.params) == {
            child_jobs.ENVELOPE_KEY,
            "frustrampnn_batch_manifest_path",
        }
        manifest_path = Path(persisted.params["frustrampnn_batch_manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["execution_owner_job_id"] == child_id
        record = manifest["records"][0]
        request_path = root / record["request_relative_path"]
        source_path = root / record["source_relative_path"]
        assert stat.S_IMODE(request_path.stat().st_mode) == 0o444
        assert stat.S_IMODE(source_path.stat().st_mode) == 0o444
        assert hashlib.sha256(request_path.read_bytes()).hexdigest() == record["request_sha256"]
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == record["source_sha256"]
        request = json.loads(request_path.read_text(encoding="utf-8"))
        assert request["source_artifact"]["sha256"] == record["source_sha256"]
        assert request["parameters"]["configuration_id"] == "frustrampnn_global_v1"
        assert len(request["parameters"]["configuration_sha256"]) == 64

        monkeypatch.setattr(nextflow_service, "resolve_nextflow_executable", lambda: "/opt/nextflow-25.10.1")
        command = nextflow_service.build_nextflow_command(
            model_id=persisted.model_id,
            mode=persisted.mode,
            params={**persisted.params, "gpu_id": 2},
            output_dir=persisted.output_dir,
            job_id=child_id,
        )
        assert command[:3] == [
            "/opt/nextflow-25.10.1",
            "run",
            "workflows/frustrampnn_analysis.nf",
        ]
        assert command[command.index("--frustrampnn_physical_gpu_id") + 1] == "2"
        assert command[command.index("--job_id") + 1] == child_id


@pytest.mark.asyncio
async def test_child_creation_commit_failure_removes_attempt_and_persists_no_job(child_db) -> None:
    sessions, results = child_db
    selection = child_jobs.upload_selection(filename="candidate.pdb", payload=_pdb(), expected_sha256=None)
    async with sessions() as session:
        async def fail_commit() -> None:
            raise RuntimeError("injected commit failure")

        session.commit = fail_commit  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="injected commit failure"):
            await child_jobs.create_child_job(
                session,
                selections=[selection],
                source_parent=None,
                trigger="upload_analyze",
            )
        await session.rollback()

    async with sessions() as session:
        assert (await session.execute(select(func.count(Job.id)))).scalar_one() == 0
    assert list(results.iterdir()) == []


def test_retired_batch_completion_owner_cannot_be_called() -> None:
    trigger_name = "maybe_trigger_batch_" + "frustrampnn"
    runner_name = "run_batch_" + "frustrampnn"
    assert not hasattr(nextflow_service, trigger_name)
    assert not hasattr(nextflow_service, runner_name)


def test_request_models_forbid_runtime_and_path_overrides() -> None:
    with pytest.raises(ValidationError):
        ReanalyzeRequest.model_validate({"gpu_id": 0})
    with pytest.raises(ValidationError):
        AnalyzeDesignsRequest.model_validate(
            {
                "selections": [{"design_id": "d1", "source_sha256": "a" * 64}],
                "output_dir": "/tmp/caller-owned",
            }
        )


@pytest.mark.asyncio
async def test_upload_router_returns_persisted_receipt_and_rejects_unknown_fields(child_db) -> None:
    sessions, _results = child_db
    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/jobs/uploads/analyze",
            files={"pdb_file": ("candidate.pdb", _pdb(), "chemical/x-pdb")},
        )
        assert response.status_code == 202
        child_id = response.json()["job_id"]
        forbidden = await client.post(
            "/api/frustrampnn/jobs/uploads/analyze?gpu_id=0",
            files={"pdb_file": ("candidate.pdb", _pdb(), "chemical/x-pdb")},
        )
        assert forbidden.status_code == 422

    async with sessions() as session:
        assert await session.get(Job, child_id) is not None
