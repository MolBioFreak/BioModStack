"""Offline automatic attachment lifecycle and provisioning regressions."""
import asyncio
from types import SimpleNamespace

import pytest
import subprocess
import os
from pathlib import Path


@pytest.fixture(autouse=True)
def no_hardware_probe(monkeypatch):
    original = subprocess.run
    def run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and Path(str(argv[0])).name == 'nvidia-smi':
            return subprocess.CompletedProcess(argv, 1, '', 'offline test')
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', run)


@pytest.mark.parametrize('stage', ['apt', 'download', 'checksum'])
def test_bootstrap_shell_reports_controlled_install_failures(tmp_path, stage):
    script = Path(targets.__file__).with_name('bootstrap_worker.sh').read_text().replace('/etc/os-release', str(tmp_path / 'os-release'))
    (tmp_path / 'os-release').write_text('ID=ubuntu\n')
    prefix = r'''
id() { echo 0; }
uname() { echo x86_64; }
unshare() { :; }
nvidia-smi() { :; }
java() { return 1; }
apptainer() { return 1; }
env() { shift; "$@"; }
apt-get() { test "$FAIL_STAGE" != apt; }
curl() { test "$FAIL_STAGE" != download; }
sha256sum() { /bin/cat >/dev/null; test "$FAIL_STAGE" != checksum; }
'''
    result = subprocess.run(['bash', '-c', prefix + script], env={**os.environ, 'FAIL_STAGE': stage}, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'BMS_SETUP_ERROR:' in result.stdout
    assert {'apt': 'Package installation failed', 'download': 'Apptainer download failed', 'checksum': 'Apptainer checksum mismatch'}[stage] in result.stdout


def test_bootstrap_script_installs_missing_only_and_rejects_namespaces(tmp_path):
    script_path = Path(targets.__file__).with_name('bootstrap_worker.sh')
    assert script_path.exists(), 'automatic VM bootstrap script is missing'
    script = script_path.read_text().replace('/etc/os-release', str(tmp_path / 'os-release'))
    (tmp_path / 'os-release').write_text('ID=ubuntu\n')
    prefix = r'''
set -eu
env() { shift; "$@"; }
id() { echo 0; }
uname() { echo x86_64; }
unshare() { test "${BLOCKED:-0}" = 0; }
mount() { :; }
umount() { :; }
nvidia-smi() { :; }
java() { test -f "$STATE/java" && echo 'openjdk version "17.0.1"' >&2; }
apptainer() { test -f "$STATE/apptainer" && echo 'apptainer version 1.3.0'; }
apt-get() {
  printf '%s\n' "$*" >> "$STATE/apt-log"
  case "$*" in *openjdk*) touch "$STATE/java";; esac
  case "$*" in *apptainer.deb*) touch "$STATE/apptainer";; esac
}
curl() { printf '%s\n' "$*" >> "$STATE/download-log"; }
sha256sum() { /bin/cat >/dev/null; }
export -f id uname unshare mount umount nvidia-smi java apptainer apt-get curl sha256sum
'''
    env = {**os.environ, 'STATE': str(tmp_path)}
    first = subprocess.run(['bash', '-c', prefix + script], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    calls = (tmp_path / 'apt-log').read_text()
    assert 'openjdk-17-jre-headless' in calls and 'apptainer.deb' in calls
    assert 'upgrade' not in calls
    second = subprocess.run(['bash', '-c', prefix + script], env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / 'apt-log').read_text() == calls
    (tmp_path / 'java').unlink()
    root = tmp_path / 'worker-root'
    nonroot_prefix = prefix + '''\nid() { echo 1000; }\nsudo() { shift; "$@"; }\nchown() { printf '%s\\n' "$*" >> "$STATE/chown-log"; }\n'''
    nonroot = subprocess.run(['bash', '-c', nonroot_prefix + script, 'bootstrap', 'install', str(root)], env=env, capture_output=True, text=True)
    assert nonroot.returncode == 0, nonroot.stderr
    assert root.is_dir() and str(root) in (tmp_path / 'chown-log').read_text()
    calls = (tmp_path / 'apt-log').read_text()
    blocked = subprocess.run(['bash', '-c', prefix + script], env={**env, 'BLOCKED': '1'}, capture_output=True, text=True)
    assert blocked.returncode != 0
    assert (tmp_path / 'apt-log').read_text() == calls

from database import ExecutionTarget
from services.remote_execution import targets
from services.remote_execution.contracts import ExecutionTargetActivateRequest
from test_vast_inventory_reconciliation import store, inventory


def test_bootstrap_failure_whitelist_rejects_untrusted_detail():
    from services.remote_execution.transport import _controlled_remote_failure
    assert _controlled_remote_failure('BMS_SETUP_ERROR:Apptainer checksum mismatch') == 'Apptainer checksum mismatch'
    assert _controlled_remote_failure('BMS_SETUP_ERROR:secret token') is None


from test_managed_runtime_safety import critical_package, load


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['25.10.1', '25.10.2'])
@pytest.mark.parametrize('damage', [None, 'jar', 'python', 'cuda', 'leased'])
async def test_full_attach_bootstraps_transfers_and_verifies_before_ready(
        store, monkeypatch, critical_package, damage, version):
    from services.remote_execution import cache, managed_inventory as mi
    session, factory = store
    _, _, requirements, worker = critical_package
    inventory(monkeypatch, ['49674511'])
    m, c = load('bms_managed_runtime'), load('bms_artifact_cache')
    monkeypatch.setattr(m, 'observed_compatibility', lambda: dict(requirements))
    root = worker / 'managed-assets/v1'
    async def noop(*args, **kwargs): pass
    async def capture(*args): return ('fixture key', 'a'*64)
    async def run(conn, argv, **kw):
        await session.refresh(await targets.get_target(session, 'vast:49674511'))
        assert not (await targets.get_target(session, 'vast:49674511')).active
        if argv[0] == 'apptainer':
            if damage == 'leased':
                async with factory() as other:
                    row = await targets.get_target(other, 'vast:49674511')
                    row.leased_job_id = 'racing'
                    await other.commit()
            if damage == 'cuda':
                raise targets.RemoteTransportError('fixture failure')
            return SimpleNamespace(stdout='BMS_CUDA_OK\n')
        return SimpleNamespace(stdout='')
    async def helper(conn, request, fence):
        await fence()
        action = request['action']
        if action == 'boot': result = {}
        elif action == 'admit': result = m.admit(root, request['manifest'], request['boot_id'], c)
        elif action == 'install': result = m.install(root, request['manifest'], request['boot_id'], c)
        elif action == 'observe': result = {'releases': [m.observe(root, v, c) for v in request['manifests']]}
        elif action == 'bounded_check':
            releases = [m.observe(root, v, c) for v in request['manifests']]
            for release in releases:
                release['native_readiness'] = m.bounded_native_check(release)
            result = {'releases': releases}
        else: raise AssertionError(action)
        await fence()
        return result | {'boot_id': m.boot_id()}
    async def push(*, connection, artifacts, operation_id, progress, check_fence):
        storage = c.Cache(worker / 'cache/artifacts/v1')
        for entry in artifacts:
            await check_fence()
            assert not (root / 'active/critical_runtime-worker.json').exists()
            if ((damage == 'jar' and entry.remote_destination.endswith('.jar'))
                    or (damage == 'python' and entry.remote_destination == 'support-python/venv/bin/python')):
                continue
            incoming = Path(storage.root) / 'incoming' / entry.sha256
            incoming.write_bytes(entry.source.read_bytes())
            storage.ingest(dict(sha256=entry.sha256, size_bytes=entry.size_bytes), incoming)
    async def probe(*args): return {'gpus': ['fixture gpu']}
    monkeypatch.setattr(targets, 'capture_host_key', capture)
    monkeypatch.setattr(targets, 'persist_host_key', noop)
    monkeypatch.setattr(targets, 'run_remote', run)
    monkeypatch.setattr(targets, 'probe_readiness', probe)
    monkeypatch.setattr(mi, 'helper_call', helper)
    monkeypatch.setattr(cache, '_cache_artifacts', push)
    started = await targets.begin_activation(session, ExecutionTargetActivateRequest(
        provider_instance_id='49674511', remote_root=str(worker)))
    if damage:
        with pytest.raises(targets.ExecutionTargetError):
            await targets.finish_activation(session, started.id)
        row = await targets.get_target(session, started.id)
        assert not row.active
        assert row.leased_job_id == 'racing' if damage == 'leased' else row.state == 'unavailable'
        assert not (root / 'active/critical_runtime-worker.json').exists()
    else:
        result = await targets.finish_activation(session, started.id)
        assert result.setup is not None and result.setup.phase == 'ready'
        binding = result.capabilities['critical_runtime_binding']
        assert binding['environment']['NXF_OFFLINE'] == 'true'
        assert binding['environment']['NXF_VER'] == version
        assert Path(binding['paths']['jar']).is_file()
        assert result.capabilities['critical_runtime']['state'] == 'verified'


@pytest.mark.asyncio
async def test_attach_default_uses_existing_transport_without_version_profile(store, monkeypatch):
    session, _ = store
    inventory(monkeypatch, ['49674511'])
    contacted = []
    async def unavailable(*args, **kwargs):
        contacted.append(args)
        raise targets.RemoteTransportError('Remote transport timed out')
    monkeypatch.setattr(targets, 'capture_host_key', unavailable)
    with pytest.raises(targets.ExecutionTargetError, match='Remote transport timed out'):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    target = await targets.get_target(session, 'vast:49674511')
    assert len(contacted) == 1
    assert target.state == 'unavailable' and not target.active


@pytest.mark.asyncio
async def test_activate_http_accepted_uses_managed_controller(store, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from routers import execution_targets
    from database import get_session
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    hold = asyncio.Event()
    async def finish(*args): await hold.wait()
    monkeypatch.setattr(targets, 'finish_activation', finish)
    app = FastAPI()
    app.include_router(execution_targets.router, prefix='/targets')
    controller = targets.AttachmentController(factory)
    app.state.attachment_controller = controller
    async def db(): yield session
    app.dependency_overrides[get_session] = db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await asyncio.wait_for(client.post('/targets/activate', json={'provider_instance_id': '49674511'}), 2)
        assert response.status_code == 202
        assert response.json()['state'] == 'probing'
        assert response.json()['setup']['phase'] == 'checking'
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_async_attach_persists_progress_and_rejects_duplicate(store, monkeypatch):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    entered, release = asyncio.Event(), asyncio.Event()
    async def finish(db, identifier):
        entered.set()
        await release.wait()
        row = await targets.get_target(db, identifier)
        await targets.set_setup(db, row, 'ready', 'Remote worker ready')
        row.state, row.active = 'ready', True
        await db.commit()
    monkeypatch.setattr(targets, 'finish_activation', finish, raising=False)
    controller = targets.AttachmentController(factory)
    request = ExecutionTargetActivateRequest(provider_instance_id='49674511')
    result = await controller.attach(session, request)
    assert result.state == 'probing' and result.setup.phase == 'checking'
    await entered.wait()
    with pytest.raises(targets.ExecutionTargetError, match='in progress'):
        await controller.attach(session, request)
    async with factory() as other:
        assert (await targets.list_targets(other))[0].setup.phase == 'checking'
    release.set()
    await asyncio.gather(*controller.tasks.values())
    async with factory() as other:
        assert (await targets.list_targets(other))[0].setup.phase == 'ready'
    await controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('detail', ['Apptainer checksum mismatch', 'Remote transport timed out'])
async def test_transport_failure_is_specific_and_cannot_overwrite_racing_lease(store, monkeypatch, detail):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    async def fail(*args): raise targets.RemoteTransportError(detail)
    monkeypatch.setattr(targets, 'capture_host_key', fail)
    with pytest.raises(targets.ExecutionTargetError, match=detail):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    row = await targets.get_target(session, 'vast:49674511')
    assert detail in row.last_error
    assert row.provider_metadata['setup']['phase'] == 'failed'
    async def racing(*args):
        async with factory() as other:
            other_row = await targets.get_target(other, 'vast:49674511')
            other_row.state, other_row.active, other_row.leased_job_id = 'ready', True, 'racing'
            await other.commit()
        raise targets.RemoteTransportError(detail)
    monkeypatch.setattr(targets, 'capture_host_key', racing)
    with pytest.raises(targets.ExecutionTargetError):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    row = await targets.get_target(session, 'vast:49674511')
    assert row.state == 'ready' and row.active and row.leased_job_id == 'racing'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stopped', 'endpoint'])
@pytest.mark.parametrize('restart', [False, True])
async def test_inventory_interruption_finishes_setup_without_replacing_inventory(store, monkeypatch, change, restart):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    saved = {}
    async def interrupt(db, identifier):
        row = await targets.get_target(db, identifier)
        await targets.set_setup(db, row, 'installing', 'Installing tools')
        inventory(monkeypatch, ['49674511'], state='stopped' if change == 'stopped' else 'running',
                  host='203.0.113.99' if change == 'endpoint' else '203.0.113.10')
        async with factory() as other:
            await targets.refresh_vast_targets(other)
            current = await targets.get_target(other, identifier)
            saved.update(state=current.state, active=current.active, host=current.host,
                         error=current.last_error, inventory=dict(current.provider_metadata['inventory']))
        raise targets.ExecutionTargetError('Vast inventory or endpoint changed during attachment')
    controller = targets.AttachmentController(factory)
    request = ExecutionTargetActivateRequest(provider_instance_id='49674511')
    try:
        if restart:
            result = await targets.begin_activation(session, request)
            with pytest.raises(targets.ExecutionTargetError):
                await interrupt(session, result.id)
            await controller.recover()
        else:
            monkeypatch.setattr(targets, 'finish_activation', interrupt)
            await controller.attach(session, request)
            await asyncio.gather(*controller.tasks.values())
        async with factory() as other:
            row = await targets.get_target(other, 'vast:49674511')
            assert row.provider_metadata['setup']['phase'] == 'failed'
            assert (row.state, row.active, row.host, row.last_error) == (saved['state'], saved['active'], saved['host'], saved['error'])
            assert row.provider_metadata['inventory'] == saved['inventory']
            assert row.leased_job_id is None
    finally:
        await controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('new_owner', ['successor', 'lease'])
async def test_old_setup_cleanup_preserves_new_owner(store, monkeypatch, new_owner):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    saved = {}
    async def replaced(db, identifier):
        row = await targets.get_target(db, identifier)
        await targets.set_setup(db, row, 'transferring', 'Transferring old attempt')
        async with factory() as other:
            inventory(monkeypatch, ['49674511'], host='203.0.113.99')
            await targets.refresh_vast_targets(other)
            current = await targets.get_target(other, identifier)
            if new_owner == 'successor':
                await targets.begin_activation(other, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
                await other.refresh(current)
            else:
                current.leased_job_id = 'new-lease'
                await other.commit()
            saved.update(state=current.state, active=current.active, error=current.last_error,
                         setup=dict(current.provider_metadata['setup']), lease=current.leased_job_id)
        raise targets.ExecutionTargetError('Vast inventory or endpoint changed during attachment')
    monkeypatch.setattr(targets, 'finish_activation', replaced)
    controller = targets.AttachmentController(factory)
    try:
        await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
        await asyncio.gather(*controller.tasks.values())
        async with factory() as other:
            row = await targets.get_target(other, 'vast:49674511')
            assert row.provider_metadata['setup'] == saved['setup']
            assert (row.state, row.active, row.last_error, row.leased_job_id) == (saved['state'], saved['active'], saved['error'], saved['lease'])
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_controller_preserves_controlled_inventory_failure(store, monkeypatch):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    detail = 'Vast inventory or endpoint changed during attachment'
    async def fail(*args):
        raise targets.ExecutionTargetError(detail)
    monkeypatch.setattr(targets, 'finish_activation', fail)
    controller = targets.AttachmentController(factory)
    try:
        await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
        await asyncio.gather(*controller.tasks.values())
        row = await targets.get_target(session, 'vast:49674511')
        assert row.last_error == row.provider_metadata['setup']['message'] == detail
        assert row.state == 'unavailable' and not row.active
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_failed_setup_is_sanitized_and_restart_requires_retry(store, monkeypatch):
    session, factory = store
    inventory(monkeypatch, ['49674511'])
    async def fail(*args):
        raise RuntimeError('secret provider token must not escape')
    monkeypatch.setattr(targets, 'finish_activation', fail, raising=False)
    controller = targets.AttachmentController(factory)
    await controller.attach(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    await asyncio.gather(*controller.tasks.values())
    row = await targets.get_target(session, 'vast:49674511')
    assert row.state == 'unavailable'
    assert row.provider_metadata['setup']['phase'] == 'failed'
    assert 'secret' not in row.last_error
    row.state = 'probing'
    await session.commit()
    await controller.recover()
    await session.refresh(row)
    assert row.state == 'unavailable' and 'retry' in row.last_error.lower()
    await controller.close()
