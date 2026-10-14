"""Exact sealing shell placement; tiny inert bytes, never model inference."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('retained', [False, True])
def test_selected_model_sealing_shell_retains_generation_without_copy(tmp_path, retained):
    generation = tmp_path/'selected-generation'
    model = generation/'selected'
    model.mkdir(parents=True)
    leaf = model/'inert-weight'
    leaf.write_bytes(b'placement-only')
    leaf.chmod(0o444)
    digest = hashlib.sha256(b'inert-weight\0' + str(leaf.stat().st_size).encode() + b'\0' + hashlib.sha256(leaf.read_bytes()).hexdigest().encode() + b'\n').hexdigest()
    record = dict(aggregate_sha256=digest, files=1, bytes=leaf.stat().st_size, retained=retained)
    module = (ROOT/'modules/ngs/dorado_basecall.nf').read_text()
    functions = module[module.index('    verify_model_tree() {'):module.index('    runtime_expected=')]
    # Rebase only the container bind prefix; execute the actual production shell.
    functions = functions.replace('/weights/dorado/2.1.2', str(generation)).replace('\\$', '$').replace('\\\\0', '\\0').replace('\\\\n', '\\n')
    outputs = []
    for name in ('first', 'second'):
        work = tmp_path/name
        work.mkdir()
        (work/'dorado_preflight.json').write_text(json.dumps({'model':record}))
        result = subprocess.run(['bash','-euc', functions + '\nmkdir sealed_models; snapshot_model selected .model'],cwd=work,capture_output=True,text=True)
        assert result.returncode == 0, result.stderr
        outputs.append(work/'sealed_models/selected/inert-weight')
    assert (outputs[0].stat().st_ino == outputs[1].stat().st_ino == leaf.stat().st_ino) is retained
    # Removing one consumer's alias cannot invalidate another or the selected
    # immutable generation. Installer lifetime remains shared-owner acceptance.
    import shutil
    shutil.rmtree(tmp_path/'first')
    assert outputs[1].read_bytes() == leaf.read_bytes()


@pytest.mark.parametrize('retained,readonly', [(True,True),(True,False),(False,True)])
def test_pod5_task_naming_uses_authority_not_mode_alone(tmp_path, retained, readonly):
    leaf = tmp_path/'source.pod5'
    leaf.write_bytes(b'POD5 placement-only; not a scientific fixture')
    leaf.chmod(0o444 if readonly else 0o644)
    target = tmp_path/'sealed.pod5'
    result = subprocess.run(['bash','-euc','source "$1"; ngs_input_snapshot "$2" "$3" "$4"','fixture',str(ROOT/'scripts/ngs_input_snapshot.sh'),str(leaf),str(target),str(retained).lower()],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert (leaf.stat().st_ino == target.stat().st_ino) is (retained and readonly)
