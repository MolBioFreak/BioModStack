"""Independent numerical checks on generic, synthetic displayed alignments.

These verify exact-symbol summaries, not whether a native aligner's inferred
homology is true. No user's sequence data is needed or accessed.
"""
from itertools import product
from math import ceil
from random import Random

import pytest

import bms_alignment.similarity as similarity
from bms_alignment.similarity import MAP_BIN_LIMIT, alignment_similarity


def pair(left, right):
    return alignment_similarity([('left', left), ('right', right)])['pairs'][0]


def independent_counts(left, right):
    """Use position sets, rather than the authority's per-column state machine."""
    left_positions = {i for i, symbol in enumerate(left) if symbol not in '-.'}
    right_positions = {i for i, symbol in enumerate(right) if symbol not in '-.'}
    paired = left_positions & right_positions
    matching = {i for i in paired if left[i].upper() == right[i].upper()}
    return left_positions, right_positions, paired, matching


def assert_map(mapping, positions, paired, matching):
    length = len(positions)
    bins = min(length, MAP_BIN_LIMIT)
    assert len(mapping) == bins
    # Derive each bin's exact source coordinate interval independently from
    # the floor(position * bins / length) assignment in the authority.
    ordered_positions = sorted(positions)
    for index, counts in enumerate(mapping):
        start = ceil(index * length / bins)
        end = ceil((index + 1) * length / bins)
        columns = set(ordered_positions[start:end])
        assert counts == [len(columns & matching),
                          len((columns & paired) - matching),
                          len(columns - paired)]
    assert [sum(values[i] for values in mapping) for i in range(3)] == [
        len(matching), len(paired - matching), len(positions - paired)]


def test_hand_calculated_internal_terminal_and_dot_gaps():
    got = pair('..AC-GT-', 'TTACCG-.')
    assert got['identical'] == got['compared'] == 3
    assert got['identity_percent'] == 100
    assert (got['left_length'], got['right_length']) == (4, 6)
    assert (got['left_aligned_percent'], got['right_aligned_percent']) == (75, 50)
    assert got['left_map'] == [[1, 0, 0], [1, 0, 0], [1, 0, 0], [0, 0, 1]]
    assert got['right_map'] == [[0, 0, 1], [0, 0, 1], [1, 0, 0],
                                [1, 0, 0], [0, 0, 1], [1, 0, 0]]


def test_perfect_identity_over_overlap_does_not_imply_equal_sequences():
    got = pair('----ACGT----', 'CCCCACGTTTTT')
    assert got['identity_percent'] == 100
    assert got['left_aligned_percent'] == 100
    assert got['right_aligned_percent'] == pytest.approx(100 / 3)
    assert (got['identical'], got['compared']) == (4, 4)


def test_literal_iupac_rna_and_protein_symbols_do_not_imply_compatibility():
    # Covers all nucleotide ambiguity symbols plus protein ambiguity/rare
    # residue letters. N=N and X=X are equal symbols, not known equal residues.
    alphabet = 'ACGTURYSWKMBDHVNEFIPQLXZOJ*'
    for left, right in product(alphabet, repeat=2):
        got = pair(left.lower(), right)
        assert got['compared'] == 1
        assert got['identical'] == int(left == right)
        assert got['identity_percent'] == (100 if left == right else 0)
    assert pair('T', 'U')['identity_percent'] == 0
    assert pair('N', 'A')['identity_percent'] == 0
    assert pair('B', 'D')['identity_percent'] == 0


@pytest.mark.parametrize('left,right', [('A-.', '-.C'), ('---', '...'), ('---', 'ACG')])
def test_no_comparable_residues_is_undefined_identity(left, right):
    got = pair(left, right)
    assert got['identity_percent'] is None
    assert got['identical'] == got['compared'] == 0
    for side, sequence in [('left', left), ('right', right)]:
        has_residues = any(c not in '-.' for c in sequence)
        assert got[f'{side}_aligned_percent'] == (0 if has_residues else None)


@pytest.mark.parametrize('width', [1, 2, 159, 160, 161, 319, 320, 321, 997])
def test_randomized_maps_and_directional_coverage_follow_independent_oracle(width):
    rng = Random(104729 + width)
    for _ in range(15):
        left = ''.join(rng.choice('AaCGTrYUN-.') for _ in range(width))
        right = ''.join(rng.choice('AaCGTrYUN-.') for _ in range(width))
        lpos, rpos, paired, matching = independent_counts(left, right)
        got, reverse = pair(left, right), pair(right, left)
        assert (got['identical'], got['compared']) == (len(matching), len(paired))
        expected_identity = 100 * len(matching) / len(paired) if paired else None
        assert got['identity_percent'] == expected_identity
        for side, positions in [('left', lpos), ('right', rpos)]:
            assert got[f'{side}_length'] == len(positions)
            expected_coverage = 100 * len(paired) / len(positions) if positions else None
            assert got[f'{side}_aligned_percent'] == expected_coverage
            assert_map(got[f'{side}_map'], positions, paired, matching)
        assert reverse['identity_percent'] == got['identity_percent']
        assert reverse['left_map'] == got['right_map']
        assert reverse['right_map'] == got['left_map']
        assert reverse['left_aligned_percent'] == got['right_aligned_percent']
        assert reverse['right_aligned_percent'] == got['left_aligned_percent']


def test_all_gap_columns_do_not_change_any_pair_statistic():
    before = pair('AC-GTN', 'A-TGNN')
    after = pair('--AC.-G.TN-', '..A-.TG.NN.')
    assert before == after


def test_pair_mean_is_unweighted_and_omits_undefined_pairs():
    got = alignment_similarity([('a', 'AA--'), ('b', 'AC--'), ('c', '--GT')])
    assert [item['identity_percent'] for item in got['pairs']] == [50, None, None]
    assert got['mean_pairwise_identity_percent'] == 50
    assert got['conserved_column_percent'] is None
    assert got['compared_columns'] == 0
    got = alignment_similarity([('a', 'ACGT'), ('b', 'ACG-'), ('c', 'AT--')])
    # Three pair identities: 3/3, 1/2, 1/2; not pooled 5/7.
    assert got['mean_pairwise_identity_percent'] == pytest.approx(200 / 3)
    assert (got['identical_columns'], got['compared_columns']) == (1, 2)


def test_optional_pair_omission_is_explicit_and_does_not_sample(monkeypatch):
    monkeypatch.setattr(similarity, 'PAIR_COUNT_LIMIT', 3)
    monkeypatch.setattr(similarity, 'PAIR_CELL_LIMIT', 6)
    rows = [('a', 'AN'), ('b', 'aN'), ('c', 'An')]
    complete = alignment_similarity(rows)
    assert complete['pairs_complete'] and len(complete['pairs']) == 3
    assert complete['conserved_column_percent'] == 100
    for oversized in [rows + [('d', 'AN')], [(key, value + 'N') for key, value in rows]]:
        omitted = alignment_similarity(oversized)
        assert not omitted['pairs_complete']
        assert omitted['pairs'] == []
        assert omitted['mean_pairwise_identity_percent'] is None
        assert omitted['conserved_column_percent'] == 100


@pytest.mark.parametrize('rows', [[], [('a', '')], [('a', 'AC'), ('b', 'A')],
                                 [('same', 'AA'), ('same', 'A-')], [('', 'AC')],
                                 [('a', 'ß'), ('b', 'A')], [('a', 'A C')],
                                 [('a', 'A\x00C')], [('a', None)]])
def test_invalid_rows_fail_before_producing_scientific_numbers(rows):
    with pytest.raises(ValueError):
        alignment_similarity(rows)
