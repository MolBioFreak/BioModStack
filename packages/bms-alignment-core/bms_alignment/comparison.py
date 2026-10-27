"""Comparison using only declared source-member/occurrence identities.

Coordinates are zero-based; no correspondence is inferred from labels or sequence.
"""
from .formats import coordinates
from .models import SequenceSet


def _index(roster):
    groups = {}
    unknown = []
    for row in roster.rows:
        if row.source_member_id is None or row.occurrence_id is None:
            unknown.append(row.row_id)
        else:
            groups.setdefault((row.source_member_id, row.occurrence_id), []).append(row)
    ambiguous = {key: rows for key, rows in groups.items() if len(rows) != 1}
    return {key: rows[0] for key, rows in groups.items() if len(rows) == 1}, unknown, ambiguous


def _ident(key):
    return {'source_member_id': key[0], 'occurrence_id': key[1]}


def compare(a: SequenceSet, b: SequenceSet) -> dict:
    left, left_unknown, left_ambiguous = _index(a)
    right, right_unknown, right_ambiguous = _index(b)
    # An identity ambiguous on either side cannot be matched even if unique on the other.
    common = sorted((left.keys() & right.keys()) - left_ambiguous.keys() - right_ambiguous.keys())
    rows = []
    for key in common:
        l, r = left[key], right[key]
        x, y = coordinates(l), coordinates(r)
        xi = {v: i for i, v in enumerate(x) if v is not None}
        yi = {v: i for i, v in enumerate(y) if v is not None}
        # A3M lowercase insertions are not ordinary alignment columns.
        known = (l.source_offset is not None and r.source_offset is not None
                 and a.source_format != 'a3m' and b.source_format != 'a3m')
        rows.append({**_ident(key), 'left_row_id': l.row_id, 'right_row_id': r.row_id,
                     'positions': [{'source': p, 'left_column': xi[p], 'right_column': yi[p]} for p in sorted(xi.keys() & yi.keys())] if known else [],
                     'left_only_positions': sorted(xi.keys() - yi.keys()) if known else [],
                     'right_only_positions': sorted(yi.keys() - xi.keys()) if known else [],
                     'mapping_unknown': not known,
                     'left_unmapped_columns': [i for i, c in enumerate(l.sequence) if c not in '-.' and (x[i] is None or a.source_format == 'a3m')],
                     'right_unmapped_columns': [i for i, c in enumerate(r.sequence) if c not in '-.' and (y[i] is None or b.source_format == 'a3m')]})
    def excluded(roster, matched):
        return [{'row_id': row.row_id, 'source_member_id': row.source_member_id,
                 'occurrence_id': row.occurrence_id} for row in roster.rows if id(row) not in matched]
    return {'rows': rows, 'left_excluded': len(a.rows)-len(common), 'right_excluded': len(b.rows)-len(common),
            'left_excluded_members': excluded(a, {id(left[k]) for k in common}),
            'right_excluded_members': excluded(b, {id(right[k]) for k in common}),
            'left_unknown_identity_rows': left_unknown, 'right_unknown_identity_rows': right_unknown,
            'left_ambiguous_identities': [{**_ident(k), 'row_ids': [r.row_id for r in v]} for k, v in sorted(left_ambiguous.items())],
            'right_ambiguous_identities': [{**_ident(k), 'row_ids': [r.row_id for r in v]} for k, v in sorted(right_ambiguous.items())],
            'identity_policy': 'explicit source member and occurrence only',
            'coordinate_convention': 'zero-based source positions and alignment columns'}
