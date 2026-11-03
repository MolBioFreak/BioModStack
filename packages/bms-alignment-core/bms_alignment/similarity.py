"""Deterministic quick identity statistics for a rectangular displayed alignment.

Exact, case-insensitive symbol identity; '-' and '.' are gaps. No substitution
matrix or inference of biological function. Limits bound the optional pair table,
not the all-row column calculation, and are reported explicitly to API callers.
"""
from collections.abc import Sequence
from itertools import combinations

PAIR_CELL_LIMIT = 5_000_000
PAIR_COUNT_LIMIT = 200
MAP_BIN_LIMIT = 160
POLICY = ('Exact symbol identity, ignoring case. Columns with a gap (- or .) in '
          'either row are excluded for that pair. Ambiguous symbols match only '
          'the identical symbol. Aligned % is the number of nongap paired positions '
          'divided by the ungapped length of each sequence. Maps use the existing '
          'multiple alignment; they do not perform a new pairwise alignment.')


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
    lengths = {ident: sum(c not in '-.' for c in sequence) for ident, sequence in normalized}
    pairs = []
    if complete:
        for (left_id, left), (right_id, right) in combinations(normalized, 2):
            left_length, right_length = lengths[left_id], lengths[right_id]
            # Bounded density maps on each sequence's ungapped coordinates.
            # Each bin stores [identical, mismatched, other-sequence gap].
            left_bins = [[0, 0, 0] for _ in range(min(MAP_BIN_LIMIT, left_length))]
            right_bins = [[0, 0, 0] for _ in range(min(MAP_BIN_LIMIT, right_length))]
            matches = denominator = left_pos = right_pos = 0
            for a, b in zip(left, right):
                has_left, has_right = a not in '-.', b not in '-.'
                if has_left and has_right:
                    denominator += 1
                    matches += a == b
                if has_left:
                    left_bins[left_pos * len(left_bins) // left_length][
                        2 if not has_right else 0 if a == b else 1] += 1
                    left_pos += 1
                if has_right:
                    right_bins[right_pos * len(right_bins) // right_length][
                        2 if not has_left else 0 if a == b else 1] += 1
                    right_pos += 1
            pairs.append({'left_row_id': left_id, 'right_row_id': right_id,
                          'identical': matches, 'compared': denominator,
                          'identity_percent': 100 * matches / denominator if denominator else None,
                          'left_length': left_length, 'right_length': right_length,
                          'left_aligned_percent': 100 * denominator / left_length if left_length else None,
                          'right_aligned_percent': 100 * denominator / right_length if right_length else None,
                          'left_map': left_bins, 'right_map': right_bins})
    defined = [pair['identity_percent'] for pair in pairs if pair['identity_percent'] is not None]
    return {'policy': POLICY, 'pairs': pairs, 'pairs_complete': complete,
            'pair_count': pair_count, 'pair_count_limit': PAIR_COUNT_LIMIT,
            'pair_cell_limit': PAIR_CELL_LIMIT, 'map_bin_limit': MAP_BIN_LIMIT,
            'mean_pairwise_identity_percent': sum(defined) / len(defined) if defined else None,
            'identical_columns': identical, 'compared_columns': comparable,
            'conserved_column_percent': 100 * identical / comparable if comparable else None}
