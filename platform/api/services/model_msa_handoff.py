"""Controller-side native MSA packaging; provider I/O belongs to msa_preparation.

No model runtime or HTTP dependency is imported here. Supplied alignments and
explicit no-MSA modes never trigger a provider request.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from biomodstack_msa_handoff import digest
from biomodstack_boltz_msa import a3m_rows, validate_boltz_msa, write_paired_csv
from biomodstack_msa_handoff import validate_a3m


def generated_msa_service_supported(service) -> bool:
    """Read-only resolver for the generated native consumer actually connected."""
    get = service.get if isinstance(service, dict) else lambda key: getattr(service, key, None)
    return (get('logical_id') == 'protenix:generated_msa'
            and get('state') == 'planned_from_generated_candidates'
            and get('provider') in {'colabfold_api', 'neurosnap_api'}
            and bool({'modules/antibody_batch.nf:BatchProtenixValidation',
                      'modules/protenix.nf:ProtenixFromComplex',
                      'modules/conformational_mapping_protenix.nf:CanonicalProtenixEnsemble',
                      'modules/confornets_experimental.nf:RunConforNets',
                      'modules/conformational_mapping_confornets.nf:RunCanonicalConforNets'}
                     .intersection((get('authority') or '').split('; '))))


def generated_protenix_request(payload: list) -> list:
    """Validate the native generated roster without changing names/chains/science.

    This boundary carries generated sequences, not worker filesystem authority.
    Supplied alignments continue through the existing prepared-input path.
    """
    from prepare_protenix_msa import build_native_protenix_input, iter_protein_chains
    native = build_native_protenix_input(seeds=[], native_payload=payload)
    chains = list(iter_protein_chains(native))
    if not chains:
        raise ValueError('Generated MSA roster requires protein chains')
    for _, _, chain in chains:
        if any(chain.get(key) for key in ('pairedMsaPath', 'unpairedMsaPath', 'msa')):
            raise ValueError('Generated service cannot read worker supplied alignment paths')
        sequence = chain.get('sequence')
        if not isinstance(sequence, str) or not sequence or not sequence.isascii() or not sequence.isalpha() or not sequence.isupper():
            raise ValueError('Generated MSA requires exact native protein sequences')
    return native


def prepare_generated_msa(request: dict, destination: Path) -> str:
    """Controller-only adapter for an already declared native external service."""
    from component_runtime import canonical_bytes, digest as request_digest
    from services.msa_preparation import prepare_protenix_inputs
    import tempfile
    identity = request.get('request_id')
    original = {key: value for key, value in request.items() if key != 'request_id'}
    if not identity or request_digest(original) != identity:
        raise ValueError('Generated MSA request identity mismatch')
    service = request['service']
    if not generated_msa_service_supported(service):
        raise ValueError('Unsupported generated native service')
    payload = generated_protenix_request(request['native_input'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.generated-roster-') as scratch:
        input_json = Path(scratch) / 'input.json'
        input_json.write_bytes(canonical_bytes(payload))
        manifest = prepare_protenix_inputs(None, input_json, destination, service['settings_json'])
    manifest['external_service_request_id'] = identity
    path = destination / 'msa-inputs.json'
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return digest(path.read_bytes())


def accept_generated_msa(runtime, request_id: str, manifest_sha256: str) -> dict:
    """Verify delivered bytes and native identities before ledger publication."""
    from component_runtime import ResultReference, file_identity
    from biomodstack_msa_handoff import hydrate_prepared_protenix_task
    from biomodstack_msa_policy import apply_msa_policy
    import re
    if not re.fullmatch('[0-9a-f]{64}', manifest_sha256):
        raise ValueError('Generated MSA manifest digest is invalid')
    request = runtime.external_service(request_id)
    source = runtime.artifact_root / 'external-services' / request_id / manifest_sha256
    manifest_path = source / 'msa-inputs.json'
    manifest = json.loads(manifest_path.read_bytes())
    if (manifest.get('external_service_request_id') != request_id
            or manifest.get('settings') != apply_msa_policy('protenix', request['service']['settings_json'])
            or manifest.get('model_input') != request['native_input']):
        raise ValueError('Generated MSA roster/settings/request binding conflicts')
    hydrate_prepared_protenix_task(request['native_input'], source, manifest_sha256)
    sha, size = file_identity(manifest_path)
    reference = ResultReference(request_id, str(manifest_path.relative_to(runtime.artifact_root)), sha, size,
                                'bms.msa-inputs.v1')
    runtime.complete_external_service(request_id, reference)
    return runtime.external_service(request_id)['result']


async def await_controller_service_operation(task, check_fence, *, stop=None):
    """Join this operation on cancellation; a stopped ticket is not a refund."""
    import asyncio
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.2)
            await check_fence()
        result = await asyncio.shield(task)
        await check_fence()
        return result
    except BaseException:
        if stop is not None:
            stop.set()  # existing provider polling interruption/ticket authority
        else:
            task.cancel()  # transport owns process-group stop and join
        cleanup = asyncio.gather(task, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        raise


async def prepare_generated_msa_on_controller(request, destination, check_fence):
    import asyncio
    import threading
    from biomodstack_msa_api import preparation_stop_scope
    await check_fence()
    stop = threading.Event()
    with preparation_stop_scope(stop):
        task = asyncio.create_task(asyncio.to_thread(prepare_generated_msa, request, destination))
    # Preserve PendingMSA's sanitized provider/ticket/backoff metadata for the
    # reconciliation owner to persist. A bare None loses the provider deadline.
    return await await_controller_service_operation(task, check_fence, stop=stop)


def enabled(value, default=False):
    return default if value is None else str(value).lower() in {'true', '1', 'yes', 'on'}


def native_alignments(result: dict, sequences: list[str], destination: Path, *, model_id: str = 'boltz2') -> list[Path]:
    """Verify cache artifacts; emit A3M or Boltz CSV with shared row-group keys.

    Paired role rows are already ordered by the shared provider. All chains
    must carry the same paired depth; never infer groups from FASTA labels.
    """
    paths = {}
    for artifact in result['artifacts']:
        index, role = artifact['chain_index'], artifact['role']
        if type(index) is not int or not 0 <= index < len(sequences):
            raise ValueError('Provider artifact chain index is invalid')
        if (index, role) in paths or role not in {'paired', 'unpaired'}:
            raise ValueError('Duplicate or invalid provider artifact role')
        source = Path(artifact['path'])
        if digest(source.read_bytes()) != artifact['sha256']:
            raise ValueError('Shared MSA cache artifact digest mismatch')
        data = validate_a3m(source, sequences[index])
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f'chain-{index}-{role}.a3m'
        target.write_bytes(data)
        paths[index, role] = target.resolve()
    if any((index, 'unpaired') not in paths for index in range(len(sequences))):
        raise ValueError('Provider result lacks an unpaired alignment for a requested chain')
    (destination / 'provider-provenance.json').write_text(json.dumps({
        key: result.get(key) for key in ('provider', 'request_digest', 'provenance', 'cache_hit')
    }, sort_keys=True, indent=2))
    paired = {i: a3m_rows(p.read_bytes()) for (i, role), p in paths.items() if role == 'paired'}
    if paired and (len(paired) != len(sequences) or len({len(rows) for rows in paired.values()}) != 1):
        raise ValueError('Paired alignments require equal row counts for every requested chain')
    if any(len(rows) > 1 for rows in paired.values()):
        outputs = []
        for index in range(len(sequences)):
            target = destination / f'chain-{index}.csv'
            write_paired_csv(target, paths[index, 'unpaired'].read_bytes(),
                             paths[index, 'paired'].read_bytes(),
                             deduplicates_paired=model_id == 'boltz_cp_experimental')
            outputs.append(target.resolve())
        (destination / 'pairing-groups.json').write_text(json.dumps({
            'schema': 'bms.boltz-row-groups.v1', 'key_semantics': 'provider-paired-row-index-not-taxonomy',
            'chain_indices': list(range(len(sequences))),
            'paired_rows': len(next(iter(paired.values()))) - 1,
        }, sort_keys=True, indent=2))
        return outputs
    return [paths[index, 'unpaired'] for index in range(len(sequences))]


def prepare_launch_msa(model_id: str, params: dict, destination: Path, *, native_invocation=None, roster=None) -> dict:
    """Return native launch parameters, calling the shared controller at most once.

    Invoked by BMS launch preparation before immutable bundle/input sealing.
    Protenix supplies its once-compiled native input roster here; its finalized
    invocation is reused. destination must be in managed input/result storage.
    """
    model_id = model_id.lower()
    effective = copy.deepcopy(params)
    if native_invocation is not None and model_id in {'protenix', 'boltz2', 'boltz_cp_experimental'}:
        if native_invocation.model_id != model_id:
            raise ValueError('MSA native invocation model mismatch')
        effective.update(copy.deepcopy(native_invocation.native_parameters))
    if model_id == 'protenix':
        if not enabled(effective.get('protenix_use_msa'), True) or effective.get('protenix_msa_backend') in {'none', 'esm'}:
            return effective
        from services.msa_preparation import prepare_remote_protenix_inputs
        # Compiled complex/batch products take precedence in the shared native
        # adapter. Preserve their paths/names and the caller's request object.
        effective.setdefault('sequence_input', effective.get('sequence', ''))
        prepare_remote_protenix_inputs(effective, destination)
        effective['protenix_prepared_msa_dir'] = str(destination.resolve())
        effective['protenix_prepared_msa_sha256'] = digest((destination / 'msa-inputs.json').read_bytes())
        return effective
    if model_id not in {'boltz2', 'boltz_cp_experimental', 'esmfold2', 'esmfold2_experimental', 'rf3'}:
        return effective
    if model_id == 'rf3':
        if enabled(params.get('rf3_use_msa')):
            raise ValueError('RF3 has no executable prediction workflow in this source revision (structure_prediction rejects pred_method=rf3). Native RF3 accepts per-component msa_path A3M with TaxID= pairing, not separate paired A3M or Boltz CSV; see docs/Native_MSA_Consumer_Contracts.md')
        return effective
    is_boltz = model_id in {'boltz2', 'boltz_cp_experimental'}
    if (model_id == 'boltz_cp_experimental'
            and effective.get('bcp_input_format', 'config_files') == 'config_files'
            and (effective.get('bcp_input_path') or effective.get('input_path'))):
        fold_cp_config_proteins(Path(effective.get('bcp_input_path') or effective['input_path']))
    if is_boltz and not enabled(params.get('boltz_use_msa')):
        return effective
    if not is_boltz:
        return prepare_esmfold2_msa(effective, destination)
    if model_id == 'boltz2' and (roster is not None or native_invocation is not None
            or effective.get('complex_json_path') or effective.get('sequence_batch_json_path')
            or ':' in str(effective.get('sequence_input') or effective.get('sequence') or '')):
        return prepare_boltz_roster(effective, destination, roster=roster)
    components = effective.get('complex_components')
    if isinstance(components, str):
        components = json.loads(components)
    proteins = [c for c in (components or []) if c.get('type', 'protein') in {'protein', 'peptide'}]
    if model_id == 'boltz_cp_experimental' and (params.get('bcp_input_path') or params.get('input_path')):
        # Native config bundles need their actual chain roster, not a sequence guess.
        return prepare_boltz_cp_bundle(effective, destination)
    if proteins:
        missing = [c for c in proteins if not c.get('msa_path')]
        for c in proteins:
            if c.get('msa_path'):
                validate_boltz_msa(Path(c['msa_path']), c['sequence'])
    else:
        supplied = params.get('msa_path') or params.get('esmf_msa_path')
        sequence = str(params.get('sequence_input') or params.get('sequence') or '').strip()
        if supplied:
            validate_boltz_msa(Path(supplied), sequence)
            return effective
        missing = [{'sequence': sequence}]
    if missing:
        _validate_partial_group(proteins, missing, effective)
        sequences = [str(c['sequence']).strip() for c in missing]
        if any(not s or ':' in s for s in sequences):
            raise ValueError('MSA preparation requires explicit per-chain protein sequences')
        from services.msa_preparation import prepare_model_msa
        result = prepare_model_msa(sequences=sequences, params=effective)
        paths = native_alignments(result, sequences, destination, model_id=model_id)
        for component, path in zip(missing, paths):
            component['msa_path'] = str(path)
    if proteins:
        effective['complex_components'] = components
    else:
        effective['msa_path' if is_boltz else 'esmf_msa_path'] = missing[0]['msa_path']
    return effective


def prepare_esmfold2_msa(params: dict, destination: Path) -> dict:
    """Fill missing native protein alignments through the shared hosted owner.

    Supplied inputs retain their original identity. Generated bytes receive a
    controller-owned scope/hash receipt; the owning Job persists it separately
    from requested science before bundle sealing and result publication.
    """
    effective = copy.deepcopy(params)
    if not enabled(effective.get('esmf_use_msa')):
        return effective
    if effective.get('core_protein_scientific_contract') != 1:
        raise ValueError('Hosted ESMFold2 MSA requires the current scientific contract')
    from biomodstack_msa_policy import apply_msa_policy
    effective = apply_msa_policy('esmfold2', effective)
    from services.msa_provider_setup import selected_provider, provider_settings
    from biomodstack_msa_api import validate_settings
    provider = selected_provider(effective)
    settings = validate_settings(provider, provider_settings(effective))
    if settings.get('pairing_mode', 'unpaired') != 'unpaired':
        raise ValueError('ESMFold2 accepts native per-chain A3M, not provider paired-row CSV; select unpaired')
    # Use the same native component representation as compile_workflow_request.
    components = effective.get('esmf_complex_components', effective.get('complex_components'))
    raw = effective.get('esmf_complex_components_json') or effective.get('complex_components_json')
    if components is not None and raw:
        raise ValueError('conflicting component sources')
    if raw:
        components = json.loads(raw)
    if isinstance(components, str):
        components = json.loads(components)
    if effective.get('pdb_sequence_path') or effective.get('esmf_pdb_sequence_path'):
        raise ValueError('Hosted ESMFold2 MSA requires explicit protein sequences, not a PDB sequence source')
    missing = []
    if components:
        if not isinstance(components, list):
            raise ValueError('complex_components must be a list')
        if effective.get('msa_path') or effective.get('esmf_msa_path'):
            raise ValueError('conflicting primary MSA and component-owned launch')
        for component in components:
            if component.get('type', 'protein') not in {'protein', 'peptide'}:
                continue
            if not component.get('msa_path'):
                identity = component.get('id')
                if not isinstance(identity, str) or not identity:
                    raise ValueError('Hosted ESMFold2 MSA requires explicit unique component IDs')
                missing.append(('component:' + identity, component))
    else:
        if effective.get('msa_path') or effective.get('esmf_msa_path'):
            return effective
        sequence = effective.get('esmf_sequence') or effective.get('sequence_input') or effective.get('sequence')
        missing.append(('primary', {'sequence': sequence}))
    if not missing:
        return effective
    scopes = [scope for scope, _ in missing]
    sequences = [component.get('sequence') for _, component in missing]
    import re
    if len(set(scopes)) != len(scopes) or any(not isinstance(s, str) or not re.fullmatch('[A-Z]+', s) for s in sequences):
        raise ValueError('Hosted ESMFold2 MSA requires exact per-chain protein sequences')
    from services.msa_preparation import prepare_model_msa
    result = prepare_model_msa(sequences=sequences, params=effective)
    if any(a.get('role') != 'unpaired' for a in result['artifacts']):
        raise ValueError('ESMFold2 cannot silently discard provider paired alignment roles')
    paths = native_alignments(result, sequences, destination, model_id='esmfold2')
    sources = []
    for (scope, component), path in zip(missing, paths):
        component['msa_path'] = str(path)
        if scope.startswith('component:'):
            for key in ('msa_max_sequences', 'msa_remove_insertions'):
                if key in effective:
                    component.setdefault(key, effective[key])
        data = path.read_bytes()
        sources.append({'scope': scope, 'sha256': digest(data), 'size_bytes': len(data),
                        'sequence_sha256': digest(component['sequence'].encode())})
    if components:
        for key in ('esmf_complex_components_json', 'complex_components_json', 'esmf_complex_components'):
            effective.pop(key, None)
        effective['complex_components'] = components
    else:
        effective['msa_path'] = missing[0][1]['msa_path']
    effective['esmf_msa_preparation'] = {
        'provider': provider, 'settings': settings, 'sources': sources,
        'request_digest': result['request_digest'], 'provenance': result.get('provenance'),
    }
    return effective


def _validate_partial_group(proteins: list, missing: list, params: dict) -> None:
    if not proteins or not missing or len(missing) == len(proteins):
        return
    from biomodstack_msa_api import validate_settings
    from services.msa_provider_setup import selected_provider, provider_settings
    scientific = validate_settings(selected_provider(params), provider_settings(params))
    if scientific['pairing_mode'] != 'unpaired':
        raise ValueError('Cannot combine supplied and generated paired groups without shared pairing identity')


def _prepare_proteins(proteins, params, destination, *, model_id, base):
    """One native task is one provider pairing operation; empty is not supplied."""
    active = [p for p in proteins if p.get('msa') != 'empty']
    missing = [p for p in active if not p.get('msa')]
    _validate_partial_group(active, missing, params)
    for protein in active:
        if protein.get('msa'):
            source = Path(protein['msa'])
            source = source if source.is_absolute() else base / source
            validate_boltz_msa(source, protein['sequence'])
            protein['msa'] = str(source.resolve())
    receipt = {'backend': 'supplied'}
    if missing:
        from services.msa_preparation import prepare_model_msa
        sequences = [p['sequence'] for p in missing]
        if any(not s or ':' in s for s in sequences):
            raise ValueError('MSA preparation requires explicit per-chain protein sequences')
        result = prepare_model_msa(sequences=sequences, params=params)
        paths = native_alignments(result, sequences, destination, model_id=model_id)
        for protein, path in zip(missing, paths):
            protein['msa'] = str(path)
        receipt = {key: result.get(key) for key in ('provider', 'request_digest', 'provenance', 'cache_hit')}
    return receipt


def _seal_native_alignment(protein, root):
    if protein.get('msa') == 'empty':
        return {'mode': 'empty'}
    source = Path(protein['msa'])
    data = validate_boltz_msa(source, protein['sequence'])
    sha = digest(data)
    relative = f'alignments/{sha}' + ('.csv' if source.suffix.lower() == '.csv' else '.a3m')
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {'mode': 'artifact', 'path': relative, 'sha256': sha, 'size_bytes': len(data),
            'format': 'csv' if relative.endswith('.csv') else 'a3m',
            'sequence_sha256': digest(protein['sequence'].encode())}


def _boltz_roster(params):
    """Read compiler materializations, not a second request or argv compiler."""
    batch = params.get('sequence_batch_json_path')
    if batch:
        entries = json.loads(Path(batch).read_text())
        if not isinstance(entries, list) or not entries:
            raise ValueError('Empty native Boltz batch')
        tasks = []
        for entry in entries:
            if params.get('complex_batch_dir'):
                source = Path(entry['complex_json'])
                payload = json.loads(source.read_text())
                tasks.append({**entry, 'components': payload['components'], 'source_path': str(source)})
            else:
                # Same explicit-ID authority as normalizeProteinDesignSequenceBatchEntries.
                ids = [str(entry[k]).strip() if entry[k] is not None else ''
                       for k in ('producer_artifact_id', 'entry_id', 'id') if k in entry]
                if ids and (not all(ids) or len(set(ids)) != 1):
                    raise ValueError('Native sequence batch explicit IDs must agree')
                tasks.append({**copy.deepcopy(entry), 'name': ids[0] if ids else str(entry.get('name') or '').strip(),
                              'metadata': copy.deepcopy(entry)})
        return tasks
    source = params.get('complex_json_path')
    name = params.get('sequence_name') or ('complex_pred' if source else 'predicted')
    if source:
        task = {'name': name, 'components': json.loads(Path(source).read_text())['components'], 'source_path': source}
    elif params.get('complex_components'):
        components = params['complex_components']
        task = {'name': name, 'components': json.loads(components) if isinstance(components, str) else components}
    else:
        task = {'name': name, 'sequence': params.get('sequence_input') or params.get('sequence')}
    count = int(params.get('num_parallel_jobs', 1))
    if not 1 <= count <= 10000:
        raise ValueError('Boltz task count outside bound')
    return [{**copy.deepcopy(task), 'name': f'{name}_job{i}' if count > 1 else name} for i in range(count)]


def _boltz_task_proteins(task, params):
    """The native task's ordered protein/peptide chains and supplied MSA policy."""
    if 'components' in task:
        proteins = []
        for index, component in enumerate(task['components']):
            kind = component.get('type', 'protein')
            if kind not in {'protein', 'peptide'}:
                continue
            sequence = component.get('sequence', '').upper()
            chain_id = component.get('id', 'A')
            # Exact native complex peptide policy; never search these chains.
            msa = 'empty' if kind == 'peptide' and len(sequence) < 30 else component.get('msa_path')
            proteins.append({'id': chain_id if isinstance(chain_id, list) else [chain_id],
                             'sequence': sequence, 'msa': msa, 'component_index': index})
        return proteins
    sequence = str(task.get('sequence') or '').strip()
    return [{'id': [chr(ord('A') + i)], 'sequence': s.strip(),
             'msa': params.get('msa_path')} for i, s in enumerate(sequence.split(':'))]


def prepare_boltz_roster(params, destination, *, roster=None):
    """Seal ordered task/chain artifacts; keep native input documents immutable."""
    import re
    tasks = copy.deepcopy(roster if roster is not None else _boltz_roster(params))
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 10000:
        raise ValueError('Invalid native Boltz task roster')
    names = [task.get('name') for task in tasks]
    if any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', n) for n in names) or len(set(names)) != len(names):
        raise ValueError('Native Boltz task names must be safe and unique')
    sealed = []
    for task_index, task in enumerate(tasks):
        base = Path(task['source_path']).parent if task.get('source_path') else Path.cwd()
        proteins = _boltz_task_proteins(task, params)
        receipt = _prepare_proteins(proteins, params, destination / 'operations' / str(task_index), model_id='boltz2', base=base)
        chains = []
        for index, protein in enumerate(proteins):
            artifact = _seal_native_alignment(protein, destination)
            chains.append({**{k: v for k, v in protein.items() if k != 'msa'}, 'chain_index': index,
                           'logical_id': f'{task_index}:{index}', 'alignment': artifact})
        sealed.append({'name': task['name'], 'task_index': task_index, 'native_task': task,
                       'chains': chains, 'provenance': receipt})
    manifest = {'schema': 'bms.boltz-msa-inputs.v1', 'tasks': sealed,
                'settings': {k: v for k, v in params.items() if k.startswith(('msa_', 'colabfold_', 'boltz_'))
                             and not k.endswith(('_path', '_dir'))}}
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / 'msa-inputs.json'
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return {**params, 'boltz_prepared_msa_dir': str(destination.resolve()),
            'boltz_prepared_msa_sha256': digest(path.read_bytes())}


def fold_cp_msa_settings(params):
    from component_runtime import _LAUNCH_BINDING_KEYS
    return {k: v for k, v in params.items()
            if k.startswith(('msa_', 'colabfold_', 'boltz_'))
            and not k.endswith(('_path', '_dir')) and k not in _LAUNCH_BINDING_KEYS}

def _fold_cp_native_config(source: Path) -> tuple[Path, Path]:
    """Fold-CP executes exactly one top-level YAML, even for directory input."""
    if source.is_dir():
        files = sorted(p for p in source.rglob('*') if p.suffix in {'.yaml', '.yml'})
        if len(files) != 1 or files[0].parent != source:
            raise ValueError('Boltz-CP config_files requires exactly one top-level YAML config; multiple or nested configs cannot be executed')
        return files[0], source
    if not source.is_file() or source.suffix not in {'.yaml', '.yml'}:
        raise ValueError('Boltz-CP config_files requires a native YAML config')
    return source, source.parent

def fold_cp_config_proteins(source: Path) -> tuple[Path, dict, list[dict]]:
    """Inspect the one executable native config before any provider operation."""
    import yaml
    path, _ = _fold_cp_native_config(source)
    payload = yaml.safe_load(path.read_text())
    if not isinstance(payload, dict) or not isinstance(payload.get('sequences'), list):
        raise ValueError('Invalid native Boltz-CP config')
    proteins = []
    for entry in payload['sequences']:
        if not isinstance(entry, dict):
            raise ValueError('Invalid native Boltz-CP sequence entry')
        if 'protein' in entry:
            protein = entry['protein']
            if not isinstance(protein, dict) or not isinstance(protein.get('sequence'), str):
                raise ValueError('Invalid native Boltz-CP protein entry')
            proteins.append(protein)
    return path, payload, proteins


def fold_cp_msa_intent(params, input_roles):
    """Input preparation, not an MSA request for future prediction outputs."""
    from component_runtime import ExternalServiceIntent, canonical_bytes
    return ExternalServiceIntent('boltz_cp_experimental:msa', params.get('msa_provider'),
        'platform/api/services/model_msa_handoff.py:prepare_boltz_cp_bundle; '
        'platform/api/services/msa_preparation.py; modules/boltz_cp_experimental.nf:RunBoltzCPExperimental',
        canonical_bytes(fold_cp_msa_settings(params)), input_roles,
        ('boltz_cp_experimental:msa_artifacts',),
        'planned_from_native_inputs' if enabled(params.get('boltz_use_msa')) else 'disabled')


def bind_prepared_fold_cp_plan(invocation, supplied):
    """Verify the controller package against the once-compiled native inputs."""
    from dataclasses import replace
    from biomodstack_boltz_msa import resolve_boltz_config
    plan = invocation.execution_plan
    if plan is None:
        raise ValueError('Prepared Fold-CP MSA requires its selected execution plan')
    native = invocation.native_parameters
    root = Path(supplied['bcp_input_path'])
    output = Path(native['out_dir'])
    if (not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents))
            or not root.resolve().is_relative_to(output.resolve())):
        raise ValueError('Prepared MSA transport unavailable outside compiled job output')
    manifest_path = root / 'msa-inputs.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('Prepared MSA manifest must be a regular native input')
    sha = supplied['boltz_prepared_msa_sha256']
    if digest(manifest_path.read_bytes()) != sha:
        raise ValueError('Prepared Fold-CP MSA manifest digest mismatch')
    manifest = json.loads(manifest_path.read_bytes())
    if (manifest.get('schema') != 'bms.boltz-cp-msa-inputs.v1'
            or manifest.get('settings') != fold_cp_msa_settings(native)):
        raise ValueError('Prepared Fold-CP MSA schema/scientific settings mismatch')
    services = [s for s in plan.metadata.external_services if s.logical_id == 'boltz_cp_experimental:msa']
    identity = 'sha256:' + sha
    if len(services) != 1 or services[0].state == 'disabled':
        raise ValueError('Prepared Fold-CP MSA has no selected service')
    if services[0].operation_identity not in (None, identity):
        raise ValueError('Prepared MSA plan operation identity changed')
    records = manifest.get('configs', [])
    names = [r['path'] for r in records]
    actual = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.suffix in {'.yaml', '.yml'})
    if (len(names) != 1 or Path(names[0]).parent != Path('.')
            or sorted(names) != actual):
        raise ValueError('Prepared Fold-CP native config roster mismatch')
    if services[0].operation_identity is None:
        source = Path(native['bcp_input_path'])
        file, _ = _fold_cp_native_config(source)
        files = [file]
        expected = {str(p.relative_to(source)) if source.is_dir() else p.name: digest(p.read_bytes()) for p in files}
        if {r['path']: r['source_sha256'] for r in records} != expected:
            raise ValueError('Prepared Fold-CP native source identity mismatch')
        import yaml
        for path in files:
            name = str(path.relative_to(source)) if source.is_dir() else path.name
            original = yaml.safe_load(path.read_text())
            prepared = yaml.safe_load((root / name).read_text())
            for document in (original, prepared):
                for entry in document['sequences']:
                    if 'protein' in entry:
                        protein = entry['protein']
                        if protein.get('msa') != 'empty':
                            protein.pop('msa', None)
            if original != prepared:
                raise ValueError('Prepared Fold-CP native scientific input mismatch')
    for name in names:
        resolve_boltz_config(root / name, root=root, manifest_sha256=sha)
    authority = 'biomodstack_boltz_msa.py:resolve_boltz_config'
    return replace(plan, metadata=replace(plan.metadata,
        external_services=tuple(replace(s, state='prepared', operation_identity=identity)
            if s.logical_id == services[0].logical_id else s for s in plan.metadata.external_services),
        artifact_roles=tuple(replace(r, identity_authority=authority,
            native_declaration='msa-inputs.json@' + identity)
            if r.role_id == 'boltz_cp_experimental:msa_artifacts' else r for r in plan.metadata.artifact_roles)))


def prepare_boltz_cp_bundle(params: dict, destination: Path) -> dict:
    """Package the single executable config, retaining file and chain identity."""
    import yaml
    if params.get('bcp_input_format', 'config_files') != 'config_files':
        return copy.deepcopy(params)
    source = Path(params.get('bcp_input_path') or params['input_path']).resolve()
    # Validate the complete roster before requesting even the first MSA.
    file, base = _fold_cp_native_config(source)
    files = [file]
    configs = []
    # Validate every declared config before any provider operation.
    for path in files:
        if not path.resolve().is_relative_to(base):
            raise ValueError('Boltz-CP config escapes input root')
        _, payload, proteins = fold_cp_config_proteins(path)
        configs.append((path, payload, proteins))
    records = []
    for index, (path, payload, proteins) in enumerate(configs):
        receipt = _prepare_proteins(proteins, params, destination / 'operations' / str(index),
                                    model_id='boltz_cp_experimental', base=path.parent)
        relative = path.relative_to(base) if source.is_dir() else Path(path.name)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        chains = []
        for chain_index, protein in enumerate(proteins):
            artifact = _seal_native_alignment(protein, destination)
            if artifact['mode'] != 'empty':
                import os
                protein['msa'] = os.path.relpath(destination / artifact['path'], target.parent)
            chains.append({'chain_index': chain_index, 'id': protein['id'], 'sequence': protein['sequence'],
                           'alignment': artifact})
        target.write_text(yaml.safe_dump(payload, sort_keys=False))
        records.append({'path': str(relative), 'source_sha256': digest(path.read_bytes()),
                        'sha256': digest(target.read_bytes()), 'chains': chains, 'provenance': receipt})
    manifest = {'schema': 'bms.boltz-cp-msa-inputs.v1', 'configs': records,
                'settings': fold_cp_msa_settings(params)}
    manifest_path = destination / 'msa-inputs.json'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return {**params, 'bcp_input_path': str(destination.resolve()),
            'boltz_prepared_msa_sha256': digest(manifest_path.read_bytes())}
