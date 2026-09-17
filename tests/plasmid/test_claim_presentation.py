import pytest
from verify_construct import variant_analysis_completed, check_display_name, write_evidence_html


def manifest(reason='CIRCULAR_ALIGNMENT_WORK_BUDGET_EXCEEDED'):
    checks={name:{'status':'review','reason_codes':[reason],'metrics':{}} for name in ('sequence_identity','read_support','coverage','contamination','topology')}
    return {'verdict':'REVIEW','reason_codes':[reason],'checks':checks,'variants':[],
            'summary':{'variant_count':0,'sequence_identity_fraction':None}}


@pytest.mark.parametrize('reason',['MISSING_OBSERVED_CONSENSUS','CIRCULAR_ALIGNMENT_WORK_BUDGET_EXCEEDED','OBSERVED_CONSENSUS_SUPPORT_CONTRADICTION','VARIANT_CALLING_UNAVAILABLE'])
def test_empty_vcf_is_not_a_negative_variant_screen(reason):
    assert not variant_analysis_completed(manifest(reason))


def test_clean_exact_match_can_report_zero_variants():
    data=manifest();data['checks']['sequence_identity'].update(status='pass',reason_codes=[],metrics={'identity_fraction':1.0})
    assert variant_analysis_completed(data)


def test_html_preserves_unavailable_state_and_narrows_claims(tmp_path):
    output=tmp_path/'report.html';write_evidence_html(output,manifest())
    text=output.read_text()
    assert 'Variant analysis was not completed' in text
    assert 'No observed variants.' not in text
    assert 'Expected-reference mapping' in text
    assert 'does not establish' in text and 'sample purity' in text
    assert 'Independent checks' not in text


def test_wire_key_can_remain_stable_with_precise_display_label():
    assert check_display_name('contamination')=='Expected-reference mapping'
