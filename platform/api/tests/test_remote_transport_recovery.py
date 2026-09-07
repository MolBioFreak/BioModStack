"""Real local processes only; no provider, SSH, or scientific execution."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from services.remote_execution import result_generation as gen
from services.remote_execution import transport
from services.remote_execution.transfer_supervisor import SCHEMA, process_identity


WRITER = """
import os, sys, time
from pathlib import Path
root = Path(sys.argv[1])
if len(sys.argv) > 2:
    if os.fork() == 0:
        while True:
            with (root / 'partial').open('ab') as f:
                f.write(b'x'); f.flush(); os.fsync(f.fileno())
            time.sleep(.01)
(root / 'ready').write_text(str(os.getpid()))
time.sleep(60)
"""


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError("Timed out waiting for local process evidence")


def record(incoming):
    marker = gen.transfer_marker(incoming)
    return json.loads(marker.read_text()) if marker.exists() else {}


def api_process(incoming, descendants=True):
    pid = os.fork()
    if pid == 0:
        try:
            gen.begin_transfer(incoming)
            asyncio.run(transport._run_owned(
                [sys.executable, '-c', WRITER, str(incoming), *(['fork'] if descendants else [])],
                incoming, timeout=60))
        finally:
            os._exit(0)
    wait_for(lambda: (incoming / 'ready').exists() and 'writer' in record(incoming))
    return pid


@pytest.mark.parametrize('descendants', [False, True])
def test_api_sigkill_receipt_restart_and_retry(tmp_path, descendants):
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    prior = tmp_path / 'previous-results'
    prior.write_bytes(b'previous-good')
    pid = api_process(incoming, descendants)
    active = record(incoming)
    assert active['supervisor'] == process_identity(active['supervisor']['pid'])
    assert active['writer']['pid'] == active['writer']['process_group']
    with pytest.raises(gen.GenerationError, match='writer-quiescence'):
        gen.prepare_transfer(incoming)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    wait_for(lambda: record(incoming).get('phase') == 'quiescent')
    before = (incoming / 'partial').read_bytes() if descendants else b''
    # A separate restarted controller consumes the durable receipt.
    restarted = os.fork()
    if restarted == 0:
        try:
            gen.prepare_transfer(incoming)
            os._exit(0)
        except Exception:
            os._exit(1)
    assert os.waitstatus_to_exitcode(os.waitpid(restarted, 0)[1]) == 0
    assert not gen.transfer_marker(incoming).exists()
    time.sleep(.1)
    if descendants:
        assert (incoming / 'partial').read_bytes() == before
    gen.begin_transfer(incoming)
    result = asyncio.run(transport._run_owned(
        [sys.executable, '-c', "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('complete')", str(incoming / 'result')],
        incoming, timeout=5))
    assert result.returncode == 0
    gen.end_transfer(incoming)
    assert (incoming / 'result').read_text() == 'complete'
    assert prior.read_bytes() == b'previous-good'


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_timeout_and_cancellation_reap_descendants(tmp_path, cancel):
    gen.begin_transfer(tmp_path)
    task = asyncio.create_task(transport._run_owned(
        [sys.executable, '-c', WRITER, str(tmp_path), 'fork'], tmp_path,
        timeout=20 if cancel else .3))
    if cancel:
        for _ in range(200):
            if (tmp_path / 'ready').exists():
                break
            await asyncio.sleep(.01)
        assert (tmp_path / 'ready').exists()
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else transport.RemoteTransportError):
        await task
    assert record(tmp_path)['phase'] == 'quiescent'
    gen.end_transfer(tmp_path)


@pytest.mark.parametrize('phase', ['starting', 'supervising'])
def test_reused_pid_never_signalled_or_accepted_as_quiescence(tmp_path, phase):
    # A real unrelated process is deliberately referenced with a stale start ID.
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as unrelated:
        try:
            stale = process_identity(unrelated.pid)
            stale['start_ticks'] -= 1
            gen.durable_json(gen.transfer_marker(tmp_path), dict(
                schema=SCHEMA, destination=str(tmp_path.resolve()), phase=phase,
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                supervisor=stale, writer=stale))
            for operation in (gen.prepare_transfer, gen.end_transfer):
                with pytest.raises(gen.GenerationError, match='writer-quiescence'):
                    operation(tmp_path)
            assert unrelated.poll() is None
            assert gen.transfer_marker(tmp_path).exists()
        finally:
            unrelated.terminate()


def test_incomplete_quiescent_receipt_cannot_release_fence(tmp_path):
    gen.durable_json(gen.transfer_marker(tmp_path), dict(
        schema=SCHEMA, destination=str(tmp_path.resolve()), phase='quiescent',
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
    with pytest.raises(gen.GenerationError, match='quiescence proof'):
        gen.prepare_transfer(tmp_path)
    assert gen.transfer_marker(tmp_path).exists()


def test_dead_supervisor_is_not_writer_death_proof(tmp_path):
    pid = api_process(tmp_path, descendants=False)
    active = record(tmp_path)
    # Frozen Python and its libc omit pidfd wrappers. Linux x86_64/aarch64
    # share these syscall numbers; this fixture never falls back to PID kills.
    import ctypes
    import platform
    assert platform.machine() in ('x86_64', 'aarch64')
    libc = ctypes.CDLL(None, use_errno=True)
    supervisor_fd = libc.syscall(434, active['supervisor']['pid'], 0)
    writer_fd = libc.syscall(434, active['writer']['pid'], 0)
    assert supervisor_fd >= 0 and writer_fd >= 0
    try:
        assert libc.syscall(424, supervisor_fd, signal.SIGKILL, 0, 0) == 0
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        with pytest.raises(gen.GenerationError, match='writer-quiescence'):
            gen.prepare_transfer(tmp_path)
        assert record(tmp_path)['phase'] == 'supervising'
    finally:
        libc.syscall(424, writer_fd, signal.SIGKILL, 0, 0)
        os.close(writer_fd)
        os.close(supervisor_fd)


@pytest.mark.asyncio
@pytest.mark.parametrize('exit_code', [0, 23])
async def test_leader_exit_reaps_still_writing_grandchild(tmp_path, exit_code):
    gen.begin_transfer(tmp_path)
    script = WRITER.replace('time.sleep(60)', f'os._exit({exit_code})')
    result = await transport._run_owned(
        [sys.executable, '-c', script, str(tmp_path), 'fork'], tmp_path, timeout=5)
    assert result.returncode == exit_code
    assert record(tmp_path)['phase'] == 'quiescent'
    gen.end_transfer(tmp_path)


def test_controller_dies_before_supervisor_launch_never_starts_writer(tmp_path):
    marker = gen.transfer_marker(tmp_path)
    gen.durable_json(marker, dict(schema=SCHEMA, phase='starting',
        destination=str(tmp_path.resolve()),
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        controller=process_identity(os.getpid())))
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    try:
        result = subprocess.run(
            [sys.executable, str(Path(transport.__file__).with_name('transfer_supervisor.py')),
             str(marker), str(read_fd), sys.executable, '-c', WRITER, str(tmp_path)],
            pass_fds=(read_fd,), timeout=5, capture_output=True)
    finally:
        os.close(read_fd)
    assert result.returncode == 125, result.stderr
    assert not (tmp_path / 'ready').exists()
    assert record(tmp_path)['phase'] == 'quiescent'
    gen.prepare_transfer(tmp_path)


@pytest.mark.asyncio
async def test_selected_result_download_uses_supervisor(tmp_path, monkeypatch):
    # Exercise the supported rsync entrypoint with a local executable fixture,
    # not a monkeypatched process creation or synthetic subprocess response.
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    executable = bin_dir / 'rsync'
    executable.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\nPath(sys.argv[-1], "payload").write_text("received")\n')
    executable.chmod(0o700)
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    gen.begin_transfer(incoming)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(transport, '_ssh_base', lambda _: ['ssh', 'fixture'])
    await transport.rsync_selected_from_remote(
        transport.RemoteConnection('target', 'localhost', 22, 'user', '/remote'),
        '/remote/results', incoming, ['payload'], max_file_bytes=100)
    assert record(incoming)['phase'] == 'quiescent'
    gen.end_transfer(incoming)
    assert (incoming / 'payload').read_text() == 'received'
