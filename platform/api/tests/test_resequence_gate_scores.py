"""Resequence gate rows carry the scores the validators wrote (display only, never a condition)."""
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from services import stage_review

PDB = ("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
       "ATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00  0.00           C\nEND\n")
STAGE = "post_structure_validation"
NAME = "001_rfd3_generation_native_input_generation_0_0_model_0_seq_0"
OTHER = "002_rfd3_generation_native_input_generation_0_0_model_0_seq_1"


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _job(tmp_path, names=(NAME, OTHER), stage=STAGE, validators=("boltz2", "protenix_v2", "esmfold2")):
    out = tmp_path / "out"
    cand = out / "validation" / "review_candidates"
    for name in names:
        (cand / f"{name}.pdb").parent.mkdir(parents=True, exist_ok=True)
        (cand / f"{name}.pdb").write_text(PDB)
    return Job(id="gate-job", name="gate", model_id="protein_modification_experimental", mode="region_redesign",
               status="awaiting_input", queue_status="completed", awaiting_input=True, awaiting_stage=stage,
               output_dir=str(out), params={"structure_validators": list(validators)},
               awaiting_payload={"candidate_dir": str(cand)})


def _validator_files(out: Path, name=NAME):
    _write(out / "validation" / "boltz2" / f"{name}_model_0_boltzpred.json", {
        "confidence_score": 0.925, "ptm": 0.914, "iptm": 0.0, "complex_plddt": 0.928, "complex_iplddt": 0.928})
    base = out / "validation" / "protenix_v2" / name / "h" / name / "seed_1" / "predictions"
    _write(base / f"{name}_summary_confidence_sample_0.json",
           {"plddt": 93.07, "ptm": 0.93, "iptm": 0.0, "ranking_score": 0.18, "gpde": 0.3, "num_recycles": 10})
    _write(out / "validation" / "esmfold2" / name / "esmfold2_results" / f"{name}_000.metrics.json",
           {"plddt_mean": 0.82, "ptm": 0.78, "iptm": 0.0})


def _isolate(monkeypatch):
    monkeypatch.setattr(stage_review, "refresh_gate_payload", lambda payload, _out: payload)
    monkeypatch.setattr(stage_review, "resolve_review_path",
                        lambda value, _out=None: Path(value) if value else None)


async def _rows(session):
    return {r.name: r for r in (await session.scalars(select(Design).where(Design.job_id == "gate-job"))).all()}


@pytest.mark.asyncio
async def test_gate_rows_carry_validator_scores(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    async with store() as session:
        job = _job(tmp_path)
        _validator_files(Path(job.output_dir))
        session.add(job)
        await session.commit()
        assert await stage_review.ensure_stage_review_rows(session, job) == 2
        rows = await _rows(session)
        row = rows[NAME]
        assert row.ptm == pytest.approx(0.914) and row.conf_score == pytest.approx(0.925)
        assert row.plddt_overall == pytest.approx(92.8)  # first listed validator (boltz2), as a percentage
        assert set(row.confidence_metrics) == {"boltz2", "protenix_v2", "esmfold2"}
        # A candidate no validator wrote scores for keeps today's empty row.
        assert rows[OTHER].ptm is None and rows[OTHER].confidence_metrics is None


@pytest.mark.asyncio
async def test_gate_without_validator_output_is_unchanged(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    async with store() as session:
        job = _job(tmp_path)
        session.add(job)
        await session.commit()
        assert await stage_review.ensure_stage_review_rows(session, job) == 2
        for row in (await _rows(session)).values():
            assert row.ptm is None and row.conf_score is None and row.confidence_metrics is None


@pytest.mark.asyncio
async def test_unreadable_score_file_changes_nothing(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    async with store() as session:
        job = _job(tmp_path, validators=("boltz2",))
        broken = Path(job.output_dir) / "validation" / "boltz2" / f"{NAME}_model_0_boltzpred.json"
        broken.parent.mkdir(parents=True)
        broken.write_text("{not json")
        session.add(job)
        await session.commit()
        assert await stage_review.ensure_stage_review_rows(session, job) == 2
        assert (await _rows(session))[NAME].ptm is None


@pytest.mark.asyncio
async def test_existing_rows_get_scores_on_next_pass_with_stable_ids(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    async with store() as session:
        job = _job(tmp_path)
        session.add(job)
        await session.commit()
        await stage_review.ensure_stage_review_rows(session, job)
        ids = {n: r.id for n, r in (await _rows(session)).items()}
        assert all(r.ptm is None for r in (await _rows(session)).values())
        _validator_files(Path(job.output_dir))
        assert await stage_review.ensure_stage_review_rows(session, job) == 2
        rows = await _rows(session)
        assert {n: r.id for n, r in rows.items()} == ids
        assert rows[NAME].ptm == pytest.approx(0.914)


@pytest.mark.asyncio
async def test_other_stages_and_other_models_are_unchanged(tmp_path, store, monkeypatch):
    _isolate(monkeypatch)
    async with store() as session:
        job = _job(tmp_path, stage="post_caliby")
        _validator_files(Path(job.output_dir))
        session.add(job)
        await session.commit()
        await stage_review.ensure_stage_review_rows(session, job)
        for r in (await _rows(session)).values():
            assert r.ptm is None and not ({"boltz2", "protenix_v2", "esmfold2"} & set(r.confidence_metrics or {}))
    async with store() as session:
        other = await session.get(Job, "gate-job")
        other.awaiting_stage = STAGE
        other.model_id, other.mode = "rfantibody", "antibody"
        await session.commit()
        await stage_review.ensure_stage_review_rows(session, other, force=True)
        assert all(r.ptm is None for r in (await _rows(session)).values())
