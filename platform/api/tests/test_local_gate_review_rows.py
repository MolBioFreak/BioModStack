"""A local component review gate lists its candidates.

Under the component runtime a local gate writes its review files into the job's result root as checkpoint artifacts.
The HTTP gate route that used to publish review rows refuses component gates, so a local Resequence gate listed 0
designs and Continue / FrustraMPNN had nothing to act on.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from services import nextflow, stage_review

PDB = ("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 90.00           N\n"
       "ATOM      2  CA  ALA A   1       1.450   0.000   0.000  1.00 90.00           C\nEND\n")
STAGE = "post_structure_validation"
FOLDER = ".bms-review/abc/candidate"


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _job(tmp_path, names, **extra):
    out = tmp_path / "out"
    for name in names:
        path = out / FOLDER / f"{name}.pdb"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PDB)
    artifacts = [dict(component_id="gate-job", relative_path=f"{FOLDER}/{name}.pdb", sha256="0" * 64,
                      size_bytes=len(PDB), schema="bms.stage-review.candidate.v1") for name in names]
    artifacts.append(dict(component_id="gate-job", relative_path=".bms-review/abc/gate-payload.json", sha256="1" * 64,
                          size_bytes=2, schema="bms.stage-review.control.v1"))
    checkpoint = dict(artifacts=artifacts, checkpoint_id="gate-job:" + STAGE, checkpoint_sha256="2" * 64, stage=STAGE)
    return Job(id="gate-job", name="local gate", model_id="protein_modification_experimental", mode="region_redesign",
               status="awaiting_input", queue_status="completed", awaiting_input=True, awaiting_stage=STAGE,
               output_dir=str(out), params={}, awaiting_payload={"component_checkpoint": checkpoint}, **extra)


def _isolate(monkeypatch):
    monkeypatch.setattr(stage_review, "refresh_gate_payload", lambda payload, _out: payload)
    monkeypatch.setattr(stage_review, "resolve_review_path",
                        lambda value, _out=None: __import__("pathlib").Path(value) if value else None)


@pytest.mark.asyncio
async def test_local_gate_publishes_one_row_per_candidate(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    async with store() as session:
        session.add(_job(tmp_path, ["one", "two", "three"]))
        await session.commit()
        assert await nextflow._publish_local_review_rows(session, "gate-job") == 3
        rows = (await session.scalars(select(Design).where(Design.job_id == "gate-job"))).all()
        assert sorted(r.name for r in rows) == ["one", "three", "two"]
        assert {r.source_stage for r in rows} == {STAGE}
        # A second call leaves the same three rows.
        assert await nextflow._publish_local_review_rows(session, "gate-job") == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,awaiting", [
    ({"execution_target_id": "vast:1"}, True),   # remote gates have their own publisher
    ({}, False),                                  # not at a gate
])
async def test_other_jobs_publish_nothing(tmp_path, store, monkeypatch, extra, awaiting):
    _isolate(monkeypatch)
    async with store() as session:
        job = _job(tmp_path, ["one"], **extra)
        job.awaiting_input = awaiting
        session.add(job)
        await session.commit()
        assert await nextflow._publish_local_review_rows(session, "gate-job") == 0
        assert (await session.scalars(select(Design).where(Design.job_id == "gate-job"))).all() == []
