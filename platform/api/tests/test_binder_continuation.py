"""Selected continuation: real endpoint, SQLite persistence and snapshot identity."""
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from experiment_database import get_experiment_session
from test_project_workflow_setups import setup_store
from routers import binder_continuation as route, jobs
from services.binder_continuation import resolve_root, snapshot_selection

PDB = ''.join(
    f'ATOM  {serial:5d}  {atom:<3} ALA A   1    {float(serial):8.3f}{2.0:8.3f}{3.0:8.3f}  1.00 80.00          {element:>2}  \n'
    for serial, (atom, element) in enumerate([('N', 'N'), ('CA', 'C'), ('C', 'C'), ('O', 'O')], 1)
) + 'TER\nEND\n'


@pytest_asyncio.fixture
async def selected(tmp_path, monkeypatch, setup_store):
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
        async def experiment_dependency():
            async with setup_store() as experiments:
                yield experiments
        app.dependency_overrides[get_experiment_session] = experiment_dependency
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


async def alternate_document_fixture(selected):
    import hashlib
    from Bio.PDB import MMCIFIO, PDBParser
    from database import JobArtifact
    _, session, _, owner, tmp_path = selected
    pdb = tmp_path / 'alternate.pdb'
    pdb.write_text(PDB.replace('ALA', 'GLY').replace('A   1 ', 'a   0A'))
    cif = tmp_path / 'alternate.cif'
    writer = MMCIFIO()
    writer.set_structure(PDBParser(QUIET=True).get_structure('alternate', pdb))
    writer.save(str(cif))
    # Bio.PDB emits unknown entity IDs; this one-chain fixture has one entity.
    # Supply the native atom-site authority required by the FrustraMPNN owner.
    document = dict(writer.dic)
    document['_atom_site.label_entity_id'] = ['1'] * len(document['_atom_site.id'])
    document['_atom_site.auth_atom_id'] = document['_atom_site.label_atom_id']
    document['_atom_site.auth_comp_id'] = document['_atom_site.label_comp_id']
    writer.set_dict(document)
    writer.save(str(cif))
    payload = cif.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    owner.provenance = {'bindcraft2_native_publication': {'candidates': [
        {'design_id': 'd1', 'structures': [{'artifact_id': 'alternate', 'target_state': 'alternate-state',
         'sha256': digest, 'logical_path': 'bindcraft2/native/alternate.cif',
         'native_to_derived_map': {'chain': {'a': 'a'}, 'residue': {'a:0A': 'a:0A'}}}]}]}}
    design = await session.get(Design, 'd1')
    design.review_role_map = {'binder': ['a']}
    design.review_artifact_manifest = {'native_format': 'mmcif', 'derived_format': 'pdb'}
    session.add(JobArtifact(id='alternate', owner_job_id=owner.id, attempt=0,
        logical_path='bindcraft2/native/alternate.cif', storage_path=str(cif),
        sha256=digest, bytes=len(payload), media_type='chemical/x-mmcif'))
    await session.commit()
    return cif, payload, digest


@pytest.mark.asyncio
@pytest.mark.parametrize('selector', [{'artifact_id': 'alternate'}, {'target_state': 'alternate-state'},
    {'artifact_id': 'alternate', 'target_state': 'alternate-state'}])
async def test_explicit_cif_document_uses_sibling_producer_and_keeps_primary(selected, selector):
    client, session, root, _, _ = selected
    cif, native, digest = await alternate_document_fixture(selected)
    # Selecting from the root must still resolve the persisted sibling producer.
    before = (await session.get(Design, 'd1')).pdb_path
    before_manifest = (await session.get(Design, 'd1')).review_artifact_manifest
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': root.id, 'design_ids': ['d1', 'd0'], 'operation': 'predict_boltz2',
        'params': {'boltz_use_msa': False}, 'candidate_documents': {'d1': selector}})
    assert response.status_code == 201, response.text
    children = response.json()['launched_jobs']
    assert len(children) == 2
    child = await session.get(Job, children[0]['id'])
    assert child.params['complex_components'] == [{'id': 'a', 'type': 'protein', 'sequence': 'G'}]
    manifest = json.loads(Path(child.params['selected_input_manifest']).read_text())
    row, primary = manifest['designs']
    assert row['selected_document']['owner_job_id'] == 'round1'
    assert row['selected_document']['artifact_sha256'] == digest
    assert row['selected_document']['producer_document']['native_to_derived_map']['chain'] == {'a': 'a'}
    assert row['source_review_role_map'] == {'binder': ['a']}
    assert row['source_review_artifact_manifest'] == before_manifest
    assert 'selected_document' not in primary
    assert Path(primary['selection_pdb_path']).read_text() == PDB
    source_meta = json.loads(Path(child.params['source_identity_json']).read_text())[0]['source_meta']
    assert source_meta['structure_state'] == 'alternate-state'
    assert source_meta['parent_design_id'] == 'd1'
    cif.unlink()
    assert Path(row['native_source_structure_path']).read_bytes() == native
    await session.refresh(await session.get(Design, 'd1'))
    assert (await session.get(Design, 'd1')).pdb_path == before


@pytest.mark.asyncio
@pytest.mark.parametrize('selector', [None, {}])
async def test_omitted_or_empty_selector_preserves_primary_with_alternate_present(selected, selector):
    client, session, _, source, _ = selected
    await alternate_document_fixture(selected)
    payload = {'source_job_id': source.id, 'design_ids': ['d1'], 'operation': 'proteinmpnn',
               'params': {'mpnn_relax_max_cycles': 0}}
    if selector is not None:
        payload['candidate_documents'] = {'d1': selector}
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    child = await session.get(Job, response.json()['launched_jobs'][0]['id'])
    assert Path(child.params['input_pdb']).read_text() == PDB


@pytest.mark.asyncio
@pytest.mark.parametrize('documents', [
    {'foreign-design': {'artifact_id': 'alternate'}},
    {'d0': {'artifact_id': 'alternate'}},
    {'d1': {'artifact_id': 'foreign-artifact'}},
    {'d1': {'artifact_id': 'alternate', 'target_state': 'wrong'}},
    {'d1': {'target_state': 'missing'}},
])
async def test_bad_document_identity_fails_before_materialization(selected, documents):
    client, _, _, source, tmp_path = selected
    await alternate_document_fixture(selected)
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source.id, 'design_ids': ['d1', 'd0'], 'operation': 'refine',
        'candidate_documents': documents})
    assert response.status_code == 422, response.text
    assert not (tmp_path / 'inputs').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'changed', 'foreign-owner', 'receipt'])
async def test_document_read_and_integrity_failures_never_queue(selected, failure):
    from database import JobArtifact
    from sqlalchemy import select
    client, session, _, source, _ = selected
    cif, _, _ = await alternate_document_fixture(selected)
    artifact = await session.get(JobArtifact, 'alternate')
    if failure == 'missing': cif.unlink()
    elif failure == 'changed': cif.write_text(cif.read_text().replace('GLY', 'ALA'))
    elif failure == 'foreign-owner': artifact.owner_job_id = 'root'
    else: artifact.sha256 = '0' * 64
    await session.commit()
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source.id, 'design_ids': ['d1'], 'operation': 'proteinmpnn',
        'candidate_documents': {'d1': {'artifact_id': 'alternate'}}})
    assert response.status_code == 422, response.text
    assert len((await session.scalars(select(Job))).all()) == 2


@pytest.mark.asyncio
async def test_alternate_remote_review_keeps_snapshot_and_single_job_destination(selected, setup_store):
    from test_project_normalized_child_requests import destination
    from experiment_models import ExperimentLaunchContext
    async with setup_store() as experiments:
        dest = await destination(experiments)
        await experiments.commit()
    client, _, _, source, tmp_path = selected
    cif, native, _ = await alternate_document_fixture(selected)
    payload = {
        'source_job_id': source.id, 'design_ids': ['d1'], 'operation': 'refine',
        'candidate_documents': {'d1': {'target_state': 'alternate-state'}},
        'params': {'binder_chains': 'a', 'target_chains': 'B', 'maturation_repack_enabled': True},
        'execution_target_id': 'vast:selected', 'launch_context_id': dest['launch_context_id'],
        'idempotency_key': 'alternate-retained-review'}
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 409, response.text
    prepared = response.json()['detail']['job_request']
    assert prepared['launch_context_id'] != dest['launch_context_id']
    async with setup_store() as experiments:
        child = await experiments.get(ExperimentLaunchContext, prepared['launch_context_id'])
        assert child.project_id == dest['project_id'] and child.run_attempt_id
        assert (await experiments.get(ExperimentLaunchContext, dest['launch_context_id'])).state == 'issued'
    assert 'launch_context_id' not in prepared['params']
    manifest = json.loads(Path(prepared['params']['selected_input_manifest']).read_text())
    cif.unlink()
    assert Path(manifest['designs'][0]['native_source_structure_path']).read_bytes() == native
    replay = await client.post('/api/binder-continuation/selected', json=payload)
    assert replay.status_code == response.status_code
    assert replay.json() == response.json()
    changed = await client.post('/api/binder-continuation/selected', json={**payload, 'design_ids': ['d0']})
    assert changed.status_code == 409
    assert changed.json() != response.json()
    assert len(list((tmp_path / 'inputs' / 'design_selections' / 'binder').iterdir())) == 1


@pytest.mark.asyncio
async def test_frustrampnn_explicit_document_uses_native_owner_snapshot(selected, monkeypatch):
    from services.frustrampnn import jobs as frustra_jobs
    client, session, root, source, tmp_path = selected
    cif, native, digest = await alternate_document_fixture(selected)
    results = tmp_path / 'frustra-results'
    results.mkdir()
    monkeypatch.setattr(frustra_jobs, 'get_results_dir', lambda: results)
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': root.id, 'design_ids': ['d1'], 'operation': 'frustrampnn',
        'candidate_documents': {'d1': {'artifact_id': 'alternate', 'target_state': 'alternate-state'}}})
    assert response.status_code == 201, response.text
    child = await session.get(Job, response.json()['launched_jobs'][0]['id'])
    selection = child.params[frustra_jobs.ENVELOPE_KEY]['selection'][0]
    assert selection['sha256'] == digest
    assert selection['producer_coordinates']['parent_design_id'] == 'd1'
    assert selection['producer_coordinates']['selected_document']['artifact_id'] == 'alternate'
    assert selection['producer_coordinates']['target_state'] == 'alternate-state'
    assert selection['producer_coordinates']['selected_document']['producer_document']['native_to_derived_map']['chain'] == {'a': 'a'}
    assert (await session.get(Design, 'd1')).pdb_path == str(tmp_path / 'state-1.pdb')
    cif.unlink()
    assert (Path(child.output_dir) / selection['snapshot_relative_path']).read_bytes() == native


@pytest.mark.asyncio
@pytest.mark.parametrize('selector', [None, {}, {'artifact_id': 'alternate'}, {'target_state': 'alternate-state'}])
async def test_frustrampnn_resolver_exact_document_and_primary_fallback(selected, selector):
    from services.binder_diagnostic_selection import CandidateDocument
    from services.frustrampnn.jobs import design_selections
    _, session, _, source, _ = selected
    _, native, digest = await alternate_document_fixture(selected)
    documents = {'d1': CandidateDocument(**selector)} if selector is not None else None
    resolved = await design_selections(session, source_parent=source, design_ids=['d1'],
                                      candidate_documents=documents)
    assert len(resolved) == 1
    item = resolved[0]
    assert item.source_job_id == source.id
    if selector:
        assert item.source_bytes == native
        assert item.source_sha256 == digest
        assert item.source_format == 'mmcif'
    else:
        assert item.source_bytes == PDB.encode()
        assert 'selected_document' not in item.producer_coordinates


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['changed', 'foreign-owner', 'unselected', 'outside-root', 'wrong-source'])
async def test_frustrampnn_resolver_preserves_owned_document_boundary(selected, failure):
    from database import JobArtifact
    from services.binder_diagnostic_selection import CandidateDocument
    from services.frustrampnn.jobs import design_selections
    _, session, root, source, tmp_path = selected
    cif, native, _ = await alternate_document_fixture(selected)
    artifact = await session.get(JobArtifact, 'alternate')
    documents = {'d1': CandidateDocument(artifact_id='alternate')}
    if failure == 'changed':
        cif.write_bytes(native + b'\n# changed\n')
    elif failure == 'foreign-owner':
        artifact.owner_job_id = root.id
    elif failure == 'unselected':
        documents['d0'] = CandidateDocument(artifact_id='alternate')
    elif failure == 'outside-root':
        source.output_dir = str(tmp_path / 'different-owned-root')
    else:
        source = root
    await session.flush()
    with pytest.raises(ValueError):
        await design_selections(session, source_parent=source, design_ids=['d1'],
                                candidate_documents=documents)


@pytest.mark.asyncio
async def test_fanout_queue_failure_rolls_back_all_jobs_without_changing_primary(selected, monkeypatch):
    from sqlalchemy import select
    client, session, _, source, _ = selected
    await alternate_document_fixture(selected)
    original = jobs.create_job
    calls = 0
    async def fail_second(request, tasks, session, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            from fastapi import HTTPException
            raise HTTPException(422, 'native owner request failed')
        return await original(request, tasks, session, **kwargs)
    monkeypatch.setattr(jobs, 'create_job', fail_second)
    source_id = source.id
    before = (await session.get(Design, 'd1')).pdb_path
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source_id, 'design_ids': ['d1', 'd0'], 'operation': 'predict_boltz2',
        'params': {'boltz_use_msa': False},
        'candidate_documents': {'d1': {'target_state': 'alternate-state'}}})
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == 'native owner request failed'
    assert calls == 2
    assert len((await session.scalars(select(Job))).all()) == 2
    assert (await session.get(Design, 'd1')).pdb_path == before


@pytest.mark.asyncio
async def test_ambiguous_state_requires_one_document_without_choosing_policy(selected):
    client, session, _, source, tmp_path = selected
    await alternate_document_fixture(selected)
    import copy
    publication = copy.deepcopy(source.provenance)
    structures = publication['bindcraft2_native_publication']['candidates'][0]['structures']
    structures.append({**structures[0], 'artifact_id': 'second-alternate'})
    source.provenance = publication
    await session.commit()
    response = await client.post('/api/binder-continuation/selected', json={
        'source_job_id': source.id, 'design_ids': ['d1'], 'operation': 'refine',
        'candidate_documents': {'d1': {'target_state': 'alternate-state'}}})
    assert response.status_code == 422
    assert 'Select one producer-bound document' in response.json()['detail']
    assert not (tmp_path / 'inputs').exists()
