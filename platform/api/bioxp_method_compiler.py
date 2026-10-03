"""Finite pure lowering of structured methods into the existing native emitter.

No robot client, persistence, execution permission, or physical retry lives here.
Resource limits constrain representation only. Drafts are never rewritten.
"""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
import hashlib
import json
import operator
from typing import NoReturn

from bioxp_method_model import validate_method
from bioxp_workflow_authoring import WorkflowPreviewRequest, preview, _NATIVE

VERSION = 'bms-method-compiler.v1'
MAX_DEPTH = 64
MAX_OCCURRENCES = 10000
MAX_ACTIONS = 100000
UNITS = {'uL': ('volume', '1', 'uL'), 'µL': ('volume', '1', 'uL'), 'mL': ('volume', '1000', 'uL'),
         'L': ('volume', '1000000', 'uL'), 's': ('time', '1', 's'), 'ms': ('time', '.001', 's'),
         'min': ('time', '60', 's'), 'h': ('time', '3600', 's'), 'degC': ('temperature', '1', 'degC'),
         'uL/s': ('flow', '1', 'uL/s'), 'mL/s': ('flow', '1000', 'uL/s'),
         'mm': ('length', '1', 'mm'), 'um': ('length', '.001', 'mm'),
         'steps': ('steps', '1', 'steps'), 'ms/step': ('step_time', '1', 'ms/step')}


class RepresentationError(ValueError):
    def __init__(self, message, path='', code='representation_error'):
        super().__init__(message)
        self.path, self.code = path, code


def fail(message, path='', code='representation_error') -> NoReturn:
    raise RepresentationError(message, path, code)


def number(value):
    if type(value) not in (str, int, float, Decimal) or value == '':
        fail('Expected an explicit finite decimal')
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        fail('Invalid decimal')
    if not n.is_finite():
        fail('Nonfinite decimal')
    if len(n.as_tuple().digits) > 1000 or abs(n.adjusted()) > 1000:
        fail('Decimal exceeds compiler resource range', code='resource_limit')
    return +n


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    dimension: str
    unit: str


def quantity(value, unit):
    if unit not in UNITS:
        fail(f'Unknown unit {unit!r}')
    dimension, factor, base = UNITS[unit]
    return Quantity(number(value) * Decimal(factor), dimension, base)


def decimal_text(n):
    return '0' if n == 0 else format(n.normalize(), 'f')


def json_value(value):
    if isinstance(value, Quantity):
        return {'value': decimal_text(value.value), 'unit': value.unit}
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_value(v) for v in value]
    return value


def evaluate(value, scope, depth=0):
    if depth > MAX_DEPTH:
        fail('Expression nesting exceeds 64', code='resource_limit')
    if isinstance(value, list):
        return [evaluate(v, scope, depth + 1) for v in value]
    if not isinstance(value, dict):
        return value
    if 'expr' not in value:
        return {k: evaluate(v, scope, depth + 1) for k, v in value.items()}
    if set(value) != {'expr'} or not isinstance(value['expr'], dict):
        fail('Expression envelope must contain only expr')
    e = value['expr']
    if e.get('version') != 1 or type(e.get('version')) is not int:
        fail('Unsupported expression version')
    op = e.get('op')
    allowed = {'version', 'op'} | ({'value', 'unit', 'type'} if op == 'literal' else {'id'} if op in ('param', 'arg') else set() if op in ('loop_index', 'loop_item') else {'args'})
    if set(e) - allowed:
        fail(f'Unknown expression fields: {sorted(set(e) - allowed)}')
    if op == 'literal':
        if 'value' not in e:
            fail('Literal value is missing')
        if 'type' in e:
            if e['type'] != 'number':
                fail('Unsupported literal type')
            return quantity(e['value'], e['unit']) if 'unit' in e else number(e['value'])
        return quantity(e['value'], e['unit']) if 'unit' in e else deepcopy(e['value'])
    if op in ('param', 'arg'):
        if e.get('id') not in scope[op]:
            fail(f'Unbound {op} {e.get("id")!r}', code='missing_binding')
        return deepcopy(scope[op][e['id']])
    if op in ('loop_index', 'loop_item'):
        if op not in scope:
            fail(f'{op} outside repeat scope')
        return deepcopy(scope[op])
    args = e.get('args')
    if not isinstance(args, list) or len(args) != (1 if op == 'not' else 2):
        fail(f'{op}: expected {1 if op == "not" else 2} operands')
    a = [evaluate(v, scope, depth + 1) for v in args]
    if op in ('and', 'or', 'not'):
        if any(type(v) is not bool for v in a):
            fail('Boolean operands required')
        return not a[0] if op == 'not' else (a[0] and a[1] if op == 'and' else a[0] or a[1])
    left, right = a
    if op in ('eq', 'ne', 'lt', 'le', 'gt', 'ge'):
        if isinstance(left, Quantity) or isinstance(right, Quantity):
            if not isinstance(left, Quantity) or not isinstance(right, Quantity) or left.dimension != right.dimension:
                fail('Comparison requires compatible units')
            left, right = left.value, right.value
        elif type(left) is not type(right) and not (type(left) in (int, float, Decimal) and type(right) in (int, float, Decimal)):
            fail('Comparison requires matching types')
        return getattr(operator, op)(left, right)
    if op not in ('add', 'sub', 'mul', 'div'):
        fail(f'Unknown expression operator {op!r}')
    lq, rq = isinstance(left, Quantity), isinstance(right, Quantity)
    if op in ('add', 'sub') and (lq or rq):
        if not lq or not rq or left.dimension != right.dimension:
            fail('Addition/subtraction requires compatible units')
        n = left.value + right.value if op == 'add' else left.value - right.value
        return Quantity(n, left.dimension, left.unit)
    if op == 'mul' and lq and rq:
        fail('Multiplication of two dimensional quantities is not in v1')
    if op == 'div' and rq:
        if not lq or left.dimension != right.dimension:
            fail('Division requires scalar denominator or compatible quantities')
        return left.value / right.value
    lv, rv = left.value if lq else number(left), right.value if rq else number(right)
    n = {'add': operator.add, 'sub': operator.sub, 'mul': operator.mul, 'div': operator.truediv}[op](lv, rv)
    q = left if lq else right if rq else None
    return Quantity(n, q.dimension, q.unit) if q else n


def bind(declarations, supplied, scope, path):
    if not isinstance(declarations, list) or not isinstance(supplied, dict):
        fail('Parameters must be an array and bindings an object', path)
    result = {}
    for i, p in enumerate(declarations):
        loc = f'{path}/{i}'
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not p['id']:
            fail('Parameter requires a nonempty id', loc)
        key = p['id']
        if key in result:
            fail('Duplicate parameter id', loc)
        if key not in supplied and 'default' not in p:
            fail(f'Missing binding {key}', loc, 'missing_binding')
        v = evaluate(supplied[key] if key in supplied else p['default'], scope)
        typ = p.get('type')
        if typ in ('number', 'integer'):
            if 'unit' in p:
                if not isinstance(v, Quantity):
                    v = quantity(v, p['unit'])
                expected = quantity(0, p['unit'])
                if v.dimension != expected.dimension:
                    fail('Incompatible parameter units', loc)
                n = v.value / Decimal(UNITS[p['unit']][1])
            else:
                if isinstance(v, Quantity):
                    fail('Unexpected dimensional value', loc)
                n = v = number(v)
            if typ == 'integer' and n != n.to_integral_value():
                fail('Count must be an exact integer', loc)
            for field, compare in (('minimum', operator.lt), ('maximum', operator.gt)):
                if field in p and compare(n, number(p[field])):
                    fail(f'Parameter outside authored {field}', loc)
        elif typ not in ('boolean', 'string', 'array', 'object') or type(v) is not {'boolean': bool, 'string': str, 'array': list, 'object': dict}.get(typ):
            fail(f'Expected parameter type {typ}', loc)
        if 'choices' in p and not any(type(v) is type(c) and v == c for c in [evaluate(x, scope) for x in p['choices']]):
            # Numeric declarations normalize choices through their own declaration.
            if typ not in ('number', 'integer') or n not in [number(c) for c in p['choices']]:
                fail('Parameter not in authored choices', loc)
        result[key] = v
    unknown = set(supplied) - {p['id'] for p in declarations}
    if unknown:
        fail(f'Unknown bindings {sorted(unknown)}', path)
    return result


def semantic(value):
    if isinstance(value, dict):
        return {k: semantic(v) for k, v in value.items() if k != 'editor_state'}
    if isinstance(value, list):
        return [semantic(v) for v in value]
    return value


def _lower(method, dependencies, params):
    procedures = {}
    procedure_paths = {}
    entries = [(p, f'/dependencies/procedures/{i}') for i, p in enumerate(dependencies.get('procedures', []))]
    entries += [(p, f'/method/procedures/{i}') for i, p in enumerate(method.get('procedures', []))]
    for p, ppath in entries:
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or p['id'] in procedures:
            fail('Missing or duplicate procedure id', ppath)
        procedures[p['id']] = p
        procedure_paths[p['id']] = ppath
    structural_visits = [0]
    # Static cycles are representation errors, including disabled/unselected bodies.
    def check_calls(nodes, stack=(), depth=0):
        structural_visits[0] += 1
        if structural_visits[0] > 200000:
            fail('Structure traversal exceeds 200000 visits', code='resource_limit')
        if depth > MAX_DEPTH:
            fail('Structure nesting exceeds 64', code='resource_limit')
        if not isinstance(nodes, list):
            fail('Children must be an array')
        ids = set()
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get('step_id'), str) or not node['step_id']:
                fail('Node requires a nonempty step_id')
            if node['step_id'] in ids:
                fail('Duplicate sibling step_id')
            ids.add(node['step_id'])
            if node.get('type') == 'call':
                pid = node.get('procedure_id')
                if pid not in procedures:
                    fail(f'Unknown procedure {pid!r}')
                if pid in stack:
                    fail(f'Recursive procedure cycle: {stack + (pid,)}', code='recursive_procedure')
                check_calls(procedures[pid].get('steps'), stack + (pid,), depth + 1)
            for key in ('steps', 'then', 'else'):
                if key in node:
                    check_calls(node[key], stack, depth + 1)
    check_calls(method['steps'])
    for pid, p in procedures.items():
        check_calls(p.get('steps'), (pid,))

    visits = [0]

    def walk(nodes, scope, path, calls, loops, policy, output, depth=0):
        visits[0] += 1
        if visits[0] > 200000:
            fail('Expansion traversal exceeds 200000 visits', path, 'resource_limit')
        if depth > MAX_DEPTH:
            fail('Expansion depth exceeds 64', path, 'resource_limit')
        count = 0
        for index, node in enumerate(nodes):
            loc = f'{path}/{index}'
            try:
                if type(node.get('enabled', True)) is not bool:
                    fail('enabled must be Boolean')
                if not node.get('enabled', True):
                    continue
                on_error = node.get('on_error', policy)
                if on_error not in ('stop', 'pause_for_operator'):
                    fail('Unknown on_error policy')
                typ = node.get('type')
                if typ == 'action':
                    count += 1
                    if output is not None:
                        identity = [node['step_id'], scope.get('_groups', []),
                                    [{k: c[k] for k in ('step_id', 'procedure_id')} for c in calls], loops]
                        occurrence_id = 'occ-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
                        output.append({'occurrence_id': occurrence_id, 'step_id': node['step_id'], 'path': loc,
                            'call_path': deepcopy(calls), 'loop_path': deepcopy(loops), 'group_path': deepcopy(scope.get('_groups', [])), 'on_error': on_error,
                            'action': node.get('action'), 'inputs': evaluate(node.get('inputs', {}), scope),
                            **({'required_capability': node['required_capability']} if 'required_capability' in node else {})})
                elif typ == 'group':
                    child = {**scope, '_groups': scope.get('_groups', []) + [node['step_id']]}
                    count += walk(node.get('steps', []), child, loc + '/steps', calls, loops, on_error, output, depth + 1)
                elif typ == 'if':
                    condition = evaluate(node.get('condition'), scope)
                    if type(condition) is not bool:
                        fail('if requires a Boolean compile-time expression')
                    key = 'then' if condition else 'else'
                    child = {**scope, '_groups': scope.get('_groups', []) + [node['step_id'], key]}
                    count += walk(node.get(key, []), child, loc + '/' + key, calls, loops, on_error, output, depth + 1)
                elif typ == 'repeat':
                    if ('count' in node) == ('items' in node):
                        fail('repeat requires exactly one of count/items')
                    if 'count' in node:
                        n = number(evaluate(node['count'], scope))
                        if n != n.to_integral_value() or n < 0:
                            fail('repeat count must be a finite nonnegative integer')
                        if n > MAX_OCCURRENCES:
                            fail('repeat exceeds resource limit', code='resource_limit')
                        items = range(int(n))
                    else:
                        items = evaluate(node['items'], scope)
                        if not isinstance(items, list):
                            fail('repeat items must be an ordered array')
                        if len(items) > MAX_OCCURRENCES:
                            fail('repeat exceeds resource limit', code='resource_limit')
                    for j, item in enumerate(items):
                        child = {**scope, 'loop_index': j, 'loop_item': item}
                        count += walk(node.get('steps', []), child, loc + '/steps', calls,
                            loops + [{'step_id': node['step_id'], 'index': j}], on_error, output, depth + 1)
                        if count > MAX_OCCURRENCES:
                            fail('Expansion exceeds 10000 occurrences', code='resource_limit')
                elif typ == 'call':
                    pid = node['procedure_id']
                    p = procedures[pid]
                    args = bind(p.get('parameters', []), node.get('arguments', {}), scope, loc + '/arguments')
                    count += walk(p['steps'], {'param': params, 'arg': args, '_groups': scope.get('_groups', [])}, procedure_paths[pid] + '/steps',
                        calls + [{'step_id': node['step_id'], 'path': loc, 'procedure_id': pid}], loops, on_error, output, depth + 1)
                else:
                    fail(f'Unknown node type {typ!r}')
                if count > MAX_OCCURRENCES:
                    fail('Expansion exceeds 10000 occurrences', code='resource_limit')
            except RepresentationError as exc:
                if not exc.path:
                    exc.path = loc
                raise
        return count
    scope = {'param': params, 'arg': {}}
    count = walk(method['steps'], scope, '/method/steps', [], [], 'stop', None)
    occurrences = []
    walk(method['steps'], scope, '/method/steps', [], [], 'stop', occurrences)
    assert count == len(occurrences)
    return occurrences


def native_values(value, field=''):
    if isinstance(value, Quantity):
        # Native manual fields use uL, uL/s, ms and controller steps, not arbitrary units.
        expected = 'uL' if field.endswith('_ul') else 'uL/s' if field.endswith('_ul_s') or field in ('speed', 'aspirate_speed', 'dispense_speed') else 's' if field.endswith(('_ms', '_s')) or field == 'seconds' else 'degC' if field.endswith('_c') else 'steps' if field.endswith('_steps') else None
        if expected is None or value.unit != expected:
            fail(f'No compatible native unit mapping for {field}')
        n = value.value * (1000 if field.endswith('_ms') else 1)
        return decimal_text(n)
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, dict):
        return {k: native_values(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [native_values(v, field) for v in value]
    return value


def emit_rows(rows):
    """Reuse the existing emitter per occurrence; retain host-only native gates."""
    document = deepcopy(_NATIVE['document_template'])
    document['protocol_id'] = 'method-content'
    actions = document['stages'][0]['actions'] = []
    visible, issues = [], []
    for row in rows:
        intent = row['intent']
        from bioxp_method_native import native_action
        try:
            direct = native_action(intent.get('operation'), {k: v for k, v in intent.items() if k != 'operation'})
        except (ValueError, TypeError) as exc:
            issues.append({'step_id': row['step_id'], 'message': str(exc)})
            continue
        if intent.get('operation') == 'transfer' and 'recipe' in intent:
            from bioxp_method_planning import positioned_transfer_recipes
            try:
                recipes = positioned_transfer_recipes({k: v for k, v in intent.items() if k != 'operation'})
                generated = []
                for pair, recipe in enumerate(recipes):
                    kind, params = native_action('liquid_recipe', {'recipe': recipe})
                    action = deepcopy(_NATIVE['document_template']['stages'][0]['actions'][0])
                    action.update(kind=kind, params=params, metadata={'pair_index': pair}, required_capability=row.get('required_capability'))
                    generated.append(action)
            except (ValueError, TypeError, KeyError) as exc:
                issues.append({'step_id': row['step_id'], 'message': str(exc)})
                continue
        elif direct is not None:
            action = deepcopy(_NATIVE['document_template']['stages'][0]['actions'][0])
            action.update(kind=direct[0], params=direct[1], metadata={}, required_capability=row.get('required_capability'))
            generated = [action]
        elif intent.get('operation') in ('checkpoint', 'note'):
            if set(intent) != {'operation', 'message'} or not isinstance(intent['message'], str):
                issues.append({'step_id': row['step_id'], 'message': 'checkpoint/note requires exactly one string message'})
                continue
            action = deepcopy(_NATIVE['document_template']['stages'][0]['actions'][0])
            action.update(kind='pause_review' if intent['operation'] == 'checkpoint' else 'note',
                          params={'message': intent['message']}, pause_message=intent['message'],
                          review_required=intent['operation'] == 'checkpoint', metadata={},
                          required_capability=row.get('required_capability'))
            generated = [action]
        else:
            emitted = preview(WorkflowPreviewRequest.model_validate({'draft': {
                'schema': 'bms.bioxp-workflow-draft.v1', 'steps': [row], 'editor_state': {}}, 'protocol_id': 'method-content'}))
            issues.extend({'step_id': i.step_id, 'message': i.message} for i in emitted.issues)
            if emitted.document is None:
                continue
            generated = emitted.document['stages'][0]['actions']
            for visible_action, action in zip(emitted.actions, generated):
                action['metadata']['pair_index'] = visible_action.pair_index
        for generated_index, action in enumerate(generated):
            action['metadata']['generated_index'] = generated_index
            index = len(actions)
            action['action_id'] = f'method-action-{index}'
            actions.append(action)
            visible.append(row['step_id'])
    return (None if issues else document), visible, issues


def native_cost(intent):
    operation = intent.get('operation')
    for field in ({'transfer': ['source', 'destination'], 'cavro_application': ['application'], 'liquid_recipe': ['recipe']}.get(operation, [])):
        if field in intent and not isinstance(intent[field], dict):
            fail(f'{field} must be an object')
    if operation == 'mix':
        return 2 * int(number(intent.get('cycles', 1)))
    if operation == 'transfer':
        pairs = len(intent.get('source', {}).get('wells', []))
        return pairs * (8 + native_cost({'operation': 'liquid_recipe', 'recipe': intent['recipe']}) if 'recipe' in intent else 8)
    if operation == 'thermal_profile':
        return max(1, int(number(intent.get('repeat', 1))) * len(intent.get('segments', [])))
    if operation == 'cavro_application':
        return max(1, len(intent.get('application', {}).get('operations', [])))
    if operation == 'liquid_recipe':
        recipe = intent.get('recipe', {})
        multi = recipe.get('multi') or {}
        count = int(number(multi.get('conditioning_back_to_source_count', 0)))
        # Lists are already authored, but their aggregate lowering and repeated
        # conditioning must be bounded before native allocates any operations.
        def width(value):
            if isinstance(value, list):
                return len(value) + sum(width(x) for x in value)
            if isinstance(value, dict):
                return sum(width(v) for k, v in value.items() if k != 'liquid_settings')
            return 0
        return 8 + count + width(recipe) + 2 * len(multi.get('aliquots', []))
    return 1


def compile_method(request):
    """Return an all-or-nothing native document with addressable findings."""
    result = {'document': None, 'issues': [], 'digest': None, 'resolved': {}, 'dependencies': {},
              'water_substitutions': [], 'provenance': [], 'simulation': {'status': 'unknown', 'issues': []}}
    try:
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 28, ROUND_HALF_EVEN
            if not isinstance(request, dict):
                fail('Compile request must be an object')
            method = request.get('method')
            validate_method(method)
            dependencies = deepcopy(request.get('dependencies', {}))
            if not isinstance(dependencies, dict):
                fail('Dependencies must be an object', '/dependencies')
            result['dependencies'] = dependencies
            params = bind(method.get('parameters', []), request.get('bindings', {}), {'param': {}, 'arg': {}}, '/method/parameters')
            result['resolved']['parameters'] = json_value(params)
            occurrences = _lower(method, dependencies, params)
            from bioxp_method_planning import prepare_occurrences, emission_inputs, initial_state
            # Normalize typed quantities before policy and class-field resolution.
            for occurrence in occurrences:
                occurrence['inputs'] = native_values(occurrence['inputs'])
            occurrences, liquid_records, substitutions, planning_issues = prepare_occurrences(occurrences, method, dependencies)
            result['resolved']['occurrences'] = json_value(occurrences)
            result['resolved']['liquids'] = liquid_records
            result['water_substitutions'] = substitutions
            result['issues'].extend(planning_issues)
            rows = []
            for occurrence in occurrences:
                inputs = emission_inputs(occurrence)
                if not isinstance(inputs, dict):
                    fail('Action inputs must be an object', occurrence['path'])
                intent = inputs if occurrence['action'] == 'native_intent' else {'operation': occurrence['action'], **inputs}
                if occurrence['action'] == 'plunger':
                    from bioxp_method_native import validate
                    diagnostic = validate(inputs, _NATIVE['request']['$defs']['DiagnosticPlunger'])
                    intent = {'operation': 'diagnostic_pipette', 'diagnostic': diagnostic}
                elif occurrence['action'] == 'tip_pickup':
                    if 'channels' in inputs and (inputs['channels'] != [0, 1, 2, 3] or any(type(c) is not int for c in inputs['channels'])):
                        fail('Source pickup is a group of four, not selected independent channels', occurrence['path'])
                    intent['operation'] = 'load_tip'
                    intent.pop('channels', None)  # source pickup is a group of four
                elif occurrence['action'] in ('tip_eject', 'eject_tip'):
                    if set(inputs) != {'channels'} or inputs.get('channels') != [0, 1, 2, 3] or any(type(c) is not int for c in inputs['channels']):
                        fail('Source eject requires exactly explicit channels [0,1,2,3]; other settings are not discarded', occurrence['path'])
                    intent = {'operation': 'tip_eject'}
                if occurrence['action'] != 'native_intent' and inputs.get('operation', occurrence['action']) != occurrence['action']:
                    fail('Action/operation mismatch', occurrence['path'])
                # Bound compound expansion before the existing emitter allocates its children.
                cost = native_cost(intent)
                if cost < 0 or cost > MAX_ACTIONS:
                    fail('Native expansion exceeds resource limit', occurrence['path'], 'resource_limit')
                row = {'step_id': occurrence['occurrence_id'], 'intent': intent}
                if 'required_capability' in occurrence:
                    row['required_capability'] = occurrence['required_capability']
                rows.append((row, cost))
            if sum(cost for _, cost in rows) > MAX_ACTIONS:
                fail('Native expansion exceeds 100000 actions', code='resource_limit')
            if not rows:
                fail('Method has no executable occurrences', '/method/steps', 'empty_expansion')
            document, visible_rows, emission_issues = emit_rows([row for row, _ in rows])
            by_id = {o['occurrence_id']: o for o in occurrences}
            for issue in emission_issues:
                o = by_id.get(issue['step_id'], {})
                result['issues'].append({'code': 'native_representation_error', 'category': 'representation',
                    'message': issue['message'], 'path': o.get('path', '/method'), 'step_id': o.get('step_id'), 'occurrence_id': issue['step_id']})
            if document is None:
                for record in result['resolved'].get('liquids', []):
                    for field in record['fields'].values():
                        field['emitted'] = {'status': 'not_emitted'}
                return result
            liquid_actions_by_occurrence = {}
            for visible, action in zip(visible_rows, document['stages'][0]['actions']):
                if action['params'].get('operation') == 'cavro_liquid_recipe':
                    liquid_actions_by_occurrence.setdefault(visible, []).append(action)
            for record in result['resolved'].get('liquids', []):
                emitted_actions = liquid_actions_by_occurrence[record['occurrence_id']]
                action = emitted_actions[0]
                for field in record['fields'].values():
                    emission = field['emitted']
                    if emission.get('status') == 'emitted':
                        actual = action['params']
                        for key in emission['native_path'].strip('/').split('/'):
                            actual = actual[key]
                        emission.update(value=deepcopy(actual), native_action_id=action['action_id'],
                                        native_action_ids=[a['action_id'] for a in emitted_actions])
                for emitted_action in emitted_actions:
                    emitted_action['params']['recipe'].setdefault('liquid_settings', {})['bms_resolution'] = deepcopy(record)
            for o in occurrences:
                result['provenance'].append({k: deepcopy(o[k]) for k in ('occurrence_id', 'step_id', 'path', 'call_path', 'loop_path')})
                result['provenance'][-1]['native_action_ids'] = []
            provenance = {p['occurrence_id']: p for p in result['provenance']}
            actions = document['stages'][0]['actions']
            for visible, action in zip(visible_rows, actions):
                o = by_id[visible]
                action['source_occurrence_id'] = o['occurrence_id']
                action['on_error'] = o['on_error']
                action['metadata']['bms_method'] = {k: deepcopy(o[k]) for k in ('occurrence_id', 'step_id', 'path', 'call_path', 'loop_path', 'group_path')}
                if 'labware_id' in o['inputs']:
                    action['metadata']['bms_method']['labware_id'] = deepcopy(o['inputs']['labware_id'])
                if 'generated_by' in o:
                    action['metadata']['bms_method']['generated_by'] = deepcopy(o['generated_by'])
                    provenance[o['occurrence_id']]['generated_by'] = deepcopy(o['generated_by'])
                for key in ('compound_parent', 'compound_index'):
                    if key in o:
                        action['metadata']['bms_method'][key] = deepcopy(o[key])
                        provenance[o['occurrence_id']][key] = deepcopy(o[key])
                if o['action'] == 'transfer' and action['kind'] in ('pipette_aspirate', 'pipette_dispense'):
                    effects = []
                    for effect in o['inputs'].get('channel_transfers', []):
                        if effect.get('pair_index', 0) == action['metadata'].get('pair_index', 0):
                            effect = deepcopy(effect)
                            effect.pop('destination' if action['kind'] == 'pipette_aspirate' else 'source', None)
                            effects.append(effect)
                    action['metadata']['bms_method']['planned_liquid_effects'] = effects
                    action['metadata']['bms_method']['effect_phase'] = 'aspirate' if action['kind'] == 'pipette_aspirate' else 'dispense'
                if action['params'].get('operation') == 'cavro_liquid_recipe':
                    recipe = action['params']['recipe']
                    effects = [deepcopy(e) for e in o['inputs'].get('channel_transfers', [])
                               if e.get('pair_index', 0) == action['metadata'].get('pair_index', 0)]
                    phases = []
                    for channel in recipe['channels']:
                        effect = next((e for e in effects if e['channel'] == channel), {})
                        for name in ('leading_air', 'trailing_air'):
                            air = recipe[name]
                            if air is not None:
                                phases.append({'recipe_phase': name, 'channel': channel, 'kind': 'air',
                                    'volume_ul': 0, 'air_ul': air['volume_ul'],
                                    'commanded_displacement_ul': air['volume_ul']})
                        phases.append({'recipe_phase': 'aspirate', 'channel': channel,
                            'source': deepcopy(effect.get('source')), 'volume_ul': recipe['target_liquid_ul'],
                            'commanded_displacement_ul': recipe['commanded_aspiration_ul'], 'air_ul': 0})
                        for index, segment in enumerate(recipe['dispense_segments']):
                            phases.append({'recipe_phase': 'dispense_segments', 'segment_index': index, 'channel': channel,
                                'destination': deepcopy(effect.get('destination')), 'volume_ul': None,
                                'commanded_displacement_ul': segment['volume_ul'], 'air_ul': None})
                        if recipe['final_empty_speed_ul_s'] is not None:
                            phases.append({'recipe_phase': 'final_empty', 'channel': channel,
                                'volume_ul': None, 'air_ul': None, 'commanded_displacement_ul': None})
                    action['metadata']['bms_method']['planned_recipe_phase_effects'] = phases
                    action['metadata']['bms_method']['effect_limitations'] = 'Phase selectors are not native child IDs; segment displacement does not measure liquid delivery or completed transfer'
                if o['action'] in {'aspirate', 'dispense', 'mix'}:
                    action['metadata']['bms_method']['planned_liquid_effects'] = deepcopy(o['inputs'].get('channel_transfers', []))
                provenance[o['occurrence_id']]['native_action_ids'].append(action['action_id'])
            # Consecutive structural groups retain native stage boundaries. A
            # repeated group gets a distinct stage; no grouping reorders actions.
            stages, previous = [], None
            for visible, action in zip(visible_rows, actions):
                o = by_id[visible]
                key = [o['group_path'], o['call_path'], o['loop_path']]
                if not stages or key != previous:
                    stage = deepcopy(_NATIVE['document_template']['stages'][0])
                    stage.update(stage_id=stage['stage_id'] if not stages else f'method-stage-{len(stages)}',
                        actions=[], metadata={'bms_method': {'group_path': o['group_path'], 'call_path': o['call_path'], 'loop_path': o['loop_path']}})
                    stages.append(stage)
                    previous = key
                action['stage_id'] = stages[-1]['stage_id']
                stages[-1]['actions'].append(action)
            document['stages'] = stages
            from bioxp_method_native import EXPORT
            content = {'compiler': VERSION, 'native_source_commit': EXPORT['source_commit'],
                       'method': semantic(method), 'bindings': request.get('bindings', {}),
                       'dependencies': dependencies, 'initial_state': request.get('initial_state'), 'document': document}
            result['digest'] = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
            document['protocol_id'] = 'method-' + result['digest']
            document['metadata']['bms_method'] = {'compiler': VERSION, 'digest': result['digest'],
                'method': deepcopy(method), 'bindings': deepcopy(request.get('bindings', {})), 'dependencies': dependencies,
                'native_source_commit': EXPORT['source_commit'], 'resolved_liquids': deepcopy(result['resolved'].get('liquids', [])),
                'water_substitutions': deepcopy(result['water_substitutions']), 'initial_state': deepcopy(request.get('initial_state'))}
            result['document'] = document
            try:
                from bioxp_method_simulation import simulate_method
            except ImportError as exc:
                if exc.name != 'bioxp_method_simulation':
                    raise
                result['simulation'] = {'status': 'unknown', 'issues': [{'code': 'simulation_integration_pending', 'category': 'advisory', 'message': 'Simulation module is not installed; no physical-state inference'}]}
            else:
                try:
                    simulation_rows = deepcopy(json_value(occurrences))
                    head, positions = {}, {}
                    for stage in document['stages']:
                        for action in stage['actions']:
                            if action['params'].get('operation') == 'cavro_liquid_recipe':
                                recipe = action['params']['recipe']
                                motion = [part for phase in ('before_leading_air', 'before_liquid', 'after_liquid') for part in recipe[phase]]
                                multi = recipe['multi']
                                if multi is not None:
                                    motion += multi['conditioning_before'] + multi['conditioning_after']
                                motion += recipe['before_dispense']
                                if multi is not None:
                                    for aliquot in multi['aliquots'] + ([multi['excess']] if multi['excess'] is not None else []):
                                        motion += aliquot['before'] + aliquot['after']
                                motion += recipe['after_dispense'] + recipe['final_empty_before']
                                for part in motion:
                                    if part.get('operation') == 'position':
                                        params = part['positioning']
                                        if params['operation'] == 'move':
                                            head = deepcopy(params)
                                        else:
                                            head.update(deepcopy(params))
                                        positions[action['source_occurrence_id']] = deepcopy(head)
                            if action['kind'] == 'pipette_position':
                                if action['params']['operation'] == 'move':
                                    head = deepcopy(action['params'])
                                else:
                                    head.update(deepcopy(action['params']))
                                positions[action['source_occurrence_id']] = deepcopy(head)
                    for occurrence in simulation_rows:
                        if occurrence['occurrence_id'] in positions:
                            occurrence['_native_head_reference'] = positions[occurrence['occurrence_id']]
                    result['simulation'] = simulate_method(simulation_rows, initial_state(method, request.get('initial_state')))
                except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                    result['simulation'] = {'status': 'unknown', 'issues': [{'code': 'simulation_unknown',
                        'category': 'advisory', 'message': str(exc)}]}
    except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as exc:
        result['document'] = None
        result['digest'] = None
        result['issues'].append({'code': getattr(exc, 'code', 'representation_error'), 'category': 'representation',
            'message': str(exc), 'path': getattr(exc, 'path', '')})
    return result
