"""Source-qualified observational readback; existing custody and runtime owners only."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess


def _text(value):
    return None if value in (None, '', '.', '?') else str(value)


def map_condition_atoms(atoms, receipts):
    """Exact label atom correspondence; ambiguous/created atoms stay observations."""
    selections = {slot: [] for slot in receipts}
    unresolved = []
    for atom in atoms:
        matches = []
        for slot, receipt in receipts.items():
            model = receipt['model_number']
            if model is None and len(receipt['model_numbers']) == 1:
                model = receipt['model_numbers'][0]
            for position in receipt.get('source_positions', []):
                if position['model_number'] != model:
                    continue
                if (position.get('label_asym_id') != atom.get('chain_id') or
                    str(position.get('label_seq_id')) != str(atom.get('res_id')) or
                    position.get('label_comp_id') != atom.get('res_name')):
                    continue
                # Native label-field parsing clears author insertion codes. Recover
                # them only through a unique label atom, never by native ins_code.
                # Duplicate label identities below remain unresolved, not broadened.
                for item in position.get('atoms', []):
                    if item.get('label_atom_id') != atom.get('atom_name'):
                        continue
                    # Absence of a native altloc annotation is not proof of a copy.
                    if _text(item.get('label_alt_id')) != _text(atom.get('altloc_id')):
                        continue
                    identity = {'modelNumber': model, 'atomIndex': item['model_atom_index']}
                    for key, value in {
                        'labelAsymId': position.get('label_asym_id'), 'labelSeqId': position.get('label_seq_id'),
                        'authAsymId': position.get('auth_asym_id'), 'authSeqId': position.get('auth_seq_id'),
                        'insertionCode': position.get('pdbx_PDB_ins_code'), 'componentId': position.get('label_comp_id'),
                        'entityId': position.get('label_entity_id'), 'labelAtomId': item.get('label_atom_id'),
                        'authAtomId': item.get('auth_atom_id'), 'altLoc': item.get('label_alt_id'),
                    }.items():
                        if _text(value) is not None:
                            identity[key] = int(value) if key in ('labelSeqId', 'authSeqId') else value
                    matches.append((slot, identity))
        if len(matches) == 1:
            slot, identity = matches[0]
            if identity not in selections[slot]:
                selections[slot].append(identity)
        else:
            unresolved.append(atom)
    return selections, unresolved


def run_condition_inspection(payload, *, image=None):
    from paths import get_container_dir
    from services.remote_execution.images import resolve_image
    image = Path(image) if image else resolve_image('rfd4_proteina.sif', get_container_dir())
    script = Path(__file__).resolve().parents[3] / 'scripts' / 'inspect_rfd4_condition.py'
    parents = {str(Path(path).parent) for path in payload['paths'].values()}
    binds = ','.join([f'{script.parents[1]}:{script.parents[1]}:ro', *[f'{p}:{p}:ro' for p in sorted(parents)]])
    try:
        completed = subprocess.run(['apptainer', 'exec', '--cleanenv', '--no-home', '--bind', binds,
                                    str(image), 'python', str(script)],
                                   input=json.dumps(payload, allow_nan=False), text=True,
                                   capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Native condition readback timed out; request unchanged') from exc
    if completed.returncode:
        raise ValueError('Native condition readback unavailable: ' + completed.stderr[-2000:])
    return {**json.loads(completed.stdout), 'runtime_image': str(image)}


async def inspect_rfd4_condition(payload, session):
    from pydantic import TypeAdapter
    from starlette.concurrency import run_in_threadpool
    from paths import resolve_allowed_path
    from services.native_generation_sources import NativeSources, NativeFileSource
    from routers.files import materialize_structure, get_governed_ngs_result_roots

    inp, row = payload['input'], payload['row']
    if inp['kind'] != 'condition_spec':
        raise ValueError('Current input is not a condition specification')
    index = row['exampleIndex']
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError('Invalid example index')
    example = inp['examples'][index]
    bindings = TypeAdapter(NativeSources).validate_python({
        source['slot']: payload['native_sources'][source['slot']] for source in example['sources']})
    governed = await get_governed_ngs_result_roots(session)
    receipts, paths = {}, {}
    for source in example['sources']:
        slot = source['slot']
        if slot in receipts:
            continue
        binding = bindings[slot]
        if isinstance(binding, NativeFileSource):
            raise ValueError('Condition inspection requires a structure source')
        receipt = await materialize_structure(binding.model_copy(update={'output_format': 'native'}), session, governed_roots=governed)
        if receipt.get('inspection_error'):
            raise ValueError('Source identity readback unavailable: ' + receipt['inspection_error'])
        if receipt['model_number'] is None and len(receipt['model_numbers']) != 1:
            raise ValueError('Select an explicit source MODEL for condition inspection; native execution is unchanged')
        receipts[slot] = receipt
        paths[slot] = str(resolve_allowed_path(receipt['path']))
    result = await run_in_threadpool(run_condition_inspection, {'input': inp, 'row': row, 'paths': paths})
    selections, unresolved = map_condition_atoms(result.pop('atoms'), receipts)
    return {**result, 'sources': {slot: {'sha256': receipt['sha256'],
            'model_number': receipt['model_number'], 'model_numbers': receipt['model_numbers'],
            'identities': selections[slot]} for slot, receipt in receipts.items()},
            'unresolved_atoms': unresolved}
