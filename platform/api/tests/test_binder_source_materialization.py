"""Real shared source routes, scratch stores and checked representation only."""
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from Bio.PDB import PDBParser, MMCIFIO
from Bio.PDB.MMCIF2Dict import MMCIF2Dict

from database import Design, Job, get_session
from experiment_database import get_experiment_session
from routers import files
from services.bindcraft2_publication import publish_native_results
from experiment_services import create_project, create_global_experiment, create_domain_experiment, create_dataset, save_dataset_revision
from services.global_experiments.receipts import attach_verified_entity
from test_project_manager_adapters import stores, _project_payload, _global_payload, _domain_payload
from test_bindcraft2_publication import campaign

PDB = 'ATOM      1  CA  ALA a  42B      1.000   2.000   3.000  1.00 20.00           C  \nEND\n'


def cif(chain='a', models=(3,)):
    structure = PDBParser(QUIET=True).get_structure('fixture', io.StringIO(PDB))
    out = io.StringIO(); writer = MMCIFIO(); writer.set_structure(structure); writer.save(out)
    columns = MMCIF2Dict(io.StringIO(out.getvalue()))
    for key, value in list(columns.items()):
        if key.startswith('_atom_site.'):
            columns[key] = value * len(models)
    columns['_atom_site.auth_asym_id'] = [chain] * len(models)
    columns['_atom_site.id'] = [str(i + 1) for i in range(len(models))]
    columns['_atom_site.pdbx_PDB_model_num'] = [str(n) for n in models]
    columns['_atom_site.Cartn_x'] = [str(n) for n in models]
    writer.set_dict(columns); out = io.StringIO(); writer.save(out)
    return out.getvalue()


@pytest_asyncio.fixture
async def source_api(stores, monkeypatch):
    root, experiments, core = stores
    import paths
    roots = {'inputs': root / 'inputs', 'bms_results': root / 'results'}
    for path in roots.values(): path.mkdir(exist_ok=True)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: roots)
    app = FastAPI(); app.include_router(files.router, prefix='/api/files')
    async with experiments() as experiment_session, core() as session:
        async def core_dep(): yield session
        async def experiment_dep(): yield experiment_session
        app.dependency_overrides[get_session] = core_dep
        app.dependency_overrides[get_experiment_session] = experiment_dep
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            yield client, session, experiment_session, root


@pytest.mark.asyncio
async def test_upload_native_cif_and_checked_pdb_boundary_preserves_author_model_identity(source_api):
    client, _, _, root = source_api
    raw = cif().encode()
    uploaded = await client.post('/api/files/upload', data={'path': 'inputs'}, files={'file': ('source.cif', raw, 'chemical/x-mmcif')})
    assert uploaded.status_code == 200, uploaded.text
    path = uploaded.json()['path']
    for output_format in ('native', 'pdb'):
        response = await client.post('/api/files/materialize-structure', json={'path': path, 'output_format': output_format})
        assert response.status_code == 200, response.text
        result = response.json()
        original = await client.get('/api/files/download/' + result['native_path'])
        assert original.content == raw
        assert result['native_sha256'] == hashlib.sha256(raw).hexdigest()
        assert result['model_numbers'] == [3]
        assert result['author_residues'] == [{'model_number': 3, 'auth_asym_id': 'a', 'auth_seq_id': 42, 'insertion_code': 'B', 'residue_name': 'ALA'}]
        consumed = await client.get('/api/files/download/' + result['path'])
        if output_format == 'native':
            assert consumed.content == raw
        else:
            assert result['path'].endswith('.pdb')
            assert b'ATOM' in consumed.content and b'_atom_site' not in consumed.content
            structure = PDBParser(QUIET=True).get_structure('derived', io.StringIO(consumed.text))
            residue = next(structure.get_residues())
            assert residue.get_parent().id == 'a' and residue.id == (' ', 42, 'B')
    assert (root / 'inputs/source.cif').read_bytes() == raw


@pytest.mark.asyncio
async def test_exact_model_selection_and_no_loss_or_primary_fallback(source_api):
    client, _, _, root = source_api
    (root / 'inputs/multi.cif').write_text(cif(models=(3, 7)))
    response = await client.post('/api/files/materialize-structure', json={'path': 'inputs/multi.cif', 'output_format': 'pdb'})
    assert response.status_code == 422 and 'single' in response.text
    response = await client.post('/api/files/materialize-structure', json={'path': 'inputs/multi.cif', 'output_format': 'pdb', 'model_number': 7})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['model_number'] == 7 and result['model_numbers'] == [3, 7]
    assert {r['model_number'] for r in result['author_residues']} == {7}
    consumed = await client.get('/api/files/download/' + result['path'])
    assert '   7.000' in consumed.text and '   3.000   2.000' not in consumed.text
    for payload in ({'path': 'inputs/multi.cif', 'model_number': 9}, {'path': 'inputs/multi.cif', 'expected_sha256': '0' * 64}, {'path': '../outside.cif'}):
        assert (await client.post('/api/files/materialize-structure', json=payload)).status_code == 422
    (root / 'inputs/long.cif').write_text(cif(chain='long_author'))
    rejected = await client.post('/api/files/materialize-structure', json={'path': 'inputs/long.cif', 'output_format': 'pdb'})
    assert rejected.status_code == 422 and 'identifiers' in rejected.text
    native = await client.post('/api/files/materialize-structure', json={'path': 'inputs/long.cif', 'output_format': 'native'})
    assert native.status_code == 200 and native.json()['author_residues'][0]['auth_asym_id'] == 'long_author'


@pytest.mark.asyncio
async def test_real_project_resource_browse_exact_document_materialization(source_api):
    client, core, experiments, root = source_api
    output = root / 'results/campaign'; campaign(output)
    for state in ('stateA', 'stateB'):
        path = output / '3_Ranked' / f't_seq0_{state}.cif'
        header = path.read_text().split('_atom_site.id')[0]
        path.write_text(header + cif(models=(3 if state == 'stateA' else 7,)).split('\n', 1)[1])
    job = Job(id='campaign', name='Fixture campaign', model_id='bindcraft2', mode='campaign', params={}, status='completed', output_dir=str(output))
    core.add(job); await core.flush(); await publish_native_results(job, output, core); await core.commit()
    project = await create_project(experiments, _project_payload())
    experiment = await create_global_experiment(experiments, project.id, _global_payload())
    domain = await create_domain_experiment(experiments, project.id, experiment.id, _domain_payload('protein_in_silico'))
    attached = await attach_verified_entity(experiments, core, project_id=project.id,
        global_experiment_id=experiment.id, domain_experiment_id=domain.id,
        adapter_id='bms.native-binder.bindcraft2.adapter.v1', entity_id=job.id,
        operation='link_output', role='produced', note=None, expected_head_generation=project.head_generation)
    await experiments.commit()
    projects = (await client.get('/api/files/structure-sources')).json()
    assert projects['items'][0]['project_id'] == project.id
    query = {'project_id': project.id}
    resources = (await client.get('/api/files/structure-sources', params=query)).json()
    assert resources['total'] == 1
    query['receipt_id'] = resources['items'][0]['receipt_id']
    assert query['receipt_id'] == attached['source_receipt_id']
    designs = (await client.get('/api/files/structure-sources', params=query)).json()
    assert designs['total'] == 1
    query['design_id'] = designs['items'][0]['design_id']
    documents = (await client.get('/api/files/structure-sources', params=query)).json()
    assert documents['items'] and documents['items'][0]['kind'] == 'document'
    doc = documents['items'][0]
    assert doc['document']['artifact_id'] and doc['document']['sha256']
    alternate = next(item for item in documents['items'] if item['document']['target_state'] == 'stateB')
    response = await client.post('/api/files/materialize-structure', json={
        'design_id': query['design_id'], 'job_id': job.id, 'document': {'artifact_id': alternate['document']['artifact_id']},
        'expected_sha256': alternate['document']['sha256'], 'output_format': 'pdb'})
    assert response.status_code == 200, response.text
    materialized = response.json()
    assert materialized['source_identity']['target_state'] == 'stateB'
    assert materialized['model_numbers'] == [7]
    assert materialized['native_sha256'] == alternate['document']['sha256']
    original_design = await core.get(Design, query['design_id'])
    assert 'stateA' in original_design.pdb_path
    snapshot = await client.get('/api/files/download/' + materialized['path'])
    assert '   7.000' in snapshot.text
    wrong = await client.post('/api/files/materialize-structure', json={'design_id': query['design_id'], 'job_id': 'other', 'output_format': 'native'})
    assert wrong.status_code == 422
    wrong = await client.post('/api/files/materialize-structure', json={'design_id': query['design_id'], 'document': {'artifact_id': 'not-this-document'}})
    assert wrong.status_code == 422
    assert (await client.get('/api/files/structure-sources', params={**query, 'design_id': 'foreign'})).status_code == 404
    assert (await client.get('/api/files/structure-sources', params={'project_id': 'foreign', 'receipt_id': query['receipt_id']})).status_code == 404
    # Pin a real Dataset revision through the existing storage owner. Dataset
    # cohort-kind admission remains with its existing owner, not this browse API.
    dataset = await create_dataset(experiments, project.id, 'Pinned sources', 'protein.generated_candidate_cohort.v1', experiment_id=domain.id)
    revision = await save_dataset_revision(experiments, dataset.aggregate_id, {'members': []}, expected_head_generation=dataset.head_generation)
    await experiments.commit()
    datasets = (await client.get('/api/files/structure-sources', params={'project_id': project.id, 'collection': 'datasets'})).json()
    assert datasets['items'][0]['revision_id'] == revision.resource_id
    assert (await client.get('/api/files/structure-sources', params={'project_id': project.id, 'dataset_id': dataset.aggregate_id, 'revision_id': revision.resource_id})).json()['total'] == 0
    assert (await client.get('/api/files/structure-sources', params={'project_id': project.id, 'dataset_id': dataset.aggregate_id, 'revision_id': 'other'})).status_code == 404
    # Exercise the existing immutable Dataset storage representation with an
    # explicit attached receipt (not a claim to expand Dataset kind admission).
    from experiment_models import ExperimentExternalEntityReceipt
    receipt = await experiments.get(ExperimentExternalEntityReceipt, attached['source_receipt_id'])
    member = {'identity': receipt.id, 'role': 'source', 'value': {
        'receipt_id': receipt.id, 'native_content_sha256': receipt.content_digest,
        'native_revision_or_generation': receipt.generation_or_revision, 'metadata': {'display_label': 'Exact campaign'}}}
    await experiments.refresh(dataset)
    populated = await save_dataset_revision(experiments, dataset.aggregate_id, {'members': [member]}, expected_head_generation=dataset.head_generation)
    await experiments.commit()
    pinned = {'project_id': project.id, 'dataset_id': dataset.aggregate_id, 'revision_id': populated.resource_id}
    page = (await client.get('/api/files/structure-sources', params=pinned)).json()
    assert page['items'][0]['receipt_id'] == receipt.id
    page = await client.get('/api/files/structure-sources', params={**pinned, 'receipt_id': receipt.id})
    assert page.status_code == 200 and page.json()['total'] == 1
    # Old revision remains empty, never silently replaced by the new head.
    assert (await client.get('/api/files/structure-sources', params={**pinned, 'revision_id': revision.resource_id})).json()['total'] == 0
    assert (await client.get('/api/files/structure-sources', params={**pinned, 'revision_id': revision.resource_id, 'receipt_id': receipt.id})).status_code == 404
    (output / '3_Ranked/t_seq0_stateB.cif').write_text('changed producer bytes')
    changed = await client.post('/api/files/materialize-structure', json={'design_id': query['design_id'], 'document': {'artifact_id': alternate['document']['artifact_id']}})
    assert changed.status_code == 422 and 'digest' in changed.text
    assert (await client.get('/api/files/download/' + materialized['path'])).content == snapshot.content


@pytest.mark.asyncio
async def test_native_inspection_metadata_failure_is_not_a_native_source_gate(source_api):
    client, _, _, root = source_api
    raw = b'data_native\n_custom.native_field retained\n'
    (root / 'inputs/native.cif').write_bytes(raw)
    response = await client.post('/api/files/materialize-structure', json={'path': 'inputs/native.cif', 'output_format': 'native'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['inspection_error'] and result['author_residues'] == []
    assert (await client.get('/api/files/download/' + result['path'])).content == raw
