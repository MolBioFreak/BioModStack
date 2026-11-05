"""Shared adapter launcher selection; no scientific runtime is launched."""
import os
import subprocess

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("installed", [False, True])
def test_adapter_prefix_selects_only_apptainer_installation(tmp_path, installed):
    home = tmp_path / "home"
    local = home / ".local/bin"
    local.mkdir(parents=True)
    selected = home / "apptainer-install/bin"
    selected.mkdir(parents=True)
    executable = selected / "apptainer"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    if installed:
        (local / "apptainer").symlink_to(executable)
    # A user tool must not shadow the system's tools as a side effect.
    shadow = local / "dirname"
    shadow.write_text("#!/bin/sh\nexit 99\n")
    shadow.chmod(0o755)
    script = (REPO_ROOT / "scripts/run_biomodstack_workflow_adapter.sh").read_text()
    prefix = 'export NVM_DIR' + script.split('export NVM_DIR', 1)[1].split('pin_nextflow_java()', 1)[0]
    env = {**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(["bash", "-c", prefix + '\nprintf "%s\\n" "$PATH"; command -v dirname'],
                            env=env, check=True, capture_output=True, text=True)
    path, dirname = result.stdout.strip().splitlines()
    expected = "/usr/local/bin:/usr/bin:/bin:" + str(home / ".cargo/bin") + ":" + str(local) + ":/usr/bin:/bin"
    assert path == ((str(selected) + ":") if installed else "") + expected
    assert dirname == "/usr/bin/dirname"


def test_workflow_adapter_prefers_the_installed_apptainer_without_reordering_path():
    script = (REPO_ROOT / 'scripts/run_biomodstack_workflow_adapter.sh').read_text()
    original = 'export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"'
    assert original in script
    assert script.index(original) < script.index('readlink -f "$HOME/.local/bin/apptainer"')
    assert 'export PATH="$_bms_apptainer_dir:$PATH"' in script

