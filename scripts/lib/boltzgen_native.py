"""Bounded BoltzGen installed-source scalar contract; never imports the model.

The supported identity is actual CLI + installed Python source + distribution
metadata bytes, NOT the Git reference alone. No inference/checkpoint claim.
"""
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil

CONTRACT = json.loads(Path(__file__).with_name('boltzgen_native_source.json').read_text())
KEYS = ('design_ptm', 'affinity_probability', 'filter_rmsd')

PROTOCOLS = ('protein-anything', 'protein-small_molecule', 'peptide-anything',
             'nanobody-anything', 'antibody-anything')


def protocol_from_entities(entities):
    """Wrapper's native top-level decision, including per-entity precedence."""
    kinds = set()
    for entity in entities:
        for kind in ('dna', 'rna', 'ligand', 'protein'):
            if kind in entity:
                kinds.add(kind)
                break
    if kinds & {'dna', 'rna'}:
        return 'protein-anything'
    return 'protein-small_molecule' if 'ligand' in kinds else 'protein-anything'


def selected_checkpoint_members(protocol, mode='both', skip_inverse_folding=False):
    """Installed BinderDesignPipeline/ARTIFACTS placement; no model import.

    Keep diverse then adherence and leave equal fractions to the native CLI.
    """
    if protocol not in PROTOCOLS or mode not in ('both', 'diverse', 'adherence'):
        raise ValueError('Unresolved native BoltzGen protocol/checkpoint selection')
    variants = ('diverse', 'adherence') if mode == 'both' else (mode,)
    members = [f'boltzgen1_{variant}.ckpt' for variant in variants]
    if not skip_inverse_folding:
        members.append('boltzgen1_ifold.ckpt')
    members.append('boltz2_conf_final.ckpt')
    if protocol == 'protein-small_molecule':
        members.append('boltz2_aff.ckpt')
    return members


class ProtocolMetadata(dict):
    """In-process compiler-derived metadata, not a JSON/request flag.

    Pass the original object in metadata_settings['_boltzgen_protocol_metadata'].
    Serialized receipts are evidence only and must be recomputed on recompilation.
    """


def protocol_request_identity(params):
    return digest(json.dumps({key: value for key, value in params.items()
                              if key.startswith('boltzgen_')}, sort_keys=True).encode())


def preview_protocol_metadata(params, *, preparation_inputs=None, allowed_input_roots=()):
    """Pure native preparation preview on validated, normalized compiler inputs.

    No generated files, SDK execution or providers. Declared file inputs are
    bounded read-only snapshots under compiler-approved roots; missing/unsafe
    native inputs remain unresolved. No process-global output redirection.
    """
    import argparse
    from scripts import prep_boltzgen as prep

    parser = prep.preparation_parser()
    values = {action.dest: action.default for action in parser._actions
              if action.dest not in ('help', 'output_yaml')}
    if preparation_inputs is None:
        for key in values:
            source = 'boltzgen_target_pdb_path' if key == 'target_pdb' else 'boltzgen_' + key
            if params.get(source) not in (None, ''):
                values[key] = params[source]
    else:
        if set(preparation_inputs) - set(values):
            raise ValueError('Unknown native preparation input')
        values.update(preparation_inputs)
    requested = params.get('boltzgen_protocol') or 'auto'
    values['output_yaml'] = 'boltzgen_input.yaml'
    identity = {name: digest(path.read_bytes()) for name, path in (
        ('scripts/prep_boltzgen.py', Path(prep.__file__)),
        ('scripts/lib/boltzgen_native.py', Path(__file__)))}
    result = ProtocolMetadata(schema=1, state='unresolved', requested_protocol=requested,
        request_identity=protocol_request_identity(params),
        effective_protocol=None, source_identity=identity,
        config_identity={'authority': 'scripts/prep_boltzgen.py:build_design_config',
                         'generated_input': 'boltzgen_input.yaml',
                         'preparation_sha256': digest(json.dumps(values, sort_keys=True).encode())},
        selected_checkpoint_members=[], unresolved_inputs=[])
    if requested not in ('auto', *PROTOCOLS):
        result['unresolved_inputs'] = ['boltzgen_protocol']
        return result
    if requested == 'auto':
        from scripts.lib.portable_inputs import _contained, MAX_DOCUMENT_BYTES
        import stat
        input_texts, input_identities = {}, {}
        for name in ('input_pdb', 'ligand_pdb', 'dna_structure', 'target_pdb'):
            value = values[name]
            if not value:
                continue
            try:
                # Reuse the shared native-input containment authority. Roots are
                # supplied only by the compiler, never by scientific params.
                path = _contained(value, [Path(root).resolve() for root in allowed_input_roots])
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_DOCUMENT_BYTES:
                        raise ValueError('Native preparation input must be a bounded regular file')
                    raw = stream.read(MAX_DOCUMENT_BYTES + 1)
                    after = os.fstat(stream.fileno())
                if len(raw) > MAX_DOCUMENT_BYTES or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('Native input changed during preview')
                input_texts[str(value)] = raw.decode()
                input_identities[name] = {'path': str(path), 'sha256': digest(raw), 'size_bytes': len(raw)}
            except (OSError, ValueError) as exc:
                result['unresolved_inputs'] = [name]
                result['reason'] = type(exc).__name__ + ': ' + str(exc)
                return result
        result['config_identity']['input_files'] = input_identities
        try:
            config = prep.build_design_config(argparse.Namespace(**values), preview=True, input_texts=input_texts)
        except prep.UnresolvedPreparationInput as exc:
            result['unresolved_inputs'] = exc.inputs
            return result
        except (ValueError, TypeError, KeyError) as exc:
            result['unresolved_inputs'] = ['invalid_native_preparation:' + type(exc).__name__]
            return result
        import yaml
        result['config_identity']['yaml_sha256'] = digest(
            yaml.dump(config, default_flow_style=False).encode())
        protocol = protocol_from_entities(config['entities'])
    else:
        protocol = requested
    try:
        members = selected_checkpoint_members(protocol, params.get('boltzgen_checkpoint_mode') or 'both',
                                               bool(params.get('boltzgen_skip_inverse_folding')))
    except ValueError:
        result['unresolved_inputs'] = ['boltzgen_checkpoint_mode']
        return result
    result.update(state='ok', effective_protocol=protocol, selected_checkpoint_members=members)
    return result


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unavailable_identity(reason='missing_producer_identity'):
    return {'schema': 1, 'state': 'unavailable', 'reason_code': reason}


def observe_source(cli=None, *, root=Path('/')):
    """Read the CLI's ordinary installed package, without executing its Python.

    root is only a filesystem prefix for static inspection/software fixtures.
    Reject alternate CLI, editable installs, source additions and changed bytes.
    """
    root = Path(root)
    cli = Path(cli or shutil.which('boltzgen') or '/nonexistent')
    expected_cli = root / 'opt/venv/bin/boltzgen'
    if cli.absolute() != expected_cli.absolute():
        return unavailable_identity('unsupported_cli_location')
    if root == Path('/') and os.environ.get('PYTHONPATH'):
        return unavailable_identity('unverified_pythonpath')
    try:
        package = root / 'opt/venv/lib/python3.11/site-packages/boltzgen'
        expected = {p for p in CONTRACT['files'] if '/site-packages/boltzgen/' in p}
        observed = {p.relative_to(root).as_posix() for p in package.rglob('*.py')}
        if observed != expected:
            return unavailable_identity('unsupported_source_inventory')
        hashes = {}
        for name in CONTRACT['files']:
            path = root / name
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root):
                return unavailable_identity('unverified_source_symlink')
            hashes[name] = digest(path.read_bytes())
        # Version is read from the installed METADATA, not stamped from a pin.
        metadata = (root / 'opt/venv/lib/python3.11/site-packages/boltzgen-0.2.0.dist-info/METADATA').read_text()
        version = next(line.split(': ', 1)[1] for line in metadata.splitlines() if line.startswith('Version: '))
        identity = {'schema': 1, 'state': 'ok', 'reason_code': None,
                    'package_version': version, 'files': hashes,
                    'source_sha256': digest(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()),
                    'reference_commit': CONTRACT['reference_commit']}
        return identity if supported(identity) else unavailable_identity('unsupported_source_bytes')
    except (OSError, ValueError, StopIteration):
        return unavailable_identity('missing_installed_source')


def supported(identity):
    return isinstance(identity, dict) and identity == {
        'schema': 1, 'state': 'ok', 'reason_code': None, **CONTRACT,
    }


def retain_source(path, output_dir, candidate, dialect, producer_identity=None, *, native_id=None):
    """Retain exact source bytes beside metadata, on the existing artifact channel."""
    path = Path(path)
    raw = path.read_bytes()
    name = f'native_{candidate}.{dialect}'
    (Path(output_dir) / name).write_bytes(raw)
    return {'candidate_id': candidate, 'native_id': native_id or path.stem,
            'dialect': dialect, 'artifact': {'path': name, 'sha256': digest(raw)},
            'producer_identity': producer_identity or unavailable_identity()}


def csv_candidate_identity(row):
    """CSV aliases use only the native optional terminal .cif suffix.

    No basename, rank, whitespace, or case rewriting can reconcile identities.
    Validate every supplied alias before any source retention or row selection.
    """
    aliases = [row[key] for key in ('file_name', 'id') if row.get(key) not in (None, '')]
    if not aliases or any(not isinstance(value, str) for value in aliases):
        raise ValueError('CSV candidate identity missing')
    normalized = {value.removesuffix('.cif') for value in aliases}
    if len(normalized) != 1 or not next(iter(normalized)):
        raise ValueError('CSV candidate identity aliases disagree')
    return next(iter(normalized))


def metric_records(source, raw, *, candidate_id, document_id='primary'):
    """Reconstruct small records from retained native bytes; no numeric fallback.

    NPZ: scalar/single-sample confidence only, never an invented sample mean.
    CSV: exact native row, including native Filter's filter_rmsd column.
    """
    identity = source.get('producer_identity')
    valid_producer = supported(identity)
    version = (f"boltzgen:{identity['package_version']}:sha256:{identity['source_sha256']}"
               if valid_producer else 'unavailable')
    values = {}
    parse_error = None
    dialect = source['dialect']
    try:
        if dialect == 'npz':
            import numpy as np
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                for name in ('design_ptm', 'affinity_probability_binary1'):
                    if name in archive:
                        value = archive[name]
                        values[name] = value.item() if value.size == 1 else 'non_scalar'
        elif dialect == 'csv':
            reader = csv.DictReader(io.StringIO(raw.decode()))
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError('duplicate_csv_columns')
            rows = [r for r in reader if csv_candidate_identity(r) == source['native_id']]
            if len(rows) != 1:
                raise ValueError('ambiguous_native_candidate')
            for name in ('design_ptm', 'affinity_probability_binary1', 'filter_rmsd'):
                val = rows[0].get(name)
                if val not in (None, ''):
                    try:
                        values[name] = float(val)
                    except (ValueError, TypeError):
                        values[name] = val
        else:
            raise ValueError('unsupported_native_dialect')
    except (ValueError, KeyError, TypeError, OSError, EOFError):
        parse_error = 'invalid_native_source'
    records = {}
    for key in KEYS:
        native_key = 'affinity_probability_binary1' if key == 'affinity_probability' else key
        value = values.get(native_key)
        state, reason = 'ok', None
        if not valid_producer:
            state, reason = 'unavailable', 'missing_or_unsupported_producer_identity'
        elif parse_error:
            state, reason = 'invalid', parse_error
        elif key == 'filter_rmsd' and type(source.get('filter_from_inverse_folded')) is not bool:
            state, reason = 'unavailable', 'unknown_native_alignment_scope'
        elif value is None:
            state, reason = 'unavailable', 'missing_native_metric'
        elif type(value) not in (int, float):
            state, reason = 'invalid', 'not_scalar_real'
        elif not math.isfinite(value):
            state, reason = 'invalid', 'nonfinite'
        elif value < 0 or (key != 'filter_rmsd' and value > 1):
            state, reason = 'invalid', 'outside_domain'
        scopes = {'design_ptm': 'native_design_chain_tokens',
                  'affinity_probability': 'native_affinity_binary1_complex',
                  'filter_rmsd': 'native_filter_complex_alignment'}
        if type(source.get('filter_from_inverse_folded')) is bool:
            scopes['filter_rmsd'] = ('native_refolded_complex_backbone' if source['filter_from_inverse_folded']
                                     else 'native_refolded_complex_allatom')
        records[key] = {'metric_key': key, 'unit': 'angstrom' if key == 'filter_rmsd' else 'fraction',
                        'direction': 'lower_is_better' if key == 'filter_rmsd' else 'higher_is_better',
                        'scope': scopes[key], 'producer_version': version,
                        'derivation_version': f'bms-boltzgen-native-scalar-v1:{dialect}',
                        'state': state, 'value': float(value) if state == 'ok' else None,
                        'reason_code': reason,
                        'source': {'artifact_sha256': digest(raw), 'candidate_id': candidate_id,
                                   'document_id': document_id}}
    return records
