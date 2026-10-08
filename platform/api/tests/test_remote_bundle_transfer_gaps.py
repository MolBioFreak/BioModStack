"""Isolated file-transfer regressions; no endpoints or Git mutations."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

import pytest

from services.remote_execution import bundle, transport
from services.remote_execution.contracts import RemoteFileRecord
from tools import bms_remote_worker as worker


def test_package_binds_executable_mode(tmp_path):
    executable = tmp_path / 'python'
    executable.write_bytes(b'executable')
    executable.chmod(0o755)
    record = bundle._record_file(executable, 'runtime/python', 'runtime')
    assert record.model_dump()['mode'] == 0o755


@pytest.mark.parametrize('mode', [0o4755, 0o2755, 0o1755])
def test_packaging_rejects_unapproved_special_modes(tmp_path, mode):
    path = tmp_path/'helper'
    path.write_bytes(b'helper')
    path.chmod(mode)
    with pytest.raises(bundle.RemoteBundleError, match='mode'):
        bundle._record_file(path, 'runtime/helper', 'runtime')


@pytest.mark.asyncio
async def test_real_local_rsync_preserves_executable_metadata(tmp_path):
    source = tmp_path/'source'
    destination = tmp_path/'destination'
    source.mkdir()
    destination.mkdir()
    executable = source/'helper'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o775)
    before = bundle._record_file(executable, 'runtime/helper', 'runtime')
    result = await transport._run(['rsync', '--archive', str(source)+'/', str(destination)+'/'])
    assert result.returncode == 0
    assert bundle._record_file(destination/'helper', 'runtime/helper', 'runtime') == before


def test_worker_rejects_mode_tamper(tmp_path):
    source = tmp_path / 'bundle/source'
    source.mkdir(parents=True)
    archive = source / '.bms-source.tar'
    archive.write_bytes(b'archive')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    record = dict(relative_path='source/.bms-source.tar', size_bytes=7,
                  sha256=digest, role='source', mode=0o755)
    envelope = dict(schema='bms.remote-execution.v1', command=['python3'],
                    files=[record], source_archive_sha256=digest,
                    working_directory=str(source), output_directory=str(tmp_path/'results'))
    (tmp_path / worker.ENVELOPE_FILE).write_text(json.dumps(envelope))
    archive.chmod(0o755)
    worker.verify_bundle(tmp_path)
    archive.chmod(0o644)
    with pytest.raises(RuntimeError, match='mode'):
        worker.verify_bundle(tmp_path)


@pytest.mark.asyncio
async def test_upload_preserves_approved_modes(tmp_path, monkeypatch):
    captured = []
    async def run(argv, **kwargs):
        captured.extend(argv)
        return transport.CommandResult(0, '', '')
    monkeypatch.setattr(transport, '_run', run)
    monkeypatch.setattr(transport, '_ssh_base', lambda connection: ['ssh', 'worker'])
    connection = transport.RemoteConnection('test', 'unused', 22, 'user', '/remote')
    await transport.rsync_to_remote(connection, tmp_path, '/remote')
    assert '--archive' in captured
    assert not any(arg.startswith('--chmod=') for arg in captured)


@pytest.mark.asyncio
async def test_cancel_transport_reaps_process_group(tmp_path):
    marker = tmp_path / 'pids'
    script = (
        'import os,subprocess,sys,time,signal; '
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        'signal.signal(signal.SIGTERM,lambda *a:(child.wait(),sys.exit(0))); '
        'open(sys.argv[1],"w").write(str(os.getpid())+" "+str(child.pid)); '
        'time.sleep(60)'
    )
    task = asyncio.create_task(transport._run([sys.executable, '-c', script, str(marker)]))
    pids = []
    try:
        for _ in range(200):
            if marker.exists() and len(marker.read_text().split()) == 2:
                break
            await asyncio.sleep(.01)
        pids = [int(value) for value in marker.read_text().split()]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(not Path(f'/proc/{pid}').exists() for pid in pids)
    finally:
        for pid in reversed(pids):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if not task.done():
            task.cancel()
