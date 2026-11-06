"""Offline native composition, not checkpoint inference or scientific parity."""
import copy
import json
import os
from pathlib import Path

import pytest
from scripts.lib.rfd4_proteina_contract import parameter_schema, normalize_request, native_overlay
from scripts.bms_rfd4.run import compose_native


def request():
    return {'input': {'kind': 'unconditional', 'name': 'sample',
                      'chains': [{'chain_id': 'A', 'min_length': 12, 'max_length': 19}]}}


def source():
    value = os.environ.get('BMS_TEST_RFD4_SOURCE')
    if not value:
        pytest.skip('BMS_TEST_RFD4_SOURCE required: actual adopted native config tree')
    return Path(value)


def setting_leaves(node, path=()):
    if node.get('type') == 'object' and node.get('properties'):
        for key, child in node['properties'].items():
            yield from setting_leaves(child, (*path, key))
    elif 'default' in node:
        value = node['default']
        # Native Hydra interpolation is inherited, never accepted as uploaded code.
        if '${' not in json.dumps(value):
            yield path, value


LEAVES = list(setting_leaves(parameter_schema()['properties']['settings']))
# Constructor-backed transform settings have no file-local native declaration.
# Exercise explicit values rather than inventing active defaults for them.
LEAVES += [
    (('generation', 'dataset', 'transform', 'path_length_search_cutoff'), None),
    (('generation', 'dataset', 'transform', 'residue_names_supporting_local_latents'), []),
    (('generation', 'dataset', 'transform', 'precompute_atom_permutation'), False),
]


@pytest.mark.parametrize('path,value', LEAVES, ids=['.'.join(p) for p, _ in LEAVES])
def test_every_declared_literal_setting_survives_saved_request_and_native_compose(tmp_path, path, value):
    from omegaconf import OmegaConf
    r = request()
    node = r.setdefault('settings', {})
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = copy.deepcopy(value)
    # Saving/reloading must not replace explicit false/zero/null/empty declarations.
    saved = json.loads(json.dumps(r))
    n = normalize_request(saved)
    assert n['settings'] == r['settings'] == native_overlay(n)
    cfg = compose_native(n, source() / 'configs', inputs=tmp_path, output=tmp_path / 'out', bundle=tmp_path / 'pair')
    actual = OmegaConf.to_container(cfg, resolve=False)
    for part in path:
        actual = actual[part]
    assert actual == value


@pytest.mark.parametrize('bounds', [
    {'min_exclusive': 0, 'max_exclusive': 1.5},
    {'min': None, 'max': None, 'min_exclusive': None, 'max_exclusive': None},
    {'mean_of': ['a', 'b'], 'min_exclusive': 0},
])
def test_official_strict_thresholds_and_clears_reach_every_native_policy_table(tmp_path, bounds):
    from omegaconf import OmegaConf
    r = request()
    r['settings'] = {'search': {'save_criteria': {'score': bounds},
        'checkpoints': {'60': {'metrics': [], 'continuation_criteria': {'score': bounds}}}}}
    r['providers'] = {'pass_criteria': {'profile': 'all_pass_criteria',
        'overrides': {'ame': {'metric_thresholds': {'score': bounds}}}}}
    r['metric_extensions'] = [{'name': 'selected', 'section': 'pre_oracle_filters',
        'implementation': 'rfproteina.metrics.oracles.pass_criterion_metric.PassCriterionMetric',
        'kwargs': {'metric_thresholds': {'score': bounds}}}]
    cfg = compose_native(json.loads(json.dumps(r)), source() / 'configs', inputs=tmp_path,
                         output=tmp_path / 'out', bundle=tmp_path / 'pair')
    for actual in [cfg.search.save_criteria.score, cfg.search.checkpoints['60'].continuation_criteria.score,
                   cfg.metrics.pass_criteria.ame.metric_thresholds.score,
                   cfg.metrics.pre_oracle_filters.selected.metric_thresholds.score]:
        assert OmegaConf.to_container(actual) == bounds


def literal_defaults(node):
    if node.get('type') == 'object' and node.get('properties'):
        result = {}
        for name, child in node['properties'].items():
            if name == '_target_':
                continue
            value = literal_defaults(child)
            if value is not ...:
                result[name] = value
        return result
    if 'default' in node and '${' not in json.dumps(node['default']):
        return copy.deepcopy(node['default'])
    return ...


PROFILES = parameter_schema()['x-native-profiles']
PROVIDER_CASES = [(group, name) for group, profiles in PROFILES.items()
                  if group.startswith('metrics/') for name in profiles]


@pytest.mark.parametrize('group,profile', PROVIDER_CASES,
                         ids=[g + '/' + p for g, p in PROVIDER_CASES])
def test_selected_official_provider_literal_leaves_roundtrip(tmp_path, group, profile):
    from omegaconf import OmegaConf
    section = group.split('/')[-1]
    overrides = literal_defaults(PROFILES[group][profile]['schema'])
    for name, kwargs in overrides.items():
        transform = kwargs.get('pre_oracle_transform')
        if isinstance(transform, dict):
            # Only registered native transform names, never raw executable targets.
            declaration = PROFILES[group][profile]['schema']['properties'][name]['properties']['pre_oracle_transform']
            assert declaration['properties']['_target_']['const'] == (
                'rfproteina.metrics.oracles.pre_oracle_transform.ApoOnlyTransform')
            assert transform == {}
            kwargs['pre_oracle_transform'] = {'kind': 'apo'}
    r = request()
    r['providers'] = {section: {'profile': profile, 'overrides': overrides}}
    saved = json.loads(json.dumps(r))
    assert normalize_request(saved)['providers'] == r['providers']
    cfg = compose_native(saved, source() / 'configs', inputs=tmp_path,
                         output=tmp_path / 'out', bundle=tmp_path / 'pair')
    expected = native_overlay(saved)['metrics'][section]
    actual = OmegaConf.to_container(cfg.metrics[section], resolve=False)
    def compare(wanted, got):
        for key, value in wanted.items():
            if isinstance(value, dict):
                compare(value, got[key])
            else:
                assert got[key] == value
    compare(expected, actual)


def test_complexa_prepare_only_uses_actual_native_config_without_api_environment(tmp_path):
    from scripts.bms_complexa.run import run
    from scripts.lib.complexa_contract import Request
    value = os.environ.get('COMPLEXA_TEST_SOURCE')
    if not value:
        pytest.skip('COMPLEXA_TEST_SOURCE required for official config composition')
    receipt = run(Request(task='AME', task_name='M0024_1nzy_v3', reward=None),
                  upstream=Path(value), weights=tmp_path / 'weights', output=tmp_path / 'result',
                  prepare_only=True)
    assert receipt['execution'] == 'not_started'
    assert receipt['selected_dependencies'] == ['complexa_ame']
    assert receipt['commands'][0][:2] == ['complexa', 'generate']
    assert not (tmp_path / 'result/complexa-results.json').exists()
    assert (tmp_path / 'result/native/configs/bms_complexa.yaml').is_file()


def test_explicit_guidance_metric_clear_replaces_selected_native_group(tmp_path):
    from omegaconf import OmegaConf
    r = request()
    r['groups'] = {'guidance': 'contract_ca'}
    inherited = compose_native(r, source() / 'configs', inputs=tmp_path, output=tmp_path / 'out', bundle=tmp_path / 'pair')
    assert 'contract_ca' in inherited.guidance.metrics
    r['guidance'] = {'metrics': [], 'terms': {}}
    cleared = compose_native(r, source() / 'configs', inputs=tmp_path, output=tmp_path / 'out', bundle=tmp_path / 'pair')
    assert OmegaConf.to_container(cleared.guidance.metrics) == {}
    assert OmegaConf.to_container(cleared.guidance.terms) == {}
