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
    assert data['unresolved_fields']  # cannot advertise full parity


def test_fail_closed_unknown_nested_and_types():
    base = {'max_trajectories': 2}
    for additional in ({'max_trajectories': True}, {'gpu_ids': [0]},
                       {'weights_interface_contacts': '0.8'}, {'weights_interface_contacts': float('nan')},
                       {'binder_lengths': [100, 50]}, {'paratope_conformations': ['invented']}, {'mystery': 1},
                       {'filters': {'i_pTM': {'threshold': '0.8'}}},
                       {'filters': {'unlisted': {'threshold': 0.7}}},
                       {'losses': {'interface_contacts': {'params': {'wrong': 2}}}},
                       {'targets': [{'name': 'x', 'target_path': 'a.cif', 'bogus': 3}]},
                       {'parameter_sweep': {'axes': []}}, {'binder_shapes': [4]},
                       {'relax_steps': 4}, {'crop_fasta_sequence': [4, 0]},
                       {'multitarget_filter_models': True}):
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


def test_pinned_native_differential_if_available(tmp_path):
    upstream = os.environ.get('BMS_TEST_BC2_UPSTREAM')
    if not upstream:
        pytest.skip('set BMS_TEST_BC2_UPSTREAM and BMS_TEST_BC2_PYTHON for native differential')
    python = os.environ['BMS_TEST_BC2_PYTHON']
    discovered = inventory(Path(upstream))
    enriched = schema()
    # The pinned native inventory is the source baseline; local annotations only
    # enrich previously null-typed settings and source-resolved metric defaults.
    assert set(discovered['fields']) == set(enriched['fields'])
    for name, field in discovered['fields'].items():
        assert {key: enriched['fields'][name][key] for key in field if key not in ('status', 'observed_types')} == {
            key: value for key, value in field.items() if key not in ('status', 'observed_types')}
        if field['status'] == 'typed':
            assert enriched['fields'][name]['observed_types'] == field['observed_types']
    for key in ('upstream_commit', 'source_sha256', 'registry_sha256', 'default_sha256', 'reference_sha256', 'presets', 'target_fields'):
        assert enriched[key] == discovered[key]
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
