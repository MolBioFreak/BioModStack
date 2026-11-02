"""A completed Resequence (region_redesign) result finalizes without a generic candidate-publication record."""
import uuid

import pytest
from sqlalchemy import select

from database import Design, Job
from services.result_state_integrity import finalize_successful_job
from test_remote_lifecycle_gaps import store  # noqa: F401  (fixture)

PDB = "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n"


async def _finalize(store, tmp_path, monkeypatch, model_id, mode):
    monkeypatch.setenv("BMS_DATA", str(tmp_path))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(tmp_path))
    output = tmp_path / "out"
    output.mkdir()

    async def ingest(job_id, output_path, session, *args, **kwargs):
        for index in range(2):
            name = f"design_{index}"
            (output / f"{name}.pdb").write_text(PDB)
            session.add(Design(id=str(uuid.uuid4()), job_id=job_id, name=name, pdb_path=f"{name}.pdb"))
        await session.flush()
        return 2

    async with store() as session:
        job = await session.get(Job, "job")
        job.model_id, job.mode = model_id, mode
        job.params = {"core_protein_scientific_contract": 1}
        job.provenance = {"core_protein_scientific_contract": 1}
        job.execution_target_id = None
        job.remote_attempt_id = job.remote_state = job.nextflow_run_id = None
        job.status, job.queue_status, job.awaiting_input = "running", "running", False
        job.output_dir = str(output)
        await session.commit()
        result = await finalize_successful_job(job, str(output), session, ingest_fn=ingest)
        return result, job, list(await session.scalars(select(Design).where(Design.job_id == "job")))


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id,mode", [("protein_modification_experimental", "region_redesign"),
                                           ("protein_local_redesign", "local_redesign")])
async def test_resequence_result_completes_without_generic_candidate_authority(store, tmp_path, monkeypatch, model_id, mode):
    result, job, designs = await _finalize(store, tmp_path, monkeypatch, model_id, mode)
    assert result.completed is True, (job.error_message, job.provenance.get("result_integrity"))
    assert job.status == "completed" and len(designs) == 2


@pytest.mark.asyncio
async def test_a_generic_marked_design_job_still_requires_candidate_authority(store, tmp_path, monkeypatch):
    result, job, _ = await _finalize(store, tmp_path, monkeypatch, "boltz2", "predict")
    assert result.completed is False
    assert "candidate publication authority" in (job.error_message or "")
