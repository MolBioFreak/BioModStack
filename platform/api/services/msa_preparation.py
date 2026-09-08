"""Controller preparation and portable Protenix input delivery.

Internal service boundary, deliberately not a worker-accessible search endpoint.
Remote bundle compilation calls this boundary before transport and stages the
portable directory as mandatory input. Public preparation remains unavailable
without the shared provider configuration and cache boundary.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / 'scripts'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from biomodstack_msa_handoff import digest, package_alignments, resolve_alignments
from biomodstack_msa_policy import apply_msa_policy


def export_protenix_inputs(payload: list, destination: Path, settings: dict, provenance: dict) -> dict:
    from prepare_protenix_msa import iter_protein_chains, all_protein_chains_have_msa
    payload = copy.deepcopy(payload)
    if not all_protein_chains_have_msa(payload):
        raise ValueError('Prepared Protenix input has missing alignments')
    chains = []
    for task, index, chain in iter_protein_chains(payload):
        for role, key in [('paired', 'pairedMsaPath'), ('unpaired', 'unpairedMsaPath')]:
            if chain.get(key):
                chains.append({'chain_id': f'{task}:{index}', 'role': role,
                               'sequence': chain['sequence'], 'source': chain[key]})
                del chain[key]
        # Legacy alignment directories must not leak controller absolute paths.
        if isinstance(chain.get('msa'), dict):
            chain['msa'].pop('precomputed_msa_dir', None)
    manifest = package_alignments(destination, chains=chains, settings=settings, provenance=provenance)
    manifest['model_input'] = payload
    (destination / 'msa-inputs.json').write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return manifest


def materialize_protenix_inputs(source: Path, output_json: Path, settings: dict) -> Path:
    from prepare_protenix_msa import iter_protein_chains
    manifest = json.loads((source / 'msa-inputs.json').read_text())
    payload = copy.deepcopy(manifest['model_input'])
    sequences = {f'{task}:{index}': chain['sequence'] for task, index, chain in iter_protein_chains(payload)}
    paths = resolve_alignments(source, manifest, sequences=sequences, settings=settings)
    for task, index, chain in iter_protein_chains(payload):
        for role, key in [('paired', 'pairedMsaPath'), ('unpaired', 'unpairedMsaPath')]:
            path = paths.get((f'{task}:{index}', role))
            if path is not None:
                chain[key] = str(path)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2))
    return output_json


def prepare_remote_protenix_inputs(params: dict, destination: Path) -> dict:
    """Called by immutable remote bundle compilation before any transport.

    Supplied shared-cache hits are converted on the controller, never delegated
    to a remote cache miss. Cache contents are query-validated by the native adapter.
    """
    import os
    import tempfile
    from services.nextflow import compile_controller_protenix_input
    from prepare_protenix_msa import hydrate_chains_from_shared_cache
    payload = compile_controller_protenix_input(params)
    destination.parent.mkdir(parents=True, exist_ok=True)
    configured = os.environ.get('BMS_MSA_CONTROLLER_CONFIG')
    config = Path(configured) if configured else None
    with tempfile.TemporaryDirectory(prefix='.msa-input-', dir=destination.parent) as scratch:
        scratch = Path(scratch)
        cache = str(params.get('msa_cache_dir') or '')
        from services.msa_provider_setup import selected_provider
        if cache and selected_provider(params) == 'colabfold_api':
            hydrate_chains_from_shared_cache(payload, scratch / 'cache', cache)
        input_json = scratch / 'input.json'
        input_json.write_text(json.dumps(payload))
        system_keys = {'msa_cache_dir', 'msa_local_db', 'protenix_container_path',
                       'protenix_model_dir', 'protenix_download_cache_dir'}
        settings = {key: value for key, value in params.items()
                    if key.startswith(('msa_', 'protenix_', 'colabfold_'))
                    and key not in system_keys}
        return prepare_protenix_inputs(config, input_json, destination, settings)


def prepare_model_msa(*, sequences: list[str], params: dict) -> dict:
    """All model adapters share the same provider client and persistent cache."""
    from biomodstack_msa_api import prepare_msa
    from services.msa_provider_setup import (
        cache_root, credential_file, provider_settings, selected_provider,
    )
    provider = selected_provider(params)
    return prepare_msa(
        sequences=sequences, provider=provider, settings=provider_settings(params),
        cache_root=cache_root(),
        credential_file=credential_file() if provider == "neurosnap_api" else None,
        cache_only=params.get("msa_cache_only") in (True, "true", "1", 1),
    )


def _protenix_paired_headers(data: bytes) -> bytes:
    """Pinned Protenix bd54a05 native row-group header convention.

    colab_request_utils.py:327-336 encodes paired row indices in UniRef
    headers for get_species_ids. These are native opaque pairing groups,
    not claims of biological taxonomy. Raw provider bytes remain cached.
    """
    lines = []
    row = -1
    for line in data.decode('ascii').splitlines():
        if line.startswith('>'):
            row += 1
            if row == 0:
                line = '>query'
            else:
                parts = line[1:].split('\t')
                parts[0] = f'{parts[0]}_{row}/'
                line = '>' + '\t'.join(parts) + f'_{row}'
        lines.append(line)
    return ('\n'.join(lines) + '\n').encode('ascii')


def prepare_protenix_inputs(config_path: Path | None, input_json: Path, destination: Path, settings: dict) -> dict:
    """Package supplied inputs or cached API alignments; no inference install.

    The legacy positional config_path is retained for caller compatibility, not
    used as a fabricated runtime/egress qualification. Provider configuration is
    deployment-owned via the shared setup boundary.
    """
    from prepare_protenix_msa import load_json, all_protein_chains_have_msa, iter_protein_chains
    effective = apply_msa_policy('protenix', settings)
    payload = load_json(input_json)
    if all_protein_chains_have_msa(payload):
        return export_protenix_inputs(payload, destination, effective, {'backend': 'supplied'})
    if effective.get('protenix_use_msa') in (False, 'false', 0):
        raise ValueError('Preparation cannot search for no-MSA requests')
    if effective.get('protenix_msa_backend') in {'none', 'esm'}:
        raise ValueError('Non-search Protenix backend must use its model compiler')
    chains = [chain for _, _, chain in iter_protein_chains(payload)]
    if not chains:
        raise ValueError('MSA preparation requires protein chains')
    result = prepare_model_msa(sequences=[chain['sequence'] for chain in chains], params=effective)
    import tempfile
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.native-pairing-', dir=destination.parent) as scratch:
        # Fill only missing roles; explicitly supplied native data remains unchanged.
        for artifact in result['artifacts']:
            index = artifact['chain_index']
            if type(index) is not int or not 0 <= index < len(chains):
                raise ValueError('MSA provider returned an invalid chain identity')
            key = {'unpaired': 'unpairedMsaPath', 'paired': 'pairedMsaPath'}.get(artifact['role'])
            if key is None:
                raise ValueError('MSA provider returned an invalid alignment role')
            path = Path(artifact['path'])
            if digest(path.read_bytes()) != artifact['sha256']:
                raise ValueError('Cached MSA artifact changed before model packaging')
            if artifact['role'] == 'paired' and result['provider'] == 'colabfold_api':
                converted = Path(scratch) / f'chain-{index}-paired.a3m'
                converted.write_bytes(_protenix_paired_headers(path.read_bytes()))
                path = converted
            if not chains[index].get(key):
                chains[index][key] = str(path)
        return export_protenix_inputs(payload, destination, effective,
            {**result['provenance'], 'backend': result['provider'],
             'request_digest': result['request_digest'], 'cache_hit': result['cache_hit'],
             'paired_conversion': 'protenix-bd54a05-native-row-group-headers-v1'
                 if any(a['role'] == 'paired' for a in result['artifacts']) and result['provider'] == 'colabfold_api' else None})
