"""Lossless, request-local Golden Gate wire projection; never a persistence owner.

DNA letters are interned separately from molecular identity. Complete material
states are shared only when every field (including identity/ancestry) agrees.
Only declared workflow DNA/material positions are visited; qualifiers and other
arbitrary scientific JSON are opaque, including reference-looking dictionaries.
"""
from copy import deepcopy
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

TAG = 'bms.golden-gate-wire.v1'
WireKind = Literal['request', 'result', 'save', 'saved', 'portable']


class WorkflowWire(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['bms.golden-gate-wire.v1'] = TAG
    kind: WireKind
    payload: dict[str, JsonValue]
    sequences: list[str]
    materials: list[dict[str, JsonValue]]


def _visit(payload, kind, dna, material):
    def fields(obj, *names):
        for name in names:
            if name in obj and obj[name] is not None:
                obj[name] = dna(obj[name])

    def source(obj):
        value = obj.get('source', {})
        if value.get('kind') == 'inline':
            fields(value, 'sequence')

    def edits(items):
        for edit in items:
            fields(edit, 'accepted_sequence')

    def request(obj):
        for value in obj.get('sources', []):
            source(value)
        if obj.get('task') == 'split_target':
            source(obj['target'])
        elif 'target' in obj:
            fields(obj['target'], 'exact_sequence')
        edits(obj.get('domestication', []))

    def evidence(items):
        for edit in items:
            fields(edit['original'], 'sequence')
            fields(edit['proposal'], 'original_sequence', 'proposed_sequence')

    def result(obj):
        request(obj['requested'])
        evidence(obj.get('edits', []))
        for candidate in obj.get('solutions', []):
            request(candidate['fixed_request'])
            design = candidate['design']
            design['materials'] = [material(m) for m in design['materials']]
            for product in design['solutions']:
                fields(product, 'sequence')
            for digest in design['digests']:
                for fragment in digest['fragments']:
                    fields(fragment, 'top_strand_sequence')
            for primer in design['primers']:
                fields(primer, 'full_sequence', 'annealing_sequence')

    def selection(obj):
        request(obj['request'])
        if obj.get('authored_request') is not None:
            request(obj['authored_request'])
        for value in obj.get('original_sources', []):
            source(value)
        edits(obj.get('accepted_edits', []))
        evidence(obj.get('edit_evidence', []))

    if kind == 'request':
        request(payload)
    elif kind == 'result':
        result(payload)
    elif kind == 'save':
        selection(payload['selection'])
    elif kind == 'saved':
        selection(payload['selection'])
        result(payload['result'])
    elif kind == 'portable':
        result(payload['result'])
    else:
        raise ValueError('Unknown Golden Gate wire kind')


def project_workflow(payload: dict, kind: WireKind) -> WorkflowWire:
    """Project native model_dump(mode='json') without mutating it."""
    sequences, materials = [], []
    sequence_ids, material_ids = {}, {}

    def dna(value):
        if not isinstance(value, str):
            raise ValueError('Expected native Golden Gate DNA string')
        if value not in sequence_ids:
            sequence_ids[value] = len(sequences)
            sequences.append(value)
        return {'sequence_ref': sequence_ids[value]}

    def material(value):
        key = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        if key not in material_ids:
            material_ids[key] = len(materials)
            state = deepcopy(value)
            state['sequence'] = dna(state['sequence'])
            materials.append(state)
        return {'material_ref': material_ids[key]}

    body = deepcopy(payload)
    _visit(body, kind, dna, material)
    return WorkflowWire(kind=kind, payload=body, sequences=sequences, materials=materials)


def expand_workflow(wire: WorkflowWire | dict, kind: WireKind) -> dict:
    """Expand wire into independent native JSON; reject dangling/mixed refs.

    The receiving route still validates the complete native DTO afterwards.
    Full native callers bypass this function and retain their original contract.
    """
    wire = WorkflowWire.model_validate(wire)
    if wire.kind != kind:
        raise ValueError('Golden Gate wire kind does not match endpoint')

    def lookup(value, key, table):
        if not isinstance(value, dict) or set(value) != {key}:
            raise ValueError(f'Expected Golden Gate {key}')
        index = value[key]
        if type(index) is not int or index < 0 or index >= len(table):
            raise ValueError(f'Invalid Golden Gate {key}')
        return deepcopy(table[index])

    def dna(value):
        return lookup(value, 'sequence_ref', wire.sequences)

    def material(value):
        state = lookup(value, 'material_ref', wire.materials)
        state['sequence'] = dna(state['sequence'])
        return state

    body = deepcopy(wire.payload)
    try:
        _visit(body, kind, dna, material)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Malformed Golden Gate wire payload') from exc
    return body
