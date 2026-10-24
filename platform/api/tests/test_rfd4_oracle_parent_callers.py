"""Actual shared callers with the parent's exact patch applied; no native execution."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
from test_rfd4_oracle_closure import params, assets_on_disk, service
from test_remote_bundle_runtime_gaps import package, compile_native


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id,profile,tags', [
    ('rfd4_proteina', 'of3', None), ('rfd4_proteina', 'af2', None),
    ('rfd4_proteina', 'af3', None), ('rfd4_proteina', 'af3', []),
    ('protein_modification_experimental', 'of3', None),
    ('rfd4_proteina', 'af3', ['cath']),
])
async def test_actual_selected_registry_and_remote_bundle(package, tmp_path, monkeypatch, model_id, profile, tags):
    from services.remote_execution import bundle
    from model_registry import selected_execution_metadata
    roots, _, job, target, _ = package
    p = params(profile, tags=tags)
    assets = assets_on_disk(roots['data'] / 'asset-fixture', p)
    # Install these tiny inert declarations in the *existing* managed roots.
    for logical, source in assets.items():
        kind, relative = logical.split('/', 1)
        destination = roots[{'containers': 'containers', 'weights': 'weights', 'data': 'data'}[kind]] / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        if Path(source).is_dir(): shutil.copytree(source, destination, dirs_exist_ok=True)
        else: shutil.copyfile(source, destination)
    (roots['containers'] / 'rfd4_proteina.sif').write_bytes(b'INERT RFD4 fixture image; never execute')
    pair = roots['weights'] / 'rfd4-proteina'; pair.mkdir(exist_ok=True)
    (pair / 'config.json').write_text('{}')
    (pair / 'model.safetensors').write_bytes(b'INERT paired weights; never load')
    p.update({'code_root': str(roots['repo']), 'weights_root': str(roots['weights']),
              'container_dir': str(roots['containers']), 'data_root': str(roots['data']),
              'rfd4_proteina_bundle': str(pair)})
    if model_id == 'protein_modification_experimental':
        p['generator'] = 'rfd4_proteina'
    job.model_id, job.mode, job.params = model_id, ('generate' if model_id == 'rfd4_proteina' else 'de_novo_design'), p
    command = compile_native(job, p)
    invocation = job.native_invocation
    invocation.materialize_inputs(Path(job.output_dir))
    expected = service.selected_oracle_assets('generate', p)
    logical_ids = {row.logical_id for row in invocation.execution_plan.dependencies}
    assert {row['kind'] + ':' + row['relative_path'] for row in expected}.issubset(logical_ids)
    assert ('weights:rfd4-oracles/of3' in logical_ids) == (profile == 'of3')
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                           native_invocation=invocation)
    runtime = json.loads(prepared.envelope.environment['BMS_RFD4_ORACLE_RUNTIME'])
    if tags == []:
        assert runtime == {}
        assert not any('rfd4-oracles' in row for row in logical_ids)
    elif tags == ['cath']:
        assert set(runtime) == {'foldseek'}
        assert set(runtime['foldseek']) == {'FOLDSEEK_EXEC', 'CATH_FOLDSEEK_DB', 'CATH_DOMAIN_LIST'}
        assert all('/materialized/runtime/data/' in path for path in runtime['foldseek'].values())
    else:
        prefix = 'AF2' if profile == 'af2' else 'AF3'
        assert set(runtime) == {profile}
        assert '/cache/runtime-images/objects/sha256/' in runtime[profile][prefix + '_IMAGE']
        assert '/cache/artifacts/v1/weights/' in runtime[profile][prefix + '_MODEL_DIR']
        assert str(roots['data']) not in json.dumps(runtime)
        assert not any('JAX_CACHE' in key for values in runtime.values() for key in values)
    assert 'BMS_RFD4_ORACLE_RUNTIME' not in p
    from services import nextflow
    class Session:
        async def commit(self): pass
    local_environment = await nextflow._pin_local_invocation_images(Session(), job, invocation)
    local_runtime = json.loads(local_environment['BMS_RFD4_ORACLE_RUNTIME'])
    assert set(local_runtime) == set(runtime)
    if tags is None:
        prefix = 'AF2' if profile == 'af2' else 'AF3'
        assert local_runtime[profile][prefix + '_MODEL_DIR'] == str(roots['weights'] / ('alphafold' if profile == 'af2' else 'rfd4-oracles/' + profile))
        assert local_runtime[profile][prefix + '_IMAGE'] == str(roots['containers'] / ('af2-open.sif' if profile == 'af2' else 'af3-open.sif'))
    assert 'JAX_CACHE' not in json.dumps(local_runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize('active', [False, True])
async def test_actual_source_free_preparation_seam(tmp_path, monkeypatch, active):
    from types import SimpleNamespace
    from services.native_generation_sources import prepare_native_sources
    from routers import files
    import paths
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'fixture': tmp_path})
    monkeypatch.setattr(paths, 'resolve_allowed_path', lambda value: tmp_path / value)
    async def no_governed(_): return []
    monkeypatch.setattr(files, 'get_governed_ngs_result_roots', no_governed)
    p = params(tags=None if active else [])
    monkeypatch.setattr(paths, 'get_container_dir', lambda: tmp_path / 'containers')
    monkeypatch.setattr(paths, 'get_weights_root', lambda: tmp_path / 'weights')
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path / 'data')
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'image-store'))
    monkeypatch.delenv('BMS_RUNTIME_IMAGE_AF3_OPEN_SIF', raising=False)
    physical = assets_on_disk(tmp_path, p)
    expected = service.resolve_oracle_runtime('generate', p, resolved_assets=physical)
    job = SimpleNamespace(model_id='rfd4_proteina', mode='generate', params=p)
    await prepare_native_sources(job, None)
    payload = json.loads(Path(job.params['rfd4_prepared_request']).read_text())
    assert payload['oracle_runtime'] == expected
    assert payload['request']['input']['kind'] == 'unconditional'
