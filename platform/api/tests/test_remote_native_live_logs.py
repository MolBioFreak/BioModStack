"""Real native files, with no workflow execution or network transport."""
import json
import os
import shutil

import pytest

from tools import bms_remote_log_reader as reader
from test_remote_live_logs import active, store
from test_remote_rectify_return import mounted


def native_files(attempt, status):
    native = attempt / 'native'
    native.mkdir()
    envelope = attempt / 'execution-envelope.json'
    envelope.write_text(json.dumps(dict(job_id='job', attempt_id='attempt',
        working_directory=str(native), environment={'SERVICE_TOKEN': 'envelope-value'})))
    (attempt / 'secret-env.json').write_text(json.dumps({'API_KEY': 'secret-file-value'}))
    (attempt / 'secret-env.json').chmod(0o600)
    for name in ('nextflow.log', 'supervisor.log'):
        (attempt / name).write_text('')
    (native / '.nextflow.log').write_text('native Nextflow launch\n')
    (native / 'component-root.log').write_text('component launch failure\n')
    return native, {key: getattr(status, key) for key in reader.IDENTITY_FIELDS}


@pytest.mark.asyncio
async def test_real_component_logs_reach_route_redacted(store, active):
    attempt, status, _, _ = active
    native, _ = native_files(attempt, status)
    (attempt / 'secret-env.json').unlink()  # Already consumed by the owned child.
    before = (attempt / 'status.json').read_bytes()
    with (native / 'component-root.log').open('a') as log:
        log.write('SERVICE_PASSWORD=assignment-value\nhttps://user:url-value@host/path\n'
                  'bare envelope-value and secret-file-value and inherited-value\n')
    async with mounted(store) as client:
        response = await client.get('/api/jobs/job/logs')
    assert response.status_code == 200
    data = response.json()
    assert data['nextflow_log'] == 'native Nextflow launch'
    assert 'component launch failure' in data['command_log']
    assert data['nextflow_log_source'] == 'remote_live'
    for secret in ('assignment-value', 'url-value', 'envelope-value', 'secret-file-value', 'inherited-value'):
        assert secret not in response.text
    assert '[REDACTED]' in data['command_log']
    assert (attempt / 'status.json').read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('observed', [False, True])
async def test_generation_offsets_and_unchanged_internal_log(active, observed):
    attempt, status, _, _ = active
    native, identity = native_files(attempt, status)
    offsets = {'component-root.log': (native / 'component-root.log').stat().st_size,
               'internal_mtime_ns': (native / '.nextflow.log').stat().st_mtime_ns}
    (attempt / 'status.json').write_text(status.model_copy(update={
        'diagnostic_offsets': offsets if observed else None}).model_dump_json())
    with (native / 'component-root.log').open('a') as log:
        log.write('new generation\n')
    result = reader.read_logs(str(attempt), identity, 200)
    assert result['nextflow_log'] is None
    assert result['command_log'] == ('new generation' if observed else None)


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['attempt', 'native', 'component-root.log', '.nextflow.log', 'nextflow.log'])
async def test_reachable_directories_and_members_reject_replacement(active, monkeypatch, target):
    attempt, status, _, _ = active
    native, identity = native_files(attempt, status)
    original = reader.os.open
    fired = False
    def racing(path, flags, *args, **kwargs):
        nonlocal fired
        # Both native members have been opened before the final status check.
        if path == 'status.json' and not fired:
            fired = True
        elif path == 'status.json':
            source = attempt if target == 'attempt' else native if target == 'native' else (
                attempt if target == 'nextflow.log' else native) / target
            moved = source.with_name(source.name + '.old')
            source.rename(moved)
            if moved.is_dir():
                shutil.copytree(moved, source)
            else:
                shutil.copyfile(moved, source)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(reader.os, 'open', racing)
    with pytest.raises(ValueError, match='replaced'):
        reader.read_logs(str(attempt), identity, 200)


@pytest.mark.asyncio
async def test_appending_during_read_is_allowed(active, monkeypatch):
    attempt, status, _, _ = active
    native, identity = native_files(attempt, status)
    original = reader.os.open
    def racing(path, flags, *args, **kwargs):
        if path == 'component-root.log':
            with (native / '.nextflow.log').open('a') as log:
                log.write('later append\n')
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(reader.os, 'open', racing)
    assert reader.read_logs(str(attempt), identity, 200)['command_log'] == 'component launch failure'


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['native', 'component-root.log', '.nextflow.log'])
async def test_native_symlinks_fail_closed(active, target):
    attempt, status, _, _ = active
    native, identity = native_files(attempt, status)
    source = native if target == 'native' else native / target
    moved = source.with_name(source.name + '.old')
    source.rename(moved)
    source.symlink_to(moved)
    with pytest.raises(OSError):
        reader.read_logs(str(attempt), identity, 200)


@pytest.mark.asyncio
async def test_cold_owner_never_declassifies_raw_logs(active):
    attempt, status, _, _ = active
    native, identity = native_files(attempt, status)
    (native / 'component-root.log').write_text('unlabelled unavailable secret\n')
    identity['workflow_start_ticks'] += 1
    (attempt / 'status.json').write_text(status.model_copy(update={'workflow_start_ticks': identity['workflow_start_ticks']}).model_dump_json())
    with pytest.raises(ValueError, match='owner unavailable'):
        reader.read_logs(str(attempt), identity, 200)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unreadable', 'owner-exits', 'status-owner-changes'])
async def test_environment_owner_fences_survive_full_read(active, monkeypatch, fault):
    from tools import bms_remote_worker as worker
    attempt, status, _, _ = active
    _, identity = native_files(attempt, status)
    original = reader.os.open
    def racing(path, flags, *args, **kwargs):
        if path == 'environ' and fault == 'unreadable':
            raise PermissionError('fixture denied environment')
        if path == 'component-root.log' and fault == 'status-owner-changes':
            (attempt / 'status.json').write_text(status.model_copy(update={
                'workflow_start_ticks': status.workflow_start_ticks + 1}).model_dump_json())
        if path == 'component-root.log' and fault == 'owner-exits':
            monkeypatch.setattr(worker, 'process_start_ticks', lambda pid: None)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(reader.os, 'open', racing)
    with pytest.raises((ValueError, PermissionError)):
        reader.read_logs(str(attempt), identity, 200)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['custody', 'plan', 'rewrite', 'mode', 'append', 'atomic-status'])
async def test_envelope_immutable_but_status_can_publish(active, monkeypatch, fault):
    attempt, status, _, _ = active
    native, identity = native_files(attempt, status)
    envelope = attempt / 'execution-envelope.json'
    value = json.loads(envelope.read_text())
    if fault in {'custody', 'plan'}:
        if fault == 'custody':
            value['working_directory'] = str(attempt.parent / 'other-attempt')
        else:
            value['plan_sha256'] = 'e' * 64
        envelope.write_text(json.dumps(value))
    original = reader.os.open
    def racing(path, flags, *args, **kwargs):
        if path == 'component-root.log':
            if fault == 'rewrite':
                envelope.write_bytes(envelope.read_bytes())
            elif fault == 'mode':
                envelope.chmod(0o600)
            elif fault == 'append':
                with envelope.open('a') as handle:
                    handle.write(' ')
            elif fault == 'atomic-status':
                replacement = attempt / 'replacement.json'
                replacement.write_text(status.model_dump_json())
                replacement.replace(attempt / 'status.json')
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(reader.os, 'open', racing)
    if fault == 'atomic-status':
        assert reader.read_logs(str(attempt), identity, 200)['command_log'] == 'component launch failure'
    else:
        with pytest.raises(ValueError):
            reader.read_logs(str(attempt), identity, 200)
