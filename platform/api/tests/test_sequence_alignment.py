from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


API_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = API_ROOT / "services" / "sequence_alignment.py"
SPEC = importlib.util.spec_from_file_location("sequence_alignment_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
sequence_alignment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sequence_alignment
SPEC.loader.exec_module(sequence_alignment)


class _LenOverflowAlignments:
    def __len__(self) -> int:
        raise OverflowError("number of optimal alignments is larger than 9223372036854775807")

    def __iter__(self):
        alignment = sequence_alignment.Align.Alignment(
            ["AAAAAA", "AAA"],
            np.array([[0, 3], [0, 3]], dtype=int),
        )
        alignment.score = 42.0
        yield alignment


class _LenOverflowAligner:
    def align(self, reference: str, query: str) -> _LenOverflowAlignments:
        return _LenOverflowAlignments()


def test_select_candidate_does_not_count_all_optimal_alignments(monkeypatch) -> None:
    monkeypatch.setattr(sequence_alignment, "_build_aligner", lambda settings: _LenOverflowAligner())

    candidate = sequence_alignment._select_candidate(
        "AAAAAA",
        "AAA",
        sequence_alignment.AlignmentSettings(mode="placement", strand="forward"),
    )

    assert candidate.alignment.score == 42.0
    assert candidate.reference_sequence == "AAAAAA"
    assert candidate.query_sequence == "AAA"
    assert candidate.strand == "forward"


# Independent affine-gap DP oracle, deliberately separate from Bio.Align and
# the service's returned metrics. Fixtures are short synthetic strings.
def _global_score_oracle(a, b):
    neg = float('-inf')
    m = [[neg] * (len(b) + 1) for _ in range(len(a) + 1)]
    ins = [[neg] * (len(b) + 1) for _ in range(len(a) + 1)]
    delete = [[neg] * (len(b) + 1) for _ in range(len(a) + 1)]
    m[0][0] = 0
    for i in range(1, len(a) + 1): delete[i][0] = -6 - (i - 1)
    for j in range(1, len(b) + 1): ins[0][j] = -6 - (j - 1)
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            m[i][j] = max(m[i-1][j-1], ins[i-1][j-1], delete[i-1][j-1]) + (2 if a[i-1] == b[j-1] else -1)
            ins[i][j] = max(m[i][j-1] - 6, delete[i][j-1] - 6, ins[i][j-1] - 1)
            delete[i][j] = max(m[i-1][j] - 6, ins[i-1][j] - 6, delete[i-1][j] - 1)
    return max(m[-1][-1], ins[-1][-1], delete[-1][-1])


def test_global_alignment_matches_independent_affine_gap_oracle():
    import random
    rng = random.Random(73291)
    for _ in range(200):
        a = ''.join(rng.choices('ACGT', k=rng.randint(1, 15)))
        b = ''.join(rng.choices('ACGT', k=rng.randint(1, 15)))
        result = sequence_alignment.align_sequences(a, b, sequence_alignment.AlignmentSettings(mode='global', strand='forward'))
        assert result['score'] == _global_score_oracle(a, b)
        assert result['reference_aligned'].replace('-', '') == a
        assert result['query_aligned'].replace('-', '') == b
        columns = list(zip(result['reference_aligned'], result['query_aligned'], strict=True))
        paired = [(x, y) for x, y in columns if '-' not in (x, y)]
        assert result['matches'] == sum(x == y for x, y in paired)
        assert result['mismatches'] == sum(x != y for x, y in paired)
        assert result['gap_columns'] + result['aligned_columns'] == len(columns)
        assert result['query_input_start'] == 0 and result['query_input_end'] == len(b)


def test_circular_exact_rotations_do_not_reuse_reference_positions():
    a = 'ACGTCCTAGTGCA'
    for offset in range(len(a)):
        b = a[offset:] + a[:offset]
        result = sequence_alignment.align_sequences(a, b, sequence_alignment.AlignmentSettings(mode='placement', strand='forward', reference_is_circular=True))
        assert result['matches'] == len(a)
        assert result['gap_columns'] == 0
        assert result['reference_start'] == offset
        assert result['reference_aligned_bases'] == len(a)
        assert result['query_input_end'] == len(a)


def test_multi_turn_circular_result_is_rejected_instead_of_false_whole_copy_identity():
    import pytest
    for mode in ('local', 'placement'):
        with pytest.raises(sequence_alignment.SequenceAlignmentError, match='more than one reference copy'):
            sequence_alignment.align_sequences('ACGTCGTA', 'ACGTCGTAACGTCGTA', sequence_alignment.AlignmentSettings(mode=mode, strand='forward', reference_is_circular=True))


def test_global_mode_runs_linear_when_circular_is_ticked():
    result = sequence_alignment.align_sequences('ACGT', 'ACGT', sequence_alignment.AlignmentSettings(mode='global', reference_is_circular=True))
    assert result['identity_pct'] == 100.0


def test_reverse_query_has_explicit_oriented_and_original_coordinates():
    result = sequence_alignment.align_sequences('GGCATTCG', 'TTTCGAATG', sequence_alignment.AlignmentSettings(mode='local', strand='reverse'))
    assert result['query_sequence'] == 'TTTCGAATG'
    assert result['query_aligned'] == 'CATTCG'
    assert result['query_coordinate_system'] == 'reverse_complement'
    assert (result['query_start'], result['query_end']) == (0, 6)
    assert (result['query_input_start'], result['query_input_end']) == (3, 9)
    # Original source slice is the reverse complement of the displayed span.
    original = result['query_sequence'][result['query_input_start']:result['query_input_end']]
    assert original.translate(str.maketrans('ACGT', 'TGCA'))[::-1] == result['query_aligned']


def test_no_paired_positions_cannot_report_defined_identity():
    import pytest
    with pytest.raises(sequence_alignment.SequenceAlignmentError, match='identity is undefined'):
        sequence_alignment.align_sequences('A', 'T', sequence_alignment.AlignmentSettings(mode='global', strand='forward', mismatch_score=-100))


def test_non_ascii_cannot_expand_into_nucleotide_symbols():
    import pytest
    for text in ('AßC', 'AſC', 'A\N{KELVIN SIGN}C'):
        with pytest.raises(sequence_alignment.SequenceAlignmentError, match='ASCII'):
            sequence_alignment.clean_alignment_sequence(text)
    assert sequence_alignment.clean_alignment_sequence(' a c\nt\tg ') == 'ACTG'


def test_rna_reference_resolves_reverse_complement_of_query_without_uracil():
    for strand in ('reverse', 'auto'):
        result = sequence_alignment.align_sequences(
            'UUUU', 'AAAA', sequence_alignment.AlignmentSettings(mode='local', strand=strand))
        assert result['query_sequence'] == 'AAAA'
        assert result['query_aligned'] == result['reference_aligned'] == 'UUUU'
        assert result['strand'] == 'reverse'
        assert result['matches'] == 4 and result['identity_pct'] == 100
        assert (result['query_input_start'], result['query_input_end']) == (0, 4)


def test_mixed_thymine_uracil_is_not_silently_normalized():
    # Mixed alphabets do not assert an RNA molecule. The established DNA
    # complement is retained and the sequence identity policy stays literal.
    assert sequence_alignment._reverse_complement('AAU', reference_sequence='T') == 'ATT'
    assert sequence_alignment._reverse_complement('AAU', reference_sequence='U') == 'AUU'
    result = sequence_alignment.align_sequences(
        'T', 'U', sequence_alignment.AlignmentSettings(mode='global', strand='forward'))
    assert result['reference_aligned'] == 'T' and result['query_aligned'] == 'U'
    assert result['matches'] == 0 and result['mismatches'] == 1


def test_local_alignment_reports_omitted_positions_as_clips_and_flanks():
    result = sequence_alignment.align_sequences(
        'GGACGTACGCCC', 'TTACGTACGAAA',
        sequence_alignment.AlignmentSettings(mode='local', strand='forward'))
    assert result['query_aligned'] == result['reference_aligned'] == 'ACGTACG'
    assert (result['query_start'], result['query_end']) == (2, 9)
    assert (result['query_soft_clip_left'], result['query_soft_clip_right']) == (2, 3)
    assert (result['reference_flank_left'], result['reference_flank_right']) == (2, 3)
    assert result['query_soft_clip_left'] + result['query_aligned_bases'] + result['query_soft_clip_right'] == 12
    assert result['reference_flank_left'] + result['reference_aligned_bases'] + result['reference_flank_right'] == 12


def test_reverse_local_clipping_is_in_the_displayed_orientation():
    result = sequence_alignment.align_sequences(
        'GGCATTCG', 'TTTCGAATG', sequence_alignment.AlignmentSettings(mode='local', strand='reverse'))
    assert (result['query_soft_clip_left'], result['query_soft_clip_right']) == (0, 3)
    assert (result['reference_flank_left'], result['reference_flank_right']) == (2, 0)
    assert (result['query_input_start'], result['query_input_end']) == (3, 9)


def test_circular_search_padding_does_not_become_terminal_flanks():
    reference = 'ACGTCCTAGTGCA'
    for mode in ('local', 'placement'):
        for query in ('CTAGTGCAACGTC', 'TGCAACG'):
            result = sequence_alignment.align_sequences(
                reference, query,
                sequence_alignment.AlignmentSettings(mode=mode, strand='forward', reference_is_circular=True))
            assert (result['reference_flank_left'], result['reference_flank_right']) == (0, 0)
            assert result['reference_wraps_origin'] is True
            assert result['reference_aligned_bases'] == len(query)
            assert result['reference_coverage'] == round(100 * len(query) / len(reference), 2)


def test_linear_placement_retains_real_terminal_flanks():
    result = sequence_alignment.align_sequences(
        'GGACGTACGCCC', 'ACGTACG',
        sequence_alignment.AlignmentSettings(mode='placement', strand='forward'))
    assert (result['reference_flank_left'], result['reference_flank_right']) == (2, 3)
    assert (result['query_soft_clip_left'], result['query_soft_clip_right']) == (0, 0)
