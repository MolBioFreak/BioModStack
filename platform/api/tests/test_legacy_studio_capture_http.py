"""Exact mounted legacy capture → private custody / Template / Project / Prepare.

Transport fixtures are emitted by deNovoV15LegacyStudioCaptureMounted; no model
execution, live stores or synthesized replacement envelopes.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from test_annotated_structure_capture import capture_api  # noqa: F401
from test_core_protein_scientific_admission import admission  # noqa: F401
from test_project_workflow_setups import setup_store  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('generator', ['rfd3', 'laproteina'])
async def test_mounted_legacy_capture_cold_http_prepare(generator, capture_api, admission, setup_store, monkeypatch, tmp_path):
    wire_root = os.environ.get('BMS_LEGACY_CAPTURE_DIR')
    if not wire_root:
        pytest.skip('Requires mounted legacy capture wire; no substitute envelope')
    wire = json.loads((Path(wire_root) / f'{generator}.json').read_text())
    envelope = wire['envelope']
    request = envelope['native_job_request']
    params = request['params']
    slot = 'input' if generator == 'rfd3' else 'motif'
    document = envelope['editor_state']['molecular_documents'][slot]
    assert document['sha256'] == hashlib.sha256(wire['cif'].encode()).hexdigest()
    assert document['provenance']['revision'] == 4
    assert 'session_id' not in document['provenance']
    assert 'molecular_documents' not in params
    app, inputs = capture_api
    import paths
    from routers import jobs
    roots = {'inputs': inputs, 'bms_results': tmp_path / 'results'}
    monkeypatch.setattr(jobs, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: inputs)
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path)
    # Replay exact browser-uploaded bytes to the real private file authority.
    async with AsyncClient(transport=ASGITransport(app), base_url='http://private') as client:
        seen = set()
        for item in wire['uploads']:
            if item['path'] in seen:
                continue
            seen.add(item['path'])
            raw = item['text'].encode()
            assert hashlib.sha256(raw).hexdigest() == item['sha256']
            immutable = item['path'].endswith('.cif')
            response = await client.post('/api/files/' + ('upload-immutable-structure' if immutable else 'upload'),
                data={'path': str(Path(item['path']).parent), **({'sha256': item['sha256']} if immutable else {})},
                files={'file': (Path(item['path']).name, raw, 'chemical/x-mmcif' if immutable else 'chemical/x-pdb')})
            assert response.status_code == 200, response.text
            assert response.json()['path'] == item['path']
            response = await client.get('/api/files/download/' + item['path'])
            assert response.status_code == 200 and response.content == raw
        checked = await client.post('/api/files/materialize-structure', json={**document['source'], 'output_format': 'pdb' if generator == 'laproteina' else 'native'})
        assert checked.status_code == 200, checked.text
        receipt = checked.json()
        assert receipt['model_number'] == 1
        assert receipt['original_sha256'] == document['sha256']
        if generator == 'laproteina':
            assert receipt['format'] == 'pdb'
            response = await client.get('/api/files/download/' + receipt['path'])
            assert b'ALA A   1' in response.content and b'GLY A   2' in response.content
            motif_path = params['pcad_laproteina_settings_json']['motif_pdb']
            response = await client.get('/api/files/download/' + motif_path)
            assert b'GLY A   2' in response.content and b'ALA A   1' not in response.content
    # Real Template POST / twice PUT / connection-disposed GET.
    from database import get_session
    from routers import user_templates
    factory = async_sessionmaker(admission.bind, expire_on_commit=False)
    def template_app():
        application = FastAPI(); application.include_router(user_templates.router, prefix='/api/user-templates')
        async def sessions():
            async with factory() as session:
                yield session
        application.dependency_overrides[get_session] = sessions
        return application
    async with AsyncClient(transport=ASGITransport(template_app()), base_url='http://private') as client:
        response = await client.post('/api/user-templates', json={'name': generator + ' edited source', 'model_id': request['model_id'], 'mode': request['mode'], 'base_template_id': 'protein_modification_experimental', 'params': envelope})
        assert response.status_code == 201, response.text
        template_url = '/api/user-templates/' + response.json()['id']
        for _ in range(2):
            response = await client.put(template_url, json={'params': envelope})
            assert response.status_code == 200 and response.json()['params'] == envelope
    await admission.bind.dispose()
    async with AsyncClient(transport=ASGITransport(template_app()), base_url='http://cold') as client:
        response = await client.get(template_url)
        assert response.status_code == 200 and response.json()['params'] == envelope
    # Existing legacy Project capability, not a new native-model route.
    from experiment_models import ExperimentWorkflowPreparation
    from routers import projects
    from test_project_workflow_setups import _project_payload
    def project_app():
        from test_denovo_project_http import app_for
        application = app_for(setup_store, factory)
        application.include_router(projects.router)
        return application
    async with AsyncClient(transport=ASGITransport(project_app()), base_url='http://private') as client:
        response = await client.post('/api/projects', json=_project_payload('Legacy capture'))
        assert response.status_code == 201, response.text
        project_id = response.json()['id']
        response = await client.post(f'/api/projects/{project_id}/workflow-setups', json={
            'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary',
            'experiment': {'name': 'Captured legacy', 'objective': 'Preserve capture'},
            'domain_kind': 'protein_in_silico', 'capability_id': f'protein.de_novo.{generator}'},
            headers={'Idempotency-Key': 'create'})
        assert response.status_code == 201, response.text
        setup = response.json()
    url = f"/api/projects/{project_id}/workflow-setups/{setup['setup_context_id']}"
    async with AsyncClient(transport=ASGITransport(project_app()), base_url='http://private') as client:
        for generation in (0, 1):
            response = await client.put(url + '/draft', json={'expected_generation': generation, 'draft': envelope}, headers={'Idempotency-Key': f'save-{generation}'})
            assert response.status_code == 200, response.text
            assert response.json()['draft'] == {**envelope['editor_state'], 'native_job_request': request}
    await setup_store.kw['bind'].dispose()
    async with AsyncClient(transport=ASGITransport(project_app()), base_url='http://cold') as client:
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.json()['draft'] == {**envelope['editor_state'], 'native_job_request': request}
        response = await client.post(url + '/prepare-launch', json={'expected_generation': 2}, headers={'Idempotency-Key': 'prepare'})
        assert response.status_code == 200, response.text
        async with setup_store() as session:
            prepared = await session.get(ExperimentWorkflowPreparation, response.json()['preparation_id'])
            scheduler = json.loads(prepared.scheduler_payload_json)
        assert scheduler['params']['generator'] == generator
        assert 'molecular_documents' not in scheduler['params']
    # Exercise the selected native preparation without running a model.
    if generator == 'rfd3':
        from services.rfd3_generation import normalize_generation_params
        normalized, _, _ = normalize_generation_params(params, job_name='captured')
        from scripts.rfd3_generation import contract
        monkeypatch.setitem(sys.modules, 'contract', contract)
        from scripts.rfd3_generation.prepare_native_input import prepare
        native, _ = prepare(normalized['rfd3_generation_request'], source_destination=tmp_path / 'native-source.cif')
        for key, value in params['conditioning'].items():
            assert native['generation_0'][key] == value
        assert (tmp_path / native['generation_0']['input']).is_file()
    else:
        settings = dict(params['pcad_laproteina_settings_json'])
        # The ordinary path resolver normally receives staged absolute bindings.
        # Resolve this private logical path into its exact already persisted bytes.
        settings['motif_pdb'] = str(inputs / Path(settings['motif_pdb']).relative_to('inputs'))
        output = tmp_path / 'cad.json'
        command = [sys.executable, str(Path(__file__).resolve().parents[3] / 'scripts/prep_protein_cad_request.py'), '--backend', 'laproteina', '--task', 'motif_scaffolding', '--num-designs', str(params['num_designs']), '--target-lengths', params['target_lengths'], '--laproteina-preset', params['laproteina_preset'], '--laproteina-contig-string', params['laproteina_contig_string'], '--laproteina-motif-task-name', params['laproteina_motif_task_name'], '--laproteina-settings-json', json.dumps(settings), '--output', str(output), '--input-dir', str(tmp_path / 'cad')]
        done = subprocess.run(command, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        native = json.loads(output.read_text())['laproteina']
        assert native['contig_string'] == '10/A2/10'
        assert native['motif_atom_spec'] is None and native['future_option'] is False
        assert 'GLY A   2' in Path(native['motif_pdb']).read_text()
        assert 'ALA A   1' not in Path(native['motif_pdb']).read_text()
