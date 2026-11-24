"""Compact method reads and small addressed edits for API/LLM callers.

Pure functions over the stored method document. The outline is presentation
only; edits change exactly the addressed values and keep every other byte of
the document, including fields this module does not understand.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


class EditError(ValueError):
    def __init__(self, index: int, message: str):
        super().__init__(f"edit {index}: {message}")
        self.index = index
        self.detail = message


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _num(value: Any) -> str:
    if isinstance(value, dict) and 'expr' in value:
        return '<param ' + str(_record(value['expr']).get('id', '?')) + '>'
    return '?' if value is None or value == '' else str(value)


def _wells(endpoint: Any) -> str:
    ep = _record(endpoint)
    if 'material_id' in ep:
        return 'material ' + str(ep['material_id'])
    wells = ep.get('wells')
    where = str(ep.get('target') or ep.get('station') or ep.get('location_id', '?'))
    if isinstance(wells, list) and wells:
        shown = ','.join(str(w) for w in wells[:6]) + (f',…(+{len(wells) - 6})' if len(wells) > 6 else '')
        return f'{where} {shown}'
    return where


def step_line(node: dict) -> str:
    """One line per step: label, then the values that matter for that action."""
    label = node.get('label') if _text(node.get('label')) else ''
    if node.get('type') in ('group', 'repeat'):
        count = f" ×{_num(node.get('count'))}" if node.get('type') == 'repeat' else ''
        return f"{label or node.get('type')}{count} [{len(node.get('steps') or [])} steps]"
    action = node.get('action') or node.get('type') or '?'
    inputs = node.get('inputs')
    if isinstance(inputs, dict) and 'expr' in inputs:
        detail = 'inputs from ' + _num(inputs)
    else:
        i = _record(inputs)
        if action == 'transfer':
            channels = i.get('channels')
            detail = f"{_num(i.get('volume_ul'))} µL {_wells(i.get('source'))} → {_wells(i.get('destination'))}" + (
                f" ch {channels}" if isinstance(channels, list) else '')
        elif action == 'mix':
            detail = f"{_num(i.get('volume_ul'))} µL ×{_num(i.get('cycles'))}"
        elif action in ('thermal_profile',):
            segs = ', '.join(f"{_num(_record(s).get('target_temp_c'))}°C {_num(_record(s).get('duration_s'))}s"
                             for s in (i.get('segments') or []))
            detail = f"({segs}) ×{_num(i.get('repeat', 1))}"
        elif action in ('thermal_hold', 'thermal_setpoint', 'chiller_setpoint'):
            detail = f"{i.get('bank', '')} {_num(i.get('target_temp_c'))}°C" + (
                f" {_num(i.get('duration_s'))}s" if 'duration_s' in i else '')
        elif action == 'tip_pickup':
            detail = f"rack {_num(i.get('tray'))} {_num(i.get('well'))}"
        elif action == 'tip_eject':
            detail = f"ch {i.get('channels')}"
        elif action == 'plate_move':
            detail = f"{i.get('plate_id') or i.get('labware_id')} → {i.get('target_location')}"
        elif action == 'move_cover':
            detail = f"{i.get('cover_id') or i.get('source')} → {i.get('target_location') or i.get('destination')}"
        elif action == 'move':
            detail = f"loc {_num(i.get('location_id'))} {i.get('well', '')}".rstrip()
        elif action in ('lower', 'lift'):
            detail = f"loc {_num(i.get('location_id'))}"
        elif action == 'wait':
            detail = f"{_num(i.get('seconds', i.get('duration')))}s"
        elif action == 'thermal_door':
            detail = str(i.get('door_command', ''))
        elif action in ('checkpoint', 'note'):
            detail = str(i.get('message', ''))[:80]
        else:
            detail = ''
    head = f"{action}: {label}" if label and label.lower() != str(action).lower() else str(action)
    return f"{head} — {detail}" if detail else head


def stages(method: dict) -> list[dict]:
    """Same grouping as the Workflows page: contiguous editor_state.workflow_pages, else one stage per root step."""
    nodes = method.get('steps') if isinstance(method.get('steps'), list) else []
    rows = [_record(r) for r in (_record(method.get('editor_state')).get('workflow_pages') or []) if isinstance(r, dict)]
    index: dict[str, list[int]] = {}
    for n, node in enumerate(nodes):
        sid = _record(node).get('step_id')
        if _text(sid):
            index.setdefault(sid, []).append(n)
    candidates = []
    for row in rows:
        ids = row.get('step_ids')
        if not _text(row.get('id')) or row.get('id') == 'starting-deck' or not _text(row.get('label')) \
                or sum(1 for o in rows if o.get('id') == row.get('id')) != 1 or not isinstance(ids, list) or not ids:
            continue
        positions = [index[i][0] if _text(i) and len(index.get(i, [])) == 1 else -1 for i in ids]
        if any(p < 0 or p != positions[0] + k for k, p in enumerate(positions)):
            continue
        candidates.append((row['id'], row['label'], positions))
    valid = [c for c in candidates if not any(o is not c and set(o[2]) & set(c[2]) for o in candidates)]
    out, n = [], 0
    while n < len(nodes):
        grouped = next((c for c in valid if c[2][0] == n), None)
        positions = grouped[2] if grouped else [n]
        node = _record(nodes[n])
        label = grouped[1] if grouped else next((v for v in (node.get('label'), node.get('action'), node.get('type')) if _text(v)), f'Step {n + 1}')
        out.append({'index': len(out) + 1, 'id': grouped[0] if grouped else f'step:/steps/{n}', 'label': label,
                    'paths': [f'/steps/{p}' for p in positions]})
        n += len(positions)
    return out


def _walk(nodes: list, base: str, depth: int, out: list) -> None:
    for n, node in enumerate(nodes):
        node = _record(node)
        path = f'{base}/{n}'
        out.append({'path': path, 'depth': depth, 'step_id': node.get('step_id'), 'line': step_line(node)})
        if isinstance(node.get('steps'), list):
            _walk(node['steps'], path + '/steps', depth + 1, out)


def outline(method: dict, compilation: dict | None = None) -> dict:
    """Stages with one line per step, the starting deck, tips and (when compiled) the check summary."""
    nodes = method.get('steps') if isinstance(method.get('steps'), list) else []
    result = {'name': method.get('name'), 'schema': method.get('schema'), 'stages': []}
    for stage in stages(method):
        lines: list[dict] = []
        for root in stage['paths']:
            k = int(root.rsplit('/', 1)[1])
            node = _record(nodes[k])
            lines.append({'path': root, 'depth': 0, 'step_id': node.get('step_id'), 'line': step_line(node)})
            if isinstance(node.get('steps'), list):
                _walk(node['steps'], root + '/steps', 1, lines)
        result['stages'].append({'index': stage['index'], 'id': stage['id'], 'label': stage['label'], 'steps': lines})
    plan = _record(method.get('deck_plan'))
    materials = {m.get('id'): m for m in (plan.get('materials') or []) if isinstance(m, dict)}
    by_labware: dict[Any, list] = {}
    for a in plan.get('assignments') or []:
        if isinstance(a, dict):
            by_labware.setdefault(a.get('labware_id'), []).append(a)
    deck = []
    for lw in plan.get('labware') or []:
        if not isinstance(lw, dict):
            continue
        contents = [{'well': a.get('well'), 'material_id': a.get('material_id'),
                     'material': _record(materials.get(a.get('material_id'))).get('name'), 'volume_ul': a.get('volume_ul')}
                    for a in by_labware.get(lw.get('id'), [])]
        deck.append({'labware_id': lw.get('id'), 'station': lw.get('station'), 'name': lw.get('name'),
                     'profile_id': lw.get('profile_id') or None, 'filled_wells': len(contents), 'contents': contents})
    result['deck'] = deck
    result['materials'] = [{'id': m.get('id'), 'name': m.get('name'), 'kind': m.get('kind')} for m in materials.values()]
    result['tips'] = [{'tray': r.get('tray'), 'tip_type': r.get('tip_type'),
                       'available': len(_record(r.get('tips')).get('available') or [])}
                      for r in plan.get('tip_racks') or [] if isinstance(r, dict)]
    result['parameters'] = [{'id': p.get('id'), 'label': p.get('label'), 'type': p.get('type')}
                            for p in method.get('parameters') or [] if isinstance(p, dict)]
    if compilation is not None:
        issues = compilation.get('issues') or []
        counts: dict[str, int] = {}
        for issue in issues:
            cat = str(_record(issue).get('category', 'other'))
            counts[cat] = counts.get(cat, 0) + 1
        result['check'] = {'document_emitted': compilation.get('document') is not None, 'issue_counts': counts,
                           'issues': [{k: _record(i).get(k) for k in ('code', 'category', 'path', 'message')} for i in issues[:40]],
                           'issues_shown': min(len(issues), 40), 'issues_total': len(issues),
                           'simulation_status': _record(compilation.get('simulation')).get('status')}
    return result


# ---------------------------------------------------------------- edits

def _parts(pointer: str) -> list[str]:
    if pointer in ('', '/'):
        return []
    if not pointer.startswith('/'):
        raise ValueError(f'path must start with "/": {pointer!r}')
    return [p.replace('~1', '/').replace('~0', '~') for p in pointer[1:].split('/')]


def _find_step(method: dict, step_id: str) -> str:
    found: list[str] = []
    def walk(nodes, base):
        for n, node in enumerate(nodes):
            if _record(node).get('step_id') == step_id:
                found.append(f'{base}/{n}')
            if isinstance(_record(node).get('steps'), list):
                walk(node['steps'], f'{base}/{n}/steps')
    walk(method.get('steps') or [], '/steps')
    if len(found) != 1:
        raise ValueError(f'step_id {step_id!r} matches {len(found)} steps')
    return found[0]


def _all_step_ids(method: dict) -> set:
    ids: set = set()
    def walk(nodes):
        for node in nodes:
            node = _record(node)
            if _text(node.get('step_id')):
                ids.add(node['step_id'])
            if isinstance(node.get('steps'), list):
                walk(node['steps'])
    walk(method.get('steps') or [])
    return ids


def _resolve(method: dict, edit: dict, key: str = 'path') -> str:
    base = _find_step(method, edit['step']) if _text(edit.get('step')) else ''
    rel = edit.get(key, '')
    if rel and not isinstance(rel, str):
        raise ValueError(f'{key} must be a string')
    return base + (rel if rel and rel != '/' else '')


def _parent(doc: Any, parts: list[str]):
    target = doc
    for p in parts[:-1]:
        if isinstance(target, list):
            target = target[int(p)]
        elif isinstance(target, dict) and p in target:
            target = target[p]
        else:
            raise ValueError(f'path segment {p!r} not found')
    return target, parts[-1]


def _get(doc: Any, parts: list[str]):
    target = doc
    for p in parts:
        target = target[int(p)] if isinstance(target, list) else target[p]
    return target


def _anchor(method: dict, edit: dict) -> tuple[list, int]:
    """Where an inserted/moved step lands: after/before a step_id, or at the end of a parent's steps."""
    if _text(edit.get('after')) or _text(edit.get('before')):
        ref = edit.get('after') or edit.get('before')
        parts = _parts(_find_step(method, ref))
        siblings = _get(method, parts[:-1])
        return siblings, int(parts[-1]) + (1 if _text(edit.get('after')) else 0)
    if _text(edit.get('parent')):
        node = _get(method, _parts(_find_step(method, edit['parent'])))
        if not isinstance(node.get('steps'), list):
            raise ValueError(f"step {edit['parent']!r} has no child steps")
        siblings = node['steps']
    else:
        siblings = method.setdefault('steps', [])
    index = edit.get('index', len(siblings))
    if not isinstance(index, int) or not 0 <= index <= len(siblings):
        raise ValueError('index out of range')
    return siblings, index


def apply_edits(method: dict, edits: list[dict]) -> tuple[dict, list[dict]]:
    """Apply ops in order to a copy. Any failing op rejects the whole batch.

    Ops (all addressed by step_id or JSON Pointer relative to the method):
      set    {step?, path, value}       replace or add one value
      remove {step?, path?}             remove a value, or the whole step when only step is given
      insert {node, after|before|parent|(index)}  insert a new step (unique step_id required)
      move   {step, after|before|parent|(index)}  move an existing step
    """
    doc = deepcopy(method)
    changes = []
    for n, edit in enumerate(edits):
        try:
            if not isinstance(edit, dict):
                raise ValueError('each edit must be an object')
            op = edit.get('op')
            if op == 'set':
                if 'value' not in edit:
                    raise ValueError('set needs value')
                pointer = _resolve(doc, edit)
                parts = _parts(pointer)
                if not parts or parts[0] == 'schema':
                    raise ValueError('cannot replace the whole method or its schema')
                parent, key = _parent(doc, parts)
                if isinstance(parent, list):
                    k = len(parent) if key == '-' else int(key)
                    before = parent[k] if k < len(parent) else None
                    if k == len(parent):
                        parent.append(deepcopy(edit['value']))
                    else:
                        parent[k] = deepcopy(edit['value'])
                elif isinstance(parent, dict):
                    before = parent.get(key)
                    parent[key] = deepcopy(edit['value'])
                else:
                    raise ValueError('parent is not an object or list')
                changes.append({'op': 'set', 'path': pointer, 'before': before, 'value': edit['value']})
            elif op == 'remove':
                pointer = _resolve(doc, edit)
                parts = _parts(pointer)
                if not parts or parts[0] == 'schema':
                    raise ValueError('cannot remove the whole method or its schema')
                parent, key = _parent(doc, parts)
                before = parent.pop(int(key)) if isinstance(parent, list) else parent.pop(key)
                changes.append({'op': 'remove', 'path': pointer, 'before': before})
            elif op == 'insert':
                node = edit.get('node')
                if not isinstance(node, dict) or not _text(node.get('step_id')) or not _text(node.get('type')):
                    raise ValueError('insert needs node with step_id and type')
                if node['step_id'] in _all_step_ids(doc):
                    raise ValueError(f"step_id {node['step_id']!r} already exists")
                siblings, index = _anchor(doc, edit)
                siblings.insert(index, deepcopy(node))
                changes.append({'op': 'insert', 'path': _find_step(doc, node['step_id']), 'value': node})
            elif op == 'move':
                if not _text(edit.get('step')):
                    raise ValueError('move needs step')
                if edit.get('after') == edit['step'] or edit.get('before') == edit['step']:
                    raise ValueError('cannot move a step relative to itself')
                source = _find_step(doc, edit['step'])
                parts = _parts(source)
                parent, key = _parent(doc, parts)
                node = parent.pop(int(key))
                try:
                    siblings, index = _anchor(doc, edit)
                except Exception:
                    parent.insert(int(key), node)
                    raise
                siblings.insert(index, node)
                changes.append({'op': 'move', 'from': source, 'path': _find_step(doc, edit['step'])})
            else:
                raise ValueError(f'unknown op {op!r}; use set, remove, insert or move')
        except EditError:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise EditError(n, str(error) if not isinstance(error, KeyError) else f'path not found: {error}') from None
    return doc, changes


# ---------------------------------------------------------------- recipes

def recipes(examples: list[dict]) -> list[dict]:
    """Each starting point with the questions it asks (its parameters). No values are invented."""
    out = []
    for entry in examples:
        method = _record(entry.get('method'))
        out.append({'id': entry.get('id'), 'name': method.get('name') or entry.get('label'), 'status': entry.get('status'),
                    'description': method.get('description') or entry.get('description'),
                    'questions': [{'id': p.get('id'), 'label': p.get('label'), 'type': p.get('type'),
                                   **({'default': p['default']} if 'default' in p else {})}
                                  for p in method.get('parameters') or [] if isinstance(p, dict)],
                    'steps': sum(1 for _ in _iter_steps(method.get('steps') or []))})
    return out


def _iter_steps(nodes):
    for node in nodes:
        yield node
        if isinstance(_record(node).get('steps'), list):
            yield from _iter_steps(node['steps'])


def build_recipe(entry: dict, answers: dict, name: str | None = None) -> dict:
    """The recipe's method with the caller's answers as bindings. Unknown answer ids are refused; unanswered stay open."""
    method = deepcopy(_record(entry.get('method')))
    known = {p.get('id') for p in method.get('parameters') or [] if isinstance(p, dict)}
    unknown = sorted(set(answers) - known)
    if unknown:
        raise ValueError('unknown question ids: ' + ', '.join(unknown))
    if name:
        method['name'] = name
    bindings = {**deepcopy(_record(entry.get('bindings'))), **deepcopy(answers)}
    return {'method': method, 'bindings': bindings, 'dependencies': deepcopy(_record(entry.get('dependencies'))),
            'unanswered': sorted(known - set(bindings))}
