import pytest

from services.family_msa.read_model import alignment_document


ROSTER = [{'row_id': 'a', 'header': 'same'}, {'row_id': 'b', 'header': 'same'}]


def test_native_order_and_explicit_row_mapping():
    result = alignment_document(result_id='result', fasta='>native2\nA-C\n>native1\nACC\n',
        roster=ROSTER, row_map={'native1': 'a', 'native2': 'b'})
    assert [(r.row_id, r.label, r.aligned) for r in result.rows] == [
        ('b', 'same', 'A-C'), ('a', 'same', 'ACC')]


@pytest.mark.parametrize('fasta', ['>a\nAC\n>a\nAC\n', '>unknown\nAC\n',
                                     '>a\nAC\n>b\nA\n', 'AC\n', ''])
def test_invalid_or_ambiguous_alignment(fasta):
    with pytest.raises(ValueError):
        alignment_document(result_id='result', fasta=fasta, roster=ROSTER)


def test_missing_rows_not_invented():
    result = alignment_document(result_id='result', fasta='>a\nAC\n', roster=ROSTER)
    assert [row.row_id for row in result.rows] == ['a']


def test_source_positions_only_when_residues_match():
    source = {'row_id': 'a', 'header': 'same', 'sequence': 'AcG', 'source_member_id': 'm', 'occurrence_id': 'o', 'source_positions': [8, 10, 13]}
    row = alignment_document(result_id='x', fasta='>a\nA-CG\n', roster=[source]).rows[0]
    assert (row.source_member_id, row.occurrence_id, row.source_positions) == ('m', 'o', [8, None, 10, 13])
    assert alignment_document(result_id='x', fasta='>a\nA-CT\n', roster=[source]).rows[0].source_positions is None
    assert alignment_document(result_id='x', fasta='>a\nA-CG\n', roster=[dict(source, source_positions=[8])]).rows[0].source_positions is None
