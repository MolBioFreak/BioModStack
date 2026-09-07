"""Exact request-owned residue correspondence; never infer mapping from key overlap.

Pairs and both domains must originate in a trusted request/preparation projection,
not a candidate manifest claiming its own authority. Coordinates retain their input
frame: sqrt(mean squared CA distance), Angstrom. This module is model-free.
"""
import math
import hashlib
import json
from pathlib import Path


def pdb_identities(data):
    """Inventory physical ATOM residue identities, never infer correspondence."""
    return list(dict.fromkeys((line[21], int(line[22:26]), line[26].strip())
                             for line in data.decode('ascii').splitlines()
                             if line.startswith('ATOM  ')))


def publish_native_export(reference_path, candidate_path, *, records, roles, domains, source_evidence):
    """Publish at the native writer, using its explicit per-residue export records.

    This is not an ingress API. Records are obtained from parser-owned identity
    tensors and the actual exporter's Protein arrays. Restore only those known
    identity/translation transforms; no fit, sequence alignment or key overlap.
    Hash the resulting bytes before any downstream rename/copy or score task.
    """
    reference_bytes = reference_path if isinstance(reference_path, bytes) else Path(reference_path).read_bytes()
    path = Path(candidate_path)
    native_bytes = path.read_bytes()
    native = {}
    sources = set()
    for record in records:
        source = residue_identity(record['source'])
        exported = residue_identity(record['exported'])
        offset = record['offset']
        if (exported in native or source in sources or len(offset) != 3
                or not all(finite_number(v) for v in offset)):
            raise ValueError('ambiguous identity or invalid source frame')
        if len(source[0]) != 1 or len(source[2]) > 1 or not -999 <= source[1] <= 9999:
            raise ValueError('source identity cannot be represented in PDB')
        native[exported] = (source, offset)
        sources.add(source)
    restored = []
    for line in native_bytes.decode('ascii').splitlines(keepends=True):
        if line.startswith(('ATOM  ', 'HETATM', 'TER   ')):
            key = (line[21], int(line[22:26]), line[26:27].strip())
            if key not in native:
                raise ValueError('native writer emitted an unowned residue')
            source, offset = native[key]
            line = line[:21] + f'{source[0]}{source[1]:4d}{source[2]:1s}' + line[27:]
            if line.startswith(('ATOM  ', 'HETATM')):
                xyz = [float(line[30+8*i:38+8*i]) + offset[i] for i in range(3)]
                fields = [f'{v:8.3f}' for v in xyz]
                if not all(finite_number(v) for v in xyz) or any(len(v) != 8 for v in fields):
                    raise ValueError('restored frame cannot be represented in PDB')
                line = line[:30] + ''.join(fields) + line[54:]
        restored.append(line)
    candidate_bytes = ''.join(restored).encode('ascii')
    reference_keys = pdb_identities(reference_bytes)
    binder = roles['binder']
    target = roles['target']
    if not binder or not target or set(binder) & set(target):
        raise ValueError('invalid producer roles')
    expected_candidate = [tuple(record['source']) for record in records]
    reference_binder = [k for k in reference_keys if k[0] in binder]
    domain_keys = {'whole_binder': reference_binder, **domains}
    selected = {residue_identity(k) for k in domains.get('selected', [])}
    domain_keys['nonselected'] = [k for k in reference_binder if k not in selected]
    compiled = {}
    for name, values in domain_keys.items():
        rd = [residue_identity(k) for k in values]
        cd = [k for k in expected_candidate if (k[0] in binder if name == 'whole_binder' else k in rd)]
        # Each identity pair below is justified by the source record, not by
        # coincidental equality between parsed input and output PDB keys.
        pairs = [[list(k), list(k)] for k in cd if k in rd]
        compiled[name] = {'reference': [list(k) for k in rd], 'candidate': [list(k) for k in cd], 'pairs': pairs}
    request = {'reference_sha256': hashlib.sha256(reference_bytes).hexdigest(),
               'candidate_sha256': hashlib.sha256(candidate_bytes).hexdigest(),
               'roles': {'reference': roles, 'candidate': roles}, 'domains': compiled,
               'native_export': {'sha256': hashlib.sha256(native_bytes).hexdigest(),
                                 'records': records, 'source_evidence': source_evidence,
                                 'restoration': 'source_identity_and_translation_only'}}
    payload = json.dumps(request, sort_keys=True, allow_nan=False)
    path.write_bytes(candidate_bytes)
    Path(str(path) + '.comparison.json').write_text(payload)
    return request


def validate_comparison_request(request, reference_bytes, candidate_bytes):
    """Validate binding, not trust: caller supplies a request-owned projection.

    A hash binds bytes; it does not prove a residue mapping. This API must not
    consume arbitrary candidate-generated manifests as comparison requests.
    """
    if not isinstance(request, dict):
        return 'invalid_comparison_request'
    for role, data in (('reference', reference_bytes), ('candidate', candidate_bytes)):
        if request.get(role + '_sha256') != hashlib.sha256(data).hexdigest():
            return role + '_identity_mismatch'
        groups = request.get('roles', {}).get(role, {})
        binder, target = groups.get('binder'), groups.get('target')
        if not isinstance(binder, list) or not isinstance(target, list) or not binder or not target:
            return 'invalid_role_authority'
        if any(not isinstance(c, str) or not c for c in binder + target) or len(set(binder + target)) != len(binder + target):
            return 'invalid_role_authority'
    if not isinstance(request.get('domains'), dict):
        return 'invalid_comparison_request'
    return None


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def residue_identity(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('invalid residue identity')
    chain, number, insertion = value
    if not isinstance(chain, str) or not chain or type(number) is not int or not isinstance(insertion, str):
        raise ValueError('invalid residue identity')
    return (chain, number, insertion)


def compare_declared_domain(reference, candidate, reference_domain, candidate_domain, pairs, *, domain, subset=None):
    result = dict(value=None, reason=None, domain=domain, unit='angstrom',
                  frame='input_coordinates_no_superposition', formula='sqrt(mean_squared_CA_distance)',
                  expected_reference_count=len(reference_domain), expected_candidate_count=len(candidate_domain),
                  matched_count=0, reference_coverage=0.0, candidate_coverage=0.0,
                  unmatched_reference=[], unmatched_candidate=[])
    try:
        rd = [residue_identity(key) for key in reference_domain]
        cd = [residue_identity(key) for key in candidate_domain]
        if len(set(rd)) != len(rd) or len(set(cd)) != len(cd):
            raise ValueError('duplicate domain identity')
        mapping = [(residue_identity(a), residue_identity(b)) for a, b in (pairs or [])]
        if len({a for a, b in mapping}) != len(mapping) or len({b for a, b in mapping}) != len(mapping):
            raise ValueError('duplicate correspondence')
        if any(a not in rd or b not in cd for a, b in mapping):
            raise ValueError('foreign correspondence')
    except (ValueError, TypeError):
        result['reason'] = 'invalid_correspondence'
        return result
    matched = []
    reasons_a, reasons_b = {}, {}
    for a, b in mapping:
        if a not in reference or b not in candidate:
            reasons_a[a] = 'missing_coordinates' if a not in reference else 'counterpart_missing_coordinates'
            reasons_b[b] = 'missing_coordinates' if b not in candidate else 'counterpart_missing_coordinates'
            continue
        av, bv = reference[a], candidate[b]
        values = [getattr(v, axis, None) for v in (av, bv) for axis in ('x', 'y', 'z')]
        if not all(finite_number(v) for v in values):
            reasons_a[a] = reasons_b[b] = 'invalid_coordinates'
            continue
        matched.append((a, b))
    ma, mb = {a for a, b in matched}, {b for a, b in matched}
    for keys, found, reasons, name in ((rd, ma, reasons_a, 'reference'), (cd, mb, reasons_b, 'candidate')):
        result['unmatched_' + name] = [dict(identity=list(key), reason=reasons.get(key, 'unmapped_identity')) for key in keys if key not in found]
        result[name + '_coverage'] = len(matched) / len(keys) if keys else 0.0
    result['matched_count'] = len(matched)
    if pairs is None:
        result['reason'] = 'missing_correspondence_authority'
    elif not matched:
        result['reason'] = 'zero_correspondence'
    elif len(matched) != len(rd) or len(matched) != len(cd):
        result['reason'] = 'incomplete_correspondence'
    if matched:
        try:
            value = math.sqrt(sum(sum((getattr(reference[a], axis) - getattr(candidate[b], axis)) ** 2 for axis in ('x', 'y', 'z')) for a, b in matched) / len(matched))
        except OverflowError:
            value = None
        if not finite_number(value):
            result['reason'] = 'invalid_coordinates'
        elif subset:
            result['subset'] = dict(name=subset, value=value, matched_count=len(matched), unit='angstrom')
            result['reason'] = result['reason'] or 'subset_only'
        elif result['reason'] is None:
            result['value'] = value
    return result


def compare_request_domains(request, reference, candidate, reference_binder, candidate_binder):
    """Consume explicit domains from the request, never intersect to define them."""
    domains = (request or {}).get('domains', {})
    results = {}
    for name in dict.fromkeys(['whole_binder', 'selected', 'nonselected', *domains]):
        spec = domains.get(name)
        if spec is None:
            results[name] = dict(value=None, reason='missing_correspondence_authority', domain=name,
                                 expected_reference_count=None, expected_candidate_count=None,
                                 matched_count=0, reference_coverage=None, candidate_coverage=None,
                                 unmatched_reference=[], unmatched_candidate=[])
            continue
        result = compare_declared_domain(reference, candidate, spec.get('reference', []),
                                         spec.get('candidate', []), spec.get('pairs'), domain=name,
                                         subset=spec.get('subset'))
        if name == 'whole_binder':
            try:
                # Full binder is never an alias for a smaller reported subset.
                mismatch = (set(map(residue_identity, spec.get('reference', []))) != set(reference_binder)
                            or set(map(residue_identity, spec.get('candidate', []))) != set(candidate_binder))
            except (TypeError, ValueError):
                mismatch = True
            if mismatch:
                result.update(value=None, reason='declared_domain_mismatch')
        results[name] = result
    return results


def canonical_payload(value):
    """Nonfinite numeric observations serialize as unavailable, never JSON NaN."""
    if isinstance(value, dict):
        return {k: canonical_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_payload(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
