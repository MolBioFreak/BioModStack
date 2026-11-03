"""Integration benchmark against the public, nonviral pUC19 vector (M77789.2).

Uses the shared checked-in public fixture. BMS_ALIGNMENT_PUBLIC_VECTOR_FASTA
may point to an installed copy instead; the pinned digest must still match.
Artificial single-base edits are software test fixtures, never persisted designs.
Optionally set BMS_ALIGNMENT_BENCHMARK_REPORT to save a compact JSON receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform

import Bio
import pytest

from services import sequence_alignment
from services.sequence_alignment import AlignmentSettings, SequenceAlignmentError, align_sequences


PUC19_SEQUENCE_SHA256 = '7c1183b16368b1435452d99152d7c95aa04fc4a5412e563a5ef1d0fbf0cf873b'


@pytest.fixture(scope='module')
def benchmark():
    default_source = (Path(__file__).resolve().parents[3]
                      / 'packages/bms-alignment-core/tests/fixtures/puc19.fa')
    source = Path(os.environ.get('BMS_ALIGNMENT_PUBLIC_VECTOR_FASTA', str(default_source)))
    lines = source.read_text().splitlines()
    assert sum(line.startswith('>') for line in lines) == 1
    reference = ''.join(line.strip() for line in lines if not line.startswith('>')).upper()
    assert len(reference) == 2686
    assert hashlib.sha256(reference.encode()).hexdigest() == PUC19_SEQUENCE_SHA256
    receipts = []
    yield reference, receipts
    report = os.environ.get('BMS_ALIGNMENT_BENCHMARK_REPORT')
    if report:
        destination = Path(report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            'benchmark': 'public-puc19-pairwise-accuracy-v1',
            'reference_name': 'pUC19', 'reference_length': len(reference),
            'reference_sequence_sha256': PUC19_SEQUENCE_SHA256,
            'reference_file_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'service_source_sha256': hashlib.sha256(Path(sequence_alignment.__file__).read_bytes()).hexdigest(),
            'test_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'python_version': platform.python_version(), 'biopython_version': Bio.__version__,
            'expected_case_count': 9, 'case_count': len(receipts),
            'all_benchmark_checks_passed': len(receipts) == 9,
            'cases': sorted(receipts, key=lambda item: item['case']),
            'scope': 'Pairwise service arithmetic, coordinate projection and known synthetic perturbations; '
                     'not a claim of universal biological alignment truth.',
        }, indent=2) + '\n')


def record(receipts, name, result):
    receipts.append({'case': name, 'status': 'passed', **{
        key: result[key] for key in (
            'score', 'mode', 'strand', 'matches', 'mismatches', 'gap_columns',
            'aligned_columns', 'identity_pct', 'reference_coverage', 'query_coverage',
            'reference_start', 'reference_end', 'reference_wraps_origin',
            'query_start', 'query_end', 'query_input_start', 'query_input_end',
            'query_coordinate_system', 'query_soft_clip_left', 'query_soft_clip_right',
            'reference_flank_left', 'reference_flank_right', 'variants',
        )}})


def isolated_position(reference):
    # Avoid homopolymer-equivalent indel positions so the expected coordinate is
    # unique; this is merely fixture determinism, not a design recommendation.
    for position in range(len(reference) // 2, len(reference) - 16):
        if (reference[position] not in (reference[position - 1], reference[position + 1])
                and reference.count(reference[position - 15:position + 16]) == 1):
            return position
    raise AssertionError('No unambiguous test position in the pinned fixture')


@pytest.mark.parametrize('case', ['identical', 'substitution', 'insertion', 'deletion'])
def test_known_full_length_edits(benchmark, case):
    reference, receipts = benchmark
    position = isolated_position(reference)
    query = reference
    expected_matches, expected_mismatches, expected_gaps = len(reference), 0, 0
    if case == 'substitution':
        base = next(base for base in 'ACGT' if base != reference[position])
        query = reference[:position] + base + reference[position + 1:]
        expected_matches -= 1
        expected_mismatches = 1
    elif case == 'insertion':
        base = next(base for base in 'ACGT' if base not in reference[position - 1:position + 1])
        query = reference[:position] + base + reference[position:]
        expected_gaps = 1
    elif case == 'deletion':
        query = reference[:position] + reference[position + 1:]
        expected_matches -= 1
        expected_gaps = 1
    result = align_sequences(reference, query, AlignmentSettings(mode='global', strand='forward'))
    assert result['reference_aligned'].replace('-', '') == reference
    assert result['query_aligned'].replace('-', '') == query
    assert (result['matches'], result['mismatches'], result['gap_columns']) == (
        expected_matches, expected_mismatches, expected_gaps)
    assert result['score'] == 2 * expected_matches - expected_mismatches - 6 * expected_gaps
    assert (result['reference_start'], result['reference_end']) == (0, len(reference))
    assert (result['query_input_start'], result['query_input_end']) == (0, len(query))
    assert result['reference_coverage'] == result['query_coverage'] == 100
    assert result['identity_pct'] == round(100 * expected_matches / (expected_matches + expected_mismatches), 2)
    if case == 'identical':
        assert result['variants'] == []
    else:
        assert len(result['variants']) == 1
        event = result['variants'][0]
        assert event['type'] == case
        assert event['start'] == position
        assert event['end'] == position + (case != 'insertion')
        assert event['query_start'] == position
        assert event['query_end'] == position + (case != 'deletion')
        assert event['reference'] == reference[event['start']:event['end']]
        assert event['query'] == query[event['query_start']:event['query_end']]
    record(receipts, case, result)


@pytest.mark.parametrize('case', ['reverse', 'rotation', 'reverse_rotation'])
def test_orientation_and_one_copy_circular_rotation(benchmark, case):
    reference, receipts = benchmark
    offset = 701 if 'rotation' in case else 0
    query = reference[offset:] + reference[:offset]
    if 'reverse' in case:
        query = query.translate(str.maketrans('ACGT', 'TGCA'))[::-1]
    result = align_sequences(reference, query, AlignmentSettings(
        mode='placement' if offset else 'global', strand='auto', reference_is_circular=bool(offset)))
    assert result['strand'] == ('reverse' if 'reverse' in case else 'forward')
    assert result['matches'] == len(reference)
    assert result['mismatches'] == result['gap_columns'] == 0
    assert result['reference_aligned'] == result['query_aligned'] == reference[offset:] + reference[:offset]
    assert result['reference_start'] == offset
    assert result['reference_end'] == (offset or len(reference))
    assert result['reference_wraps_origin'] is bool(offset)
    assert result['reference_aligned_bases'] == len(reference)
    assert result['query_coverage'] == result['reference_coverage'] == result['identity_pct'] == 100
    assert result['variants'] == []
    assert (result['query_input_start'], result['query_input_end']) == (0, len(query))
    assert (result['reference_flank_left'], result['reference_flank_right']) == (0, 0)
    record(receipts, case, result)


def test_local_reverse_fragment_coordinates(benchmark):
    reference, receipts = benchmark
    start, end = 401, 1301
    query = reference[start:end].translate(str.maketrans('ACGT', 'TGCA'))[::-1]
    result = align_sequences(reference, query, AlignmentSettings(mode='local', strand='auto'))
    assert result['strand'] == 'reverse'
    assert result['query_aligned'] == result['reference_aligned'] == reference[start:end]
    assert (result['reference_start'], result['reference_end']) == (start, end)
    assert (result['reference_flank_left'], result['reference_flank_right']) == (start, len(reference) - end)
    assert (result['query_input_start'], result['query_input_end']) == (0, len(query))
    assert result['matches'] == end - start
    assert result['identity_pct'] == result['query_coverage'] == 100
    assert result['reference_coverage'] == round(100 * (end - start) / len(reference), 2)
    assert result['variants'] == []
    record(receipts, 'local_reverse_fragment', result)


def test_multiple_reference_turns_are_rejected(benchmark):
    reference, receipts = benchmark
    with pytest.raises(SequenceAlignmentError, match='more than one reference copy'):
        align_sequences(reference, reference * 2,
                        AlignmentSettings(mode='placement', strand='forward', reference_is_circular=True))
    receipts.append({'case': 'multiple_turns', 'status': 'passed',
                     'expected_error': 'Circular alignment spans more than one reference copy'})
