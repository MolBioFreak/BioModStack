"""Portable, content-verified MSA inputs. No network or search operations."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_a3m(path: Path, sequence: str) -> bytes:
    """Require an exact query identity; never repair/replace a mismatched query."""
    data = gzip.decompress(path.read_bytes()) if str(path).endswith('.gz') else path.read_bytes()
    rows = []
    current = None
    for line in data.decode('utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('>'):
            if current is not None:
                rows.append(current)
            current = ''
        elif current is None:
            raise ValueError('MSA sequence before FASTA header')
        else:
            current += line
    if current is not None:
        rows.append(current)
    if not rows or rows[0] != sequence:
        raise ValueError('MSA query identity does not match the requested sequence')
    for row in rows:
        aligned = ''.join(c for c in row if not c.islower() and c != '.')
        if len(aligned) != len(sequence) or any(not (c.isascii() and (c.isalpha() or c in '-.')) for c in row):
            raise ValueError('Invalid A3M alignment row')
    return data


def package_alignments(root: Path, *, chains: list[dict], settings: dict, provenance: dict) -> dict:
    """Copy role-specific A3Ms into a portable input directory.

    Each chain carries chain_id, sequence, role (paired/unpaired), and source.
    Paired rows are preserved byte-for-byte, not generated from unpaired hits.
    Caller owns model-specific paired-chain compatibility validation.
    """
    root.mkdir(parents=True, exist_ok=True)
    artifacts = []
    identities = set()
    for chain in chains:
        identity = (chain['chain_id'], chain['role'])
        if identity in identities or chain['role'] not in {'paired', 'unpaired'}:
            raise ValueError('Duplicate chain role or unsupported MSA role')
        identities.add(identity)
        data = validate_a3m(Path(chain['source']), chain['sequence'])
        sha = digest(data)
        relative = f'alignments/{sha}.a3m'
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        artifacts.append({'kind': 'msa_a3m', 'path': relative, 'sha256': sha,
                          'sequence_sha256': digest(chain['sequence'].encode()),
                          'chain_id': chain['chain_id'], 'role': chain['role']})
    manifest = {'schema': 'bms.msa-inputs.v1', 'settings': settings,
                'provenance': provenance, 'artifacts': artifacts}
    (root / 'msa-inputs.json').write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return manifest


def resolve_alignments(root: Path, manifest: dict, *, sequences: dict, settings: dict) -> dict:
    """Validate transferred files and materialize paths under the worker root."""
    if manifest.get('schema') != 'bms.msa-inputs.v1' or manifest.get('settings') != settings:
        raise ValueError('MSA manifest schema/settings mismatch')
    resolved = {}
    root = root.resolve()
    for artifact in manifest['artifacts']:
        relative = PurePosixPath(artifact['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative):
            raise ValueError('MSA artifact path must be portable and contained')
        path = (root / str(relative)).resolve()
        if not path.is_relative_to(root):
            raise ValueError('MSA artifact escapes input root')
        chain_id, role = artifact['chain_id'], artifact['role']
        if artifact['kind'] != 'msa_a3m' or role not in {'paired', 'unpaired'} or (chain_id, role) in resolved:
            raise ValueError('Invalid or duplicate MSA artifact identity')
        sequence = sequences[chain_id]
        data = validate_a3m(path, sequence)
        if digest(data) != artifact['sha256'] or digest(sequence.encode()) != artifact['sequence_sha256']:
            raise ValueError('MSA artifact digest/sequence mismatch')
        resolved[chain_id, role] = path
    if set(sequences) != {chain for chain, _role in resolved}:
        raise ValueError('MSA manifest does not cover all requested chains')
    return resolved


def _protenix_task_identity(task: dict) -> list:
    """Ordered native entity identities; task names/seeds are replica metadata."""
    identities = []
    for wrapper in task.get('sequences', []):
        if 'proteinChain' not in wrapper:
            identities.append(wrapper)
            continue
        chain = wrapper['proteinChain']
        identity = {key: value for key, value in chain.items()
                    if key not in {'pairedMsaPath', 'unpairedMsaPath', 'msa',
                                   'templatesPath', 'templatePath'}}
        identity.setdefault('count', 1)
        identities.append({'proteinChain': identity})
    return identities


def hydrate_prepared_protenix_task(payload: list, source: Path, manifest_sha256: str) -> list:
    """Bind a native task or batch to the sealed controller roster, without network I/O.

    Only alignment paths change. Ordered native entity IDs/counts/sequences and
    nonprotein entities must match. Batch subsets require exact native task names;
    duplicate names/rosters are ambiguous, never a first-match or sequence join.
    A single prepared task permits replica renaming after exact entity matching.
    """
    import copy
    raw = (source / 'msa-inputs.json').read_bytes()
    if not manifest_sha256 or digest(raw) != manifest_sha256:
        raise ValueError('Prepared MSA manifest digest mismatch')
    manifest = json.loads(raw)
    prepared = manifest['model_input']
    sequences = {f'{task_index}:{chain_index}': wrapper['proteinChain']['sequence']
                 for task_index, task in enumerate(prepared)
                 for chain_index, wrapper in enumerate(task.get('sequences', []))
                 if 'proteinChain' in wrapper}
    paths = resolve_alignments(source, manifest, sequences=sequences, settings=manifest['settings'])
    result = copy.deepcopy(payload)
    used = set()
    for task in result:
        candidates = [i for i, native in enumerate(prepared)
                      if _protenix_task_identity(task) == _protenix_task_identity(native)
                      and (len(prepared) == 1 or
                           (task.get('name') and task.get('name') == native.get('name')))]
        if len(candidates) != 1 or candidates[0] in used:
            raise ValueError('Prepared MSA task identity is missing, ambiguous, or does not match controller input')
        index = candidates[0]
        used.add(index)
        for chain_index, wrapper in enumerate(task.get('sequences', [])):
            if 'proteinChain' not in wrapper:
                continue
            chain = wrapper['proteinChain']
            for role, key in [('paired', 'pairedMsaPath'), ('unpaired', 'unpairedMsaPath')]:
                path = paths.get((f'{index}:{chain_index}', role))
                if chain.get(key):
                    supplied = validate_a3m(Path(chain[key]), chain['sequence'])
                    if path is None or supplied != path.read_bytes():
                        raise ValueError('Supplied native MSA conflicts with prepared chain/role')
                elif path is not None:
                    chain[key] = str(path)
    return result
