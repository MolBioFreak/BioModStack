import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
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
    source, request = example(tmp_path)
    _, complete, ablated = runner.prepare(source, request['target_patch'], 'B')
    row = {'batch_idx': 0, 'design_idx': 0, 'sampled_patch': {'A1': 'A', 'A2': 'V'},
           'patch_residue_count': 2, 'patch_exact_matches': 1}
    data = {'schema': 'bms.ligandmpnn.interface-context.experimental.v1',
            'status': 'completed_unclassified', 'qualification': 'unqualified',
            'model_type': 'ligand_mpnn', 'method': 'masked_whole_patch_sampling_with_binder_ablation',
            'checkpoint_sha256': runner.CHECKPOINT_SHA256, 'foundry_version': runner.VERSION,
            'candidate_id': 'candidate', 'round_id': 'round', 'source_sha256': request['source_sha256'],
            'target_patch': ['A1', 'A2'], 'reference_patch': {'A1': 'A', 'A2': 'G'},
            'fixed_binder_chain': 'B', 'target_chain': 'A', 'seed': 1, 'temperature': 0.1,
            'samples': 1, 'conditions': {}}
    for label, filename, content in [('supplied_complex', 'masked_complex.pdb', complete),
                                     ('without_binder', 'masked_without_binder.pdb', ablated)]:
        staged = tmp_path / filename
        staged.write_text(content)
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


def test_reader_rejects_rehashed_leakage_and_comparator_change(tmp_path):
    source, request = example(tmp_path)
    ref, complete, ablated = runner.prepare(source, request['target_patch'], 'B')
    row = {'batch_idx': 0, 'design_idx': 0, 'sampled_patch': ref,
           'patch_residue_count': 2, 'patch_exact_matches': 2}
    data = {'schema': 'bms.ligandmpnn.interface-context.experimental.v1',
            'status': 'completed_unclassified', 'qualification': 'unqualified',
            'candidate_id': 'candidate', 'round_id': 'round', 'source_sha256': request['source_sha256'],
            'model_type': 'ligand_mpnn', 'method': 'masked_whole_patch_sampling_with_binder_ablation',
            'checkpoint_sha256': runner.CHECKPOINT_SHA256, 'foundry_version': runner.VERSION,
            'target_patch': request['target_patch'], 'reference_patch': ref,
            'fixed_binder_chain': 'B', 'target_chain': 'A', 'seed': 1,
            'temperature': 0.1, 'samples': 1, 'conditions': {}}
    for name, filename, content in [('supplied_complex', 'masked_complex.pdb', complete),
                                    ('without_binder', 'masked_without_binder.pdb', ablated)]:
        file = tmp_path / filename
        file.write_text(content)
        data['conditions'][name] = {'masked_input_sha256': runner.digest(file), 'samples': [row]}
    result = tmp_path / 'result.json'
    def read():
        result.write_text(json.dumps(data))
        return read_context_result(result, candidate_id='candidate', round_id='round',
                                   source_sha256=request['source_sha256'])
    assert read() == data
    leaked = complete.replace('UNK A   1', 'ALA A   1')
    file = tmp_path / 'masked_complex.pdb'
    file.write_text(leaked)
    data['conditions']['supplied_complex']['masked_input_sha256'] = runner.digest(file)
    with pytest.raises(ValueError, match='held-out'):
        read()
    file.write_text(complete)
    data['conditions']['supplied_complex']['masked_input_sha256'] = runner.digest(file)
    ablated_file = tmp_path / 'masked_without_binder.pdb'
    ablated_file.write_text(ablated.replace('UNK A   2', 'ALA A   2'))
    data['conditions']['without_binder']['masked_input_sha256'] = runner.digest(ablated_file)
    with pytest.raises(ValueError, match='held-out'):
        read()
    ablated_file.write_text(ablated + pdb_line(100, 'N', 'ALA', 'A', 3, 3.0))
    data['conditions']['without_binder']['masked_input_sha256'] = runner.digest(ablated_file)
    with pytest.raises(ValueError, match='comparator'):
        read()


def test_installed_foundry_whole_patch_identity_blinding(tmp_path):
    """Opt-in image/checkpoint differential; fixtures alone cannot prove native masks."""
    image = os.environ.get('BMS_TEST_FOUNDRY_IMAGE')
    if not image or not shutil.which('apptainer'):
        pytest.skip('set BMS_TEST_FOUNDRY_IMAGE and provide apptainer for native CPU smoke')
    assert image is not None
    source = Path(__file__).resolve().parents[3] / 'platform/api/assets/md/admitted_structures/1AKI.pdb'
    atoms = [line for line in source.read_text().splitlines(keepends=True)
             if line.startswith('ATOM  ') and line[16] == ' ' and line[21] == 'A']
    ids = list(dict.fromkeys(line[22:27] for line in atoms))[:60]
    assert len(ids) == 60
    base = ''.join(line[:21] + ('B' if line[22:27] in ids[30:] else 'A') + line[22:]
                   for line in atoms if line[22:27] in ids) + 'END\n'
    patch = ['A' + ids[i].strip() for i in (3, 4)]
    original = [line for line in base.splitlines(keepends=True)
                if line.startswith('ATOM  ') and line[21] == 'A' and line[22:27] == ids[3]]
    old = original[0][17:20]
    replacement = 'VAL' if old != 'VAL' else 'ALA'
    perturbed = ''.join(line[:17] + replacement + line[20:] if line in original else line
                        for line in base.splitlines(keepends=True))
    results = []
    for suffix, text in (('original', base), ('perturbed', perturbed)):
        pdb = tmp_path / f'{suffix}.pdb'
        pdb.write_text(text)
        request = {'candidate_id': 'native-smoke', 'round_id': 'native-smoke',
                   'source_sha256': runner.digest(pdb), 'structure_path': str(pdb),
                   'binder_chain': 'B', 'target_chain': 'A', 'target_patch': patch,
                   'seed': 7, 'samples': 1, 'temperature': 0.1}
        request_path = tmp_path / f'{suffix}.json'
        request_path.write_text(json.dumps(request))
        binds = ['--bind', f'{tmp_path}:{tmp_path}',
                 '--bind', f'{SCRIPT}:/probe/ligandmpnn_context.py']
        # The API test namespace maps the host user to root; --no-home avoids
        # shadowing the image's /foundry -> /root/.foundry checkpoint tree.
        subprocess.run(['apptainer', 'exec', '--no-home', *binds, image,
                        'python', '/probe/ligandmpnn_context.py', str(request_path),
                        str(tmp_path / f'result-{suffix}')],
                       check=True, timeout=180)
        results.append(read_context_result(tmp_path / f'result-{suffix}/result.json',
                                           candidate_id=request['candidate_id'],
                                           round_id=request['round_id'],
                                           source_sha256=request['source_sha256']))
    first, second = results
    assert first['reference_patch'][patch[0]] != second['reference_patch'][patch[0]]
    for label in ('supplied_complex', 'without_binder'):
        a, b = first['conditions'][label], second['conditions'][label]
        assert a['masked_input_sha256'] == b['masked_input_sha256']
        assert [row['sampled_patch'] for row in a['samples']] == [row['sampled_patch'] for row in b['samples']]
