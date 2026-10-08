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
    if any(len(rows) > 1 for rows in paired.values()):
        if len(paired) != len(sequences) or len({len(rows) for rows in paired.values()}) != 1:
            raise ValueError('Paired alignments require equal row counts for every requested chain')
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


def prepare_launch_msa(model_id: str, params: dict, destination: Path) -> dict:
    """Return native launch parameters, calling the shared controller at most once.

    Invoked on BMS before command compilation/input sealing for both local and
    remote jobs. destination must be in managed input/result storage.
    """
    model_id = model_id.lower()
    effective = copy.deepcopy(params)
    if model_id == 'protenix':
        if not enabled(params.get('protenix_use_msa'), True) or params.get('protenix_msa_backend') in {'none', 'esm'}:
            return effective
        from services.msa_preparation import prepare_remote_protenix_inputs
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


def prepare_boltz_cp_bundle(params: dict, destination: Path) -> dict:
    """Package supplied native YAML with portable relative alignment paths."""
    import yaml
    source = Path(params.get('bcp_input_path') or params['input_path'])
    if source.is_dir() and (source / 'input.yaml').is_file():
        source = source / 'input.yaml'
    if not source.is_file() or source.suffix not in {'.yaml', '.yml'}:
        raise ValueError('Controller Boltz-CP MSA preparation currently requires one native YAML config')
    payload = yaml.safe_load(source.read_text())
    proteins = [entry['protein'] for entry in payload['sequences'] if 'protein' in entry]
    missing = [p for p in proteins if not p.get('msa') or p['msa'] == 'empty']
    destination.mkdir(parents=True, exist_ok=True)
    if missing:
        from services.msa_preparation import prepare_model_msa
        sequences = [p['sequence'] for p in missing]
        paths = native_alignments(prepare_model_msa(sequences=sequences, params=params), sequences, destination / 'alignments', model_id='boltz_cp_experimental')
        for protein, path in zip(missing, paths):
            protein['msa'] = str(path)
    for index, protein in enumerate(proteins):
        path = Path(protein['msa'])
        if not path.is_absolute():
            path = source.parent / path
        data = validate_boltz_msa(path, protein['sequence'])
        relative = f'chain-{index}.csv' if path.suffix.lower() == '.csv' else f'chain-{index}.a3m'
        (destination / relative).write_bytes(data)
        protein['msa'] = relative
    (destination / 'input.yaml').write_text(yaml.safe_dump(payload, sort_keys=False))
    return {**params, 'bcp_input_path': str(destination.resolve())}
