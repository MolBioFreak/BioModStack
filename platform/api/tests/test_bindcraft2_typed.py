"""BC2 typed inventory/agent adapter evidence, with optional exact-pin differential."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from services.bindcraft2_inventory import inventory
from services.bindcraft2_native import _canonical, write_compilation
from services.bindcraft2_typed import compile_typed, schema, validate_request


def test_inventory_is_closed_against_native_registry():
    data = schema()
    assert len(data['fields']) == 239
    assert len(data['registered_metrics']['losses']) == 37
    assert len(data['registered_metrics']['filters']) == 47
    assert data['nested_surfaces']['targets']['fields'] == data['target_fields']
    assert 'cutoff' in data['registered_metrics']['filters']['Interface_Residues']['params']
    assert 'weights_interface_contacts' in data['fields']
    assert data['fields']['max_trajectories']['has_native_default'] is False
    assert len(data['unresolved_fields']) == 16
    assert data['fields']['binder_shapes']['observed_types'] == ['array']
    assert data['fields']['humanize']['observed_types'] == ['boolean']
    assert data['fields']['number_of_final_designs']['runtime_fallback'] == 1
    assert data['fields']['cyclic_offset_mode']['choices'] == ['distance', 'direction', 'neighbours']
    assert data['unresolved_fields']  # cannot advertise full parity


def test_fail_closed_unknown_nested_and_types():
    base = {'max_trajectories': 2}
    for additional in ({'max_trajectories': True}, {'gpu_ids': [0]},
                       {'weights_interface_contacts': '0.8'}, {'weights_interface_contacts': float('nan')},
                       {'binder_lengths': [100, 50]}, {'paratope_conformations': ['invented']}, {'mystery': 1},
                       {'filters': {'i_pTM': {'threshold': '0.8'}}},
                       {'filters': {'i_pTM': {'higher': True}}},
                       {'filters': {'unlisted': {'threshold': 0.7}}},
                       {'losses': {'interface_contacts': {'params': {'wrong': 2}}}},
                       {'targets': [{'name': 'x', 'target_path': 'a.cif', 'bogus': 3}]},
                       {'parameter_sweep': {'axes': ['unknown']}},
                       {'parameter_sweep': {'levels': [0]}},
                       {'parameter_sweep': {'max_arms': 0}},
                       {'parameter_sweep': {'multiplier': 2, 'levels': [0.5]}},
                       {'parameter_sweep': {'bogus': 1}},
                       {'binder_shapes': [4]}, {'crop_fasta_sequence': [4, 0]},
                       {'multitarget_filter_models': True}, {'relax_steps': 4}):
        with pytest.raises(ValueError):
            validate_request({**base, **additional})


def test_request_effective_identity_and_presets(tmp_path):
    requested = {'max_trajectories': 2, 'modality': ['binder'], 'targets': [
        {'name': 'on', 'target_path': '/inputs/on.cif', 'hotspots': 'A:10'},
        {'name': 'off', 'target_path': '/inputs/off.cif', 'objective': 'detarget'}],
        'filters': {'i_pTM': {'threshold': 0.8, 'higher': True}}}
    def resolver(native):
        return {**native, 'binder_lengths': [60, 180]}
    compiled = compile_typed(requested, tmp_path / 'campaign', resolver)
    assert compiled['requested_settings'] == requested
    assert compiled['request_sha256'] == hashlib.sha256(_canonical(requested)).hexdigest()
    assert compiled['effective_sha256'] == hashlib.sha256(_canonical(compiled['effective_settings'])).hexdigest()
    assert compiled['effective_settings']['targets'] == requested['targets']
    assert compiled['request_sha256'] != compiled['effective_sha256']
    with pytest.raises(ValueError, match='unknown preset'):
        validate_request({'max_trajectories': 2, 'modality': ['invented']})


def test_sweep_admits_only_finite_aggregate(tmp_path):
    request = {'max_trajectories': 7, 'parameter_sweep': {'axes': ['weights_interface_contacts'], 'levels': [0.5, 2.0]}}
    arms = lambda _: (('baseline', {}), ('low', {}), ('high', {}))
    resolved = lambda native: dict(native)
    result = compile_typed(request, tmp_path / 'campaign', resolved, arms)
    assert result['sweep_budget'] == {'arms': 3, 'per_arm': 2, 'aggregate_allowance': 6}
    assert result['native_request']['parameter_sweep'] == request['parameter_sweep']
    with pytest.raises(ValueError, match='exceeding requested'):
        compile_typed({**request, 'max_trajectories': 2}, tmp_path / 'campaign', resolved, arms)


def test_pinned_native_sweep_budget_if_available(tmp_path):
    if not os.environ.get('BMS_TEST_BC2_UPSTREAM'):
        pytest.skip('pinned native interpreter not configured')
    python = os.environ['BMS_TEST_BC2_PYTHON']
    request = {'max_trajectories': 7, 'modality': ['binder'],
               'parameter_sweep': {'axes': ['weights_interface_contacts'], 'levels': [0.5, 2.0]}}
    def native(native_request):
        code = 'import json,sys; from bindcraft.settings import load_settings; print(json.dumps(load_settings(json.load(sys.stdin))))'
        return json.loads(subprocess.check_output([python, '-c', code], input=json.dumps(native_request), text=True))
    def arms(effective):
        code = 'import json,sys; from bindcraft.parameter_sweep import parameter_sweep_arms; print(json.dumps(parameter_sweep_arms(json.load(sys.stdin))))'
        return json.loads(subprocess.check_output([python, '-c', code], input=json.dumps(effective), text=True))
    compiled = compile_typed(request, tmp_path / 'campaign', native, arms)
    assert compiled['sweep_budget'] == {'arms': 3, 'per_arm': 2, 'aggregate_allowance': 6}
    assert len(arms(compiled['effective_settings'])) == 3


def test_pinned_native_differential_if_available(tmp_path):
    upstream = os.environ.get('BMS_TEST_BC2_UPSTREAM')
    if not upstream:
        pytest.skip('set BMS_TEST_BC2_UPSTREAM and BMS_TEST_BC2_PYTHON for native differential')
    python = os.environ['BMS_TEST_BC2_PYTHON']
    discovered = inventory(Path(upstream))
    annotated = json.loads((Path(__file__).parents[1] / 'config/models/bindcraft2_native_inventory.json').read_text())
    assert set(discovered['fields']) == set(annotated['fields'])
    for name, original in discovered['fields'].items():
        assert all(annotated['fields'][name][key] == value for key, value in original.items()
                   if key not in ('status', 'observed_types'))
        if original['status'] == 'typed':
            assert annotated['fields'][name]['observed_types'] == original['observed_types']
    for key in ('upstream_commit', 'source_sha256', 'registry_sha256', 'default_sha256', 'reference_sha256', 'presets', 'target_fields'):
        assert annotated[key] == discovered[key]
    assert schema()['fields']['aa_bias'] == discovered['fields']['aa_bias']
    assert set(schema()['fields']) == set(discovered['fields'])
    constants = subprocess.check_output([python, '-c',
        "import json; from bindcraft.settings import CYCLIC_OFFSET_MODES, OLIGOMER_TIES; from bindcraft.protein import BINDER_ALONE; from bindcraft.epitope_targeting import EPITOPE_CUTOFF; print(json.dumps([list(CYCLIC_OFFSET_MODES), sorted(OLIGOMER_TIES), BINDER_ALONE, EPITOPE_CUTOFF]))"], text=True)
    modes, ties, binder_alone, epitope_cutoff = json.loads(constants)
    typed = schema()
    assert typed['fields']['cyclic_offset_mode']['choices'] == modes
    assert typed['fields']['oligomer_tie']['choices'] == ties
    assert typed['registered_metrics']['filters']['Binder_RMSD']['params']['reference_state']['resolved_default'] == binder_alone
    assert typed['registered_metrics']['filters']['Epitope_Residues_Contacted']['params']['epitope_cutoff']['resolved_default'] == epitope_cutoff
    request = {'max_trajectories': 2, 'modality': ['binder'], 'targets': [
        {'name': 'on', 'target_path': '/inputs/on.cif'}], 'weights_interface_contacts': 0.8,
        'filters': {'i_pTM': {'threshold': 0.75, 'higher': True}}}
    def native(native_request):
        code = 'import json,sys; from bindcraft.settings import load_settings; print(json.dumps(load_settings(json.load(sys.stdin))))'
        return json.loads(subprocess.check_output([python, '-c', code], input=json.dumps(native_request), text=True))
    compiled = compile_typed(request, tmp_path / 'campaign', native)
    names = subprocess.check_output([python, '-c',
        'import json; from bindcraft.settings import known_campaign_settings; print(json.dumps(sorted(known_campaign_settings())))'], text=True)
    assert set(json.loads(names)) == set(schema()['fields'])
    assert compiled['effective_settings'] == native(compiled['native_request'])
    assert compiled['effective_settings']['filters']['i_pTM']['threshold'] == 0.75
    assert compiled['effective_settings']['weights_interface_contacts'] == 0.8
    receipt = tmp_path / 'compilation.json'
    write_compilation(compiled, receipt)
    serialized = json.loads(receipt.read_text())
    assert serialized['effective_sha256'] == hashlib.sha256(_canonical(compiled['effective_settings'])).hexdigest()
    assert '$bc2_nonfinite_float' in receipt.read_text()
    assert 'NaN' not in receipt.read_text()

@pytest.mark.parametrize('setting,value', [
    ('humanize', True), ('bigbang', True), ('number_of_final_designs', 3),
    ('cyclic_offset_mode', 'direction'), ('crop_fasta_sequence', [20, 40]),
    ('validation_models', 2), ('binder_shapes', [['complex'], ['binder_alone']]),
    ('multitarget_merged_gradient_budget', 'model_calls'),
    ('multitarget_rounds_per_target', ['anneal']),
    ('filters', {'Epitope_Residues_Contacted': {'threshold': 2, 'higher': True, 'params': {'epitope_cutoff': 10.0}}}),
    ('losses', {'induced_fit_global': {'params': {'reference_state': 'binder_alone'}}}),
])
def test_native_resolver_preserves_typed_operator_values(setting, value):
    upstream = os.environ.get('BMS_TEST_BC2_UPSTREAM')
    if not upstream:
        pytest.skip('pinned native checkout required')
    request = {'max_trajectories': 2, setting: value}
    validate_request(request)
    code = 'import json,sys; from bindcraft.settings import load_settings; print(json.dumps(load_settings(json.load(sys.stdin))))'
    resolved = json.loads(subprocess.check_output([os.environ['BMS_TEST_BC2_PYTHON'], '-c', code],
                                                input=json.dumps(request), text=True))
    if setting in ('filters', 'losses'):
        name = next(iter(value))
        assert all(resolved[setting][name][key] == item for key, item in value[name].items())
    else:
        assert resolved[setting] == value

def test_preset_modality_target_property_layers_against_native():
    if not os.environ.get('BMS_TEST_BC2_UPSTREAM'):
        pytest.skip('pinned native checkout required')
    request = {'max_trajectories': 2, 'modality': ['VHH'], 'target': ['hPD1'],
               'humanize': True, 'min_interface_buried_area_final': 100.0}
    validate_request(request)
    code = 'import json,sys; from bindcraft.settings import load_settings; print(json.dumps(load_settings(json.load(sys.stdin))))'
    resolved = json.loads(subprocess.check_output([os.environ['BMS_TEST_BC2_PYTHON'], '-c', code],
                                                input=json.dumps(request), text=True))
    assert resolved['min_interface_buried_area_final'] == 100.0
    assert resolved['humanize'] is True
    assert resolved['binder_scaffold']
    assert resolved['targets']
