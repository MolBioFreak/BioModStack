"""Synthetic native differential tests; set BMS_MSA_SIF to the pinned CPU image.

The tiny FASTA reader and coordinate oracle here do not use the production parser.
These assert fidelity, not alignment quality or biological ground truth.
"""
import os
import json
from pathlib import Path
import pytest
from bms_alignment import AlignmentRequest, parse, run_engine
from bms_alignment.formats import coordinates

SIF = os.environ.get('BMS_MSA_SIF')
pytestmark = pytest.mark.skipif(not SIF, reason='BMS_MSA_SIF not configured')


def read_fasta(path):
    records = {}
    for line in path.read_text().splitlines():
        if line.startswith('>'):
            key = line[1:].split()[0]
            assert key not in records
            records[key] = ''
        elif line.strip():
            records[key] += line.strip()
    return records


def test_native_fidelity(tmp_path):
    prefix = ['apptainer', 'exec', SIF]
    cases = [('mafft', 'dna', 'acgTACGTRYNacgtACGT', {}),
             ('mafft', 'rna', 'acgUACGURYNacguACGU', {}),
             ('famsa', 'protein', 'acDEFGHIKLMNPQRSTVWYX', {}),
             ('famsa', 'protein', 'acDEFGHIKLMNPQRSTVWYX', {'keep_duplicates': True}),
             ('muscle', 'dna', 'acgTACGTRYNacgtACGT', {}),
             ('muscle', 'rna', 'acgUACGURYNacguACGU', {}),
             ('twilight', 'dna', 'acgTACGTRYNacgtACGT', {'guide_source': 'mafft', 'max_ambig': 1.0}),
             ('twilight', 'rna', 'acgUACGURYNacguACGU', {'guide_source': 'mafft', 'max_ambig': 1.0})]
    for number, (engine, molecule, seq, settings) in enumerate(cases):
        text = f'>same label Ω\n{seq}\n>same label Ω\n{seq}\n>other\n{seq[:-2]}AA\n'
        request = AlignmentRequest(roster=parse(text, 'fasta', molecule), settings={'engine': engine, **settings})
        result = run_engine(request, tmp_path / str(number), prefix + [engine], prefix + ['mafft'])
        attempt = Path(result['attempt_dir'])
        assert result['returncode'] == 0, (engine, molecule, result, (attempt/'engine.log').read_text() if (attempt/'engine.log').exists() else '')
        raw = read_fasta(attempt / 'alignment.fasta')
        ids = [r.row_id for r in request.roster.rows] if engine == 'mafft' else [f'bmsrow{i:08d}' for i in range(1, 4)]
        assert set(raw) == set(ids)
        if engine != 'mafft':
            assert json.loads((attempt / 'row-map.json').read_text()) == dict(zip(ids, [r.row_id for r in request.roster.rows]))
        observation = result['observations'] if engine == 'mafft' else result['observations'][0]
        assert not observation['changed_residue_ids']
        assert set(observation['case_changed_ids']) == (set(ids) if engine in ('mafft', 'muscle') else set())
        assert len({len(x) for x in raw.values()}) == 1
        print(engine, molecule, settings, [(ident, raw[ident].replace('-', '').replace('.', '')) for ident in ids])
        # Independently reconstruct source positions from actual output gaps.
        for ident, row in zip(ids, request.roster.rows):
            ungapped = raw[ident].replace('-', '').replace('.', '')
            assert ungapped.upper() == row.sequence.upper()
            n = 0
            mapping = []
            for symbol in raw[ident]:
                if symbol in '-.': mapping.append(None)
                else:
                    mapping.append(n + 17)
                    n += 1
            mapped_row = row.model_copy(update={'sequence': raw[ident], 'source_positions': mapping, 'transformation_provenance': 'synthetic forward offset'})
            assert coordinates(mapped_row) == mapping


def test_twilight_default_ambiguity_is_observed_not_a_gate(tmp_path):
    prefix = ['apptainer', 'exec', SIF]
    seq = 'acgTACGTRYNacgtACGT'
    roster = parse(f'>a\n{seq}\n>b\n{seq}\n>c\n{seq[:-2]}AA\n', 'fasta', 'dna')
    request = AlignmentRequest(roster=roster, settings={'engine': 'twilight', 'guide_source': 'mafft'})
    result = run_engine(request, tmp_path, prefix + ['twilight'], prefix + ['mafft'])
    assert result['returncode'] == 0
    assert len(result['observations'][0]['missing_ids']) == 3
    assert (Path(result['attempt_dir']) / 'alignment.fasta').read_text() == ''


def test_alternative_alignment_coordinate_oracle():
    from bms_alignment.comparison import compare
    left = parse('>a\nA-CG\n>b\nAT-G\n', 'fasta', 'dna')
    right = parse('>renamed b\nA-TG\n>renamed a\nAC-G\n', 'fasta', 'dna')
    for roster, names in ((left, ['a', 'b']), (right, ['b', 'a'])):
        for row, name in zip(roster.rows, names):
            row.source_member_id = name
            row.occurrence_id = 'occurrence'
            row.transformation_provenance = 'synthetic offset'
            pos = 10
            mapping = []
            for char in row.sequence:
                mapping.append(None if char == '-' else pos)
                if char != '-': pos += 1
            row.source_positions = mapping
    result = compare(left, right)
    expected = {'a': [(10, 0, 0), (11, 2, 1), (12, 3, 3)],
                'b': [(10, 0, 0), (11, 1, 2), (12, 3, 3)]}
    assert len(result['rows']) == 2
    for row in result['rows']:
        assert [(p['source'], p['left_column'], p['right_column']) for p in row['positions']] == expected[row['source_member_id']]
    assert result['left_excluded'] == result['right_excluded'] == 0
