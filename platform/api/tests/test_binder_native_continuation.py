"""Saved native structures continue without becoming accepted Designs."""
import json
from pathlib import Path
import pytest
from sqlalchemy import select
from database import Design, Job, JobArtifact
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store
from test_bindcraft2_publication import campaign
from services.result_ingester import ingest_job_results
from services.bindcraft2_publication import read_published_native_results, native_workbench_page
from services.bindcraft2_native_results import native_result_page
from services.binder_native_selection import resolve_native_source


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,name', [('2_Refolded', 't_candidate1'), ('1_Trajectories', 't')])
@pytest.mark.parametrize('placement', ['local', 'vast:selected'])
async def test_native_ingestion_to_selected_job(selected, stage, name, placement):
    client, session, root, source, tmp = selected
    directory = tmp / 'bc2'
    campaign(directory)
    saved = directory / stage / (name + '.pdb')
    saved.write_text(PDB)
    alternate = directory / stage / (name + '_alternate.pdb')
    alternate.write_text(PDB.replace('ALA', 'GLY'))
    owner = Job(id='bc2', name='BC2', model_id='bindcraft2', mode='campaign', status='completed',
                params={}, output_dir=str(directory), lineage_root_job_id=root.id)
    session.add(owner)
    await session.commit()
    assert await ingest_job_results(owner.id, str(directory), session) == 1
    publication, receipt = await read_published_native_results(owner, session)
    page = native_workbench_page(native_result_page(publication, stage='document'), receipt)
    row = next(row for row in page['rows'] if row['path'] == f'{stage}/{name}.pdb')
    row = row['structures'][0]
    path, identity = await resolve_native_source(session, owner.id, row['artifact_id'])
    assert path == saved and identity['lineage_root_job_id'] == root.id
    if stage == '2_Refolded':
        assert identity['producer_document']['native_rows'][0]['outcome'] == 'rejected'
    payload = {'source_job_id': source.id, 'native_sources': [row['native_source']],
               'operation': 'refine', 'execution_target_id': None if placement == 'local' else placement,
               'params': {'binder_chains': 'A', 'target_chains': 'B', 'maturation_repack_enabled': True}}
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == (201 if placement == 'local' else 409), response.text
    if placement == 'local':
        child = await session.get(Job, response.json()['launched_jobs'][0]['id'])
        params = child.params
        assert child.lineage_root_job_id == root.id
        payload['source_job_id'] = child.id
        again = await client.post('/api/binder-continuation/selected', json=payload)
        assert again.status_code == 201, again.text
    else:
        params = response.json()['detail']['job_request']['params']
    manifest = json.loads(Path(params['selected_input_manifest']).read_text())
    item = manifest['designs'][0]
    assert item['design_id'] is None
    assert item['selected_document']['artifact_id'] == row['artifact_id']
    assert Path(item['selection_pdb_path']).read_text() == PDB
    assert params['iteration_source_design_ids'] == []
    designs = (await session.scalars(select(Design).where(Design.job_id == owner.id))).all()
    assert [d.name for d in designs] == ['t_seq0']
    alt = next(row for row in page['rows'] if row['path'].endswith('_alternate.pdb'))
    path, _ = await resolve_native_source(session, owner.id, alt['structures'][0]['artifact_id'])
    assert path.read_text() == PDB.replace('ALA', 'GLY')
    if placement == 'local':
        from services.frustrampnn.jobs import ENVELOPE_KEY
        frustra = await client.post('/api/binder-continuation/selected', json={
            'source_job_id': source.id, 'design_ids': ['d1'],
            'native_sources': [row['native_source']], 'operation': 'frustrampnn'})
        assert frustra.status_code == 201, frustra.text
        children = frustra.json()['launched_jobs']
        assert len(children) == 2
        native_child = await session.get(Job, children[1]['id'])
        selection = native_child.params[ENVELOPE_KEY]['selection'][0]
        assert selection['design_id'] is None
        assert selection['producer_coordinates']['selected_document']['artifact_id'] == row['artifact_id']
        single = await client.post('/api/binder-continuation/selected', json={
            'source_job_id': source.id, 'native_sources': [alt['structures'][0]['native_source']],
            'operation': 'proteinmpnn', 'params': {'mpnn_relax_max_cycles': 0}})
        assert single.status_code == 201, single.text
        child = await session.get(Job, single.json()['launched_jobs'][0]['id'])
        assert child.params['iteration_source_design_ids'] == []
        assert Path(child.params['input_pdb']).read_text() == PDB.replace('ALA', 'GLY')
    _, after = await read_published_native_results(owner, session)
    assert after == receipt
    saved.unlink()
    retained_path, retained_identity = await resolve_native_source(session, owner.id, row['artifact_id'])
    assert retained_path == saved and retained_identity == identity
