"""Full-length public pUC19 integrity benchmark, never a user project mutation.

Set BMS_MSA_SIF to the approved native CPU image. The controls have analytically
known changes; they do not establish homology accuracy on arbitrary sequences.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest
from bms_alignment import AlignmentRequest, run_engine
from bms_alignment.similarity import alignment_similarity

FIXTURE = Path(__file__).with_name('fixtures') / 'puc19.fa'
SIF = os.environ.get('BMS_MSA_SIF')


class KnownNativeQualificationFailure(AssertionError):
    """Only the reproduced pinned-native defect may count as an expected failure."""


def read_fasta(path):
    """Independent tiny reader; do not use the production alignment parser."""
    rows = {}
    for line in path.read_text().splitlines():
        if line.startswith('>'):
            name = line[1:].split()[0]
            assert name not in rows
            rows[name] = ''
        elif line.strip():
            rows[name] += line.strip().upper()
    return rows


def sequence():
    seq = read_fasta(FIXTURE)['pUC19']
    metadata = json.loads(FIXTURE.with_suffix('.provenance.json').read_text())
    assert len(seq) == metadata['length_bp'] == 2686
    assert hashlib.sha256(seq.encode()).hexdigest() == metadata['sequence_sha256'] == '7c1183b16368b1435452d99152d7c95aa04fc4a5412e563a5ef1d0fbf0cf873b'
    assert set(seq) == set('ACGT')
    return seq


def test_public_puc19_fixture_identity():
    sequence()


def compare_rows(left, right):
    paired = [(a, b) for a, b in zip(left, right) if a not in '-.' and b not in '-.']
    return len(paired), sum(a == b for a, b in paired)


@pytest.mark.skipif(not SIF, reason='BMS_MSA_SIF not configured')
@pytest.mark.parametrize('engine,mode', [
    ('mafft', None), ('twilight', None),
    pytest.param('muscle', 'align', marks=pytest.mark.xfail(strict=True, raises=KnownNativeQualificationFailure, reason='Pinned MUSCLE 5.3 misplaces the pUC19 deletion: 2684/2685 matches, expected 2685/2685; raw CLI reproduces')),
    pytest.param('muscle', 'super5', marks=pytest.mark.xfail(strict=True, raises=KnownNativeQualificationFailure, reason='Pinned MUSCLE 5.3 Super5 misplaces the pUC19 deletion: 2684/2685 matches, expected 2685/2685; not accuracy-qualified')),
])
def test_native_puc19_known_edits(tmp_path, engine, mode):
    seq = sequence()
    changed = dict(zip('ACGT', 'CGTA'))[seq[1111]]
    inputs = {
        'reference': seq,
        'identical': seq,
        'substitution': seq[:1111] + changed + seq[1112:],
        'insertion': seq[:1700] + 'A' + seq[1700:],
        'deletion': seq[:1900] + seq[1901:],
    }
    settings = {'engine': engine}
    if mode is not None:
        settings['mode'] = mode
    if engine == 'twilight':
        settings['guide_source'] = 'mafft'
    request = AlignmentRequest(roster={'molecule': 'dna', 'rows': [
        {'row_id': name, 'header': name, 'sequence': bases} for name, bases in inputs.items()
    ]}, settings=settings)
    prefix = ['apptainer', 'exec', '--cleanenv', SIF]
    result = run_engine(request, tmp_path, prefix + [engine], prefix + ['mafft'])
    attempt = Path(result['attempt_dir'])
    assert result['returncode'] == 0, (attempt / 'engine.log').read_text()
    assert result['output_integrity']['status'] == 'valid', result['output_integrity']
    raw = read_fasta(attempt / 'alignment.fasta')
    mapping = json.loads((attempt / 'row-map.json').read_text()) if engine != 'mafft' else {key: key for key in inputs}
    output = {mapping[key]: value for key, value in raw.items()}
    assert set(output) == set(inputs)
    assert len({len(row) for row in output.values()}) == 1
    for name, bases in output.items():
        assert bases.replace('-', '').replace('.', '') == inputs[name]
    expected = {'identical': (2686, 2686), 'substitution': (2686, 2685),
                'insertion': (2686, 2686), 'deletion': (2685, 2685)}
    for name, counts in expected.items():
        actual = compare_rows(output['reference'], output[name])
        if engine == 'muscle' and name == 'deletion' and actual == (2685, 2684):
            raise KnownNativeQualificationFailure(f'{mode}: deletion has {actual}, expected {counts}')
        assert actual == counts
    # The reported percentages and directional coverage must agree with the
    # independent oracle, including the insertion/deletion denominators.
    report = alignment_similarity(list(output.items()))
    for pair in report['pairs']:
        left, right = pair['left_row_id'], pair['right_row_id']
        count, matches = compare_rows(output[left], output[right])
        assert pair['compared'] == count and pair['identical'] == matches
        assert pair['identity_percent'] == pytest.approx(100 * matches / count)
        assert pair['left_aligned_percent'] == pytest.approx(100 * count / len(inputs[left]))
        assert pair['right_aligned_percent'] == pytest.approx(100 * count / len(inputs[right]))


@pytest.mark.skipif(not SIF, reason='BMS_MSA_SIF not configured')
def test_native_puc19_authorized_reverse_complement(tmp_path):
    seq = sequence()
    reverse = seq.translate(str.maketrans('ACGT', 'TGCA'))[::-1]
    request = AlignmentRequest(roster={'molecule': 'dna', 'rows': [
        {'row_id': name, 'header': name, 'sequence': bases}
        for name, bases in [('reference', seq), ('reverse', reverse), ('identical', seq)]
    ]}, settings={'engine': 'mafft', 'direction': 'adjustdirectionaccurately'})
    result = run_engine(request, tmp_path, ['apptainer', 'exec', '--cleanenv', SIF, 'mafft'])
    assert result['returncode'] == 0
    assert result['output_integrity']['status'] == 'valid'
    assert result['observations']['reverse_complemented_ids'] == ['reverse']
    output = read_fasta(Path(result['attempt_dir']) / 'alignment.fasta')
    assert set(output) == {'reference', '_R_reverse', 'identical'}
    assert all(value.replace('-', '').replace('.', '') == seq for value in output.values())
    assert compare_rows(output['reference'], output['_R_reverse']) == (2686, 2686)
