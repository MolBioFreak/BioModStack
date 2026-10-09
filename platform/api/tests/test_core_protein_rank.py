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
