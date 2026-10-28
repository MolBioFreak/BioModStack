import shutil
import pytest
from bms_alignment.models import SequenceSet
from bms_alignment.formats import parse, export, export_bytes, project_a3m
from bms_alignment.bundle import make_bundle, verify_bundle


def test_stockholm_annotations_and_relocated_bytes(tmp_path):
    raw = (b'# STOCKHOLM 1.0\r\n#=GF ID sample name\r\n#=GS x DE description here\r\n'
           b'x AC-\r\n#=GR x SS HHH\r\n#=GC RF xx.\r\n\r\n'
           b'x GT\r\n#=GR x SS EE\r\n#=GC RF xx\r\n#=XX unknown\r\n//\r\n')
    roster = parse(raw, 'stockholm', 'protein')
    assert roster.rows[0].sequence == 'AC-GT'
    assert [x['kind'] for x in roster.stockholm_annotations] == ['GF', 'GS', 'GR', 'GC', 'GR', 'GC', 'unknown']
    assert roster.stockholm_annotations[1]['value'] == 'description here'
    assert roster.format_observations
    src = tmp_path/'src'; src.mkdir()
    (src/'roster.json').write_text(roster.model_dump_json(), encoding='utf8')
    (src/'original').write_bytes(raw)
    bundle = tmp_path/'bundle'; make_bundle(src, bundle, ['roster.json', 'original'])
    shutil.rmtree(src); moved = tmp_path/'moved'; bundle.rename(moved)
    assert verify_bundle(moved)['valid']
    restored = SequenceSet.model_validate_json((moved/'roster.json').read_bytes())
    assert export_bytes(restored, 'stockholm') == (raw, [])
    assert export_bytes(restored, 'stockholm', original=True)[0] == (moved/'original').read_bytes()
    assert export(restored, 'fasta')[1]
    restored.rows[0].sequence = 'AAAAA'
    with pytest.raises(ValueError, match='after edits'): export(restored, 'stockholm')
    assert export_bytes(restored, 'stockholm', original=True)[0] == raw


def test_stockholm_distinct_records_and_indented_rows():
    raw = (b'# STOCKHOLM 1.0\r\n#=GF ID first\r\n  x AC\r\n  x -G\r\n//\r\n'
           b'# STOCKHOLM 1.0\r\n#=GF ID second\r\n#=GR x SS HH\r\n  x GT\r\n//\r\n')
    roster = parse(raw, 'stockholm', 'dna')
    assert [(r.header, r.sequence) for r in roster.rows] == [('x', 'AC-G')]
    assert roster.stockholm_annotations[-1]['record'] == 2
    assert any('2 records; only the first is displayed' in x for x in roster.format_observations)
    assert export_bytes(roster, 'stockholm') == (raw, [])
    assert any('only the first' in x for x in export(roster, 'fasta')[1])
    assert parse('# STOCKHOLM 1.0\nx AC\n//\n', 'stockholm', 'dna').rows[0].sequence == 'AC'


def test_malformed_annotations_and_unknown_references():
    roster = parse('# STOCKHOLM 1.0\n#=GR x\n#=GS absent DE hi\nx AC\n', 'stockholm', 'dna')
    assert roster.stockholm_annotations[0]['kind'] == 'unknown'
    assert any('unknown sequence' in x for x in roster.format_observations)
    assert any('terminator' in x for x in roster.format_observations)
    with pytest.raises(UnicodeDecodeError): parse(b'\xff', 'stockholm', 'dna')


def test_a3m_mapping_and_conversion():
    roster = parse(b'>a\r\naaA.cC-\r\n>b\r\nA-C\r\n', 'a3m', 'protein')
    item = project_a3m(roster)[0]
    assert item['match_columns'] == 'AC-'
    assert item['insertions_after_match_count'] == ['aa', '.c', '', '']
    assert [m['raw_index'] for m in item['raw_mapping']] == list(range(7))
    assert item['raw_mapping'][3]['insertion_slot'] == 1
    assert export_bytes(roster, 'a3m', True)[0].startswith(b'>a\r\n')
    with pytest.raises(ValueError, match='insertion alignment'): export(roster, 'fasta')
    clean = parse('>a\nAC-\n>b\nA-C\n', 'a3m', 'protein')
    assert export(clean, 'fasta')[0] == '>a\nAC-\n>b\nA-C\n'
    with pytest.raises(ValueError, match='insertion states'): export(parse('>a\nAC\n', 'fasta', 'protein'), 'a3m')
    with pytest.raises(ValueError, match='unequal'): export(parse('>a\nAC\n>b\nA\n', 'a3m', 'protein'), 'fasta')
