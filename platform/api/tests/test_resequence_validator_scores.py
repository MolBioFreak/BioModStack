"""Resequence finals carry the scores their validators wrote.

Every Resequence final design's row had pLDDT 0.0 and no pTM, because the structure's B-factors carry no confidence
and the ingester never read the per-validator output beside the job. The files below are shaped like real worker output
(Boltz-2 *_boltzpred.json, Protenix v2 *_summary_confidence_sample_N.json, ESMFold2 *.metrics.json).
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from services.plr_validator_scores import apply_validator_scores, validator_scores

PDB = ("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
       "ATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00  0.00           C\nEND\n")
NAME = "001_rfd3_generation_native_input_generation_0_0_model_0_seq_0"


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _boltz(root: Path, name=NAME):
    _write(root / "validation" / "boltz2" / f"{name}_model_0_boltzpred.json", {
        "confidence_score": 0.925, "ptm": 0.914, "iptm": 0.0, "ligand_iptm": 0.0, "protein_iptm": 0.0,
        "complex_plddt": 0.928, "complex_iplddt": 0.928, "complex_pde": 0.337, "complex_ipde": 0.0,
        "chains_ptm": {"0": 0.91}, "pair_chains_iptm": {"0": {"0": 0.9}}})


def _protenix(root: Path, name=NAME):
    base = root / "validation" / "protenix_v2" / name / "hash" / name / "seed_42" / "predictions"
    for sample, (ranking, ptm) in enumerate([(0.1855, 0.9285), (0.1860, 0.9301), (0.1850, 0.9270)]):
        _write(base / f"{name}_summary_confidence_sample_{sample}.json", {
            "plddt": 93.07, "gpde": 0.347, "ptm": ptm, "iptm": 0.0, "has_clash": 0, "disorder": 0.0,
            "ranking_score": ranking, "num_recycles": 10})


def _esmfold(root: Path, name=NAME):
    _write(root / "validation" / "esmfold2" / name / "esmfold2_results" / f"{name}_000.metrics.json",
           {"plddt_mean": 0.8206, "ptm": 0.783, "iptm": 0.0, "sequence_name": name})


def test_boltz2_scores_fill_the_existing_columns(tmp_path):
    _boltz(tmp_path)
    columns, whole = validator_scores(tmp_path, NAME, ["boltz2"])
    assert columns["ptm"] == pytest.approx(0.914) and columns["conf_score"] == pytest.approx(0.925)
    assert columns["plddt_overall"] == pytest.approx(92.8)  # stored as a percentage, as the other ingest paths do
    assert columns["chains_ptm"] == {"0": 0.91}
    assert whole["boltz2"]["complex_pde"] == pytest.approx(0.337)


def test_protenix_reports_the_sample_its_own_ranking_picks(tmp_path):
    _protenix(tmp_path)
    columns, whole = validator_scores(tmp_path, NAME, ["protenix_v2"])
    assert columns["ptm"] == pytest.approx(0.9301)  # sample 1 has the best ranking_score
    assert columns["plddt_overall"] == pytest.approx(93.07)
    assert columns["num_recycles"] == 10 and columns["has_clash"] is False
    assert whole["protenix_v2"]["samples"] == 3


def test_esmfold2_scores_are_read(tmp_path):
    _esmfold(tmp_path)
    columns, _ = validator_scores(tmp_path, NAME, ["esmfold2"])
    assert columns["plddt_overall"] == pytest.approx(82.06) and columns["ptm"] == pytest.approx(0.783)


def test_every_validator_is_kept_and_the_first_listed_fills_the_columns(tmp_path):
    _boltz(tmp_path); _protenix(tmp_path); _esmfold(tmp_path)
    columns, whole = validator_scores(tmp_path, NAME, ["boltz2", "esmfold2"])
    assert set(whole) == {"boltz2", "protenix_v2", "esmfold2"}
    assert columns["ptm"] == pytest.approx(0.914)  # boltz2 was listed first


def test_missing_or_unreadable_output_changes_nothing(tmp_path):
    design = Design(id="d", job_id="j", name=NAME, pdb_path="x.pdb", plddt_overall=None)
    assert apply_validator_scores(design, tmp_path, NAME, ["boltz2"]) is False
    assert design.ptm is None and design.confidence_metrics is None
    bad = tmp_path / "validation" / "boltz2" / f"{NAME}_model_0_boltzpred.json"
    bad.parent.mkdir(parents=True); bad.write_text("{not json")
    assert apply_validator_scores(design, tmp_path, NAME, ["boltz2"]) is False
    assert apply_validator_scores(design, None, NAME, ["boltz2"]) is False


@pytest.mark.asyncio
async def test_resequence_final_rows_get_their_validator_scores_at_ingest(tmp_path):
    from services.result_ingester import ingest_loose_files
    output = tmp_path / "output"
    final = output / ".bms-review" / "selections" / "abc"
    final.mkdir(parents=True)
    (final / f"{NAME}.pdb").write_text(PDB)
    _boltz(output)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scores.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = Job(id="loop", name="loop", model_id="protein_modification_experimental", mode="region_redesign",
                  params={"plr_final_candidate_dir": str(final), "structure_validators": ["boltz2"]},
                  output_dir=str(output))
        session.add(job)
        await session.commit()
        assert await ingest_loose_files(job.id, output, session, current_job=job) == 1
    async with factory() as session:
        row = (await session.execute(select(Design))).scalar_one()
        assert row.ptm == pytest.approx(0.914) and row.plddt_overall == pytest.approx(92.8)
        assert row.confidence_metrics["boltz2"]["complex_pde"] == pytest.approx(0.337)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_job_without_a_named_final_folder_is_read_as_before(tmp_path):
    from services.result_ingester import ingest_loose_files
    output = tmp_path / "output"
    (output / "pdb_files").mkdir(parents=True)
    (output / "pdb_files" / f"{NAME}.pdb").write_text(PDB)
    _boltz(output)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plain.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = Job(id="other", name="other", model_id="protein_modification_experimental", mode="region_redesign",
                  params={"structure_validators": ["boltz2"]}, output_dir=str(output))
        session.add(job)
        await session.commit()
        await ingest_loose_files(job.id, output, session, current_job=job)
    async with factory() as session:
        rows = (await session.execute(select(Design))).scalars().all()
        assert rows and all(row.ptm is None for row in rows if row.name == NAME)
    await engine.dispose()


@pytest.mark.asyncio
async def test_remote_continuation_finals_get_scores_from_the_job_output_not_the_nested_ingest_folder(tmp_path):
    """A pulled remote Resequence is ingested from <job>/.bms-review/selections/<sha>/continuation while the validators
    wrote <job>/validation; reading only the ingest folder left every final unscored on the real worker runs."""
    from services.result_ingester import ingest_loose_files
    job_output = tmp_path / "job"
    final = job_output / ".bms-review" / "selections" / "abc"
    continuation = final / "continuation"
    continuation.mkdir(parents=True)
    (final / f"{NAME}.pdb").write_text(PDB)
    _boltz(job_output)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'remote.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = Job(id="remote", name="remote", model_id="protein_modification_experimental", mode="region_redesign",
                  params={"plr_final_candidate_dir": str(final), "structure_validators": ["boltz2"]},
                  output_dir=str(job_output))
        session.add(job)
        await session.commit()
        assert await ingest_loose_files(job.id, continuation, session, current_job=job) == 1
    async with factory() as session:
        row = (await session.execute(select(Design))).scalar_one()
        assert row.ptm == pytest.approx(0.914) and row.plddt_overall == pytest.approx(92.8)
    await engine.dispose()
