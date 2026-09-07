"""Closed RF criterion evidence validation; no structural recomputation."""
import math

THRESHOLDS = {
    'rf3_prediction_filter': {
        'plddt': ('rf3_min_plddt', None), 'ptm': ('rf3_min_ptm', None),
        'pae': (None, 'rf3_max_pae'), 'rmsd': (None, 'rf3_max_rmsd_overall'),
        'rmsd_binder': (None, 'rf3_max_rmsd_binder')},
    'rfd3_backbone_filter': {
        'total_ss': ('rfd_min_ss', 'rfd_max_ss'), 'helices': ('rfd_min_helices', 'rfd_max_helices'),
        'strands': ('rfd_min_strands', 'rfd_max_strands'), 'rog': ('rfd_min_rog', 'rfd_max_rog')},
}
UNITS = {'plddt': 'percent', 'ptm': 'fraction', 'pae': 'angstrom', 'rmsd': 'angstrom',
         'rmsd_binder': 'angstrom', 'rog': 'angstrom', 'total_ss': 'native', 'helices': 'native', 'strands': 'native'}


def number(name, value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('filter criterion requires finite domain number')
    if ((name == 'ptm' and value > 1) or (name == 'plddt' and value > 100)
            or (name in ('total_ss', 'helices', 'strands') and int(value) != value)):
        raise ValueError('filter criterion number outside domain')
    return value


def enabled_criteria(stage_id, settings):
    result = {}
    for name, keys in THRESHOLDS[stage_id].items():
        bounds = [settings.get(key) if key else None for key in keys]
        for value in bounds:
            if value is not None: number(name, value)
        if all(value is not None for value in bounds) and bounds[0] > bounds[1]:
            raise ValueError('filter inverted criterion bounds')
        if any(value is not None for value in bounds): result[name] = bounds
    return result


def validate_outcome(row, stage_id, settings):
    base = {'description', 'fold_id', 'seq_id', 'file', 'passed', 'reason',
            'core_protein_scientific_contract', 'candidate_id', 'disposition', 'criteria', 'source_sha256', 'artifacts'}
    metrics = ({'plddt', 'ptm', 'pae', 'rmsd', 'rmsd_binder'} if stage_id == 'rf3_prediction_filter'
               else {'rfd_helices', 'rfd_strands', 'rfd_total_ss', 'rfd_RoG'})
    if not base | metrics <= set(row) or set(row) - base - metrics - {'descriptor_provenance', 'candidate_failure'}:
        raise ValueError('filter outcome fields invalid')
    if type(row['core_protein_scientific_contract']) is not int or row['core_protein_scientific_contract'] != 1:
        raise ValueError('filter outcome revision invalid')
    expected = enabled_criteria(stage_id, settings)
    criteria = row['criteria']
    if not isinstance(criteria, list): raise ValueError('filter criteria missing')
    seen = set()
    for criterion in criteria:
        if not isinstance(criterion, dict) or set(criterion) != {'candidate_id', 'criterion', 'minimum', 'maximum', 'disposition', 'evidence'}:
            raise ValueError('filter criterion fields invalid')
        name = criterion['criterion']
        if not isinstance(name, str) or name not in expected or name in seen or criterion['candidate_id'] != row['candidate_id']:
            raise ValueError('filter criterion identity invalid')
        seen.add(name)
        bounds = [criterion['minimum'], criterion['maximum']]
        for value in bounds:
            if value is not None: number(name, value)
        if bounds != expected[name]: raise ValueError('filter criterion differs from owner thresholds')
        item = criterion['evidence']
        if not isinstance(item, dict) or set(item) != {'state', 'value', 'units', 'reason_code'} or item['units'] != UNITS[name]:
            raise ValueError('filter criterion evidence fields/units invalid')
        if item['state'] == 'ok':
            value = number(name, item['value'])
            if item['reason_code'] is not None: raise ValueError('filter ok criterion has reason')
            disposition = 'rejected_threshold' if ((bounds[0] is not None and value < bounds[0]) or (bounds[1] is not None and value > bounds[1])) else 'passed'
        else:
            if item['state'] not in ('unavailable', 'invalid') or item['value'] is not None or not isinstance(item['reason_code'], str) or not item['reason_code'].strip():
                raise ValueError('filter criterion missing/invalid reason')
            disposition = 'invalid_evidence' if item['state'] == 'invalid' else 'unevaluable_missing'
        if criterion['disposition'] != disposition: raise ValueError('filter criterion disposition contradiction')
    if seen != set(expected): raise ValueError('filter enabled criteria missing')
    disposition = next((state for state in ('invalid_evidence', 'unevaluable_missing', 'rejected_threshold')
                        if any(c['disposition'] == state for c in criteria)), 'passed')
    failure = row.get('candidate_failure')
    if failure is not None:
        if (not isinstance(failure, dict) or set(failure) != {'code', 'detail'}
                or failure['code'] != 'candidate_evidence_failure'
                or not isinstance(failure['detail'], str) or not failure['detail'].strip()):
            raise ValueError('filter candidate failure reason invalid')
        disposition = 'invalid_evidence'
    if row['disposition'] != disposition: raise ValueError('filter outer criterion disposition contradiction')
    if disposition == 'passed':
        if row['reason'] is not None: raise ValueError('filter passed outcome has reason')
    elif not isinstance(row['reason'], str) or not row['reason'].strip():
        raise ValueError('filter nonpassing outcome lacks reason')
