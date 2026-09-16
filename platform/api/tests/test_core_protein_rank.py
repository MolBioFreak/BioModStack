from types import SimpleNamespace

import pytest

from services.design_metrics import build_design_metric_completeness, build_design_metric_provenance


def subject():
    return {'name': 'candidate-A', 'ppiflow_objective_score': 2, 'iptm': 0.8,
            'rosetta_interface_score': -12,
            'confidence_metrics': {'ppiflow_rank_inputs': {
                'validator_iptm': {'value': 0.8, 'candidate_id': 'candidate-A', 'interface_id': 'A_B',
                                   'source_sha256': 'a' * 64, 'metric_kind': 'native_scalar_iptm'},
                'rosetta_interface_score': {'value': -12, 'candidate_id': 'candidate-A', 'interface_id': 'A_B',
                                          'source_sha256': 'b' * 64, 'unit': 'REU', 'metric_kind': 'raw_interface_score'},
            }}}


def rank(s):
    job = SimpleNamespace(provenance={'core_protein_scientific_contract': 1})
    metrics = build_design_metric_provenance(s, job=job)['metrics']
    record = next(m for m in metrics if m['metric_key'] == 'ppiflow_paper_rank_score')
    completeness = build_design_metric_completeness(s, job=job)['ppiflow']
    assert completeness['paper_rank_available'] == (record['state'] == 'ok')
    assert completeness['paper_rank_reason_code'] == record['reason_code']
    return record


def test_native_scalar_bound_rank():
    record = rank(subject())
    assert record['value'] == 92
    assert record['state'] == 'ok'
    assert record['reason_code'] is None
    assert record['direction'] == 'higher_is_better'


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), -0.1, 1.1])
def test_invalid_iptm(value):
    s = subject()
    s['confidence_metrics']['ppiflow_rank_inputs']['validator_iptm']['value'] = value
    assert rank(s)['state'] == 'invalid'
    assert rank(s)['value'] is None


@pytest.mark.parametrize('field,value', [('candidate_id', 'foreign'), ('interface_id', 'A_C'), ('source_sha256', 'bad')])
def test_invalid_binding(field, value):
    s = subject()
    s['confidence_metrics']['ppiflow_rank_inputs']['validator_iptm'][field] = value
    assert rank(s)['state'] == 'invalid'


@pytest.mark.parametrize('fallback', [{'ptm': 0.8}, {'validator_backend': 'boltz2'}, {'pair_chains_iptm': [[0.8]]}])
def test_no_substitutes(fallback):
    s = subject()
    del s['confidence_metrics']['ppiflow_rank_inputs']['validator_iptm']
    s.pop('iptm')
    s.update(fallback)
    assert rank(s)['state'] == 'unavailable'


def test_alias_conflict():
    s = subject()
    s['confidence_metrics']['validator_iptm'] = 0.5
    assert rank(s)['reason_code'] == 'conflicting_iptm_aliases'


def test_zero_is_legitimate():
    s = subject()
    s['iptm'] = 0
    s['rosetta_interface_score'] = 0
    s['confidence_metrics']['ppiflow_rank_inputs']['validator_iptm']['value'] = 0
    s['confidence_metrics']['ppiflow_rank_inputs']['rosetta_interface_score']['value'] = 0
    assert rank(s)['value'] == 0


def test_untrusted_marker_does_not_opt_in():
    s = {'ptm': 0.8, 'rosetta_interface_score': -12, 'confidence_metrics': {'core_protein_scientific_contract': 1}}
    record = build_design_metric_provenance(s)['metrics'][-1]
    assert record['value'] == 92
    assert 'state' not in record



@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), '12', 10 ** 400])
def test_invalid_rosetta_scalar_is_a_typed_record(value):
    s = subject()
    s['confidence_metrics']['ppiflow_rank_inputs']['rosetta_interface_score']['value'] = value
    record = rank(s)
    assert record['state'] == 'invalid'
    assert record['value'] is None


def test_rosetta_alias_conflict():
    s = subject()
    s['rosetta_interface_dg'] = 99
    assert rank(s)['reason_code'] == 'conflicting_rosetta_aliases'


@pytest.mark.parametrize('revision', [True, '1', 0, 2, None])
def test_invalid_revision_is_not_a_legacy_downgrade(revision):
    with pytest.raises(ValueError):
        build_design_metric_provenance(subject(), job=SimpleNamespace(provenance={'core_protein_scientific_contract': revision}))



@pytest.mark.parametrize('malformed', [None, [], 'invalid'])
def test_malformed_evidence_container_is_invalid(malformed):
    s = subject()
    s['confidence_metrics']['ppiflow_rank_inputs'] = malformed
    assert rank(s)['state'] == 'invalid'



def test_canonical_rank_envelope_uses_exact_owner_descriptor_and_source():
    from services import design_metrics
    from services.core_protein_scientific_contract import validate_metric
    build = getattr(design_metrics, 'build_ppiflow_rank_envelope', None)
    assert callable(build), 'canonical rank adapter is missing'
    s = subject()
    for value in s['confidence_metrics']['ppiflow_rank_inputs'].values():
        value['document_id'] = 'document-1'
    descriptor = {'metric_key': 'ppiflow_paper_rank_score', 'unit': 'composite_score',
                  'direction': 'higher_is_better', 'scope': 'A_B',
                  'producer_version': 'ppiflow-result-v1', 'derivation_version': 'paper-rank-v1'}
    source = {'artifact_sha256': 'c' * 64, 'candidate_id': 'candidate-A', 'document_id': 'document-1'}
    envelope = build(s, descriptor=descriptor, expected_source=source)
    assert validate_metric(envelope, expected_source=source) == envelope
    assert envelope['value'] == 92
    assert envelope == {**descriptor, 'source': source, 'state': 'ok', 'value': 92, 'reason_code': None}
    s['confidence_metrics']['ppiflow_rank_inputs']['validator_iptm']['document_id'] = 'other-document'
    assert build(s, descriptor=descriptor, expected_source=source)['state'] == 'invalid'
