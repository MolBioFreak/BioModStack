"""Advisory current-row readback in the selected RFD4 image; no inference.

Observe the actual native resolver call during condition application, after contig
assembly and with native original-residue numbering and prior conditions in effect.
The hook is process-local and restored; no query grammar or condition compiler here.
"""
from __future__ import annotations
import contextlib
import json
from pathlib import Path
import sys


def inspect_condition(payload):
    import numpy as np
    import rfproteina.inference_utils.condition_spec as native
    from bms_rfd4.inputs import condition_example_spec

    inp, row = payload['input'], payload['row']
    example = inp['examples'][row['exampleIndex']]
    spec = condition_example_spec(inp, example, payload['paths'])
    target = (row['family'], row['value'], row['rowIndex'])
    ordered = [(family, value, index) for family, values in spec.get('conditions', {}).items()
               for value, entries in values.items() for index, _ in enumerate(entries)]
    ordinal = ordered.index(target)
    current = spec['conditions'][row['family']][row['value']][row['rowIndex']]
    if current != {key: row[key] for key in ('select', 'group') if key in row}:
        raise ValueError('Condition row changed before inspection')
    resolve = native.resolve_mask
    observed, calls = [], 0

    def observe(array, expression):
        nonlocal calls
        mask = resolve(array, expression)
        if calls == ordinal:
            if expression != row['select']:
                raise ValueError('Native row evaluation order differs; no inferred mask')
            categories = array.get_annotation_categories()
            for index in np.flatnonzero(mask):
                observed.append({key: array.get_annotation(key)[index].item()
                                 for key in ('chain_id', 'res_id', 'ins_code', 'atom_name', 'res_name', 'altloc_id')
                                 if key in categories})
        calls += 1
        return mask

    native.resolve_mask = observe
    try:
        native.build_annotated_atom_array_from_condition_spec(
            spec, spec_dir=Path('.'), example_id=example['name'], validate=False)
    finally:
        native.resolve_mask = resolve
    if calls != len(ordered):
        raise ValueError('Native row evaluation order/count differs; no inferred mask')
    return {'atoms': observed, 'input_as_annotated_cif': spec['input_as_annotated_cif'],
            'example_name': example['name'], 'row': row,
            'scope': 'Row targets at native application time, not final last-write-wins condition values'}


if __name__ == '__main__':
    payload = json.load(sys.stdin)
    with contextlib.redirect_stdout(sys.stderr):
        result = inspect_condition(payload)
    print(json.dumps(result, allow_nan=False))
