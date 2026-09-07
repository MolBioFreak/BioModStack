"""Trusted PrepFAMPNN correspondence -> exact input scopes -> native IDs.

Preflight runs before inference; candidate binding runs after the native writer.
Neither stage derives a biological domain from filenames, hashes, or length.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from analyse_fampnn_seq_probs import (
    STRICT_DIALECT, WORKFLOW_DECLARATIONS, _source_identities, _validate_policy,
    _resolve_policy,
)

TRANSFORM = 'prep_fampnn_designs:pdb_info:v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prep_receipt(source, output, pairs):
    """Pairs are captured from the transform's residue objects, never aligned here."""
    source_ids = list(_source_identities(Path(source).read_bytes()).values())
    output_ids = list(_source_identities(Path(output).read_bytes()).values())
    pairs = [list(pair) for pair in pairs]
    if (len(pairs) != len(source_ids) or len(pairs) != len(output_ids)
            or {p[0] for p in pairs} != set(source_ids)
            or {p[1] for p in pairs} != set(output_ids)):
        raise ValueError('PrepFAMPNN transform requires complete bijective provenance')
    receipt = dict(schema_version=1, transform=TRANSFORM, source_pdb_sha256=digest(source),
                   prepared_pdb_sha256=digest(output), source_domain=source_ids,
                   prepared_domain=output_ids, pairs=pairs)
    from antibody_fampnn_provenance import read_export
    antibody = read_export(output)
    if antibody is not None:
        receipt['antibody'] = antibody
    return receipt


def resolve_declaration(declaration, receipts, prepared_dir):
    """Validate all actual prepared inputs before any native model execution."""
    if isinstance(declaration, dict) and 'materialization' in declaration:
        if (declaration.get('owner'), declaration.get('declaration')) != ('antibody_denovo', 'authorized_sequence_design_region'):
            raise ValueError('unsupported deferred antibody declaration')
        if not receipts:
            raise ValueError('missing PrepFAMPNN transform provenance')
        from antibody_fampnn_provenance import materialize
        result = None
        for name, receipt in receipts.items():
            concrete = materialize(declaration, receipt, Path(prepared_dir)/(name + '.pdb'))
            resolved = resolve_declaration(concrete, {name: receipt}, prepared_dir)
            if result is None:
                result = resolved
            else:
                result['inputs'].update(resolved['inputs'])
        return result
    keys = {'schema_version', 'owner', 'version', 'declaration', 'input_domain',
            'sequence_design', 'summary', 'fixed', 'summary_override',
            'mutation_override', 'allow_summary_override', 'require_full_coverage'}
    if not isinstance(declaration, dict) or set(declaration) != keys:
        raise ValueError('closed admission declaration required')
    if (type(declaration['schema_version']) is not int or declaration['schema_version'] != 1
            or type(declaration['version']) is not int or declaration['version'] != 1
            or declaration['declaration'] not in WORKFLOW_DECLARATIONS.get(declaration['owner'], set())):
        raise ValueError('unsupported declaration authority/version')
    for flag in ('allow_summary_override', 'require_full_coverage'):
        if type(declaration[flag]) is not bool:
            raise ValueError('declaration boolean required')
    policy = {key: deepcopy(declaration[key]) for key in ('schema_version', 'owner', 'version',
              'declaration', 'allow_summary_override', 'require_full_coverage')}
    policy['dialect'] = STRICT_DIALECT
    policy['inputs'] = {}
    if not isinstance(receipts, dict) or not receipts:
        raise ValueError('missing PrepFAMPNN transform provenance')
    for name, receipt in receipts.items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ValueError('invalid prepared input ID')
        path = Path(prepared_dir) / (name + '.pdb')
        if receipt.get('schema_version') != 1 or receipt.get('transform') != TRANSFORM:
            raise ValueError('untrusted PrepFAMPNN transform')
        if digest(path) != receipt.get('prepared_pdb_sha256'):
            raise ValueError('prepared bytes changed after transform')
        observed = set(_source_identities(path.read_bytes()).values())
        pairs = receipt.get('pairs', [])
        if (any(not isinstance(p, list) or len(p) != 2 for p in pairs)
                or len(pairs) != len(observed) or len(pairs) != len(receipt['source_domain'])
                or {p[0] for p in pairs} != set(receipt['source_domain'])
                or {p[1] for p in pairs} != observed
                or observed != set(receipt['prepared_domain'])):
            raise ValueError('incomplete PrepFAMPNN residue provenance')
        mapping = dict(pairs)
        def project(key):
            values = declaration[key]
            if values is None and key.endswith('_override'):
                return None
            if (not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                    or len(values) != len(set(values)) or not set(values) <= set(mapping)):
                raise ValueError(f'{key} outside preparation source domain')
            return [mapping[v] for v in values]
        entry = {key: project(key) for key in ('input_domain', 'sequence_design', 'summary',
                                             'summary_override', 'mutation_override')}
        domain, authorized = set(entry['input_domain']), set(entry['sequence_design'])
        if not authorized <= domain or not set(entry['summary']) <= domain:
            raise ValueError('declaration scope outside input domain')
        if declaration['declaration'] in {'authorized_sequence_design_region', 'sequence_redesign_positions_spec'} and set(entry['summary']) != authorized:
            raise ValueError('declaration summary conflicts with sequence-design region')
        if declaration['declaration'] == 'declared_protein_inputs' and set(entry['summary']) != domain:
            raise ValueError('declared protein summary conflicts with input domain')
        if entry['summary_override'] is not None and (not declaration['allow_summary_override'] or not set(entry['summary_override']) <= domain):
            raise ValueError('forbidden summary override')
        fixed = set(project('fixed'))
        if entry['mutation_override'] is not None and not set(entry['mutation_override']) <= authorized - fixed:
            raise ValueError('mutation override outside nonfixed sequence-design domain')
        # Exclusion remains independent of summary; native fixed masks are also
        # checked by the existing analyzer. A null request does not mean [].
        if fixed:
            entry['mutation_override'] = [v for v in (entry['mutation_override'] if entry['mutation_override'] is not None else entry['sequence_design']) if v not in fixed]
        entry['artifact_binding'] = dict(producer_input_id=name, source_pdb_sha256=digest(path))
        policy['inputs'][name] = entry
    return policy


def bind_native_candidates(scopes, native_dir):
    """Enumerate actual pinned writer outputs; never extrapolate requested counts."""
    policy = deepcopy(scopes)
    observed = {name: [] for name in policy['inputs']}
    for path in sorted(Path(native_dir).glob('*.pdb')):
        match = re.fullmatch(r'(.+)_sample(0|[1-9][0-9]*)', path.stem)
        if not match or match.group(1) not in observed:
            raise ValueError('foreign native candidate output')
        observed[match.group(1)].append(path.stem)
    for name, ids in observed.items():
        if not ids:
            raise ValueError('missing native candidate outputs')
        policy['inputs'][name]['artifact_binding']['producer_candidate_ids'] = ids
    _validate_policy(policy)
    for name, entry in policy['inputs'].items():
        _resolve_policy(policy, name, entry['input_domain'])
    return policy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--declaration')
    parser.add_argument('--prepared-dir', default='.')
    parser.add_argument('--scopes')
    parser.add_argument('--native-dir')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.declaration:
        receipts = {path.name.removesuffix('.fampnn_prep.json'): json.loads(path.read_text())
                    for path in Path(args.prepared_dir).glob('*.fampnn_prep.json')}
        result = resolve_declaration(json.loads(Path(args.declaration).read_text()), receipts, args.prepared_dir)
    else:
        result = bind_native_candidates(json.loads(Path(args.scopes).read_text()), args.native_dir)
    Path(args.output).write_text(json.dumps(result, allow_nan=False, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
