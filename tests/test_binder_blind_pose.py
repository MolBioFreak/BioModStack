"""Blind pose source isolation and native sample-identity contracts (CPU only)."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_binder_blind_pose import compile_selected_inputs, run


def pdb(chain, residues, *, offset=0):
    return ''.join(
        f'ATOM  {i:5d}  CA  {res:3s} {chain}{i:4d}    {offset + i:8.3f}{offset + i:8.3f}{offset + i:8.3f}  1.00 30.00           C\n'
        for i, res in enumerate(residues, 1)
    ) + 'END\n'


def sources(tmp_path):
    (tmp_path / 'candidate.pdb').write_text(pdb('B', ['ALA', 'GLY'], offset=90) + pdb('T', ['TRP'], offset=900))
    target = tmp_path / 'target.pdb'
    target.write_text(pdb('T', ['TYR'], offset=1))
    manifest = {'schema_version': 1, 'target_chains': ['T'], 'candidates': [
        {'candidate_key': 'selected-1', 'source_pdb': 'candidate.pdb', 'binder_chains': ['B']}]}
    manifest_file = tmp_path / 'selection.json'
    manifest_file.write_text(json.dumps(manifest))
    return manifest, manifest_file, target


def test_compiles_selected_binder_and_independent_target_sequences_only(tmp_path):
    manifest, _, target = sources(tmp_path)
    compiled = compile_selected_inputs(manifest, tmp_path, target)
    assert [(c['id'], c['sequence'], c['source']) for c in compiled[0]['components']] == [
        ('B', 'AG', 'binder_sequence_from_candidate'), ('T', 'Y', 'target_sequence_from_declared_target')]
    before = compiled[0]['components']
    (tmp_path / 'candidate.pdb').write_text(pdb('B', ['ALA', 'GLY'], offset=1900) + pdb('T', ['ALA'], offset=10))
    assert compile_selected_inputs(manifest, tmp_path, target)[0]['components'] == before


def test_rejects_unselected_roles_or_ambiguous_identity(tmp_path):
    manifest, _, target = sources(tmp_path)
    manifest['candidates'][0]['binder_chains'] = ['Z']
    with pytest.raises(ValueError, match='no selected polymer chains|disagree'):
        compile_selected_inputs(manifest, tmp_path, target)
    manifest['candidates'][0]['binder_chains'] = ['T']
    with pytest.raises(ValueError, match='overlap'):
        compile_selected_inputs(manifest, tmp_path, target)
    manifest['candidates'][0]['candidate_key'] = '../escape'
    with pytest.raises(ValueError, match='key'):
        compile_selected_inputs(manifest, tmp_path, target)


def test_native_pairing_and_unclassified_receipt(tmp_path, monkeypatch):
    _, manifest_file, target = sources(tmp_path)
    calls = []

    def native(command, check):
        assert check
        assert '--pdb-sequence-path' not in command
        assert '--complex-components-file' in command
        assert '--target-pdb' not in command
        components = json.loads(Path(command[command.index('--complex-components-file') + 1]).read_text())
        assert [(c['id'], c['sequence']) for c in components] == [('B', 'AG'), ('T', 'Y')]
        output = Path(command[command.index('--output-dir') + 1])
        samples = []
        for number in range(2):
            sample_id = f'selected-1_{number:03d}'
            cif, metrics = sample_id + '.cif', sample_id + '.metrics.json'
            (output / cif).write_text('data_test\n')
            (output / metrics).write_text(json.dumps({'sample_id': sample_id, 'cif': cif, 'iptm': .2}))
            samples.append({'sample_id': sample_id, 'cif': cif, 'metrics': metrics})
        (output / 'manifest.json').write_text(json.dumps({'workflow': 'esmfold2', 'sequence_name': 'selected-1',
                                                          'sample_count': 2, 'samples': samples}))
        calls.append(command)

    monkeypatch.setattr('run_binder_blind_pose.subprocess.run', native)
    receipt = run(manifest_file, tmp_path, target, tmp_path / 'out', model_variant='fast',
                  model_id_or_path='', num_loops=1, num_sampling_steps=25,
                  num_diffusion_samples=2, seed=7, device='cuda', runner=tmp_path / 'native.py')
    assert len(calls) == 1
    assert len(receipt['records']) == 2
    assert all(r['classification'] == 'unclassified' and r['raw_metrics']['iptm'] == .2
               for r in receipt['records'])
    assert json.loads((tmp_path / 'out' / 'blind_pose_receipt.json').read_text()) == receipt


def test_mismatched_native_peer_does_not_publish_receipt(tmp_path, monkeypatch):
    _, manifest_file, target = sources(tmp_path)

    def malformed_native(command, check):
        output = Path(command[command.index('--output-dir') + 1])
        (output / 'selected-1_000.cif').write_text('data_test\n')
        (output / 'selected-1_000.metrics.json').write_text(json.dumps({
            'sample_id': 'different_000', 'cif': 'selected-1_000.cif'}))
        (output / 'manifest.json').write_text(json.dumps({
            'workflow': 'esmfold2', 'sequence_name': 'selected-1', 'sample_count': 1,
            'samples': [{'sample_id': 'selected-1_000', 'cif': 'selected-1_000.cif',
                         'metrics': 'selected-1_000.metrics.json'}]}))

    monkeypatch.setattr('run_binder_blind_pose.subprocess.run', malformed_native)
    with pytest.raises(ValueError, match='Native metrics disagree'):
        run(manifest_file, tmp_path, target, tmp_path / 'out', model_variant='fast',
            model_id_or_path='', num_loops=1, num_sampling_steps=25,
            num_diffusion_samples=1, seed=None, device='cuda', runner=tmp_path / 'native.py')
    assert not (tmp_path / 'out' / 'blind_pose_receipt.json').exists()
