"""Selected continuation: real endpoint, SQLite persistence and snapshot identity."""
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from routers import binder_continuation as route, jobs
from services.binder_continuation import resolve_root, snapshot_selection

PDB = ''.join(
    f'ATOM  {serial:5d}  {atom:<3} ALA A   1    {float(serial):8.3f}{2.0:8.3f}{3.0:8.3f}  1.00 80.00          {element:>2}  \n'
    for serial, (atom, element) in enumerate([('N', 'N'), ('CA', 'C'), ('C', 'C'), ('O', 'O')], 1)
) + 'TER\nEND\n'


@pytest_asyncio.fixture
async def selected(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(jobs, '_resolve_design_structure_path', lambda path: Path(path))
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "selected.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        root = Job(id='root', name='generic root', model_id='rfdiffusion3', mode='design',
                   status='completed', params={}, output_dir=str(tmp_path))
        source = Job(id='round1', name='round one', model_id='binder_refinement', mode='refine',
                     status='completed', params={'lineage_root_job_id': 'root'},
                     lineage_root_job_id='root', output_dir=str(tmp_path))
        session.add_all([root, source])
        for ordinal, owner in enumerate([root, source]):
            path = tmp_path / f'state-{ordinal}.pdb'
            path.write_text(PDB)
            session.add(Design(id=f'd{ordinal}', job_id=owner.id, name='same-display-name',
                pdb_path=str(path), lineage_root_job_id=root.id,
                provenance={'structure_state': f'state-{ordinal}', 'producer_candidate_key': f'key-{ordinal}'},
                origin_design_id='d0', parent_design_id='d0' if ordinal else None))
        await session.commit()
        app = FastAPI()
        app.include_router(route.router, prefix='/api/binder-continuation')
        async def dependency():
            yield session
        app.dependency_overrides[get_session] = dependency
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client, session, root, source, tmp_path
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_root_snapshot_keeps_exact_parents_states_and_native_bytes(selected):
    _, session, root, source, tmp_path = selected
    designs = [await session.get(Design, 'd1'), await session.get(Design, 'd0')]
    directory = snapshot_selection(source, root, designs)
    rows = json.loads((directory / 'source_identity.json').read_text())
    assert [row['source_meta']['parent_design_id'] for row in rows] == ['d1', 'd0']
    assert [row['source_meta']['source_job_id'] for row in rows] == ['round1', 'root']
    assert [row['source_meta']['structure_state'] for row in rows] == ['state-1', 'state-0']
    for row in rows:
        snapshot = Path(row['source_path'])
        assert not snapshot.is_symlink()
        original = Path(row['source_meta']['source_structure_path'])
        original.write_text('changed parent')
        assert snapshot.read_text() == PDB


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,model_id,mode', [('refine', 'binder_refinement', 'refine'), ('caliby', 'caliby_binder', 'design')])
async def test_endpoint_creates_real_model_owned_job_and_repeated_round(selected, operation, model_id, mode):
    client, session, root, source, tmp_path = selected
    params = {'binder_chains': 'A', 'target_chains': 'B'}
    if operation == 'refine':
        params.update(maturation_repack_enabled=True, maturation_anchors_enabled=False,
                      maturation_flow_enabled=False)
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source.id, 'design_ids': ['d1', 'd0'], 'operation': operation, 'params': params})
    assert response.status_code == 201, response.text
    payload = response.json()
    created = await session.get(Job, payload['launched_jobs'][0]['id'])
    assert created is not None
    assert (created.model_id, created.mode, created.lineage_root_job_id) == (model_id, mode, root.id)
    assert created.selection_source_job_id == source.id
    assert created.params['iteration_source_design_ids'] == ['d1', 'd0']
    assert created.params['binder_chains'] == 'A'
    from model_registry import get_registry
    definition = get_registry().get_model(model_id)
    assert definition is not None
    for field in definition.params:
        if field.default is not None and field.name not in params:
            assert created.params[field.name] == field.default
    assert (await resolve_root(session, created.id))[1].id == root.id
    # An operator may return to the exact original state after a child round.
    again = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': created.id, 'design_ids': ['d0'], 'operation': operation, 'params': params})
    assert again.status_code == 201, again.text
    assert again.json()['root_job_id'] == root.id
    assert again.json()['launched_jobs'][0]['id'] != created.id


@pytest.mark.asyncio
async def test_foreign_design_and_missing_id_are_not_materialized(selected):
    client, session, _, source, tmp_path = selected
    foreign = Job(id='foreign', name='foreign', model_id='rfdiffusion3', mode='design', params={})
    session.add(foreign)
    session.add(Design(id='foreign-design', name='foreign', job_id=foreign.id,
                       pdb_path=str(tmp_path / 'foreign.pdb')))
    await session.commit()
    for ids, status in [(['foreign-design'], 422), (['missing'], 404), (['d0', 'd0'], 422)]:
        response = await client.post('/api/binder-continuation/selected', json={
            'source_job_id': source.id, 'design_ids': ids, 'operation': 'refine'})
        assert response.status_code == status, response.text
    assert not (tmp_path / 'inputs').exists()


@pytest.mark.asyncio
async def test_remote_review_retains_once_materialized_request(selected):
    client, _, _, source, tmp_path = selected
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source.id, 'design_ids': ['d1'], 'operation': 'refine',
        'params': {'binder_chains': 'A', 'target_chains': 'B', 'maturation_repack_enabled': True},
        'execution_target_id': 'vast:selected'})
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'remote_prepared_job_review_required'
    params = detail['job_request']['params']
    assert Path(params['source_identity_json']).is_file()
    assert Path(params['pdb_paths']).read_text() == PDB
    assert len(list((tmp_path / 'inputs' / 'design_selections' / 'binder').iterdir())) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,model_id,params', [
    ('proteinmpnn', 'proteinmpnn', {'mpnn_relax_max_cycles': 0}),
    ('fampnn', 'fampnn', {'design_chain': 'A'}),
    ('predict_boltz2', 'boltz2', {'boltz_use_msa': False}),
    ('predict_protenix', 'protenix', {'protenix_use_msa': False}),
])
async def test_individual_native_owners_keep_every_selected_state(selected, operation, model_id, params):
    client, session, root, source, _ = selected
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source.id, 'design_ids': ['d1', 'd0'], 'operation': operation, 'params': params})
    assert response.status_code == 201, response.text
    children = response.json()['launched_jobs']
    assert len(children) == 2
    for child_response, design_id in zip(children, ['d1', 'd0']):
        child = await session.get(Job, child_response['id'])
        assert child.model_id == model_id
        assert child.lineage_root_job_id == root.id
        assert child.params['iteration_source_design_ids'] == [design_id]
        if operation.startswith('predict_'):
            assert child.params['complex_components'] == [{'id': 'A', 'type': 'protein', 'sequence': 'A'}]
        else:
            assert Path(child.params['input_pdb']).read_text() == PDB


@pytest.mark.asyncio
async def test_full_frustrampnn_owner_persists_native_requests_for_same_root_states(selected, monkeypatch):
    from services.frustrampnn import jobs as frustra_jobs
    client, session, root, source, tmp_path = selected
    results = tmp_path / 'frustra-results'
    results.mkdir()
    monkeypatch.setattr(frustra_jobs, 'get_results_dir', lambda: results)
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source.id, 'design_ids': ['d1', 'd0'], 'operation': 'frustrampnn'})
    assert response.status_code == 201, response.text
    children = response.json()['launched_jobs']
    assert len(children) == 2
    for child_response, design_id in zip(children, ['d1', 'd0']):
        child = await session.get(Job, child_response['id'])
        assert child.model_id == 'frustrampnn'
        assert child.lineage_root_job_id == root.id
        envelope = child.params[frustra_jobs.ENVELOPE_KEY]
        selection = envelope['selection'][0]
        assert selection['design_id'] == design_id
        assert selection['producer_coordinates']['parent_design_id'] == design_id
        snapshot = Path(child.output_dir) / selection['snapshot_relative_path']
        assert snapshot.read_text() == PDB
        batch = json.loads(Path(child.params['frustrampnn_batch_manifest_path']).read_text())
        request = json.loads((Path(child.output_dir) / batch['records'][0]['request_relative_path']).read_text())
        assert request['requested_settings']['protein_selection']['mode'] == 'all_protein_entities'


def test_cif_snapshot_preserves_native_and_representable_insertion(tmp_path, monkeypatch):
    from Bio.PDB import MMCIFIO, PDBParser
    pdb = tmp_path / 'source.pdb'
    pdb.write_text(PDB.replace('A   1 ', 'a   0A'))
    cif = tmp_path / 'source.cif'
    writer = MMCIFIO()
    writer.set_structure(PDBParser(QUIET=True).get_structure('test', pdb))
    writer.save(str(cif))
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    monkeypatch.setattr(jobs, '_resolve_design_structure_path', lambda path: Path(path))
    root = Job(id='root', name='root', params={})
    design = Design(id='design', name='design', job_id=root.id, pdb_path=str(cif))
    directory = snapshot_selection(root, root, [design])
    item = json.loads((directory / 'selection_manifest.json').read_text())['designs'][0]
    assert Path(item['native_source_structure_path']).read_bytes() == cif.read_bytes()
    output = Path(item['selection_pdb_path'])
    parsed = PDBParser(QUIET=True).get_structure('test', output)
    assert list(parsed[0].child_dict) == ['a']
    assert next(parsed.get_residues()).id == (' ', 0, 'A')
