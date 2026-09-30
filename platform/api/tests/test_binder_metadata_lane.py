"""Metadata lane receiving-boundary proofs; no model kernels or compiler."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ConfigDict

from model_registry import ModelParameter, get_registry
from routers.models import router
from services.bindcraft2_inventory import inventory, PIN
from services.bindcraft2_typed import schema
from services.bindcraft2_typed_settings import display_projection, _namespace, _json_values
from services.boltzgen_request_compatibility import normalize_boltzgen_generation_request


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix='/api/models')
    return TestClient(app)


def test_schema_only_and_selected_http_do_not_compile(client, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('discovery invoked subprocess'))
    baseline = client.get('/api/models/bindcraft2/native-settings').json()['settings']
    assert 'display' not in baseline
    requested = {'core': 'benchmark', 'relax_accepted_designs': True, 'copies': 2}
    response = client.get('/api/models/bindcraft2/native-settings', params={'selectors': json.dumps(requested)})
    assert response.status_code == 200, response.text
    display = response.json()['settings']['display']
    assert display['selectors'] == requested
    assert display['values']['kept_sequences'] == 1
    assert display['values']['relax_steps'] == 200
    assert display['values']['relax_learning_rate'] == 0.02
    assert display['values']['oligomer_tie'] == 'symmetric'
    assert display['values']['campaign_seed'] == 0
    assert display['origins']['campaign_seed'].endswith('settings/core/benchmark.json')
    assert isinstance(display['origins']['relax_steps'], str)
    assert schema() == baseline


@pytest.mark.parametrize('selectors', [[], {'filters': {}}, {'target': ['missing']}, {'core': 1}, {'humanization': 'yes'}])
def test_invalid_display_does_not_change_schema_or_launch(client, selectors):
    before = schema()
    response = client.get('/api/models/bindcraft2/native-settings', params={'selectors': json.dumps(selectors)})
    assert response.status_code == 422
    assert 'display unavailable' in response.json()['detail']
    assert schema() == before
    assert client.get('/api/models/bindcraft2/native-settings').status_code == 200


def test_current_projection_inapplicability_sparse_and_stale_pin():
    requested = {'core': 'benchmark', 'relax_accepted_designs': False, 'copies': 1}
    original = copy.deepcopy(requested)
    off = display_projection(requested)
    assert 'relax_steps' not in off['values']
    assert 'oligomer_tie' not in off['values']
    assert 'max_trajectories' not in off['values']
    assert requested == original
    on = display_projection({'relax_accepted_designs': True, 'copies': 2})
    assert on['selectors'] != off['selectors']
    assert on['values']['relax_steps'] == 200
    data = schema()
    data['display_resolver']['source_files']['settings.py'] = '0' * 64
    with pytest.raises(ValueError, match='source pin mismatch'):
        display_projection({}, data)
    assert display_projection(requested) == off


def test_registered_signatures_and_inherited_metrics_are_separate():
    data = schema()
    before = copy.deepcopy(data)
    inherited = display_projection({'modality': 'binder'}, data)['values']
    assert inherited['filters']['i_pTM']['threshold'] == data['default_values']['filters']['i_pTM']['threshold']
    assert inherited['losses']['interface_contacts']['params']
    assert data == before
    for block in ('losses', 'filters'):
        for entry in data['registered_metrics'][block].values():
            assert all('source_default' in field and 'required' in field for field in entry['params'].values())
    # Explicit metric partial/null/zero handling remains at the pinned owner.
    native = _namespace(data)
    result = native['load_settings']({'filters': {'i_pTM': {'threshold': None}}, 'losses': {'interface_contacts': 0}})
    assert result['filters']['i_pTM']['threshold'] is None
    assert result['weights_interface_contacts'] == 0
    assert 'pTM' in result['filters']


def test_catalog_list_detail_preserve_parameter_metadata(client, monkeypatch):
    # The real HTTP serializers receive the actual registry row. Extra fields
    # model the parent-owned ModelParameter declaration without editing its file.
    class PresentationParameter(ModelParameter):
        model_config = ConfigDict(extra='allow')
    registry = get_registry()
    model = registry.get_model('protenix')
    assert model is not None
    field = next(p for p in model.params if p.name == 'protenix_n_step')
    enriched = PresentationParameter.model_validate({**field.model_dump(),
        'label': 'Diffusion steps', 'step': 1, 'ui_control': 'slider',
        'group': 'sampling', 'units': 'steps', 'applicability': {'use_msa': True},
        'accepted_types': ['integer']})
    monkeypatch.setattr(model, 'params', [enriched if p.name == field.name else p for p in model.params])
    detail = client.get('/api/models/protenix').json()
    listed = next(m for m in client.get('/api/models').json() if m['id'] == 'protenix')
    for document in (detail, listed):
        parameter = next(p for p in document['params'] if p['name'] == field.name)
        assert parameter == enriched.model_dump()
        assert parameter['default'] == field.default
        assert parameter['minimum'] == field.minimum
        assert parameter['maximum'] == field.maximum
    modes = client.get('/api/models/protenix/modes')
    assert modes.status_code == 200
    assert modes.json()['modes'] == detail['modes']


@pytest.mark.parametrize('mode', ['protein_binder', 'peptide_binder', 'nanobody_binder'])
def test_actual_boltzgen_metadata_and_unchanged_normalization(client, mode):
    response = client.get('/api/models/boltzgen/generation-settings', params={'mode': mode})
    assert response.status_code == 200, response.text
    fields = {f['name']: f for f in response.json()['parameters']}
    alpha = fields['boltzgen_alpha']
    assert (alpha['minimum'], alpha['maximum'], alpha['step'], alpha['ui_control']) == (0, 1, .01, 'slider')
    assert alpha['default'] == .01
    assert fields['boltzgen_min_plddt']['ui_control'] == 'unavailable'
    assert fields['boltzgen_min_plddt']['read_only'] is True
    requested = {'alpha': .0123456789, 'min_plddt': None}
    if mode == 'nanobody_binder':
        requested['nanobody_scaffold_specs'] = ['example.yaml', 'second.yaml']
        assert fields['boltzgen_nanobody_scaffold_specs']['accepted_types'] == ['string', 'array']
        assert fields['boltzgen_nanobody_scaffold_specs']['items'] == {'type': 'string'}
    result = normalize_boltzgen_generation_request(mode, requested)
    assert result['boltzgen_alpha'] == .0123456789
    assert result['boltzgen_min_plddt'] is None
    with pytest.raises(ValueError, match='pLDDT is unavailable'):
        normalize_boltzgen_generation_request(mode, {'min_plddt': 70})


def test_exact_pinned_source_regeneration_and_native_differential():
    location = os.environ.get('BMS_METADATA_UPSTREAM')
    if not location:
        pytest.skip('Exact pinned small source export requires BMS_METADATA_UPSTREAM')
    upstream = Path(location)
    data = schema()
    snapshot = json.loads((Path(__file__).parents[1] / 'config/models/bindcraft2_native_inventory.json').read_text())
    assert inventory(upstream) == snapshot
    assert hashlib.sha256((upstream / 'bindcraft/settings.py').read_bytes()).hexdigest() == snapshot['source_sha256']
    # Independent oracle uses ORIGINAL AST nodes and native filesystem layer
    # reads, rather than generated source or an independently guessed merger.
    tree = ast.parse((upstream / 'bindcraft/settings.py').read_text())
    nodes = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
             and (node.lineno <= 483 or isinstance(node, ast.FunctionDef)
                  and node.name in ('campaign_binder_lengths', 'overridden_settings'))]
    native = _namespace(data)
    native['__file__'] = str(upstream / 'bindcraft/settings.py')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(upstream / 'bindcraft/settings.py'), 'exec'), native)
    cases = [{}, {'core': 'benchmark'}, {'modality': ['induced_fit']},
             {'modality': ['peptide', 'binder']}, {'modality': ['binder', 'peptide']},
             {'modality': 'homo_oligomer'}, {'target': 'default'},
             {'copies': 2}, {'sparse_output': True},
             {'binder_scaffold': '/not-acquired/framework.cif', 'mutate_positions': 'A30-35'},
             {'targets': [{'name': 'sequence', 'target_path': '/not-acquired/target.fasta'}]}]
    # Each shipped independent property exercises its native effect without
    # enumerating combinations. Skip properties with source filesystem bindings
    # only if they cannot be represented; the tested pin currently has none.
    cases += [{key: True} for key in data['presets']['property']]
    cases += [{'modality': key} for key in data['presets']['modality']]
    cases += [{'target': key} for key in data['presets']['target']]
    cases += [{'target': ['hPD1', 'hPDL1']}, {'target': ['hPDL1', 'hPD1']}]
    def expected_display(value):
        if isinstance(value, str) and value.startswith(str(upstream) + '/'):
            return None  # Package asset is not bound by discovery.
        if isinstance(value, dict):
            return {key: expected_display(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [expected_display(item) for item in value]
        return _json_values(value)
    for selectors in cases:
        try:
            expected = native['load_settings'](copy.deepcopy(selectors))
        except ValueError:
            with pytest.raises(ValueError):
                display_projection(selectors)
            continue
        actual = display_projection(selectors)['values']
        for key, value in expected.items():
            if key in ('binder_scaffold', 'targets'):
                # Source-bound values remain in shipped metadata, not invented
                # effective/acquired paths in settings controls.
                assert key not in actual
            else:
                assert actual[key] == expected_display(value), (selectors, key)
    # Reference is documentation, never a baseline layer or source of defaults.
    referenced = native['load_settings']({'core': 'reference'})
    referenced.pop('core')
    assert referenced == native['load_settings']({})
    displayed = display_projection({'core': 'reference'})['values']
    displayed.pop('core')
    assert displayed == display_projection({})['values']
