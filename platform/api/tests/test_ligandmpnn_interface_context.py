import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from services.ligandmpnn_interface_context import read_context_result

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'run_ligandmpnn_interface_context.py'
spec = importlib.util.spec_from_file_location('ligandmpnn_context_runner', SCRIPT)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def pdb_line(serial, atom, res, chain, index, x):
    return (f'ATOM  {serial:5d} {atom:<4s} {res:>3s} {chain}{index:4d}    '
            f'{x:8.3f}{1.0:8.3f}{2.0:8.3f}{1.0:6.2f}{1.0:6.2f}          {atom[0]:>2s}  \n')


def example(tmp_path):
    lines = []
    for chain, index, res in [('A', 1, 'ALA'), ('A', 2, 'GLY'), ('B', 1, 'LYS')]:
        for atom in ['N', 'CA', 'C', 'O', 'CB']:
            if atom == 'CB' and res == 'GLY':
                continue
            lines.append(pdb_line(len(lines) + 1, atom, res, chain, index, float(index)))
    path = tmp_path / 'source.pdb'
    path.write_text(''.join(lines) + 'END\n')
    request = dict(candidate_id='candidate', round_id='round', source_sha256=runner.digest(path),
                   structure_path=str(path), binder_chain='B', target_chain='A',
                   target_patch=['A1', 'A2'], seed=1, samples=2, temperature=0.1)
    return path, request


def test_whole_patch_mask_and_context_ablation(tmp_path):
    source, request = example(tmp_path)
    runner.validate(request)
    original = source.read_bytes()
    ref, complete, ablated = runner.prepare(source, request['target_patch'], 'B')
    assert ref == {'A1': 'A', 'A2': 'G'}
    assert 'UNK A   1' in complete and 'UNK A   2' in complete
    assert 'LYS B   1' in complete and 'LYS B   1' not in ablated
    assert all(line[12:16].strip() in runner.BACKBONE for line in complete.splitlines()
               if line.startswith('ATOM') and line[21] == 'A')
    assert source.read_bytes() == original
    # Perturb the withheld identity/side-chain atoms: both native inputs stay byte-identical.
    changed = source.read_text().replace('ALA A   1', 'VAL A   1').replace('CB   VAL A   1', 'CB   VAL A   1')
    source.write_text(changed)
    _, new_complete, new_ablated = runner.prepare(source, request['target_patch'], 'B')
    assert (new_complete, new_ablated) == (complete, ablated)
    with pytest.raises(ValueError, match='digest mismatch'):
        runner.validate(request)


@pytest.mark.parametrize('change', [
    {'target_patch': ['A1', 'A1']}, {'target_patch': ['B1']},
    {'binder_chain': 'A'}, {'samples': 0}, {'seed': -1}, {'temperature': float('nan')},
    {'extra': 'unknown'},
])
def test_invalid_request_refused(tmp_path, change):
    _, request = example(tmp_path)
    request.update(change)
    with pytest.raises(ValueError):
        runner.validate(request)


def test_reader_exact_identity_and_cardinality(tmp_path):
    _, request = example(tmp_path)
    row = {'batch_idx': 0, 'design_idx': 0, 'sampled_patch': {'A1': 'A', 'A2': 'V'},
           'patch_residue_count': 2, 'patch_exact_matches': 1}
    data = {'schema': 'bms.ligandmpnn.interface-context.experimental.v1',
            'status': 'completed_unclassified', 'qualification': 'unqualified',
            'model_type': 'ligand_mpnn', 'method': 'masked_whole_patch_sampling_with_binder_ablation',
            'candidate_id': 'candidate', 'round_id': 'round', 'source_sha256': request['source_sha256'],
            'target_patch': ['A1', 'A2'], 'reference_patch': {'A1': 'A', 'A2': 'G'},
            'samples': 1, 'conditions': {}}
    for label, filename in [('supplied_complex', 'masked_complex.pdb'), ('without_binder', 'masked_without_binder.pdb')]:
        staged = tmp_path / filename
        staged.write_text(label)
        data['conditions'][label] = {'samples': [row], 'masked_input_sha256': hashlib.sha256(staged.read_bytes()).hexdigest()}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(data))
    assert read_context_result(path, candidate_id='candidate', round_id='round', source_sha256=request['source_sha256']) == data
    with pytest.raises(ValueError, match='identity mismatch'):
        read_context_result(path, candidate_id='other', round_id='round', source_sha256=request['source_sha256'])
    data['conditions']['without_binder']['samples'] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='cardinality'):
        read_context_result(path, candidate_id='candidate', round_id='round', source_sha256=request['source_sha256'])
