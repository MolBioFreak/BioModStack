"""Offline setup qualification; executable fixtures are not worker acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from services import msa_provider_setup as setup
from test_managed_runtime_safety import critical_package, install_critical_fixture

API = Path(__file__).resolve().parents[1]
BOOTSTRAP = API / 'services/remote_execution/bootstrap_worker.sh'


def executable(path, source):
    path.write_text(source)
    path.chmod(0o755)


@pytest.mark.parametrize('version', [(3, 8), (3, 9), (3, 10), (3, 11), (3, 12), None])
@pytest.mark.parametrize('mode', ['check', 'install'])
def test_bootstrap_qualifies_python_before_any_setup(tmp_path, version, mode):
    """Run the actual shell entrypoint with capability/command doubles only."""
    tools = tmp_path / 'bin'
    tools.mkdir()
    actions = tmp_path / 'actions'
    env = {**os.environ, 'PATH': str(tools), 'ACTIONS': str(actions)}
    for name in ('unshare', 'nvidia-smi', 'rsync', 'tar', 'sha256sum', 'curl', 'apptainer'):
        executable(tools / name, '#!/bin/sh\nexit 0\n')
    executable(tools / 'id', '#!/bin/sh\nprintf "0\\n"\n')
    executable(tools / 'uname', '#!/bin/sh\nprintf "x86_64\\n"\n')
    executable(tools / 'java', '#!/bin/sh\nprintf \'openjdk version "17.0.1"\\n\'\n')
    (tools / 'grep').symlink_to('/usr/bin/grep')
    for name in ('apt-get', 'mkdir', 'chown'):
        executable(tools / name, '#!/bin/sh\nprintf "mutation\\n" >> "$ACTIONS"\nexit 99\n')
    if version is not None:
        executable(tools / 'python3', f'#!{sys.executable}\nimport sys\n'
                   f'sys.version_info = {version!r}\nexec(sys.argv[2])\n')
    # Compatible install is a no-op; incompatible setup must not create a root.
    compatible = version is not None and version >= (3, 11)
    worker = tmp_path / 'worker'
    if compatible:
        worker.mkdir()
    # Mock only /etc/os-release at the shell's source boundary: the test host
    # need not itself be an accepted worker distro. Execute unchanged source.
    shell = '.() { [ "$1" = /etc/os-release ] || exit 99; ID=ubuntu; }; source "$@"'
    result = subprocess.run(['/bin/bash', '-c', shell, 'bootstrap-fixture',
                             str(BOOTSTRAP), mode, str(worker)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == (0 if compatible else 20), result.stdout + result.stderr
    if not compatible:
        from services.remote_execution.transport import _controlled_remote_failure
        message = _controlled_remote_failure(result.stdout)
        assert message and 'System python3 3.11 or newer is required' in message
    assert not actions.exists()


def test_supported_python_runs_managed_helper_and_provisioning(critical_package, monkeypatch, tmp_path):
    """Real helper CLI/cache/transport, fake native runtime bytes and version tools."""
    python = os.environ.get('BMS_TEST_HELPER_PYTHON', sys.executable)
    version = subprocess.check_output([python, '-c', 'import sys; print(sys.version_info[:2])'], text=True)
    assert version.strip() in ('(3, 11)', '(3, 12)', '(3, 13)', '(3, 14)')
    manifest, _, _, worker = critical_package
    _, _, root = install_critical_fixture(critical_package, monkeypatch)
    tools = tmp_path / 'version-tools'
    tools.mkdir()
    for tool in ('java', 'apptainer', 'nvidia-smi'):
        executable(tools / tool, '#!/bin/sh\nprintf "offline-version-fixture\\n"\n')
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']}
    command = [python, str(API / 'tools/bms_managed_runtime.py'), '--root', str(root),
               '--cache-helper', str(API / 'tools/bms_artifact_cache.py')]
    def call(action, **fields):
        result = subprocess.run(command, input=json.dumps(dict(action=action, **fields)),
                                text=True, capture_output=True, env=env, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    boot = call('boot')['boot_id']
    assert call('admit', manifest=manifest, boot_id=boot)['boot_id'] == boot
    installed = call('install', manifest=manifest, boot_id=boot)
    assert installed['release']['state'] == 'verified'
    assert call('observe', manifests=[manifest])['releases'][0]['state'] == 'verified'
    from services.remote_execution.transport import RemoteConnection, _provision_argv
    connection = RemoteConnection(target_id='fixture', host='fixture.invalid', port=22, username='fixture',
                                  remote_root=str(worker))
    for mode in ('run', 'quiesce'):
        argv = _provision_argv(connection, 'setup-qualification', mode,
                               [python, '-c', 'print("fixture-payload")'] if mode == 'run' else [])
        assert argv[0] == 'python3'
        result = subprocess.run([python, *argv[1:]], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        if mode == 'run':
            assert result.stdout.strip() == 'fixture-payload'
        else:
            assert json.loads(result.stdout)['quiescent'] is True


@pytest.mark.parametrize('kind,blocker', [
    ('valid', None), ('missing_parents', None), ('file', 'must be a directory'),
    ('symlink', 'must not traverse symlinks'), ('parent_symlink', 'must not traverse symlinks'),
    ('public', 'service-owned'), ('wrong_uid', 'service-owned'),
    ('unwritable', 'not writable'), ('parent_unwritable', 'not writable'),
    ('parent_file', 'not writable'),
])
def test_colabfold_checks_actual_outer_state_without_mutation(tmp_path, monkeypatch, kind, blocker):
    state = tmp_path / 'outer-state'
    if kind in ('valid', 'public', 'unwritable', 'wrong_uid'):
        state.mkdir(mode=0o700)
    elif kind == 'file':
        state.write_text('not a directory')
    elif kind == 'symlink':
        state.symlink_to(tmp_path)
    elif kind == 'parent_symlink':
        state.symlink_to(tmp_path)
        state = state / 'not-created'
    elif kind == 'parent_file':
        state.write_text('not a directory')
        state = state / 'not-created'
    else:
        state = state / 'missing-parent' / 'state'
    if kind == 'public':
        state.chmod(0o777)
    if kind == 'wrong_uid':
        original = Path.stat
        def wrong_uid(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == state:
                fields = list(info)
                fields[4] += 1
                return os.stat_result(fields)
            return info
        monkeypatch.setattr(Path, 'stat', wrong_uid)
    if kind in ('unwritable', 'parent_unwritable'):
        original = os.access
        blocked = state if kind == 'unwritable' else tmp_path
        monkeypatch.setattr(os, 'access', lambda path, mode: False if Path(path) == blocked else original(path, mode))
    cache, inner = tmp_path / 'cache', tmp_path / 'inner'
    cache.mkdir(mode=0o700)
    inner.mkdir(mode=0o700)
    monkeypatch.setattr(setup, 'cache_root', lambda: cache)
    monkeypatch.setenv('BMS_MSA_API_STATE_ROOT', str(inner))
    config = tmp_path / 'controller.json'
    config.write_text(json.dumps(dict(machine_id=Path('/etc/machine-id').read_text().strip(),
        qualified_single_egress=True, egress_identity='private-fixture-egress', role='msa_controller',
        state_dir=str(state))))
    monkeypatch.setenv('BMS_MSA_CONTROLLER_CONFIG', str(config))
    # Deny all readiness writes and credential reads, not only state creation.
    key = tmp_path / 'key'
    key.write_text('private-fixture-key')
    key.chmod(0o600)
    monkeypatch.setenv('BMS_NEUROSNAP_API_KEY_FILE', str(key))
    original_open = Path.open
    def readonly(path, mode='r', *args, **kwargs):
        assert path != key and not any(flag in mode for flag in 'wax+'), 'readiness attempted mutation/secret read'
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', readonly)
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: pytest.fail('readiness created a directory'))
    before = set(tmp_path.rglob('*'))
    result = setup.provider_readiness()
    provider = result['providers']['colabfold_api']
    assert provider['configured'] is (blocker is None)
    if blocker:
        assert any('ColabFold controller state' in item and blocker in item for item in provider['blockers'])
    assert provider['authentication'] == 'not_required'
    assert provider['live_acceptance'] == 'not_checked_by_setup'
    assert set(tmp_path.rglob('*')) == before
    assert not any(value in json.dumps(result) for value in (str(state), 'private-fixture-egress', 'private-fixture-key'))
