"""Deterministic quick identity statistics for a rectangular displayed alignment.

Exact, case-insensitive symbol identity; '-' and '.' are gaps. No substitution
matrix or inference of biological function. Limits bound the optional pair table,
not the all-row column calculation, and are reported explicitly to API callers.
"""
from collections.abc import Sequence
from itertools import combinations

PAIR_CELL_LIMIT = 5_000_000
PAIR_COUNT_LIMIT = 200
POLICY = ('Exact symbol identity, ignoring case. Columns with a gap (- or .) in '
          'either row are excluded for that pair. Ambiguous symbols match only '
          'the identical symbol. Percentages cover the whole loaded alignment.')


def alignment_similarity(rows: Sequence[tuple[str, str]]) -> dict:
    if not rows or len({len(sequence) for _, sequence in rows}) != 1:
        raise ValueError('a nonempty rectangular alignment is required')
    normalized = [(ident, sequence.upper()) for ident, sequence in rows]
    columns = len(normalized[0][1])
    comparable = identical = 0
    for column in zip(*(sequence for _, sequence in normalized)):
        if any(symbol in '-.' for symbol in column):
            continue
        comparable += 1
        identical += len(set(column)) == 1
    pair_count = len(rows) * (len(rows) - 1) // 2
    complete = pair_count <= PAIR_COUNT_LIMIT and pair_count * columns <= PAIR_CELL_LIMIT
    pairs = []
    if complete:
        for (left_id, left), (right_id, right) in combinations(normalized, 2):
            matches = denominator = 0
            for a, b in zip(left, right):
                if a in '-.' or b in '-.':
                    continue
                denominator += 1
                matches += a == b
            pairs.append({'left_row_id': left_id, 'right_row_id': right_id,
                          'identical': matches, 'compared': denominator,
                          'identity_percent': 100 * matches / denominator if denominator else None})
    defined = [pair['identity_percent'] for pair in pairs if pair['identity_percent'] is not None]
    return {'policy': POLICY, 'pairs': pairs, 'pairs_complete': complete,
            'pair_count': pair_count, 'pair_count_limit': PAIR_COUNT_LIMIT,
            'pair_cell_limit': PAIR_CELL_LIMIT,
            'mean_pairwise_identity_percent': sum(defined) / len(defined) if defined else None,
            'identical_columns': identical, 'compared_columns': comparable,
            'conserved_column_percent': 100 * identical / comparable if comparable else None}
