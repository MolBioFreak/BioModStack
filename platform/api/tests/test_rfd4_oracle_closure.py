"""Offline oracle dependency/path transport. No model import or neural inference."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from services import rfd4_proteina as service
from bms_rfd4 import run as runner


def params(profile='af3', tags=None, family=None):
    request = {'input': {'kind': 'unconditional', 'name': 'tiny', 'chains': [
        {'chain_id': 'A', 'min_length': 12, 'max_length': 12}]},
        'settings': {'metrics_tags': ['unconditional_refolding'] if tags is None else tags},
        'providers': {'refolding_oracles': {'profile': profile, 'overrides': {}}}}
    if family is not None:
        request['oracle_parameter_family'] = family
    return {'rfd4_request': request}


def assets_on_disk(tmp_path, p):
    result = {}
    prefixes = {'image': 'containers', 'weights': 'weights', 'runtime_data': 'data',
                'reference_database': 'data'}
    for row in service.selected_oracle_assets('generate', p):
        logical = prefixes[row['kind']] + '/' + row['relative_path']
        path = tmp_path / logical
        path.parent.mkdir(parents=True, exist_ok=True)
        if row['kind'] in {'weights', 'reference_database'}:
            path.mkdir(exist_ok=True)
            (path / 'selected-member').write_bytes(b'inert selected fixture')
        else:
            path.write_bytes(b'inert fixture, NOT a runtime image or executable')
        result[logical] = str(path)
    return result


@pytest.mark.parametrize('profile,family', [('af2', 'af2'), ('af3', 'af3'), ('of3', 'of3'), ('af3', 'of3')])
def test_selected_exact_family_and_retained_real_files(tmp_path, profile, family):
    p = params(profile, family=family if family == 'of3' else None)
    rows = service.selected_oracle_assets('generate', p)
    assert {row['family'] for row in rows} == {family}
    assert {row['relative_path'] for row in rows} == {
        'af2-open.sif' if family == 'af2' else 'af3-open.sif',
        'alphafold/params' if family == 'af2' else 'rfd4-oracles/' + family}
    physical = assets_on_disk(tmp_path / 'worker', p)
    runtime = service.resolve_oracle_runtime('generate', p, resolved_assets=physical)
    prepared = service.prepare_for_job('generate', p, tmp_path / 'inputs',
        resolved_sources={}, allowed_roots=[tmp_path], oracle_runtime=runtime)
    payload = json.loads(Path(prepared['rfd4_prepared_request']).read_text())
    assert payload['oracle_runtime'] == runtime
    prefix = 'AF2' if family == 'af2' else 'AF3'
    assert Path(runtime[family][prefix + '_IMAGE']).is_file()
    assert Path(runtime[family][prefix + '_MODEL_DIR']).is_dir()
    environment = runner.oracle_environment(p['rfd4_request'], runtime, cache_root=tmp_path / 'attempt/cache')
    assert environment[prefix + '_MODEL_DIR'] == runtime[family][prefix + '_MODEL_DIR']
    cache = Path(environment[prefix + '_JAX_CACHE_DIR'])
    assert cache.is_dir() and cache.is_relative_to(tmp_path / 'attempt')
    assert not cache.is_relative_to(tmp_path / 'worker')
    assert 'openfold3/of3-p2-155k.pt' not in json.dumps(rows)


def test_no_active_oracle_does_not_resolve_or_acquire(tmp_path, monkeypatch):
    p = params(tags=[])
    assert service.selected_oracle_assets('generate', p) == ()
    assert service.resolve_oracle_runtime('generate', p, resolved_assets={}) == {}
    poisoned = {'af3': {'AF3_IMAGE': '/controller/inactive', 'AF3_MODEL_DIR': '/controller/weights'}}
    prepared = service.prepare_for_job('generate', p, tmp_path, resolved_sources={},
        allowed_roots=[tmp_path], oracle_runtime=poisoned)
    assert json.loads(Path(prepared['rfd4_prepared_request']).read_text())['oracle_runtime'] == {}
    bound, generated = service.bind_for_job('generate', p, tmp_path / 'generated', oracle_runtime=poisoned)
    payload = json.loads(next(row.payload for row in generated if row.relative_path.endswith('request.json')))
    assert payload['oracle_runtime'] == {}
    assert runner.oracle_environment(p['rfd4_request'], poisoned, cache_root=tmp_path / 'unused') == {}
    assert not (tmp_path / 'unused').exists()


@pytest.mark.parametrize('profile', ['af2', 'af3', 'of3'])
def test_generated_source_free_binding_matches_preparation(tmp_path, profile):
    p = params(profile)
    runtime = service.resolve_oracle_runtime('generate', p,
        resolved_assets=assets_on_disk(tmp_path / 'managed', p))
    _, generated = service.bind_for_job('generate', p, tmp_path / 'generated', oracle_runtime=runtime)
    payload = json.loads(next(row.payload for row in generated if row.relative_path.endswith('request.json')))
    assert payload['oracle_runtime'] == runtime
    assert not any('JAX_CACHE' in key for values in runtime.values() for key in values)


@pytest.mark.parametrize('tag,expected', [([], set()), (['cath'], {'FOLDSEEK_EXEC', 'CATH_FOLDSEEK_DB', 'CATH_DOMAIN_LIST'})])
def test_selected_foldseek_and_companions(tmp_path, tag, expected):
    p = params(tags=tag)
    runtime = service.resolve_oracle_runtime('generate', p,
        resolved_assets=assets_on_disk(tmp_path / 'managed', p))
    assert set(runtime.get('foldseek', {})) == expected
    environment = runner.oracle_environment(p['rfd4_request'], runtime, cache_root=tmp_path / 'attempt')
    assert set(environment) == expected


def test_foldseek_archive_extension_reuses_source_slot(tmp_path):
    p = params(tags=[])
    p['rfd4_request']['metric_extensions'] = [{
        'name': 'novelty', 'section': 'inline_metrics', 'implementation': 'rfproteina.metrics.foldseek_novelty.FoldseekNoveltyMetric',
        'kwargs': {}, 'archive_slot': 'archive'}]
    rows = service.selected_oracle_assets('generate', p)
    assert [row['environment'] for row in rows] == ['FOLDSEEK_EXEC']
    archive = tmp_path / 'archive'; archive.mkdir(); (archive / 'db').write_bytes(b'archive fixture')
    runtime = service.resolve_oracle_runtime('generate', p,
        resolved_assets=assets_on_disk(tmp_path / 'managed', p))
    prepared = service.prepare_for_job('generate', p, tmp_path / 'retained',
        resolved_sources={'archive': archive}, allowed_roots=[tmp_path], oracle_runtime=runtime)
    binding = json.loads(Path(prepared['rfd4_bindings']).read_text())['archive']
    assert (Path(prepared['rfd4_input_closure']) / binding / 'db').read_bytes() == b'archive fixture'


@pytest.mark.parametrize('override', [True, False])
def test_actual_cli_worker_mapping_overrides_controller_payload(tmp_path, monkeypatch, override):
    p = params('of3')
    controller = service.resolve_oracle_runtime('generate', p,
        resolved_assets=assets_on_disk(tmp_path / 'controller', p))
    worker = service.resolve_oracle_runtime('generate', p,
        resolved_assets=assets_on_disk(tmp_path / 'worker/cas', p))
    prepared = service.prepare_for_job('generate', p, tmp_path / 'retained',
        resolved_sources={}, allowed_roots=[tmp_path], oracle_runtime=controller)
    request = Path(prepared['rfd4_prepared_request'])
    original = request.read_bytes()
    if override:
        monkeypatch.setenv('BMS_RFD4_ORACLE_RUNTIME', json.dumps(worker))
    else:
        monkeypatch.delenv('BMS_RFD4_ORACLE_RUNTIME', raising=False)
    seen = {}
    def inert(request, bindings, output, **kwargs):
        seen.update(runner.oracle_environment(request, kwargs['oracle_runtime'], cache_root=Path(output) / '.cache'))
    monkeypatch.setattr(runner, 'run', inert)
    monkeypatch.setattr(sys, 'argv', ['bms_rfd4.run', '--request', str(request), '--bindings',
        prepared['rfd4_bindings'], '--output', str(tmp_path / 'attempt/output')])
    runner.main()
    expected = worker if override else controller
    assert seen['AF3_IMAGE'] == expected['of3']['AF3_IMAGE']
    assert seen['AF3_MODEL_DIR'] == expected['of3']['AF3_MODEL_DIR']
    assert Path(seen['AF3_JAX_CACHE_DIR']).is_relative_to(tmp_path / 'attempt')
    assert request.read_bytes() == original


def test_pinned_local_oracle_image_is_not_reselected(tmp_path, monkeypatch):
    import paths
    from services.remote_execution import images
    p = params('of3')
    image = tmp_path / 'retained-image/runtime.sif'
    image.parent.mkdir(); image.write_bytes(b'inert pinned oracle fixture')
    monkeypatch.setattr(paths, 'get_container_dir', lambda: tmp_path / 'containers')
    monkeypatch.setattr(paths, 'get_weights_root', lambda: tmp_path / 'weights')
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path / 'data')
    def no_reselection(*args):
        raise AssertionError('retained local oracle image must not reselect a release')
    monkeypatch.setattr(images, 'image_reference', no_reselection)
    runtime = service.resolve_oracle_runtime('generate', p,
        image_environment={'BMS_SELECTED_IMAGE_AF3_OPEN_SIF': str(image)})
    assert runtime['of3']['AF3_IMAGE'] == str(image)


def test_mutable_cache_is_attempt_private_not_retained(tmp_path):
    p = params('of3')
    runtime = service.resolve_oracle_runtime('generate', p,
        resolved_assets=assets_on_disk(tmp_path / 'managed', p))
    runtime['of3']['AF3_JAX_CACHE_DIR'] = '/controller/stale/cache'
    first = runner.oracle_environment(p['rfd4_request'], runtime, cache_root=tmp_path / 'a')
    second = runner.oracle_environment(p['rfd4_request'], runtime, cache_root=tmp_path / 'b')
    assert first['AF3_JAX_CACHE_DIR'] != second['AF3_JAX_CACHE_DIR']
    assert 'JAX_CACHE' not in json.dumps(service.retained_oracle_runtime('generate', p, runtime))
