"""Lossless explicit numeric literal contract shared with the method UI."""
from copy import deepcopy
from decimal import Decimal
import pytest
from bioxp_method_compiler import compile_method, evaluate, RepresentationError
from bioxp_method_model import method_schema, validate_method

SCOPE = {'param': {}, 'arg': {}}
def numeric(raw):
    return {'expr': {'version': 1, 'op': 'literal', 'type': 'number', 'value': raw}}

@pytest.mark.parametrize('raw', ['', 'not a number', 'NaN', 'Infinity'])
def test_blank_invalid_numbers_persist_but_never_get_coerced_to_zero(raw):
    method = {'schema': 'bms.bioxp-method.v1', 'steps': [], 'parameters': [
        {'id': 'raw', 'type': 'number', 'default': numeric(raw)}]}
    before = deepcopy(method)
    validate_method(method)
    result = compile_method({'method': method})
    assert result['document'] is None and result['issues']
    assert method == before

@pytest.mark.parametrize('raw', ['9007199254740993.000100', '0002.34000000000000000001', '-1.2500e+10'])
def test_explicit_numeric_literal_is_decimal_without_javascript_rounding(raw):
    original = numeric(raw)
    assert evaluate(original, SCOPE) == Decimal(raw)
    assert original == numeric(raw)
    assert evaluate({'expr': {'version': 1, 'op': 'literal', 'value': raw}}, SCOPE) == raw

def test_no_unknown_string_or_literal_field_coercion():
    original = {'future': '9007199254740993', 'blank': '', 'nil': None, 'false': False}
    assert evaluate(original, SCOPE) == original
    with pytest.raises(RepresentationError, match='Unknown expression fields'):
        evaluate({'expr': {**numeric('2')['expr'], 'future': None}}, SCOPE)
    with pytest.raises(RepresentationError, match='Unsupported literal type'):
        evaluate({'expr': {**numeric('2')['expr'], 'type': 'future'}}, SCOPE)
    assert method_schema()['$defs']['expression']['properties']['type']['enum'] == ['number']

def test_exact_count_temperature_timeout_and_large_default_compile():
    method = {'schema':'bms.bioxp-method.v1', 'parameters': [
        {'id':'precise','type':'number','default':numeric('9007199254740993.000100')}],
        'steps':[{'step_id':'repeat','type':'repeat','count':numeric('0002.000'), 'steps':[
            {'step_id':'hold','type':'action','action':'thermal_hold','inputs':{
                'bank':'nest','target_temp_c':numeric('030.000'), 'duration_s':numeric('01.500'),
                'start':'attainment','tolerance_c':numeric('00.2500'),'timeout_s':numeric('0005.000')}}]}]}
    before = deepcopy(method)
    result = compile_method({'method':method})
    assert result['document'], result['issues']
    actions = [a for s in result['document']['stages'] for a in s['actions']]
    assert len(actions) == 2
    assert all(a['params']['target_temp_c'] == 30 and a['params']['timeout_s'] == 5 for a in actions)
    assert result['resolved']['parameters']['precise'] == '9007199254740993.0001'
    assert method == before
