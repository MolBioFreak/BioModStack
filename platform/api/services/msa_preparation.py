"""Controller preparation and portable Protenix input delivery.

Internal service boundary, deliberately not a worker-accessible search endpoint.
Remote bundle compilation calls this boundary before transport and stages the
portable directory as mandatory input. Public preparation remains unavailable
without deployment-owned controller qualification and an installed runtime pin.
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

from biomodstack_msa_controller import PUBLIC_HOST, prepare
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


def qualify_protenix_controller_runtime(config_path: Path | None) -> dict:
    """Check deployment-owned in-process native adapter pin before queue entry.

    A SIF or a worker environment is not an in-process controller runtime.
    No installation, subprocess, provider import or public request is performed.
    """
    import importlib.metadata
    import importlib.util
    try:
        if config_path is None or not config_path.is_absolute():
            raise ValueError('BMS_MSA_CONTROLLER_CONFIG must be an absolute deployment-owned path')
        config = json.loads(config_path.read_text())
        pin = config['protenix_runtime']
        version = importlib.metadata.version('protenix')
        spec = importlib.util.find_spec('runner.msa_search')
        if (not isinstance(pin, dict) or not pin.get('version') or
                version != pin['version'] or spec is None or not spec.origin or
                digest(Path(spec.origin).read_bytes()) != pin.get('msa_search_sha256')):
            raise ValueError('native adapter version/digest does not match deployment pin')
        return {'version': version, 'msa_search_sha256': pin['msa_search_sha256']}
    except (OSError, KeyError, ValueError, ImportError, TypeError) as exc:
        raise RuntimeError('Controller Protenix MSA runtime unavailable or unpinned: configure '
                           'BMS_MSA_CONTROLLER_CONFIG with protenix_runtime.version and '
                           'protenix_runtime.msa_search_sha256 for the installed in-process '
                           'runner.msa_search. Worker public-API fallback is forbidden.') from exc


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
        if cache:
            hydrate_chains_from_shared_cache(payload, scratch / 'cache', cache)
        input_json = scratch / 'input.json'
        input_json.write_text(json.dumps(payload))
        system_keys = {'msa_cache_dir', 'msa_local_db', 'protenix_container_path',
                       'protenix_model_dir', 'protenix_download_cache_dir'}
        settings = {key: value for key, value in params.items()
                    if key.startswith(('msa_', 'protenix_', 'colabfold_'))
                    and key not in system_keys}
        return prepare_protenix_inputs(config, input_json, destination, settings)


def prepare_protenix_inputs(config_path: Path | None, input_json: Path, destination: Path, settings: dict) -> dict:
    """Prepare using the real model adapter on the bound controller computer.

    Supplied complete alignments bypass external preparation. No-MSA requests
    remain the responsibility of the existing model compiler, not this service.
    """
    from prepare_protenix_msa import load_json, all_protein_chains_have_msa, prepare_with_colabfold_api
    effective = apply_msa_policy('protenix', settings)
    payload = load_json(input_json)
    if all_protein_chains_have_msa(payload):
        return export_protenix_inputs(payload, destination, effective, {'backend': 'supplied'})
    if effective.get('msa_cache_only') in (True, 'true', 1) or effective.get('protenix_use_msa') in (False, 'false', 0):
        raise ValueError('Preparation cannot search for cache-only or no-MSA requests')
    if effective.get('protenix_msa_backend') in {'none', 'esm'}:
        raise ValueError('Non-search Protenix backend must use its model compiler')
    runtime = qualify_protenix_controller_runtime(config_path)
    supported_search_keys = {'msa_provider', 'protenix_msa_backend', 'protenix_use_msa', 'msa_cache_only'}
    unsupported = sorted(key for key in effective
                         if ('msa' in key.lower() or key.startswith('colabfold_'))
                         and key not in supported_search_keys)
    if unsupported:
        raise ValueError('Controller Protenix adapter has no execution mapping for MSA settings: '
                         + ', '.join(unsupported))
    request = {'model': 'protenix', 'input_sha256': digest(input_json.read_bytes()), 'settings': effective,
               'controller_runtime': runtime, 'destination': str(destination.resolve())}

    def operation():
        import tempfile
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Provider scratch and absolute model-native paths are not delivery inputs.
        with tempfile.TemporaryDirectory(prefix='.msa-provider-', dir=destination.parent) as scratch:
            work = Path(scratch)
            prepared = prepare_with_colabfold_api(input_json, work / 'prepared.json', work, PUBLIC_HOST)
            return export_protenix_inputs(load_json(prepared), destination, effective,
                                         {'backend': 'colabfold_api', 'service': PUBLIC_HOST,
                                          'controller_runtime': runtime, 'provider_database_version': None})

    result = prepare(config_path, request, operation)
    # Completed replay is not proof that local files remain intact.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='.msa-verify-', dir=destination.parent) as scratch:
        materialize_protenix_inputs(destination, Path(scratch) / 'verified.json', effective)
    return result
