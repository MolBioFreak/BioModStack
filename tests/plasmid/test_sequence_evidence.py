import json
from pathlib import Path
import pytest
import verify_construct as verify
from build_fastq_support_tables import PositionSupport, _consensus_and_fraction


def row(base='A', depth=30):
    return {**{b: depth if b==base else 0 for b in 'ACGTN'},
            'deletion_count':0,'insertion_count':0,'insertion_alleles':{},
            'depth':depth,'consensus_base':base}


def test_zero_depth_does_not_copy_expected_base():
    assert _consensus_and_fraction(PositionSupport(), 'A')==('N',0.0)
    assert verify._consensus_options_from_support(row(depth=0),'A')=={'N'}


def test_zero_depth_is_unavailable_not_contradiction():
    r=row(depth=0)
    with pytest.raises(verify.SequenceEvidenceUnavailable,match='UNCOVERED'):
        verify.validate_observed_consensus_binding('A','A',{1:r},{1:r})


@pytest.mark.parametrize(('reference','observed'), [('AN','AA'),('AA','AN')])
def test_ambiguous_sequence_cannot_prove_exactness(reference,observed):
    rows={1:row(),2:row()}
    with pytest.raises(verify.SequenceEvidenceUnavailable,match='AMBIGUOUS'):
        verify.validate_observed_consensus_binding(reference,observed,rows,rows)


def test_supported_deletion_is_an_observation():
    reference='ACGTT';observed='ACTT'
    rows={i:row(b) for i,b in enumerate(reference,1)}
    rows[3]={**row(depth=0),'deletion_count':30,'depth':30,'consensus_base':'-'}
    verify.validate_observed_consensus_binding(reference,observed,rows,rows)


def test_missing_row_is_invalid_not_zero_depth():
    with pytest.raises(ValueError,match='row unavailable') as exc:
        verify.validate_observed_consensus_binding('AC','AC',{1:row()},{1:row()})
    assert not isinstance(exc.value,verify.SequenceEvidenceUnavailable)


def test_omitted_strong_insertion_is_not_a_matching_consensus():
    rows={1:row(),2:row('C')}
    rows[1].update(insertion_count=30,insertion_alleles={'G':30})
    with pytest.raises(ValueError,match='omits supported insertion'):
        verify.validate_observed_consensus_binding('AC','AC',rows,rows)


def test_new_profile_is_strict_but_not_qualified():
    doc=json.loads((Path(__file__).resolve().parents[2]/'config/ngs/construct_verify_profiles.json').read_text())
    old=doc['profiles']['plasmid_strict_v1']; new=doc['profiles']['plasmid_complete_v2']
    assert old['min_coverage_fraction']==.99
    assert new['min_coverage_fraction']==1 and new['max_low_depth_fraction']==0
    for profile in (old,new):
        assert profile['automatic_pass_eligible'] is False
        assert profile['public_accuracy_validated'] is False
