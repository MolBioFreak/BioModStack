from copy import deepcopy
from decimal import Decimal, localcontext
import json
import os
from pathlib import Path
import time

import pytest
from jsonschema import Draft202012Validator
from bioxp_method_model import method_schema, method_catalog, method_examples, migrate_legacy, validate_method
from bioxp_method_compiler import compile_method, evaluate, Quantity, RepresentationError
from bioxp_workflow_authoring import preview, WorkflowPreviewRequest


def expr(op, **kw):
    return {'expr': {'version': 1, 'op': op, **kw}}


def move(sid='move'):
    return {'step_id': sid, 'type': 'action', 'action': 'move', 'inputs': {'location_id': 0, 'well': 'A1', 'position_flag': 0}}


def method(*steps, **kw):
    return {'schema': 'bms.bioxp-method.v1', 'name': 'Test', 'parameters': [], 'procedures': [], 'steps': list(steps), **kw}


def compile(*steps, **kw):
    return compile_method({'method': method(*steps, **kw)})


def actions(result):
    assert result['document'], result['issues']
    return [action for stage in result['document']['stages'] for action in stage['actions']]


def test_native_emitter_equivalence_and_snapshot():
    row = move()
    m = method(row)
    original = deepcopy(m)
    result = compile_method({'method': m})
    expected = preview(WorkflowPreviewRequest.model_validate({'protocol_id': 'x', 'draft': {
        'schema': 'bms.bioxp-workflow-draft.v1', 'editor_state': {}, 'steps': [{'step_id': 'move', 'intent': {'operation': 'move', **row['inputs']}}]}}))
    actual = actions(result)[0]
    for k, v in expected.document['stages'][0]['actions'][0].items():
        if k not in ('metadata', 'source_occurrence_id', 'action_id'):
            assert actual[k] == v
    assert m == original
    assert result['provenance'][0]['native_action_ids'] == [actual['action_id']]


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('capability', ['omitted', None, 'pipette'])
def test_lossless_legacy_migration(version, capability):
    original = {'schema': f'bms.bioxp-workflow-draft.v{version}', 'steps': [{'step_id': 'x', 'intent': {
        'operation': 'future', 'volume_ul': '01.000', 'blank': '', 'unknown': [None, False, 0]}}],
        'editor_state': {'collapsed': True}, 'future': None}
    if capability != 'omitted':
        original['steps'][0]['required_capability'] = capability
    if version == 2:
        original['deck_plan'] = {'labware': [], 'assignments': [], 'materials': []}
    migrated = migrate_legacy(original)
    validate_method(migrated)
    assert migrated['legacy_original'] == original
    assert migrated['steps'][0]['inputs'] == original['steps'][0]['intent']
    assert ('required_capability' in migrated['steps'][0]) == (capability != 'omitted')
    assert not compile_method({'method': migrated})['document']
    assert migrate_legacy(migrated) == migrated


def test_capability_null_and_precision_native_migration():
    d = {'schema': 'bms.bioxp-workflow-draft.v1', 'editor_state': {}, 'steps': [
        {'step_id': 'x', 'required_capability': None, 'intent': {'operation': 'aspirate', 'channels': [0], 'volume_ul': '12.5000', 'speed': '30'}}]}
    m = migrate_legacy(d)
    r = compile_method({'method': m})
    assert actions(r)[0]['required_capability'] is None
    assert actions(r)[0]['params']['volume_ul'] == 12.5
    assert m['steps'][0]['inputs']['volume_ul'] == '12.5000'


def test_nested_duplicate_loops_procedure_scope_and_selected_branch():
    p = {'id': 'move_proc', 'parameters': [{'id': 'well', 'type': 'string'}], 'steps': [
        {**move(), 'inputs': {'location_id': 0, 'well': expr('arg', id='well'), 'position_flag': 0}}]}
    call = {'step_id': 'call', 'type': 'call', 'procedure_id': 'move_proc', 'arguments': {'well': expr('loop_item')}}
    loop = {'step_id': 'loop', 'type': 'repeat', 'items': ['A1', 'A1'], 'steps': [call]}
    selected = {'step_id': 'choose', 'type': 'if', 'condition': True, 'then': [loop], 'else': [
        {**move(), 'inputs': expr('param', id='not-bound')}]}
    r = compile(selected, procedures=[p])
    assert len(actions(r)) == 2
    ids = [p['occurrence_id'] for p in r['provenance']]
    assert len(set(ids)) == 2
    assert [p['loop_path'][0]['index'] for p in r['provenance']] == [0, 1]
    assert r['provenance'][0]['call_path'][0]['procedure_id'] == 'move_proc'


@pytest.mark.parametrize('count', [-1, 1.2, True, None, 'NaN', 'Infinity', 10001])
def test_bad_repeat_never_returns_prefix(count):
    r = compile(move(), {'step_id': 'repeat', 'type': 'repeat', 'count': count, 'steps': [move()]})
    assert r['document'] is None
    assert r['issues']


def test_zero_disabled_and_empty_repeat():
    r = compile({'step_id': 'zero', 'type': 'repeat', 'count': 0, 'steps': [move()]},
        {**move('disabled'), 'enabled': False}, {'step_id': 'empty', 'type': 'repeat', 'items': [], 'steps': [move()]}, move())
    assert len(actions(r)) == 1


def test_recursion_and_expansion_count_before_evaluation():
    call = {'step_id': 'c', 'type': 'call', 'procedure_id': 'p', 'arguments': {}}
    r = compile(call, procedures=[{'id': 'p', 'parameters': [], 'steps': [call]}])
    assert r['issues'][0]['code'] == 'recursive_procedure'
    r = compile({'step_id': 'outer', 'type': 'repeat', 'count': 101, 'steps': [
        {'step_id': 'inner', 'type': 'repeat', 'count': 100, 'steps': [{**move(), 'inputs': expr('param', id='unbound')}]}]})
    assert r['issues'][0]['code'] == 'resource_limit'
    assert 'occurrences' not in r['resolved']


def test_typed_units_arithmetic_and_binding_bounds():
    n = expr('add', args=[expr('literal', value='0.001', unit='mL'), expr('literal', value='1.5', unit='uL')])
    node = {'step_id': 'a', 'type': 'action', 'action': 'aspirate', 'inputs': {'volume_ul': n, 'channels': [0], 'speed': expr('literal', value=30, unit='uL/s')}}
    assert actions(compile(node))[0]['params']['volume_ul'] == 2.5
    node['inputs']['volume_ul'] = expr('literal', value=1, unit='s')
    assert compile(node)['document'] is None
    m = method(move(), parameters=[{'id': 'count', 'type': 'integer', 'minimum': 0, 'maximum': 5}])
    for value in [None, False, 1.5, 6]:
        assert compile_method({'method': m, 'bindings': {'count': value}})['document'] is None
    assert compile_method({'method': m, 'bindings': {'count': 0}})['document']


@pytest.mark.parametrize('ast', [expr('param', id='missing'), expr('loop_item'), expr('measure'),
    expr('div', args=[1, 0]), expr('and', args=[1, True]), expr('eq', args=[True, 1]),
    expr('add', args=[expr('literal', value=1, unit='s'), expr('literal', value=1, unit='uL')])])
def test_expression_failures(ast):
    with pytest.raises((RepresentationError, ArithmeticError)):
        evaluate(ast, {'param': {}, 'arg': {}})


def test_digest_editor_exclusion_dependency_inclusion_and_unicode():
    m = method(move(), name='µL', editor_state={'collapsed': False})
    req = {'method': m, 'dependencies': {'water': {'revision': 1}}}
    first = compile_method(req)
    m['editor_state']['collapsed'] = True
    assert compile_method(req)['digest'] == first['digest']
    req['dependencies']['water']['revision'] = 2
    assert compile_method(req)['digest'] != first['digest']
    assert compile_method(json.loads(json.dumps(req)))['digest'] == compile_method(req)['digest']


def test_unknown_actions_multiple_findings_and_no_truncation():
    r = compile(move(), {'step_id': 'unknown', 'type': 'action', 'action': 'unavailable', 'inputs': {}},
        {'step_id': 'thermal', 'type': 'action', 'action': 'thermal_profile', 'inputs': {}})
    assert r['document'] is None and r['digest'] is None
    assert len(r['issues']) == 2
    assert all(i['path'].startswith('/method/steps/') for i in r['issues'])


def test_native_mix_resource_limit_and_policy_not_review_alias():
    m = {'step_id': 'mix', 'type': 'action', 'action': 'mix', 'inputs': {'cycles': 1000000000}}
    assert compile(m)['issues'][0]['code'] == 'resource_limit'
    r = compile({**move(), 'on_error': 'pause_for_operator'})
    assert actions(r)[0]['on_error'] == 'pause_for_operator'
    assert actions(r)[0]['review_required'] is False
    assert compile(move(), tip_policy='per_transfer')['document'] is not None  # no liquid needs no tips


def test_discovery_examples_are_unbound_and_raw_roundtrip():
    schema = method_schema()
    Draft202012Validator.check_schema(schema)
    examples = method_examples()
    assert {e['id'] for e in examples} == {'cfps', 'purification', 'gibson', 'golden_gate', 'pcr', 'dna_purification', 'rna_purification', 'cfps_and_purification'}
    for entry in examples:
        Draft202012Validator(schema).validate(entry['method'])
        validate_method(entry['method'])
        r = compile_method({'method': entry['method']})
        assert r['document'] is None
        assert r['issues'][0]['code'] == 'missing_binding'
        assert any(c['stage_owner'] == 'operator' for c in entry['coverage'])
    names = {a['action'] for a in method_catalog()['actions']}
    assert {'transfer', 'thermal_profile', 'barcode', 'cut_seal', 'pierce_seal', 'seal_separate', 'source_purge'} <= names


def test_transfer_child_provenance_and_export_native_oracle():
    inputs = dict(source=dict(station='LOC_MS', location_id=0, wells=['A1', 'B1']),
        destination=dict(station='LOC_TC', location_id=2, wells=['A1', 'B1']), channels=[0, 1],
        volume_ul='12.500', aspirate_speed='30', dispense_speed='40', source_position_flag=1,
        destination_position_flag=2, source_lift_height_steps=None, destination_lift_height_steps=100)
    r = compile({'step_id': 'transfer', 'type': 'action', 'action': 'transfer', 'inputs': inputs})
    assert len(actions(r)) == 16
    assert len(r['provenance']) == 1
    assert len(r['provenance'][0]['native_action_ids']) == 16
    if os.environ.get('BIOXP_METHOD_EXPORT'):
        checkpoint = compile({'step_id': 'operator', 'type': 'action', 'action': 'checkpoint', 'inputs': {'message': 'Operator separation'}})
        Path(os.environ['BIOXP_METHOD_EXPORT']).write_text(json.dumps([r['document'], compile(move())['document'], checkpoint['document']]))


def test_stable_occurrence_identity_after_reorder_and_group_uniqueness():
    first = compile(move('a'), move('b'))
    second = compile(move('b'), move('a'))
    assert {p['step_id']: p['occurrence_id'] for p in first['provenance']} == {p['step_id']: p['occurrence_id'] for p in second['provenance']}
    grouped = compile({'step_id': 'g1', 'type': 'group', 'steps': [move()]},
                      {'step_id': 'g2', 'type': 'group', 'steps': [move()]})
    assert len({p['occurrence_id'] for p in grouped['provenance']}) == 2


def test_simulation_findings_and_invalid_assumptions_are_not_gates(monkeypatch):
    import bioxp_method_simulation
    def simulate(*args):
        raise ValueError('Unknown initial fill')
    monkeypatch.setattr(bioxp_method_simulation, 'simulate_method', simulate)
    r = compile(move())
    assert r['document']
    assert not r['issues']
    assert r['simulation']['issues'][0]['category'] == 'advisory'


def test_checkpoint_note_and_invalid_message():
    r = compile({'step_id': 'c', 'type': 'action', 'action': 'checkpoint', 'inputs': {'message': 'Separate externally'}},
                {'step_id': 'n', 'type': 'action', 'action': 'note', 'inputs': {'message': 'Account only'}})
    assert [a['kind'] for a in actions(r)] == ['pause_review', 'note']
    assert [a['review_required'] for a in actions(r)] == [True, False]
    assert compile({'step_id': 'c', 'type': 'action', 'action': 'checkpoint', 'inputs': {'message': None}})['document'] is None


def test_external_embedded_procedure_exact_pointer_and_dependency_digest():
    p = {'id': 'p', 'revision': 1, 'parameters': [], 'steps': [move()]}
    req = {'method': method({'step_id': 'call', 'type': 'call', 'procedure_id': 'p', 'arguments': {}}), 'dependencies': {'procedures': [p]}}
    r = compile_method(req)
    assert actions(r)
    assert r['provenance'][0]['path'] == '/dependencies/procedures/0/steps/0'
    p['revision'] = 2
    assert compile_method(req)['digest'] != r['digest']


def test_resource_benchmark():
    start = time.monotonic()
    r = compile({'step_id': 'repeat', 'type': 'repeat', 'count': 10000, 'steps': [move()]})
    assert len(actions(r)) == 10000
    print(f'10000 move occurrences: {time.monotonic() - start:.3f}s')
