"""Real source custody -> native preview -> scratch Job insertion, no inference."""
import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from database import Job, get_session
from experiment_database import get_experiment_session
from routers import jobs, files, models
from schemas import JobCreate
from tests.test_binder_source_materialization import cif
from test_project_workflow_setups import setup_store
from test_project_generation_bound_jobs import isolated_core
from test_project_setup_job_preparation import isolate_applied_local_policy


def request(model, binding):
    native = ({'input': {'kind': 'annotated', 'slots': ['target']},
               'settings': {'seed': 0, 'metrics_tags': []}} if model == 'rfd4_proteina' else
              {'task': 'protein_binder', 'task_name': 'source-test', 'source': {'document_id': 'target'}})
    return dict(name='native source', model_id=model, mode='generate', execution_target_id=None,
                params={('rfd4_request' if model == 'rfd4_proteina' else 'complexa_request'): native,
                        'native_sources': {'target': binding}})


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['rfd4_proteina', 'complexa'])
@pytest.mark.parametrize('source_kind', ['path', 'candidate'])
@pytest.mark.parametrize('experimental', [False, True])
async def test_preview_insert_replay_clone_real_stores(isolated_core, setup_store,
        isolate_applied_local_policy, tmp_path, monkeypatch, model, source_kind, experimental):
    import paths
    roots = {'inputs': tmp_path / 'owned/inputs', 'bms_results': tmp_path / 'owned/results'}
    for module in (paths, jobs, files):
        monkeypatch.setattr(module, 'get_allowed_roots', lambda: roots)
    original = roots['inputs'] / 'selected.cif.gz'
    original.write_bytes(gzip.compress(cif(models=(3, 7)).encode()))
    original_digest = hashlib.sha256(original.read_bytes()).hexdigest()
    payload = request(model, {'path': 'inputs/selected.cif.gz', 'model_number': 7})
    if source_kind == 'candidate':
        from tests.test_denovo_selected_source import source_rows
        async with isolated_core() as session:
            original, _ = await source_rows(session, tmp_path / 'owned', False)
        original_digest = hashlib.sha256(original.read_bytes()).hexdigest()
        payload['params']['native_sources']['target'] = {
            'candidate': {'kind': 'design', 'job_id': 'source', 'design_id': 'design'},
            'model_number': 7}
        if model == 'rfd4_proteina':
            payload['params']['rfd4_request']['input'] = {'kind': 'condition_spec', 'examples': [
                {'name': 'conditioned', 'sources': [{'slot': 'target'}], 'contig': 'a42-42'}]}
    if experimental:
        payload.update(model_id='protein_modification_experimental', mode='de_novo_design')
        payload['params']['generator'] = model
    app = FastAPI()
    app.include_router(jobs.router, prefix='/jobs')
    app.include_router(models.router, prefix='/models')
    async def core_session():
        async with isolated_core() as session:
            yield session
    async def project_session():
        async with setup_store() as session:
            yield session
    app.dependency_overrides[get_session] = core_session
    app.dependency_overrides[get_experiment_session] = project_session
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        discovery = await client.get(f'/models/{model}/generation-settings?mode=generate')
        assert discovery.status_code == 200, discovery.text
        assert discovery.json()['native_sources_schema']['additionalProperties']['anyOf']
        preview = await client.post('/jobs/execution-plan/preview', json=payload)
        assert preview.status_code == 200, preview.text
        document = preview.json()
        assert document['native_review']['model_id'] == model
        assert document['admissible'], document['blockers']
        retained = document['request']
        source = retained['params']['native_sources']['target']
        assert source['expected_sha256'] == original_digest and source['model_number'] == 7
        assert source['path'].endswith('original.cif.gz')
        original.unlink()
        again = await client.post('/jobs/execution-plan/preview', json=retained)
        assert again.status_code == 200, again.text
        assert again.json()['approval_digest'] == document['approval_digest']
        response = await client.post('/jobs', json=retained)
        assert response.status_code == 201, response.text
        job_id = response.json()['id']
        detail = await client.get('/jobs/' + job_id)
        assert detail.status_code == 200, detail.text
    async with isolated_core() as session:
        job = await session.get(Job, job_id)
        assert job.execution_target_id is None and job.parent_job_id is None
        assert job.params['native_sources'] == retained['params']['native_sources']
        if model == 'rfd4_proteina':
            closure = Path(job.params['rfd4_input_closure'])
            bindings = json.loads(Path(job.params['rfd4_bindings']).read_text())
            consumed = closure / bindings['target']
            identity = json.loads(Path(job.params['rfd4_prepared_request']).read_text())['runtime_identity']['sources']['target']
            assert consumed.suffix == '.cif'
        else:
            closure = Path(job.params['complexa_request_dir'])
            consumed = closure / 'source.pdb'
            identity = json.loads((closure / 'sources.json').read_text())['target']
            assert consumed.read_text().startswith(('ATOM', 'MODEL'))
            assert 'data_' not in consumed.read_text()
        assert identity['original_sha256'] == original_digest
        assert identity['model_number'] == 7
        assert hashlib.sha256(consumed.read_bytes()).hexdigest() == identity['consumed_sha256']
        clone = JobCreate.model_validate({**retained, 'name': 'clone'})
        # A browser-copied closure is never trusted; source custody rebuilds it.
        clone.params.update(rfd4_input_closure='/invented', complexa_request_dir='/invented')
        await jobs._prepare_selected_structure(clone, session)
        key = 'rfd4_input_closure' if model == 'rfd4_proteina' else 'complexa_request_dir'
        assert clone.params[key] == job.params[key]
    assert document['input_identities'], 'Shared portable-input discovery must include the native closure'
    refs = document['input_identities']
    assert {row['source_path'] for row in refs} == {
        str(path) for path in closure.rglob('*') if path.is_file()}
    assert all(row['selector'][0] == key for row in refs)
    consumed_ref = next(row for row in refs if row['source_path'] == str(consumed))
    assert consumed_ref['sha256'] == identity['consumed_sha256']
    assert consumed_ref['size_bytes'] == consumed.stat().st_size
    # Exercise the real portable receiver after controller closure removal.
    import shutil
    from scripts.lib.portable_inputs import ENV, SCHEMA, resolve_input_path
    worker = tmp_path / 'worker'
    shutil.copytree(closure, worker)
    envelope = tmp_path / 'bindings.json'
    envelope.write_text(json.dumps({'schema': SCHEMA, 'roots': [str(worker)],
        'bindings': [{'reference': row,
                      'path': str(worker / Path(row['source_path']).relative_to(closure))}
                     for row in refs]}))
    shutil.rmtree(closure)
    monkeypatch.setenv(ENV, str(envelope))
    for row in refs:
        received = resolve_input_path(row['source_path'])
        assert received.is_relative_to(worker)
        assert hashlib.sha256(received.read_bytes()).hexdigest() == row['sha256']
    received = resolve_input_path(str(consumed))
    received.write_bytes(b'changed worker source')
    with pytest.raises(ValueError, match='digest/size mismatch'):
        resolve_input_path(str(consumed))


@pytest.mark.parametrize('model,mode,generator', [
    ('boltz2', 'predict', 'rfd4_proteina'),
    ('protein_modification_experimental', 'shape_blueprint', 'complexa'),
    ('protein_modification_experimental', 'de_novo_design', 'rfd3'),
])
def test_closure_discovery_is_model_scoped(tmp_path, model, mode, generator):
    from scripts.lib.portable_inputs import discover_native_input_references
    # Out-of-scope fields must not be opened, even if copied into params.
    assert discover_native_input_references(model, mode, {
        'generator': generator, 'rfd4_input_closure': '/invented/rfd4',
        'complexa_request_dir': '/invented/complexa'}, (),
        output_dir=tmp_path, allowed_roots=[tmp_path]) == []


@pytest.mark.asyncio
async def test_file_archive_and_legacy_list(isolated_core, tmp_path, monkeypatch):
    import paths
    roots = {'inputs': tmp_path / 'owned/inputs', 'bms_results': tmp_path / 'owned/results'}
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    archive = roots['inputs'] / 'adapter.pt'
    archive.write_bytes(b'inert archive bytes\x00')
    from tests.test_denovo_native_core import rfd4
    native = rfd4()
    native['adapter_slot'] = 'adapter'
    payload = JobCreate(name='archive', model_id='rfd4_proteina', mode='generate', params={
        'rfd4_request': native, 'native_sources': {'adapter': {'kind': 'file', 'path': 'inputs/adapter.pt'}}})
    async with isolated_core() as session:
        await jobs._prepare_selected_structure(payload, session)
        before = copy.deepcopy(payload.params)
        archive.unlink()
        await jobs._prepare_selected_structure(payload, session)
        assert payload.params == before
        closure = Path(payload.params['rfd4_input_closure'])
        bindings = json.loads(Path(payload.params['rfd4_bindings']).read_text())
        assert (closure / bindings['adapter']).read_bytes() == b'inert archive bytes\x00'
        legacy = JobCreate(name='legacy', model_id='boltz2', mode='predict',
                           params={'sequence': 'ACDE', 'native_sources': [{'path': 'untouched'}]})
        await jobs._prepare_selected_structure(legacy, session)
        assert legacy.params['native_sources'] == [{'path': 'untouched'}]
        from fastapi import HTTPException
        changed = paths.resolve_allowed_path(payload.params['native_sources']['adapter']['path'])
        changed.write_bytes(b'changed')
        with pytest.raises(HTTPException, match='Selected source bytes changed'):
            await jobs._prepare_selected_structure(payload, session)
        absent = JobCreate(name='no sources', model_id='rfd4_proteina', mode='generate',
                          params={'rfd4_request': rfd4()})
        before = copy.deepcopy(absent.params)
        await jobs._prepare_selected_structure(absent, session)
        assert absent.params == before


def test_discovery_slot_annotations():
    from services.rfd4_proteina import generation_settings
    from services.complexa import discovery
    schema = generation_settings()['parameter_schema']
    branches = schema['properties']['input']['oneOf']
    annotated = next(b for b in branches if b['properties']['kind']['const'] == 'annotated')
    condition = next(b for b in branches if b['properties']['kind']['const'] == 'condition_spec')
    assert annotated['properties']['slots']['items']['x-native-source-kind'] == 'structure'
    assert condition['properties']['examples']['items']['properties']['sources']['items']['properties']['slot']['x-native-source-kind'] == 'structure'
    assert schema['properties']['resume_slot']['x-native-source-kind'] == 'file'
    assert schema['properties']['adapter_slot']['x-native-source-kind'] == 'file'
    assert discovery()['schema']['$defs']['Source']['properties']['document_id']['x-native-source-kind'] == 'structure'
    counts = {}
    def check(node):
        if isinstance(node, dict):
            for key, child in node.get('properties', {}).items():
                if key == 'ligand_params_slots':
                    branch = next(b for b in child['anyOf'] if b.get('type') == 'array')
                    assert branch['items']['x-native-source-kind'] == 'file'
                    counts[key] = counts.get(key, 0) + 1
                if key == 'off_target_slots':
                    assert child['additionalProperties']['x-native-source-kind'] == 'structure'
                    counts[key] = counts.get(key, 0) + 1
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(schema)
    assert set(counts) == {'ligand_params_slots', 'off_target_slots'}
