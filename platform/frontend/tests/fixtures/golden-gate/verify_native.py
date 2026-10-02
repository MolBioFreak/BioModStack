"""Verify mounted-callback artifacts with the real native owners, offline.

Run from platform/api with repository root + API on PYTHONPATH, the locked
scientific environment, a route-free namespace, and scratch BMS paths.
Argument 1 is BMS_GG_UI_EVIDENCE from the preceding mounted Vitest run.
This script does not invoke app lifespan, routes, persistence, or user data.
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from services.assembly.golden_gate_design_types import GoldenGateDesignRequest, GoldenGateDesignResult
from services.assembly.golden_gate_design import design_golden_gate
from services.assembly.golden_gate_domestication import DomesticationSettings
from services.assembly.golden_gate_reaction import ReactionRequest, calculate_reaction
from services.assembly.golden_gate_fidelity import EndInstance, SearchSettings, evaluate_overhangs, optimize_overhangs, optimize_sequence_windows

root = Path(__file__).resolve().parents[5]
fixtures = Path(__file__).resolve().parent
evidence = Path(sys.argv[1]).resolve()
schema = json.loads((root / 'schemas/ngs_molbio/molbio-assembly-golden_gate-design-v1.schema.json').read_text())
validator = Draft202012Validator(schema)
checks = []

def load(name):
    return json.loads((evidence / (name + '.json')).read_text())

def core_check(name, payload):
    validator.validate(payload)
    request = GoldenGateDesignRequest.model_validate(payload)
    assert request.model_dump(mode='json') == payload, f'{name}: unexpected dropped/defaulted field'
    checks.append({'name': name, 'kind': 'native_request_and_checked_in_schema'})
    return request

for name in ['mounted-identities', 'mounted-wrap', 'mounted-preparations', 'mounted-chemistry', 'mounted-sapi']:
    core_check(name, load(name))
request = core_check('mounted-edited-callback', load('mounted-edited-callback')['core'])
computed = design_golden_gate(request)
assert computed.solutions and computed.solutions[0].display_origin == 3
checks.append({'name': 'mounted-edited-native-execution', 'kind': 'real_native_design', 'product_bp': len(computed.solutions[0].sequence)})
for enzyme in ['BbsI', 'SapI']:
    raw = json.loads((fixtures / f'{enzyme}-request.json').read_text())
    request = core_check(enzyme, raw)
    result = GoldenGateDesignResult.model_validate_json((fixtures / f'{enzyme}-result.json').read_text())
    actual = design_golden_gate(request)
    assert actual.model_dump(mode='json') == result.model_dump(mode='json')
    checks.append({'name': f'{enzyme}-fixture-fresh-native-equality', 'kind': 'real_native_result', 'product_bp': len(actual.solutions[0].sequence)})

for name in ['mounted-fidelity', 'mounted-windows', 'mounted-domestication', 'mounted-reaction']:
    s = load(name)
    for d in s['domestication']:
        model = DomesticationSettings.model_validate(d['settings'])
        assert model.model_dump(mode='json') == d['settings']
    r = ReactionRequest.model_validate(s['reaction'])
    assert r.model_dump(mode='json') == s['reaction']
    worksheet = calculate_reaction(r)
    assert worksheet.request.reagents[0].formulation == s['reaction']['reagents'][0]['formulation']
    for key in ['overhang_search', 'window_search']:
        SearchSettings(**s[key]['settings'])
    f = s['fidelity']
    fidelity = evaluate_overhangs(f['junctions'], f['dataset_id'], condition_use=f['condition_use'], inventory=[EndInstance(**x) for x in f['inventory']], inventory_complete=f['inventory_complete'], include_pair_observations=f['include_pair_observations'])
    if name == 'mounted-fidelity':
        args = dict(s['overhang_search'])
        args['settings'] = SearchSettings(**args['settings'])
        search = optimize_overhangs(**args, dataset_id=f['dataset_id'], condition_use=f['condition_use'])
        assert search['constraints']['fixed'] == ['AAC']
    if name == 'mounted-windows':
        args = dict(s['window_search'])
        args['settings'] = SearchSettings(**args['settings'])
        search = optimize_sequence_windows(**args, dataset_id='pryor2020-s005', condition_use=f['condition_use'])
        assert search['constraints']['fixed_positions'] == [None, 4]
    checks.append({'name': name, 'kind': 'native_supplemental_settings_and_execution', 'fidelity_status': fidelity['status'], 'reaction_formulation': worksheet.request.reagents[0].formulation})

window_fixture = json.loads((fixtures / 'circular_windows.json').read_text())
assert optimize_sequence_windows('ACGTTGCAAT', [(9, 2), (4, 5)], 'pryor2020-s005', end_length=3, topology='circular', fixed_positions=[9, 4]) == window_fixture
checks.append({'name': 'circular-window-viewer-fixture', 'kind': 'real_native_search'})

score_fixture = json.loads((fixtures / 'published_11.json').read_text())
assert evaluate_overhangs(score_fixture['junctions'], score_fixture['dataset_id'], condition_use=score_fixture['condition_use']) == score_fixture
checks.append({'name': 'published-11-viewer-fixture', 'kind': 'real_native_score', 'f_set': score_fixture['f_set']})
search_fixture = json.loads((fixtures / 'bounded_search.json').read_text())
c = search_fixture['constraints']
search_result = optimize_overhangs(c['candidate_domain'], c['junction_count'], search_fixture['dataset_id'], end_length=c['end_length'], required=c['required'], fixed=c['fixed'], excluded=c['excluded'], condition_use=search_fixture['condition_use'], settings=SearchSettings(**search_fixture['settings']))
assert search_result == search_fixture
checks.append({'name': 'bounded-search-viewer-fixture', 'kind': 'real_native_search', 'attempted': search_result['search_scope']['attempted']})

print(json.dumps({'checks': checks, 'count': len(checks), 'status': 'passed', 'scope': 'Mounted callback → checked-in schema/native models and real native computation; not HTTP or persistence integration'}, indent=2))
