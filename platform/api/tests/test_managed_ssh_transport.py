"""Actual shared command construction and bounded subprocess lifecycle."""
import asyncio
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest
from services.remote_execution import transport as t


@pytest.fixture
def connection(tmp_path, monkeypatch):
    key = tmp_path / 'managed key'; key.touch()
    monkeypatch.setenv('BMS_REMOTE_SSH_KEY', str(key))
    monkeypatch.setenv('BMS_REMOTE_KNOWN_HOSTS', str(tmp_path / 'pins'))
    monkeypatch.setenv('SSH_AUTH_SOCK', '/untrusted/agent')
    return t.RemoteConnection('fixture', 'example.test', 2222, 'root', '/opt/biomodstack')


def test_effective_openssh_configuration(connection):
    argv = t._ssh_base(connection)
    result = subprocess.run([*argv[:-1], '-G', argv[-1]], capture_output=True, text=True, check=True)
    config = dict(line.split(' ', 1) for line in result.stdout.splitlines())
    assert config['identitiesonly'] == 'yes'
    assert config['identityagent'] == 'none'
    assert config['preferredauthentications'] == 'publickey'
    assert config['stricthostkeychecking'] == 'true'
    assert config['globalknownhostsfile'] == '/dev/null'
    assert config.get('controlpath', 'none') == 'none'
    assert config['hostname'] == connection.host
    assert config['port'] == '2222'
    assert config['identityfile'] == str(t.private_key_path())
    assert config['userknownhostsfile'] == str(t.known_hosts_path())


@pytest.mark.asyncio
async def test_all_rsync_consumers_share_managed_ssh(connection, tmp_path, monkeypatch):
    calls = []
    async def run(argv, **kwargs):
        calls.append(argv)
        return t.CommandResult(0, '', '')
    monkeypatch.setattr(t, '_run', run)
    source = tmp_path / 'source'; source.mkdir()
    await t.rsync_to_remote(connection, source, '/opt/biomodstack/a')
    await t.rsync_from_remote(connection, '/opt/biomodstack/a', tmp_path / 'download')
    await t.rsync_selected_from_remote(connection, '/opt/biomodstack/a', tmp_path / 'selected', ['one'], max_file_bytes=10)
    assert len(calls) == 3
    for argv in calls:
        assert shlex.split(argv[argv.index('--rsh') + 1]) == t._ssh_base(connection)[:-1]


@pytest.mark.asyncio
async def test_real_marker_and_stdin_preserve_output(connection, monkeypatch):
    monkeypatch.setattr(t, '_ssh_base', lambda _: ['sh', '-c'])
    result = await t.run_remote(connection, [sys.executable, '-c', 'import sys;print(sys.stdin.read())'],
                                input_bytes=b'payload', establishment_timeout=1, timeout=1)
    assert result.stdout == 'payload\n'
    assert result.command_started is not None


@pytest.mark.asyncio
async def test_slow_establishment_does_not_consume_execution_budget():
    code = "import time;time.sleep(.2);print('BMS_COMMAND_READY',flush=True);time.sleep(.04);print('ok')"
    result = await t._run([sys.executable, '-c', code], establishment_timeout=1, timeout=.15)
    assert result.stdout == 'ok\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['establishment', 'execution', 'cancel'])
async def test_timeout_and_cancellation_reap_owned_group(tmp_path, phase):
    pidfile = tmp_path / 'child'
    ready = "print('BMS_COMMAND_READY',flush=True);" if phase != 'establishment' else ''
    code = ("import subprocess,time;from pathlib import Path;"
            "p=subprocess.Popen(['sleep','30']);"
            f"Path({str(pidfile)!r}).write_text(str(p.pid));" + ready + 'time.sleep(30)')
    task = asyncio.create_task(t._run([sys.executable, '-c', code], establishment_timeout=.2, timeout=.2))
    if phase == 'cancel':
        for _ in range(100):
            if pidfile.exists(): break
            await asyncio.sleep(.01)
        task.cancel()
    error = {'establishment': t.RemoteConnectionError, 'execution': t.RemoteExecutionTimeout,
             'cancel': asyncio.CancelledError}[phase]
    with pytest.raises(error): await task
    pid = int(pidfile.read_text())
    stat = Path(f'/proc/{pid}/stat')
    assert not stat.exists() or stat.read_text().split()[2] == 'Z'


@pytest.mark.asyncio
async def test_host_mismatch_and_missing_identity_refuse(connection, monkeypatch):
    # Actual phase owner receives SSH's host-key refusal before any marker.
    monkeypatch.setattr(t, '_ssh_base', lambda _: ['sh', '-c', 'exit 255', 'ignored'])
    with pytest.raises(t.RemoteConnectionError):
        await t.run_remote(connection, ['true'], establishment_timeout=1)
    monkeypatch.delenv('BMS_REMOTE_SSH_KEY')
    with pytest.raises(t.RemoteTransportError, match='not configured'):
        t.private_key_path()


@pytest.mark.asyncio
async def test_changed_pin_is_not_overwritten(connection):
    import base64, hashlib
    key = base64.b64encode(b'original').decode()
    line = f'[example.test]:2222 ssh-ed25519 {key}'
    await t.persist_host_key(line, hashlib.sha256(b'original').hexdigest())
    before = t.known_hosts_path().read_bytes()
    with pytest.raises(t.RemoteTransportError, match='changed'):
        await t.persist_host_key('[example.test]:2222 ssh-ed25519 ' + base64.b64encode(b'changed').decode(),
                                 hashlib.sha256(b'changed').hexdigest())
    assert t.known_hosts_path().read_bytes() == before
