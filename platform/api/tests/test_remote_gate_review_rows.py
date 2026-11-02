"""A remote review gate lists its candidates.

Remote gates carry candidates only as checkpoint artifacts on the worker, so the host-side publisher
(which scans a local candidate directory) saw nothing and the gate listed 0 designs, leaving the
Continue and FrustraMPNN actions with nothing to act on.
"""
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from services import stage_review
from services.remote_execution import executor

PDB = ("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 90.00           N\n"
       "ATOM      2  CA  ALA A   1       1.450   0.000   0.000  1.00 90.00           C\nEND\n")
STAGE = "post_structure_validation"


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _checkpoint(names):
    folder = ".bms-review/abc/candidate"
    artifacts = [dict(component_id="gate-job", relative_path=f"{folder}/{name}.pdb", sha256="0" * 64,
                      size_bytes=len(PDB), schema="bms.stage-review.candidate.v1") for name in names]
    artifacts.append(dict(component_id="gate-job", relative_path=".bms-review/abc/gate-payload.json",
                          sha256="1" * 64, size_bytes=2, schema="bms.stage-review.control.v1"))
    return dict(artifacts=artifacts, checkpoint_id="gate-job:" + STAGE, checkpoint_sha256="2" * 64, stage=STAGE)


def _job(tmp_path, checkpoint):
    return Job(id="gate-job", name="remote gate", model_id="protein_modification_experimental",
               mode="region_redesign", status="awaiting_input", queue_status="completed",
               awaiting_input=True, awaiting_stage=STAGE, output_dir=str(tmp_path / "out"),
               params={}, awaiting_payload={"component_checkpoint": checkpoint})


def _retrieved(tmp_path, checkpoint):
    root = tmp_path / "review-root"
    for item in checkpoint["artifacts"]:
        path = root / item["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PDB if path.suffix == ".pdb" else "{}")
    return root


def _isolate(monkeypatch):
    monkeypatch.setattr(stage_review, "refresh_gate_payload", lambda payload, _out: payload)
    monkeypatch.setattr(stage_review, "resolve_review_path", lambda value, _out=None: __import__("pathlib").Path(value) if value else None)


@pytest.mark.asyncio
async def test_remote_gate_publishes_one_row_per_fetched_candidate(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    checkpoint = _checkpoint(["one", "two", "three"])
    root = _retrieved(tmp_path, checkpoint)

    async def fetched(session, job, cp):
        assert cp == checkpoint
        return root
    monkeypatch.setattr(executor, "retrieve_remote_checkpoint_review", fetched)
    async with store() as session:
        session.add(_job(tmp_path, checkpoint))
        await session.commit()
        job = await session.get(Job, "gate-job")
        assert await executor.publish_remote_review_rows(session, job) == 3
        rows = (await session.scalars(select(Design).where(Design.job_id == "gate-job"))).all()
        assert sorted(row.name for row in rows) == ["one", "three", "two"]
        assert {row.source_stage for row in rows} == {STAGE}


@pytest.mark.asyncio
async def test_a_failed_fetch_never_blocks_the_gate(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)

    async def broken(*_args, **_kwargs):
        raise executor.RemoteExecutionError("worker unreachable")
    monkeypatch.setattr(executor, "retrieve_remote_checkpoint_review", broken)
    async with store() as session:
        session.add(_job(tmp_path, _checkpoint(["one"])))
        await session.commit()
        job = await session.get(Job, "gate-job")
        assert await executor.publish_remote_review_rows(session, job) == 0
        stored = await session.get(Job, "gate-job")
        assert stored.awaiting_input is True and stored.awaiting_stage == STAGE
