"""Native-image CPU checks; no scientific scores or payloads are synthesized."""
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.lib.complexa_contract import Request


@pytest.mark.parametrize('selection,package,entrypoint', [
    ('protein_mpnn', 'ProteinMPNN', 'protein_mpnn_run.py'),
    ('ligand_mpnn', 'LigandMPNN', 'run.py'),
    ('soluble_mpnn', 'LigandMPNN', 'run.py'),
])
def test_runner_prepares_native_entrypoints(tmp_path, monkeypatch, selection, package, entrypoint):
    from omegaconf import OmegaConf
    runner = importlib.import_module('scripts.bms_complexa.run')
    upstream = Path(os.environ.get('COMPLEXA_ROOT', '/opt/complexa'))
    assert upstream.is_dir(), 'Run this qualification inside the selected Complexa image'
    monkeypatch.setattr(runner, 'compose_request', lambda *a, **k: OmegaConf.create({}))
    monkeypatch.setattr(runner, 'selected_dependencies', lambda *a: [selection])
    weights = tmp_path / 'selected-weights'
    weights.mkdir()
    member = {'protein_mpnn': 'v_48_020.pt', 'ligand_mpnn': 'ligandmpnn_v_32_010_25.pt',
              'soluble_mpnn': 'solublempnn_v_48_020.pt'}[selection]
    (weights / member).write_bytes(b'inert selected-layout fixture; never load as a checkpoint')
    # Complete selected layout with inert bytes; --help checks imports/argv only.
    monkeypatch.setenv('COMPLEXA_PROTEINMPNN_CA_WEIGHTS', str(weights))
    monkeypatch.setenv('COMPLEXA_LIGANDMPNN_WEIGHTS', str(weights))
    output = tmp_path / 'result'
    runner.run(Request(task='protein_binder', task_name='qualification', operation='evaluate'), upstream=upstream,
               weights=tmp_path, output=output, prepare_only=True)
    private = output / 'native/community_models' / package
    script = private / entrypoint
    assert script.is_file(), 'Native cwd-relative entrypoint must exist'
    assert not script.is_symlink(), 'Native realpath(__file__) must remain private'
    assert script.read_bytes() == (upstream / 'community_models' / package / entrypoint).read_bytes()
    weight_dir = private / ('ca_model_weights' if selection == 'protein_mpnn' else 'model_params')
    assert weight_dir.is_symlink() and weight_dir.resolve() == weights
    assert (weight_dir / member).stat().st_ino == (weights / member).stat().st_ino
    assert sorted(p.name for p in private.parent.iterdir()) == [package]
    result = subprocess.run([sys.executable, str(script), '--help'], cwd=output / 'native',
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '--ca_only' in result.stdout if selection == 'protein_mpnn' else '--model_type' in result.stdout


def test_installed_proteinmpnn_callable_uses_ca_checkpoint(tmp_path, monkeypatch):
    from proteinfoundation.metrics import inverse_folding_models as native
    calls = []
    def record(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(native.subprocess, 'run', record)
    monkeypatch.setattr(native, 'extract_gen_seqs_proteinmpnn', lambda path: [])
    native.run_proteinmpnn('input.pdb', str(tmp_path))
    assert len(calls) == 1
    assert './community_models/ProteinMPNN/protein_mpnn_run.py' in calls[0]
    assert '--ca_only' in calls[0]


def test_selected_projection_matches_native_ca_default():
    # Load only controller selector in its API environment in a separate suite;
    # here preserve actual native script's own default filename contract.
    source = Path('/opt/complexa/community_models/ProteinMPNN/protein_mpnn_run.py').read_text()
    assert "'/ca_model_weights/'" in source
    assert 'default="v_48_020"' in source
