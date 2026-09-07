"""Version-1 filter evidence. Only trusted workflow activation selects this path.

Evidence records have a closed shape; raw native columns remain in the native
metadata, not in this schema. Units are declared, never inferred from magnitude.
"""
import csv
import hashlib
import io
import json
import math
from numbers import Real
from pathlib import Path

PROBABILITIES = {'ptm', 'iptm', 'design_ptm', 'design_iptm', 'target_ptm', 'protein_iptm', 'affinity_probability'}
NONNEGATIVE = {'filter_rmsd', 'rmsd', 'rmsd_binder', 'pae', 'rog', 'helices', 'strands', 'total_ss', 'binder_length'}
CORE = ('design_ptm', 'affinity_probability', 'filter_rmsd', 'plddt')


def metric_evidence(name, value, units=None):
    unit = 'percent' if name == 'plddt' else ('fraction' if name in PROBABILITIES or name.endswith('_fraction') else ('angstrom' if name in {'filter_rmsd', 'rmsd', 'rmsd_binder', 'pae', 'rog'} else 'native'))
    record = {'state': 'ok', 'value': None, 'units': unit, 'reason_code': None}
    reason = None
    if value is None:
        record.update(state='unavailable', reason_code='missing_metric')
        return record
    if isinstance(value, bool) or not isinstance(value, Real):
        reason = 'not_real'
    elif not math.isfinite(value):
        reason = 'nonfinite'
    elif name == 'plddt' and units not in ('percent', 'fraction'):
        reason = 'unknown_plddt_units'
    else:
        value = float(value)
        if name == 'plddt':
            maximum = 1 if units == 'fraction' else 100
            if not 0 <= value <= maximum:
                reason = 'outside_domain'
            elif units == 'fraction':
                value *= 100
        elif (name in PROBABILITIES or name.endswith('_fraction')) and not 0 <= value <= 1:
            reason = 'outside_domain'
        elif name in NONNEGATIVE and value < 0:
            reason = 'outside_domain'
        elif name in {'helices', 'strands', 'total_ss', 'binder_length'} and not value.is_integer():
            reason = 'outside_domain'
    if reason:
        record.update(state='invalid', reason_code=reason)
    else:
        record['value'] = value
    return record


def evaluate(criteria, metrics, candidate_id, evidence=None, plddt_units=None, required=()):
    records = []
    for name, low, high in criteria:
        if low is None and high is None and name not in required:
            continue
        for bound in (low, high):
            if bound is not None and metric_evidence(name, bound, 'percent')['state'] != 'ok':
                raise ValueError(f'Invalid threshold for {name}')
        if low is not None and high is not None and low > high:
            raise ValueError(f'Inverted threshold for {name}')
        item = (evidence or {}).get(name)
        if item is not None:
            schema_fields = {'state', 'value', 'units', 'reason_code'}
            valid = isinstance(item, dict) and set(item) == schema_fields
            if valid and item['state'] == 'ok':
                valid = item['reason_code'] is None and item['units'] == metric_evidence(name, None)['units'] and metric_evidence(name, item['value'], item['units'])['state'] == 'ok'
            elif valid:
                valid = item['state'] in ('unavailable', 'invalid') and item['value'] is None and isinstance(item['reason_code'], str) and bool(item['reason_code'])
            if not valid:
                item = {'state': 'invalid', 'value': None, 'units': 'native', 'reason_code': 'invalid_evidence_schema'}
        else:
            item = metric_evidence(name, metrics.get(name), plddt_units)
        if item['state'] == 'unavailable':
            disposition = 'unevaluable_missing'
        elif item['state'] == 'invalid':
            disposition = 'invalid_evidence'
        elif (low is not None and item['value'] < low) or (high is not None and item['value'] > high):
            disposition = 'rejected_threshold'
        else:
            disposition = 'passed'
        records.append({'candidate_id': candidate_id, 'criterion': name, 'minimum': low, 'maximum': high, 'disposition': disposition, 'evidence': item})
    disposition = next((state for state in ('invalid_evidence', 'unevaluable_missing', 'rejected_threshold') if any(r['disposition'] == state for r in records)), 'passed')
    return {'core_protein_scientific_contract': 1, 'candidate_id': candidate_id, 'disposition': disposition, 'criteria': records}


def native_metadata(values, design_id, source_bytes, dialect):
    values = dict(values)
    if 'affinity_probability_binary1' in values:
        values['affinity_probability'] = values['affinity_probability_binary1']
    evidence = {}
    # Preserve additional native numeric columns so requested metrics are not dropped.
    for name in set(CORE) | {k for k,v in values.items() if isinstance(v, Real)}:
        evidence[name] = metric_evidence(name, values.get(name), values.get('plddt_units'))
        values[name] = evidence[name]['value']
    values.update(design_id=design_id, source='boltzgen', metrics_source=dialect,
                  core_protein_scientific_contract=1,
                  source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                  metric_evidence=evidence)
    if evidence['plddt']['state'] == 'ok':
        values['plddt_units'] = 'percent'
    return values


def csv_metadata(csv_path, output_dir, known_design_ids=None, batch_prefix='', producer_identity=None, filter_from_inverse_folded=None):
    raw = Path(csv_path).read_bytes()
    from lib.boltzgen_native import csv_candidate_identity
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError('CSV candidate identity columns ambiguous')
    rows = [(row, csv_candidate_identity(row)) for row in reader]
    for row, base in rows:
        rank = row.get('final_rank')
        candidates = ([f'rank{int(float(rank))}_{base}'] if rank else []) + [base]
        candidates = [batch_prefix + c for c in candidates]
        matches = [c for c in candidates if known_design_ids is None or c in known_design_ids]
        if not matches:
            raise ValueError(f'CSV identity not bound to converted candidate: {base}')
        values = {}
        for key, value in row.items():
            if value == '':
                values[key] = None
            else:
                try:
                    values[key] = float(value)
                except (ValueError, TypeError):
                    values[key] = value
        data = native_metadata(values, matches[0], raw, 'csv')
        from lib.boltzgen_native import retain_source
        data['native_scalar_source'] = retain_source(csv_path, output_dir, matches[0], 'csv', producer_identity, native_id=base)
        data['native_scalar_source']['filter_from_inverse_folded'] = filter_from_inverse_folded
        (Path(output_dir) / f'confidence_{matches[0]}.json').write_text(json.dumps(data, allow_nan=False, indent=2))
    return True


def npz_metadata(batch_dir, output_dir, known_design_ids=None, batch_prefix='', producer_identity=None):
    import numpy as np
    count = 0
    seen = set()
    for sub in ('intermediate_designs_inverse_folded/fold_out_npz', 'intermediate_designs_inverse_folded', 'final_ranked_designs', ''):
        for path in (Path(batch_dir) / sub).glob('*.npz'):
            identity = batch_prefix + path.stem
            if identity in seen:
                continue
            if known_design_ids is not None and identity not in known_design_ids:
                continue
            raw = path.read_bytes()
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                values = {}
                for key in archive.files:
                    array = archive[key]
                    if key == 'plddt_units':
                        values[key] = str(array.item())
                    elif array.size == 0:
                        values[key] = None
                    elif array.dtype.kind == 'b':
                        values[key] = bool(array.flat[0])
                    elif array.dtype.kind in 'fiu':
                        # Any invalid member invalidates the metric; never nanmean.
                        units = str(archive['plddt_units'].item()) if 'plddt_units' in archive.files else None
                        valid = np.isfinite(array).all() and all(metric_evidence(key, float(v), units)['state'] == 'ok' for v in array.flat)
                        values[key] = float(array.mean()) if valid else float('nan')
                    else:
                        continue
                data = native_metadata(values, identity, raw, 'npz')
                from lib.boltzgen_native import retain_source
                data['native_scalar_source'] = retain_source(path, output_dir, identity, 'npz', producer_identity)
            (Path(output_dir) / f'confidence_{identity}.json').write_text(json.dumps(data, allow_nan=False, indent=2))
            seen.add(identity)
            count += 1
    return count
