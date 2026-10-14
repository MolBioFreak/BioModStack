import pytest
from validate_clone_input_model import inspect_records

MODEL='dna_r10.4.1_e8.2_400bps_hac@v5.0.0'


def record(group='rg1',flag=0):
    return f'r1\t{flag}\tp\t1\t60\t4M\t*\t0\t0\tACGT\tIIII'+(f'\tRG:Z:{group}' if group is not None else '')


def header(model=MODEL): return f'@RG\tID:rg1\tDS:runid=abc basecall_model={model}'


def test_every_primary_read_is_bound_to_declared_model():
    result=inspect_records([header(),record()],MODEL)
    assert result['status']=='valid' and result['primary_reads_checked']==1
    assert result['instrument_authenticity_verified'] is False


@pytest.mark.parametrize('lines', [[record()], [header(),record(None)],['@RG\tID:rg1',record()], [header(),record('other')]])
def test_missing_model_is_unknown_not_a_veto(lines):
    result=inspect_records(lines,MODEL)
    assert result['status']=='valid'
    assert result['declared_model']==MODEL
    assert result['observed_models']==[]
    assert result['unknown_primary_reads']==1
    assert result['evidence_basis']=='unknown'


def test_sup_reads_are_not_relabelled_hac():
    result=inspect_records([header(MODEL.replace('hac','sup')),record()],MODEL)
    assert result['model_conflict'] is True
    assert result['observed_models']==[MODEL.replace('hac','sup')]
    assert result['declared_model']==MODEL


def test_empty_input_is_not_model_verified():
    result=inspect_records([header()],MODEL)
    assert result['primary_reads_checked']==0
    assert result['evidence_basis']=='unknown'


def test_duplicate_read_group_is_invalid():
    with pytest.raises(ValueError,match='READ_GROUP_INVALID'): inspect_records([header(),header(),record()],MODEL)


def test_mixed_groups_are_reported_without_relabelling():
    result=inspect_records([header(),header(MODEL.replace('hac','sup')).replace('ID:rg1','ID:rg2'),record(),record('rg2')],MODEL)
    assert result['model_conflict'] is True
    assert result['observed_models']==sorted([MODEL,MODEL.replace('hac','sup')])
