"""Use the persistence reader's native scalar authority for decisive filtering."""
import hashlib
from pathlib import Path

from .evidence import metric_evidence


def canonical_evidence(payload, raw, candidate_id):
    from lib.boltzgen_native import metric_records
    source = payload['native_scalar_source']
    if source['candidate_id'] != candidate_id:
        raise ValueError('foreign_native_candidate')
    if hashlib.sha256(raw).hexdigest() != source['artifact']['sha256']:
        raise ValueError('native_source_bytes_changed')
    result = {}
    for name, record in metric_records(source, raw, candidate_id=candidate_id).items():
        evidence = {'state': record['state'], 'value': record['value'],
                    'units': record['unit'], 'reason_code': record['reason_code']}
        supplied = payload.get('metric_evidence', {}).get(name)
        scalar = metric_evidence(name, payload.get(name))
        if record['state'] == 'ok' and (scalar['state'] != 'ok' or scalar['value'] != record['value']
                or (supplied is not None and supplied != evidence)):
            evidence.update(state='invalid', value=None, reason_code='native_scalar_conflict')
        result[name] = evidence
    return result


def gate_evidence(payload, metadata_path, candidate_id):
    """Read a declared local native artifact before evaluating or ranking."""
    source = payload.get('native_scalar_source')
    if source is None:
        return payload.get('metric_evidence')  # Prior marked sidecar dialect.
    relative = source['artifact']['path']
    path = Path(metadata_path).parent / relative
    if Path(relative).name != relative or path.is_symlink():
        raise ValueError('foreign_native_source')
    return {**payload.get('metric_evidence', {}),
            **canonical_evidence(payload, path.read_bytes(), candidate_id)}
