"""Comparison using only declared source-member/occurrence identities.

Coordinates are zero-based; no correspondence is inferred from labels or sequence.
"""
from .formats import coordinates
from .models import SequenceSet
from collections import Counter


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
        if a.source_format == 'a3m': x = [None] * len(l.sequence); xi = {}
        if b.source_format == 'a3m': y = [None] * len(r.sequence); yi = {}
        lu = [i for i, c in enumerate(l.sequence) if c not in '-.' and x[i] is None]
        ru = [i for i, c in enumerate(r.sequence) if c not in '-.' and y[i] is None]
        rows.append({**_ident(key), 'left_row_id': l.row_id, 'right_row_id': r.row_id,
                     'positions': [{'source': p, 'left_column': xi[p], 'right_column': yi[p]} for p in sorted(xi.keys() & yi.keys())],
                     'left_only_positions': sorted(xi.keys() - yi.keys()) if not ru else [],
                     'right_only_positions': sorted(yi.keys() - xi.keys()) if not lu else [],
                     'mapping_unknown': bool(lu or ru),
                     'left_unmapped_columns': lu, 'right_unmapped_columns': ru,
                     'left_transformation_provenance': l.transformation_provenance,
                     'right_transformation_provenance': r.transformation_provenance})
    def excluded(roster, matched):
        return [{'row_id': row.row_id, 'source_member_id': row.source_member_id,
                 'occurrence_id': row.occurrence_id} for row in roster.rows if id(row) not in matched]
    # Each shared source residue contributes to exactly one column in each
    # alignment. Grouping by columns counts residue pairs without materializing
    # the potentially quadratic set of row pairs in a dense column.
    lc, rc, both = Counter(), Counter(), Counter()
    for row in rows:
        for p in row['positions']:
            lcol, rcol = p['left_column'], p['right_column']
            lc[lcol] += 1; rc[rcol] += 1; both[lcol, rcol] += 1
    pairs = lambda counts: sum(n * (n - 1) // 2 for n in counts.values())
    lp, rp, shared = pairs(lc), pairs(rc), pairs(both)
    union = lp + rp - shared
    agreement = {
        'method': 'shared-source-residue-pair-jaccard.v1',
        'matched_rows': len(rows),
        'shared_mapped_residues': sum(len(row['positions']) for row in rows),
        'left_unmapped_residues': sum(len(row['left_unmapped_columns']) for row in rows),
        'right_unmapped_residues': sum(len(row['right_unmapped_columns']) for row in rows),
        'rows_with_unknown_mapping': sum(row['mapping_unknown'] for row in rows),
        'left_only_mapped_residues_known': sum(len(row['left_only_positions']) for row in rows),
        'right_only_mapped_residues_known': sum(len(row['right_only_positions']) for row in rows),
        'left_aligned_pairs': lp, 'right_aligned_pairs': rp,
        'shared_aligned_pairs': shared, 'union_aligned_pairs': union,
        'jaccard': shared / union if union else None,
    }
    return {'rows': rows, 'agreement': agreement, 'left_excluded': len(a.rows)-len(common), 'right_excluded': len(b.rows)-len(common),
            'left_excluded_members': excluded(a, {id(left[k]) for k in common}),
            'right_excluded_members': excluded(b, {id(right[k]) for k in common}),
            'left_unknown_identity_rows': left_unknown, 'right_unknown_identity_rows': right_unknown,
            'left_ambiguous_identities': [{**_ident(k), 'row_ids': [r.row_id for r in v]} for k, v in sorted(left_ambiguous.items())],
            'right_ambiguous_identities': [{**_ident(k), 'row_ids': [r.row_id for r in v]} for k, v in sorted(right_ambiguous.items())],
            'identity_policy': 'explicit source member and occurrence only',
            'coordinate_convention': 'zero-based source positions and alignment columns'}
