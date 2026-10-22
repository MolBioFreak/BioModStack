from __future__ import annotations
import json
import os
from pathlib import Path
import pwd
import runpy
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'config/mk1d-reconnect'
INSTALLER = runpy.run_path(str(SOURCE / 'install-native-root-helper.py'))


@pytest.mark.parametrize('user', ['root', 'operator;command', '../operator', ''])
def test_native_recovery_rejects_root_or_invalid_owner(user):
    with pytest.raises(ValueError):
        INSTALLER['render_native_helper'](SOURCE, user)


@pytest.mark.parametrize('minknow_state,expected_start', [('active', False), ('inactive', True), ('deactivating', False)])
def test_native_recovery_preserves_active_minknow_and_has_one_fixed_user_service(
    tmp_path, minknow_state, expected_start,
):
    owner = pwd.getpwnam('nobody')
    files = INSTALLER['render_native_helper'](SOURCE, owner.pw_name)
    text = files['/usr/local/sbin/bms-reconnect-mk1d'][0]
    assert 'docker' not in text.lower().replace('minknow or docker', '')
    assert '127.0.0.1:18798' in text
    log = tmp_path / 'commands'
    tools = tmp_path / 'bin'; tools.mkdir()
    for name, body in {
        'systemctl': 'echo "systemctl $*" >> "$COMMAND_LOG"\nif [ "$1" = show ]; then echo "$MINKNOW_STATE"; fi\n',
        'setpriv': 'echo "setpriv $*" >> "$COMMAND_LOG"\n',
        'curl': 'echo "curl $*" >> "$COMMAND_LOG"\n',
    }.items():
        path = tools / name; path.write_text('#!/bin/sh\n'+body); path.chmod(0o755)
    # Redirect only the fixed host executables/lock in this isolated copy.
    text = text.replace('PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', f'PATH={tools}:/usr/bin:/bin')
    text = text.replace('/usr/bin/setpriv', str(tools / 'setpriv'))
    text = text.replace("readonly LOCK_FILE='/run/biomodstack/mk1d-reconnect.lock'", f"readonly LOCK_FILE='{tmp_path / 'lock'}'")
    script = tmp_path / 'helper'; script.write_text(text)
    result = subprocess.run(['bash', str(script)], capture_output=True, text=True,
                            env=os.environ | {'COMMAND_LOG':str(log), 'MINKNOW_STATE':minknow_state})
    receipt = json.loads(result.stdout)
    commands = log.read_text()
    assert ('systemctl start minknow.service' in commands) is expected_start
    assert 'systemctl restart minknow.service' not in commands
    if minknow_state == 'deactivating':
        assert receipt['status'] == 'blocked'
        assert 'setpriv' not in commands
    else:
        assert receipt['status'] == 'completed'
        assert '--user restart bns-minknow-host-agent.service' in commands
        assert 'curl --fail --silent --show-error --max-time 2 http://127.0.0.1:18798/ont/status' in commands
