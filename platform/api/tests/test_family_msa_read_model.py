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
