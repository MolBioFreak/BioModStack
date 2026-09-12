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
    if is_boltz and not enabled(params.get('boltz_use_msa')):
        return effective
    if not is_boltz:
        # ESMFold2's current closed schema exposes optional supplied MSA paths,
        # not an automatic-search switch. Preserve that native input contract;
        # do not silently turn sequence-only inference into MSA inference.
        return effective
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
        else:
            sequence = str(task.get('sequence') or '').strip()
            proteins = [{'id': [chr(ord('A') + i)], 'sequence': s.strip(),
                         'msa': params.get('msa_path')} for i, s in enumerate(sequence.split(':'))]
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


def prepare_boltz_cp_bundle(params: dict, destination: Path) -> dict:
    """Package every native config separately, retaining file and chain identity."""
    import yaml
    if params.get('bcp_input_format', 'config_files') != 'config_files':
        return copy.deepcopy(params)
    source = Path(params.get('bcp_input_path') or params['input_path']).resolve()
    files = sorted(source.rglob('*.yaml')) + sorted(source.rglob('*.yml')) if source.is_dir() else [source]
    if not files or any(not p.is_file() or p.suffix not in {'.yaml', '.yml'} for p in files):
        raise ValueError('Boltz-CP config_files requires native YAML configs')
    base = source if source.is_dir() else source.parent
    configs = []
    # Validate every declared config before any provider operation.
    for path in files:
        if not path.resolve().is_relative_to(base):
            raise ValueError('Boltz-CP config escapes input root')
        payload = yaml.safe_load(path.read_text())
        if not isinstance(payload, dict) or not isinstance(payload.get('sequences'), list):
            raise ValueError('Invalid native Boltz-CP config')
        proteins = [entry['protein'] for entry in payload['sequences'] if 'protein' in entry]
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
    manifest = {'schema': 'bms.boltz-cp-msa-inputs.v1', 'configs': records}
    manifest_path = destination / 'msa-inputs.json'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2))
    return {**params, 'bcp_input_path': str(destination.resolve()),
            'boltz_prepared_msa_sha256': digest(manifest_path.read_bytes())}
