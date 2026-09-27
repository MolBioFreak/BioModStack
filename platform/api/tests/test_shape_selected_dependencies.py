"""Selected Shape asset contracts; no downloads, scientific execution or workers."""
from copy import deepcopy

import pytest

from model_registry import native_checkpoint_dependencies, model_runtime_dependencies


@pytest.mark.parametrize('name', ['caliby', 'soluble_caliby', 'soluble_caliby_v1'])
def test_shape_caliby_uses_ordinary_native_selected_checkpoint(name):
    params = {'shape_request': {'sequence_engine': 'caliby_experimental',
                               'sequence_settings': {'model_name': name, 'temperature': 0.25}}}
    before = deepcopy(params)
    dependencies, blockers = native_checkpoint_dependencies('RunShapeCaliby', params)
    assert not blockers
    assert [(d.kind, d.relative_path) for d in dependencies] == [
        ('weights', f'caliby/model_params/caliby/{name}.ckpt')]
    assert params == before
    assert not any('af2' in (d.relative_path or '') or 'packer' in (d.relative_path or '') for d in dependencies)


@pytest.mark.parametrize('enabled', [False, True])
def test_shape_protenix_template_assets_follow_its_retained_native_selection(enabled):
    params = {'shape_request': {'validator_settings': {
        'protenix_v2': {'protenix_use_template': enabled}}}}
    before = deepcopy(params)
    dependencies, blockers = native_checkpoint_dependencies('RunShapeProtenixValidator', params)
    assert not blockers
    members = {d.relative_path for d in dependencies}
    assert ('protenix/mmcif' in members) is enabled
    assert ('protenix/common/release_date_cache.json' in members) is enabled
    assert 'protenix/checkpoint/protenix-v2.pt' in members
    assert params == before


def test_historical_shape_does_not_inherit_unconsumed_global_template_switch():
    dependencies, blockers = native_checkpoint_dependencies('RunShapeProtenixValidator', {
        'shape_request': {'schema': 'bms_shape_design_request_v2'},
        'protenix_use_template': True,
    })
    assert not blockers
    assert 'protenix/mmcif' not in {d.relative_path for d in dependencies}


def test_shape_family_preload_contains_ordinary_designer_without_packing_or_af2():
    refs = model_runtime_dependencies('protein_modification_experimental')
    assert ('image', 'caliby.sif') in {(r.kind, r.relative_path) for r in refs}
    members = {r.relative_path for r in refs if r.relative_path.startswith('caliby/')}
    assert members == {'caliby/model_params/caliby/soluble_caliby_v1.ckpt'}
