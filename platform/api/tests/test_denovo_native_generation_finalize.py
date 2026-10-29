"""Native La-Proteina/DISCO/Complexa/RFD4 returns finalize through the real finalizer.

Producer files are inert fixtures; ingestion, publication registration and
finalization are the production owners on a private SQLite store.
"""
import pytest
from sqlalchemy import select

from database import Design, Job, JobArtifact
from services.result_state_integrity import finalize_successful_job
from services.rfd4_proteina_results import read_published_generation_page
from test_denovo_canonical_return import native_tree
from test_remote_lifecycle_gaps import store  # noqa: F401  (fixture)


def _success(tree_root):
    """Mark the fixture publication as a completed native run."""
    import json
    for name in ('laproteina_publication.json', 'disco_publication.json'):
        path = tree_root / name
        if path.exists():
            data = json.loads(path.read_text())
            data.update(execution_failed=False, native_returncode=0)
            path.write_text(json.dumps(data))


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['laproteina', 'disco'])
async def test_marked_parent_job_with_native_publication_completes(store, tmp_path, monkeypatch, model):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'out'
    root = native_tree(output, model, 'partial', compressed=False)
    _success(root)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id, job.mode = 'protein_modification_experimental', 'de_novo_design'
        job.params = {'generator': model, 'core_protein_scientific_contract': 1}
        job.provenance = {'core_protein_scientific_contract': 1}
        job.execution_target_id = None
        job.remote_attempt_id = job.remote_state = None
        job.status, job.queue_status, job.awaiting_input = 'running', 'running', False
        job.output_dir = str(output)
        await session.commit()
        result = await finalize_successful_job(job, str(output), session)
        assert result.completed is True, job.provenance.get('result_integrity')
        assert job.status == 'completed'
        assert job.provenance['result_integrity']['state'] == 'validated'
        assert not list(await session.scalars(select(Design).where(Design.job_id == job.id)))
        assert await session.scalar(select(JobArtifact.id).where(JobArtifact.owner_job_id == job.id).limit(1))
    async with store() as fresh:
        page = await read_published_generation_page(await fresh.get(Job, 'job'), fresh)
        assert page['model_id'] == model and page['records']


@pytest.mark.asyncio
async def test_marked_job_without_publication_still_requires_candidate_authority(store, tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'out'
    output.mkdir()
    (output / 'stray.pdb').write_text('ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n')
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id, job.mode = 'protein_modification_experimental', 'de_novo_design'
        job.params = {'generator': 'laproteina', 'core_protein_scientific_contract': 1}
        job.provenance = {'core_protein_scientific_contract': 1}
        job.execution_target_id = None
        job.remote_attempt_id = job.remote_state = None
        job.status, job.queue_status, job.awaiting_input = 'running', 'running', False
        job.output_dir = str(output)
        await session.commit()
        result = await finalize_successful_job(job, str(output), session)
        assert result.completed is False
