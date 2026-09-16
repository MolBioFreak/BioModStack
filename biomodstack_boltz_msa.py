"""File-only Boltz native MSA contract (CSV keys are opaque row groups).

Boltz2 runtime: boltz-community 7ebf1be087d4d61a02234c878402838bf3712d8b,
Boltz-CP parser: 15f9775bc2280cd8a9338feb4d29511c27beaf21,
src/boltz/data/parse/csv.py and data/types.py (signed int32 keys).
No taxonomy annotations are manufactured. CSV key N denotes paired row N
in the provider's ordered per-chain paired alignments; -1 denotes unpaired.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from biomodstack_msa_handoff import validate_a3m


def a3m_rows(data: bytes) -> list[str]:
    rows = []
    for line in data.decode().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('>'):
            rows.append('')
        else:
            rows[-1] += line
    return rows


def validate_boltz_msa(path: Path, sequence: str) -> bytes:
    """Accept native A3M or CSV, requiring an exact query and aligned width."""
    if path.suffix.lower() != '.csv':
        return validate_a3m(path, sequence)
    data = path.read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode()))
    if sorted(reader.fieldnames or []) != ['key', 'sequence']:
        raise ValueError('Boltz CSV requires exactly key,sequence columns')
    rows = list(reader)
    if not rows or rows[0]['sequence'] != sequence:
        raise ValueError('Boltz CSV query identity mismatch')
    for row in rows:
        if None in row or row['key'] is None or row['sequence'] is None:
            raise ValueError('Invalid Boltz CSV row column count')
        key = row['key']
        if key and (not key.lstrip('-').isdigit() or not -1 <= int(key) <= 2147483647):
            raise ValueError('Boltz CSV pairing key must be -1 or a nonnegative int32')
        aligned = ''.join(c for c in row['sequence'] if not c.islower())
        alphabet = 'ARNDCQEGHILKMFPSTWYVXBZUO-'
        if (len(aligned) != len(sequence) or any(c not in alphabet for c in aligned)
                or any(c.upper() not in alphabet or not c.isascii() for c in row['sequence'])):
            raise ValueError('Invalid Boltz CSV alignment row')
    return data


def write_paired_csv(path: Path, unpaired: bytes, paired: bytes, *, deduplicates_paired: bool) -> None:
    """Emit query once, ordered paired hits, then unpaired hits.

    CP unconditionally deduplicates line.replace('-', '').upper(), including
    paired rows. Refuse that precise loss, rather than altering residues or
    silently assigning a different row to a pairing group.
    """
    urows, prows = a3m_rows(unpaired), a3m_rows(paired)
    if not urows or not prows or urows[0] != prows[0]:
        raise ValueError('Paired/unpaired query identities differ')
    visited = {prows[0].replace('-', '').upper()}
    for row in prows[1:]:
        identity = row.replace('-', '').upper()
        if deduplicates_paired and identity in visited:
            raise ValueError('Boltz-CP parse_csv deduplicates paired sequences (including query matches); cannot preserve these pairing groups')
        visited.add(identity)
    with path.open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['key', 'sequence'])
        writer.writerow([-1, urows[0]])
        writer.writerows((index, row) for index, row in enumerate(prows[1:], 1))
        writer.writerows((-1, row) for row in urows[1:])
    validate_boltz_msa(path, urows[0])


def _prepared_manifest(source, sha256, schema):
    import json
    from biomodstack_msa_handoff import digest
    raw = (source / 'msa-inputs.json').read_bytes()
    if not sha256 or digest(raw) != sha256:
        raise ValueError('Prepared Boltz MSA manifest digest mismatch')
    manifest = json.loads(raw)
    if manifest.get('schema') != schema:
        raise ValueError('Prepared Boltz MSA manifest schema mismatch')
    return manifest


def _resolve_native_alignment(source, artifact, sequence):
    from pathlib import PurePosixPath
    from biomodstack_msa_handoff import digest
    if artifact.get('mode') == 'empty':
        return 'empty'
    relative = PurePosixPath(artifact['path'])
    if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative):
        raise ValueError('Prepared Boltz MSA path must be portable and contained')
    path = (source / str(relative)).resolve()
    if not path.is_relative_to(source.resolve()):
        raise ValueError('Prepared Boltz MSA escapes root')
    data = validate_boltz_msa(path, sequence)
    if (artifact.get('mode') != 'artifact' or digest(data) != artifact['sha256']
            or len(data) != artifact['size_bytes']
            or digest(sequence.encode()) != artifact['sequence_sha256']
            or artifact['format'] != ('csv' if path.suffix == '.csv' else 'a3m')):
        raise ValueError('Prepared Boltz MSA artifact identity mismatch')
    return str(path)


def _prepared_boltz_task(source, sha256, task_name):
    manifest = _prepared_manifest(source, sha256, 'bms.boltz-msa-inputs.v1')
    matches = [task for task in manifest['tasks'] if task['name'] == task_name]
    if len(matches) != 1:
        raise ValueError('Prepared Boltz task identity missing or ambiguous')
    return matches[0]


def hydrate_prepared_boltz_task(payload, source, manifest_sha256, *, task_name):
    """Bind already native YAML, retaining every non-MSA field and row identity.

    File-only generated-stage handoff: a task must already exist in the sealed
    roster. This does not schedule children or request alignments at runtime.
    """
    import copy
    source = Path(source)
    task = _prepared_boltz_task(source, manifest_sha256, task_name)
    result = copy.deepcopy(payload)
    proteins = [entry['protein'] for entry in result['sequences'] if 'protein' in entry]
    chains = task['chains']
    identities = [(p['id'] if isinstance(p['id'], list) else [p['id']], p['sequence']) for p in proteins]
    if identities != [(p['id'], p['sequence']) for p in chains]:
        raise ValueError('Prepared Boltz task chain identity mismatch')
    for protein, chain in zip(proteins, chains):
        path = _resolve_native_alignment(source, chain['alignment'], chain['sequence'])
        supplied = protein.get('msa')
        if supplied and supplied != path:
            if supplied == 'empty' or path == 'empty':
                raise ValueError('Supplied native no-MSA conflicts with prepared chain')
            if validate_boltz_msa(Path(supplied), protein['sequence']) != Path(path).read_bytes():
                raise ValueError('Supplied native MSA conflicts with prepared chain')
        protein['msa'] = path
    return result


def hydrate_prepared_boltz_components(payload, source, manifest_sha256, *, task_name):
    """Bind immutable native complex JSON before its existing YAML generator."""
    import copy
    task = _prepared_boltz_task(Path(source), manifest_sha256, task_name)
    expected = task['native_task'].get('components')
    components = payload.get('components')
    def identity(values):
        return [{k: v for k, v in c.items() if k != 'msa_path'} for c in values]
    if expected is None or components is None or identity(expected) != identity(components):
        raise ValueError('Prepared Boltz native component identity mismatch')
    result = copy.deepcopy(payload)
    for chain in task['chains']:
        component = result['components'][chain['component_index']]
        path = _resolve_native_alignment(Path(source), chain['alignment'], chain['sequence'])
        if path == 'empty':
            # Native short-peptide policy owns this sentinel; do not make it a path.
            continue
        supplied = component.get('msa_path')
        original = expected[chain['component_index']].get('msa_path')
        # An unchanged archived reference has already been validated/sealed by
        # the controller; relocation must not read that former host path.
        if supplied and supplied != original and validate_boltz_msa(Path(supplied), chain['sequence']) != Path(path).read_bytes():
            raise ValueError('Supplied component MSA conflicts with prepared chain')
        component['msa_path'] = path
    return result


def resolve_boltz_config(path, *, root=None, manifest_sha256=None):
    """Return derived native config with contained paths, without rewriting input.

    When a sealed package digest is supplied, validate its config and alignment
    hashes as well. Legacy supplied configs retain validation without pretending
    they carry a sealed controller receipt.
    """
    import yaml
    from biomodstack_msa_handoff import digest
    path = Path(path).resolve()
    root = Path(root).resolve() if root is not None else path.parent
    if not path.is_relative_to(root):
        raise ValueError('Boltz config escapes bundle root')
    raw = path.read_bytes()
    payload = yaml.safe_load(raw)
    proteins = [entry['protein'] for entry in payload['sequences'] if 'protein' in entry]
    if manifest_sha256:
        manifest = _prepared_manifest(root, manifest_sha256, 'bms.boltz-cp-msa-inputs.v1')
        records = [record for record in manifest['configs'] if record['path'] == path.relative_to(root).as_posix()]
        if len(records) != 1 or records[0]['sha256'] != digest(raw):
            raise ValueError('Boltz config identity mismatch')
        chains = records[0]['chains']
        if [(p['id'], p['sequence']) for p in proteins] != [(c['id'], c['sequence']) for c in chains]:
            raise ValueError('Boltz config chain identity mismatch')
        for protein, chain in zip(proteins, chains):
            protein['msa'] = _resolve_native_alignment(root, chain['alignment'], protein['sequence'])
    else:
        for protein in proteins:
            value = protein.get('msa')
            if value == 'empty':
                continue
            if not value:
                raise ValueError('MSA-enabled Boltz requires prepared or supplied alignments')
            alignment = Path(value)
            alignment = (alignment if alignment.is_absolute() else path.parent / alignment).resolve()
            if not alignment.is_relative_to(root):
                raise ValueError('Boltz MSA escapes bundle root')
            validate_boltz_msa(alignment, protein['sequence'])
            protein['msa'] = str(alignment)
    return payload
