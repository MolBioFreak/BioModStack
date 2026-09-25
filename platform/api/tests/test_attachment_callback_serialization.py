"""Actual attachment callbacks and cache fan-out against isolated async SQLite.

Each scenario runs in a bounded child: the historical shared-session race can
hang aiosqlite cancellation/cleanup, so it must not strand the pytest process.
Only SSH/provider/runtime projection are doubles; callbacks and cache are real.
"""
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('scenario', ['parallel', 'attempt', 'lease', 'endpoint', 'cancel'])
def test_attachment_callbacks_are_serialized_without_serializing_transfers(tmp_path, scenario):
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), scenario, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1], env=dict(os.environ),
            capture_output=True, text=True, timeout=25,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f'attachment callback child hung ({scenario}): {exc.stdout!r} {exc.stderr!r}')
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'PASS {scenario}' in result.stdout
    print(result.stdout, end='')


class CacheComplete(BaseException):
    """Stop after actual cache completion, before unrelated runtime qualification."""


async def _scenario(scenario, directory):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from database import ExecutionTarget, Job
    from services.remote_execution import cache, critical_runtime, managed_inventory, targets
    from services.remote_execution.contracts import ExecutionTargetActivateRequest
    from test_vast_inventory_reconciliation import inventory

    engine = create_async_engine(f'sqlite+aiosqlite:///{directory}/callbacks.db')
    async with engine.begin() as connection:
        await connection.run_sync(ExecutionTarget.__table__.create)
        await connection.run_sync(Job.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with pytest.MonkeyPatch.context() as patch:
        inventory(patch, ['49674511'])
        patch.setenv('BMS_REMOTE_SSH_KEY', str(directory / 'offline-key'))
        (directory / 'offline-key').write_text('offline transport fixture')
        async def capture(*args):
            return 'fixture pinned key', 'a' * 64
        async def noop(*args, **kwargs):
            pass
        async def remote(*args, **kwargs):
            return SimpleNamespace(stdout='BMS_ATTACHED\nBMS_TELEMETRY\n')
        async def probe(*args):
            return {'gpus': ['fixture gpu']}
        patch.setattr(targets, 'capture_host_key', capture)
        patch.setattr(targets, 'persist_host_key', noop)
        patch.setattr(targets, 'run_remote', remote)
        patch.setattr(targets, 'probe_readiness', probe)
        def project(root, staging):
            artifacts = []
            for index in range(12):
                payload = str(index).encode()
                source = staging / str(index)
                source.write_bytes(payload)
                artifacts.append(SimpleNamespace(source=source, role='runtime',
                    sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload),
                    remote_destination=f'{root}/fixture/{index}'))
            return {'selection': 'offline-fixture'}, artifacts
        patch.setattr(critical_runtime, 'project_runtime', project)
        async def helper(connection, request, fence):
            await fence()
            assert request['action'] in {'boot', 'admit'}
            return {'boot_id': 'fixture-boot'}
        patch.setattr(managed_inventory, 'helper_call', helper)
        async def installed_helper(connection, fence):
            await fence()
            return 'offline-helper'
        patch.setattr(cache, '_install_helper', installed_helper)
        patch.setattr(cache, 'BATCH_COUNT', 2)
        patch.setattr(cache, 'BATCH_BYTES', 100)
        patch.setattr(cache, 'TRANSFER_CONCURRENCY', 6)
        actions = Counter()
        async def cache_remote(connection, args, *, input_bytes, timeout):
            request = json.loads(input_bytes)
            actions[request['action']] += 1
            await asyncio.sleep(0)
            response = ({'artifacts': [dict(entry, state='missing') for entry in request['artifacts']]}
                        if request['action'] == 'probe' else {})
            return SimpleNamespace(stdout=json.dumps(response))
        patch.setattr(cache, 'run_remote', cache_remote)
        transfers = Counter()
        all_transferring = asyncio.Event()
        release = asyncio.Event()
        async def rsync(*args, **kwargs):
            transfers['started'] += 1
            transfers['active'] += 1
            transfers['peak'] = max(transfers['peak'], transfers['active'])
            if transfers['active'] == 6:
                all_transferring.set()
            try:
                # All six must enter before any can finish: a lock around remote
                # operations, or around the entire cache call, deadlocks this barrier.
                await release.wait()
            except asyncio.CancelledError:
                transfers['cancelled'] += 1
                raise
            finally:
                transfers['active'] -= 1
        patch.setattr(cache, 'rsync_to_remote', rsync)
        completed = []
        async def activated(*args):
            completed.append(True)
            raise CacheComplete()
        patch.setattr(managed_inventory, 'activate_release', activated)

        async with factory() as session:
            started = await targets.begin_activation(session, ExecutionTargetActivateRequest(
                provider_instance_id='49674511', remote_root='/offline-worker'))
            owners = Counter()
            observed = Counter()
            def instrument(operation):
                async def tracked(*args, **kwargs):
                    task = asyncio.current_task()
                    owners[task] += 1
                    observed['peak'] = max(observed['peak'], len(owners))
                    observed[operation.__name__] += 1
                    if len(owners) > 1:
                        print('OVERLAPPING_SESSION_CALLBACKS', flush=True)
                    try:
                        await asyncio.sleep(0)
                        return await operation(*args, **kwargs)
                    except Exception as exc:
                        print(type(exc).__name__, str(exc), flush=True)
                        raise
                    finally:
                        owners[task] -= 1
                        if not owners[task]:
                            del owners[task]
                return tracked
            # Count distinct task owners over the COMPLETE set_setup transaction,
            # including nested refreshes; same-task nesting is not concurrency.
            patch.setattr(session, 'refresh', instrument(session.refresh))
            patch.setattr(targets, 'set_setup', instrument(targets.set_setup))
            task = asyncio.create_task(targets.finish_activation(session, started.id))
            try:
                await asyncio.wait_for(all_transferring.wait(), 8)
                assert transfers['peak'] == transfers['started'] == 6
                if scenario in {'attempt', 'lease', 'endpoint'}:
                    async with factory() as other:
                        row = await other.get(ExecutionTarget, started.id)
                        if scenario == 'attempt':
                            metadata = deepcopy(row.provider_metadata)
                            metadata['setup'].update(started_at='successor', message='successor owns progress')
                            row.provider_metadata = metadata
                        elif scenario == 'lease':
                            row.leased_job_id = 'successor-job'
                        else:
                            row.host = '203.0.113.99'
                        await other.commit()
                if scenario == 'cancel':
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert transfers['cancelled'] == 6
                    assert not completed
                    assert actions['ingest_many'] == actions['remove_incoming'] == 0
                else:
                    release.set()
                    if scenario == 'parallel':
                        with pytest.raises(CacheComplete):
                            await task
                        assert completed == [True]
                        assert actions['probe'] == actions['ingest_many'] == actions['remove_incoming'] == 6
                    else:
                        with pytest.raises(targets.ExecutionTargetError, match='changed during attachment'):
                            await task
                        assert not completed
                        assert actions['ingest_many'] == actions['remove_incoming'] == 0
                assert transfers['active'] == 0
                assert observed['peak'] == 1, dict(observed)
                assert observed['set_setup'] >= 7
                assert not owners
                await session.refresh(await session.get(ExecutionTarget, started.id))
                row = await session.get(ExecutionTarget, started.id)
                assert row.state != 'ready'
                if scenario == 'attempt':
                    assert row.provider_metadata['setup']['started_at'] == 'successor'
                    assert row.provider_metadata['setup']['message'] == 'successor owns progress'
                elif scenario == 'lease':
                    assert row.leased_job_id == 'successor-job'
                elif scenario == 'endpoint':
                    assert row.host == '203.0.113.99'
                elif scenario == 'parallel':
                    assert row.provider_metadata['setup']['phase'] == 'verifying'
                print(f'PASS {scenario} transfers={dict(transfers)} db={dict(observed)} actions={dict(actions)}', flush=True)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    await engine.dispose()


if __name__ == '__main__':
    # Direct execution needs both API modules and sibling fixture utilities.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    asyncio.run(_scenario(sys.argv[1], Path(sys.argv[2])))
