"""Rejected native artifact -> receiving editor; no scientific inference."""
import hashlib

import pytest
from sqlalchemy import select

from database import Design, Job, JobArtifact
from routers import jobs
from schemas import JobCreate
from services.binder_source_materialization import StructureSourceRequest
from services.result_ingester import ingest_job_results
from tests.test_binder_source_materialization import source_api, stores, PDB
from tests.test_bindcraft2_publication import campaign


@pytest.mark.asyncio
async def test_rejected_native_source_materializes_and_reopens_without_original(source_api):
    client, session, _, root = source_api
    output = root / 'results/rejected'; campaign(output)
    original = output / '2_Refolded/t_candidate1.pdb'
    original.write_text(PDB)
    source = Job(id='rejected', name='Rejected native fixture', model_id='bindcraft2',
                 mode='campaign', status='completed', params={}, output_dir=str(output))
    session.add(source); await session.commit()
    assert await ingest_job_results(source.id, str(output), session) == 1
    artifact = (await session.scalars(select(JobArtifact).where(
        JobArtifact.owner_job_id == source.id, JobArtifact.storage_path == str(original)))).one()
    selection = {'job_id': source.id, 'document': {'artifact_id': artifact.id}}
    response = await client.post('/api/files/materialize-structure', json=selection)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['native_sha256'] == hashlib.sha256(PDB.encode()).hexdigest()
    assert result['author_residues'][0]['auth_asym_id'] == 'a'
    assert result['author_residues'][0]['insertion_code'] == 'B'
    assert result['source_structure']['job_id'] == source.id
    assert result['source_structure']['document'] == selection['document']
    retained = StructureSourceRequest.model_validate(result['source_structure'])
    original.unlink()
    # Reopen the retained exact snapshot, without reacquiring the producer file.
    clone = JobCreate(name='Native source editor', model_id='esmfold2', mode='predict',
                      params={'sequence': 'A', 'pred_method': 'esmfold2'},
                      source_structure=retained, execution_target_id=None)
    identity = await jobs._prepare_selected_structure(clone, session)
    assert identity['owner_job_id'] == source.id
    assert identity['artifact_id'] == artifact.id
    assert clone.parent_job_id is None and clone.execution_target_id is None
    assert clone.source_structure == retained
    assert 'source_structure' not in clone.params
    # Source result accounting and acceptance are untouched by the handoff.
    assert [row.name for row in (await session.scalars(select(Design))).all()] == ['t_seq0']
    assert source.status == 'completed'


@pytest.mark.asyncio
async def test_native_editor_never_substitutes_another_jobs_artifact(source_api):
    client, session, _, root = source_api
    for name in ('one', 'two'):
        output = root / f'results/{name}'; campaign(output, zero=True)
        (output / '1_Trajectories/t.pdb').write_text(PDB)
        session.add(Job(id=name, name=name, model_id='bindcraft2', mode='campaign',
                        status='completed', params={}, output_dir=str(output)))
        await session.commit()
        assert await ingest_job_results(name, str(output), session) == 0
    artifact = (await session.scalars(select(JobArtifact).where(
        JobArtifact.owner_job_id == 'one', JobArtifact.storage_path.endswith('t.pdb')))).one()
    response = await client.post('/api/files/materialize-structure', json={
        'job_id': 'two', 'document': {'artifact_id': artifact.id}})
    assert response.status_code in (404, 422), response.text
    assert not (await session.scalars(select(Design))).all()
