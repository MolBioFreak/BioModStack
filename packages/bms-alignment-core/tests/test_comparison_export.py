import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from bms_alignment import compare, parse
from bms_alignment.formats import project_a3m


def cli(*args):
    return subprocess.run([sys.executable, '-m', 'bms_alignment.cli', *map(str, args)],
                          capture_output=True, text=True, check=True)


def test_unknown_ambiguous_and_offsets():
    a = parse('>duplicate\nA-CG\n>duplicate\nA-CG\n>no-id\nA-CG\n', 'fasta', 'dna')
    b = parse('>renamed\nACG-\n>other\nACG-\n', 'fasta', 'dna')
    for row in (a.rows[0], b.rows[0]):
        row.source_member_id, row.occurrence_id, row.source_offset = 'm', 'one', 4
        row.transformation_provenance = 'declared original'
    a.rows[0].source_positions = [4, None, 5, 6]
    b.rows[0].source_positions = [4, 5, 6, None]
    a.rows[1].source_member_id, a.rows[1].occurrence_id = 'm', 'two'
    b.rows[1].source_member_id, b.rows[1].occurrence_id = 'm', 'two'
    b.rows[1].source_offset = 4
    result = compare(a, b)
    assert result['rows'][0]['positions'][1] == {'source': 5, 'left_column': 2, 'right_column': 1}
    assert result['rows'][1]['mapping_unknown']
    assert result['rows'][1]['right_only_positions'] == []
    assert result['rows'][1]['left_unmapped_columns'] == [0, 2, 3]
    assert result['left_unknown_identity_rows'] == [a.rows[2].row_id]
    a.rows[2].source_member_id, a.rows[2].occurrence_id = 'm', 'one'
    result = compare(a, b)
    assert len(result['rows']) == 1
    assert result['left_ambiguous_identities'][0]['row_ids'] == [a.rows[0].row_id, a.rows[2].row_id]
    assert result['right_excluded'] == 1


def test_a3m_projection_preserves_insertions():
    a = parse('>a\naaACg-.T\n>b\nAC--T\n', 'a3m', 'protein')
    rows = project_a3m(a)
    assert rows[0]['match_columns'] == 'AC-T'
    assert rows[0]['insertions_after_match_count'] == ['aa', '', 'g', '.', '']
    assert rows[1]['match_columns'] == 'AC--T'


def test_cli_compare_and_relocated_bundle(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    a = parse('>a\nA-C\n', 'fasta', 'dna')
    b = parse('>different\nAC-\n', 'fasta', 'dna')
    for roster in (a, b):
        roster.rows[0].source_member_id = 'member'
        roster.rows[0].occurrence_id = 'occ'
        roster.rows[0].source_offset = 12
        roster.rows[0].transformation_provenance = 'declared original'
    a.rows[0].source_positions = [12, None, 13]
    b.rows[0].source_positions = [12, 13, None]
    (source / 'a.json').write_text(a.model_dump_json())
    (source / 'b.json').write_text(b.model_dump_json())
    result = json.loads(cli('compare', source/'a.json', source/'b.json').stdout)
    assert result['rows'][0]['positions'][1]['right_column'] == 1
    bundle = tmp_path/'bundle'
    manifest = json.loads(cli('bundle', 'create', source, bundle, 'a.json', 'b.json', '--exclude', 'tree not available').stdout)
    assert manifest['dependencies']['excluded'] == ['tree not available']
    shutil.rmtree(source)
    relocated = tmp_path/'relocated'; bundle.rename(relocated)
    assert json.loads(cli('bundle', 'verify', relocated).stdout)['valid']
    assert json.loads(cli('compare', relocated/'a.json', relocated/'b.json').stdout) == result
    (relocated/'a.json').write_text('corrupted')
    p = subprocess.run([sys.executable, '-m', 'bms_alignment.cli', 'bundle', 'verify', str(relocated)], capture_output=True, text=True)
    assert p.returncode == 1 and not json.loads(p.stdout)['valid']


def test_explicit_reverse_noncontiguous_duplicates_and_missing():
    a = parse('>same\nA-CGT\n', 'fasta', 'dna')
    b = parse('>same\nAC-GT\n', 'fasta', 'dna')
    for row in (a.rows[0], b.rows[0]):
        row.source_member_id = 'source'; row.occurrence_id = 'occ'
        row.source_offset = 4
    assert compare(a, b)['rows'][0]['mapping_unknown']
    assert compare(a, b)['rows'][0]['positions'] == []
    a.rows[0].source_positions = [9, None, 5, 2, 0]
    b.rows[0].source_positions = [0, 2, None, 5, 9]
    a.rows[0].transformation_provenance = 'reverse and subset'
    b.rows[0].transformation_provenance = 'forward subset'
    row = compare(a, b)['rows'][0]
    assert row['positions'] == [dict(source=0, left_column=4, right_column=0),
                                dict(source=2, left_column=3, right_column=1),
                                dict(source=5, left_column=2, right_column=3),
                                dict(source=9, left_column=0, right_column=4)]
    assert not row['mapping_unknown']
    a.rows[0].source_positions = [9, None, 5, 5, None]
    row = compare(a, b)['rows'][0]
    assert row['positions'] == [dict(source=9, left_column=0, right_column=4)]
    assert row['left_unmapped_columns'] == [2, 3, 4]
    assert row['right_only_positions'] == [] and row['mapping_unknown']
    a.rows[0].source_positions = [9]
    assert compare(a, b)['rows'][0]['left_unmapped_columns'] == [0, 2, 3, 4]
    a.rows[0].source_positions = [9, None, 5, 2, 0]
    a.rows[0].transformation_provenance = None
    assert compare(a, b)['rows'][0]['positions'] == []
