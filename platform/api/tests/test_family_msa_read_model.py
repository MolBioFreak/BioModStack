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
    source = {'row_id': 'a', 'header': 'same', 'sequence': 'AcG', 'source_member_id': 'm', 'occurrence_id': 'o', 'source_positions': [8, 10, 13], 'transformation_provenance': 'declared'}
    row = alignment_document(result_id='x', fasta='>a\nA-CG\n', roster=[source]).rows[0]
    assert (row.source_member_id, row.occurrence_id, row.source_positions) == ('m', 'o', [8, None, 10, 13])
    assert alignment_document(result_id='x', fasta='>a\nA-CT\n', roster=[source]).rows[0].source_positions is None
    assert alignment_document(result_id='x', fasta='>a\nA-CG\n', roster=[dict(source, source_positions=[8])]).rows[0].source_positions is None


@pytest.mark.parametrize(('extra', 'expected'), [
    ({'source_offset': 8}, None),
    ({'source_positions': [8, 9, 10]}, None),
    ({'source_positions': [8, 9, 10], 'transformation_provenance': '  '}, None),
    ({'source_positions': [9, 5, 0], 'transformation_provenance': 'reverse'}, [9, None, 5, 0]),
    ({'source_positions': [9, 9, 0], 'transformation_provenance': 'declared'}, [None, None, None, 0]),
    ({'source_positions': [True, -1, {}], 'transformation_provenance': 'declared'}, [None, None, None, None]),
    ({'source_positions': [8, None, 0], 'transformation_provenance': 'declared'}, [8, None, None, 0]),
])
def test_uncertain_mapping_does_not_block_projection(extra, expected):
    source = {'row_id': 'a', 'header': 'same', 'sequence': 'ACG', **extra}
    row = alignment_document(result_id='x', fasta='>a\nA-CG\n', roster=[source]).rows[0]
    assert row.aligned == 'A-CG'
    assert row.source_positions == expected


def test_similarity_gap_policy_case_and_duplicate_labels():
    result = alignment_document(result_id='x', fasta='>a\naN-.C\n>b\nANAAc\n>c\nAT-A-\n',
                                roster=[{'row_id': x, 'header': 'same'} for x in 'abc'])
    stats = result.similarity
    assert [(p['left_row_id'], p['right_row_id'], p['identical'], p['compared']) for p in stats['pairs']] == [
        ('a', 'b', 3, 3), ('a', 'c', 1, 2), ('b', 'c', 2, 3)]
    assert stats['mean_pairwise_identity_percent'] == pytest.approx((100 + 50 + 200 / 3) / 3)
    assert stats['conserved_column_percent'] == 50
    assert stats['compared_columns'] == 2
    assert stats['pairs_complete'] is True


def test_similarity_undefined_and_single_row():
    from bms_alignment.similarity import alignment_similarity
    result = alignment_similarity([('a', 'A-'), ('b', '-C')])
    assert result['pairs'][0]['identity_percent'] is None
    assert result['mean_pairwise_identity_percent'] is None
    assert result['conserved_column_percent'] is None
    assert alignment_similarity([('a', 'AC')])['mean_pairwise_identity_percent'] is None


def test_similarity_large_alignment_reports_omission_without_sampling():
    from bms_alignment.similarity import alignment_similarity
    result = alignment_similarity([(str(i), 'aC-') for i in range(22)])
    assert result['pairs_complete'] is False
    assert result['pair_count'] == 231
    assert result['pairs'] == []
    assert result['mean_pairwise_identity_percent'] is None
    assert result['conserved_column_percent'] == 100
    assert result['compared_columns'] == 2
