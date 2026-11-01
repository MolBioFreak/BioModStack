"""Opening a review gate publishes the review rows the results view only reads."""
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import routers.jobs as jobs
from database import Base, Design, Job
from services import stage_review

PDB = ("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 90.00           N\n"
       "ATOM      2  CA  ALA A   1       1.450   0.000   0.000  1.00 90.00           C\nEND\n")


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _job(tmp_path):
    review = tmp_path / "validation" / "review_candidates"
    review.mkdir(parents=True)
    for name in ("one", "two"):
        (review / f"{name}.pdb").write_text(PDB)
    return review, Job(id="gate-job", name="gate", model_id="protein_modification_experimental",
                       mode="region_redesign", status="running", queue_status="running",
                       output_dir=str(tmp_path), params={})


def _isolate(monkeypatch):
    monkeypatch.setattr(stage_review, "refresh_gate_payload", lambda payload, _out: payload)
    monkeypatch.setattr(jobs, "refresh_gate_payload", lambda payload, _out: payload)
    monkeypatch.setattr(stage_review, "resolve_review_path", lambda value, _out=None: Path(value) if value else None)


@pytest.mark.asyncio
async def test_opening_the_gate_publishes_one_row_per_candidate(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    review, job = _job(tmp_path)
    async with store() as session:
        session.add(job)
        await session.commit()
        request = jobs.OpenStageGateRequest(payload={"candidate_dir": str(review)})
        out = await jobs.open_stage_gate("gate-job", "post_structure_validation", request, session)
        assert out["awaiting_stage"] == "post_structure_validation"
        rows = (await session.scalars(select(Design).where(Design.job_id == "gate-job"))).all()
        assert sorted(row.name for row in rows) == ["one", "two"]
        assert {row.source_stage for row in rows} == {"post_structure_validation"}


@pytest.mark.asyncio
async def test_a_row_publication_failure_never_blocks_the_gate(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("row publication failed")
    monkeypatch.setattr(jobs, "ensure_stage_review_rows", broken)
    review, job = _job(tmp_path)
    async with store() as session:
        session.add(job)
        await session.commit()
        request = jobs.OpenStageGateRequest(payload={"candidate_dir": str(review)})
        out = await jobs.open_stage_gate("gate-job", "post_structure_validation", request, session)
        assert out["awaiting_stage"] == "post_structure_validation"
        stored = await session.get(Job, "gate-job")
        assert stored.awaiting_input is True and stored.awaiting_stage == "post_structure_validation"
