"""Offline process regressions for durable worker status publication."""
import subprocess
import sys

from tools import bms_remote_worker as worker


def test_unchanged_status_poll_does_not_republish(tmp_path, monkeypatch):
    envelope = {"job_id": "job", "attempt_id": "attempt", "output_directory": str(tmp_path / "results")}
    worker.atomic_json(tmp_path / worker.ENVELOPE_FILE, envelope)
    worker.atomic_json(tmp_path / worker.STATUS_FILE, worker.base_status(envelope, "running"))
    monkeypatch.setattr(worker, "process_matches", lambda *_: True)
    writes = []
    original = worker._write_atomic_json
    def record(path, value):
        if path.name == worker.STATUS_FILE:
            writes.append(dict(value))
        original(path, value)
    monkeypatch.setattr(worker, "_write_atomic_json", record)
    assert worker.status(tmp_path)["state"] == "running"
    assert worker.status(tmp_path)["state"] == "running"
    assert writes == []
    worker.atomic_json(tmp_path / worker.CANCEL_REQUEST_FILE, {"attempt_id": "attempt"})
    assert worker.status(tmp_path)["state"] == "cancelling"
    assert worker.status(tmp_path)["state"] == "cancelling"
    assert [value["state"] for value in writes] == ["cancelling"]
    assert worker.load_json(tmp_path / worker.STATUS_FILE)["state"] == "cancelling"


def test_quiescence_scans_once_per_pass_and_retains_pid_fence(monkeypatch):
    import os
    scans, signals, checked = [], [], []
    identity = {"known_writers": {101: 1, 102: 2}}
    def writers(value):
        scans.append(value)
        return [101, 102] if len(scans) == 1 else []
    def pidfd(name, *args):
        if name == "pidfd_open":
            return os.open(os.devnull, os.O_RDONLY)
        signals.append(args)
        return 0
    def matches(pid, ticks):
        checked.append((pid, ticks))
        return pid == 101  # The other task's PID has been reused.
    monkeypatch.setattr(worker, "attempt_writers", writers)
    monkeypatch.setattr(worker, "_pidfd_call", pidfd)
    monkeypatch.setattr(worker, "process_matches", matches)
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    assert worker.quiesce_writers(identity)
    assert len(scans) == 2 and len(signals) == 1
    assert checked == [(101, 1), (102, 2)]


def test_worker_launch_restores_only_authenticated_portable_bindings(tmp_path, monkeypatch):
    import json
    import tarfile
    keys = ("BMS_PORTABLE_INPUT_BINDINGS", "APPTAINERENV_BMS_PORTABLE_INPUT_BINDINGS")
    for bound in (True, False):
        attempt = tmp_path / str(bound)
        source = attempt / "bundle/source"
        inputs = attempt / "bundle/inputs"
        source.mkdir(parents=True)
        inputs.mkdir()
        archive = source / ".bms-source.tar"
        with tarfile.open(archive, "w"):
            pass
        artifact = inputs / "native.txt"
        artifact.write_text("verified native input")
        bindings = inputs / "bindings.json"
        bindings.write_text(json.dumps({"schema": "bms.portable-input-bindings.v1",
            "roots": [str(inputs)], "bindings": [{"path": str(artifact), "reference": {
                "source_path": "/controller/native.txt", "sha256": worker.sha256_file(artifact),
                "size_bytes": artifact.stat().st_size}}]}))
        output = attempt / "results"
        script = ("import json,os; from pathlib import Path; "
            "from scripts.lib.portable_inputs import resolve_input_path; "
            "values={k:os.environ.get(k) for k in " + repr(keys) + "}; "
            "values['native']=resolve_input_path('/controller/native.txt').read_text() "
            "if values['BMS_PORTABLE_INPUT_BINDINGS'] else None; "
            "Path(" + repr(str(output / "consumed.json")) + ").write_text(json.dumps(values))")
        files = [dict(relative_path=str(path.relative_to(attempt / "bundle")),
            sha256=worker.sha256_file(path), size_bytes=path.stat().st_size,
            mode=path.stat().st_mode & 0o777) for path in (archive, artifact, bindings)]
        envelope = {"schema": "bms.remote-execution.v1", "job_id": "job", "attempt_id": str(bound),
            "source_revision": "a" * 40, "source_tree": "b" * 40,
            "source_archive_sha256": worker.sha256_file(archive), "files": files,
            "command": [sys.executable, "-c", script], "working_directory": str(source),
            "output_directory": str(output), "environment": {k: str(bindings) for k in keys} if bound else {}}
        worker.atomic_json(attempt / worker.ENVELOPE_FILE, envelope)
        secret = attempt / "secret-env.json"
        worker.atomic_json(secret, {k: str(attempt / "unverified.json") for k in keys})
        secret.chmod(0o600)
        for key in keys:
            monkeypatch.setenv(key, str(attempt / "ambient.json"))
        worker.prepare(attempt)
        result = subprocess.run([sys.executable, worker.__file__, "supervise", "--attempt-dir", str(attempt)],
            capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, (result.stdout, result.stderr)
        consumed = json.loads((output / "consumed.json").read_text())
        assert {k: consumed[k] for k in keys} == {k: str(bindings) if bound else None for k in keys}
        assert consumed["native"] == ("verified native input" if bound else None)
        assert worker.status(attempt)["state"] == "succeeded"
        assert not secret.exists()


def test_writer_fence_setsid_orphan(tmp_path):
    import os
    import select
    import signal
    import subprocess
    import sys
    import time
    from tools import bms_remote_worker as worker
    from scripts.lib.component_adapter import _owned_group_writers, _stop_processes

    scope = dict(BMS_COMPONENT_CONTEXT=str(tmp_path / 'context.json'),
                 BMS_COMPONENT_JOB_ID='isolated-child', BMS_COMPONENT_OUTPUT_DIR=str(tmp_path))
    # Handshake keeps the native leader alive until its identity is captured.
    # Its writer then changes session and outlives the leader before first scan.
    writer = ("import os,time; from pathlib import Path; os.setsid(); p=Path('writes'); "
              "p.touch(); print(os.getpid(),flush=True)\n"
              "while True:\n with p.open('a') as f: f.write('x')\n time.sleep(0.02)")
    leader = subprocess.Popen([sys.executable, '-c',
        'import subprocess,sys; subprocess.Popen([sys.executable,"-c",sys.argv[1]]); sys.stdin.readline()', writer],
        cwd=tmp_path, env=dict(os.environ, **scope), process_group=0,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    fd = None
    try:
        leader._bms_writer_identity = dict(boot_id=worker.boot_id(), supervisor_pid=leader.pid,
            supervisor_start_ticks=worker.process_start_ticks(leader.pid), component_scope=scope)
        assert select.select([leader.stdout], [], [], 5)[0]
        pid = int(leader.stdout.readline())
        fd = worker._pidfd_call("pidfd_open", pid, 0)
        assert os.getpgid(pid) != leader.pid
        leader.stdin.write('exit\n'); leader.stdin.flush()
        leader.wait(timeout=5)
        assert pid in _owned_group_writers(leader)
        before = (tmp_path / 'writes').stat().st_size
        deadline = time.monotonic() + 5
        while (tmp_path / 'writes').stat().st_size == before and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (tmp_path / 'writes').stat().st_size > before
        assert _stop_processes([leader], timeout=0.1)
        assert not _owned_group_writers(leader)
        stopped = (tmp_path / 'writes').stat().st_size
        time.sleep(0.1)
        assert (tmp_path / 'writes').stat().st_size == stopped
    finally:
        if fd is not None:
            try:
                worker._pidfd_call("pidfd_send_signal", fd, signal.SIGKILL, 0, 0)
            except ProcessLookupError:
                pass
            os.close(fd)
        if leader.poll() is None:
            leader.kill()
        leader.wait(timeout=5)
        leader.stdin.close(); leader.stdout.close()


def test_writer_fence_reused_owner_pid(tmp_path):
    import pytest
    import subprocess
    import sys
    from tools import bms_remote_worker as worker
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                               start_new_session=True)
    try:
        identity = dict(boot_id=worker.boot_id(), supervisor_pid=process.pid,
                        supervisor_start_ticks=worker.process_start_ticks(process.pid) + 1)
        with pytest.raises(RuntimeError, match='identity changed'):
            worker.quiesce_writers(identity, timeout_seconds=0)
        assert process.poll() is None
        identity['component_scope'] = dict(BMS_COMPONENT_CONTEXT=str(tmp_path / 'context'),
            BMS_COMPONENT_JOB_ID='child', BMS_COMPONENT_OUTPUT_DIR=str(tmp_path))
        with pytest.raises(RuntimeError, match='identity changed'):
            worker.quiesce_writers(identity, timeout_seconds=0)
        assert process.poll() is None
        identity['boot_id'] = 'prior-boot'
        assert worker.attempt_writers(identity) == []
        assert process.poll() is None
    finally:
        process.kill(); process.wait(timeout=5)


def test_stale_process_cannot_replace_terminal_status(tmp_path):
    path = tmp_path / worker.STATUS_FILE
    running = {"job_id": "job", "attempt_id": "attempt", "state": "running"}
    worker.atomic_json(path, running)
    # A separate poller snapshots running before the supervisor completes.
    script = '''
import json, sys
from pathlib import Path
from tools import bms_remote_worker as w
p = Path(sys.argv[1])
v = w.load_json(p)
print("snapshot", flush=True)
sys.stdin.readline()
w.atomic_json(p, v)
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "snapshot"
    terminal = dict(running, state="succeeded", result_manifest_sha256="a" * 64)
    worker.atomic_json(path, terminal)
    process.communicate("publish\n", timeout=10)
    assert process.returncode == 0
    assert worker.load_json(path) == terminal


def test_status_read_modify_write_is_serialized_across_processes(tmp_path):
    import select
    path = tmp_path / worker.STATUS_FILE
    worker.atomic_json(tmp_path / worker.ENVELOPE_FILE, {
        "job_id": "job", "attempt_id": "attempt", "output_directory": str(tmp_path / "results")})
    worker.atomic_json(path, {"job_id": "job", "attempt_id": "attempt", "state": "running"})
    poll = '''
import sys
from pathlib import Path
from tools import bms_remote_worker as w
first = True
def alive(*_):
    global first
    if first:
        first = False
        print("reading", flush=True)
        sys.stdin.readline()
    return True
w.process_matches = alive
w.status(Path(sys.argv[1]))
'''
    writer = '''
import sys
from pathlib import Path
from tools import bms_remote_worker as w
print("started", flush=True)
w.atomic_json(Path(sys.argv[1]), {"job_id": "job", "attempt_id": "attempt", "state": "succeeded"})
print("published", flush=True)
'''
    p = subprocess.Popen([sys.executable, "-c", poll, str(tmp_path)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "reading"
    w = subprocess.Popen([sys.executable, "-c", writer, str(path)], stdout=subprocess.PIPE, text=True)
    assert w.stdout.readline().strip() == "started"
    try:
        # The terminal publisher must wait for the poll's complete transaction.
        assert not select.select([w.stdout], [], [], 0.2)[0]
    finally:
        p.communicate("release\n", timeout=10)
        w.communicate(timeout=10)
    assert p.returncode == w.returncode == 0
    assert worker.load_json(path)["state"] == "succeeded"


def test_stale_running_publication_preserves_cancelling_and_owner(tmp_path):
    path = tmp_path / worker.STATUS_FILE
    current = {"job_id": "job", "attempt_id": "attempt", "state": "cancelling", "workflow_pid": 42}
    worker.atomic_json(path, current)
    worker.atomic_json(path, dict(current, state="running", workflow_pid=None))
    assert worker.load_json(path) == current


def test_result_manifest_publication_does_not_add_unmanifested_lock_artifacts(tmp_path):
    worker.atomic_json(tmp_path / worker.RESULT_MANIFEST_FILE, {"artifacts": []})
    assert sorted(p.name for p in tmp_path.iterdir()) == [worker.RESULT_MANIFEST_FILE]


def test_competing_process_writers_use_distinct_temporary_files(tmp_path):
    script = '''
import sys
from pathlib import Path
from tools import bms_remote_worker as w
p = Path(sys.argv[1])
for i in range(100):
    w.atomic_json(p, {"writer": sys.argv[2], "n": i})
'''
    path = tmp_path / "receipt.json"
    processes = [subprocess.Popen([sys.executable, "-c", script, str(path), str(i)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i in range(4)]
    results = [(p, p.communicate(timeout=20)) for p in processes]
    assert all(p.returncode == 0 for p, _ in results), results
    assert worker.load_json(path)["n"] == 99
    assert not list(tmp_path.glob("*.tmp"))


def test_reboot_loses_attempt_without_signalling_reused_pid(tmp_path, monkeypatch):
    envelope = {"job_id": "job", "attempt_id": "attempt", "output_directory": str(tmp_path / "results")}
    worker.atomic_json(tmp_path / worker.ENVELOPE_FILE, envelope)
    worker.atomic_json(tmp_path / worker.STATUS_FILE, dict(
        worker.base_status(envelope, "running"), boot_id="previous-boot", supervisor_pid=123))
    monkeypatch.setattr(worker, "process_matches", lambda *_: (_ for _ in ()).throw(AssertionError("old boot PID checked")))
    value = worker.status(tmp_path)
    assert value["state"] == "lost" and value["quiescent"] is True
    assert worker.start(tmp_path) == value


def test_same_boot_owner_loss_retains_uncertain_writers(tmp_path):
    envelope = {"job_id": "job", "attempt_id": "attempt", "output_directory": str(tmp_path / "results")}
    worker.atomic_json(tmp_path / worker.ENVELOPE_FILE, envelope)
    worker.atomic_json(tmp_path / worker.STATUS_FILE, worker.base_status(envelope, "running"))
    value = worker.status(tmp_path)
    assert value["state"] == "lost" and value["quiescent"] is False


def test_ambiguous_launch_claim_is_never_replayed(tmp_path, monkeypatch):
    envelope = {"job_id": "job", "attempt_id": "attempt", "output_directory": str(tmp_path / "results")}
    worker.atomic_json(tmp_path / worker.ENVELOPE_FILE, envelope)
    worker.atomic_json(tmp_path / worker.STATUS_FILE, worker.base_status(envelope, "prepared"))
    worker.atomic_json(tmp_path / "launch-claim.json", {"attempt_id": "attempt", "boot_id": worker.boot_id()})
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("replayed")))
    assert worker.start(tmp_path)["state"] == "prepared"


def test_supervisor_joins_detached_grandchild_before_terminal(tmp_path):
    import time
    import json
    child = tmp_path / "child.json"
    script = "import subprocess,time,json; from pathlib import Path; p=subprocess.Popen(['sleep','60'], start_new_session=True); Path(%r).write_text(json.dumps(p.pid)); time.sleep(60)" % str(child)
    envelope = {"job_id": "job", "attempt_id": "attempt", "command": [sys.executable, "-c", script],
                "working_directory": str(tmp_path), "output_directory": str(tmp_path / "results")}
    worker.atomic_json(tmp_path / worker.ENVELOPE_FILE, envelope)
    worker.atomic_json(tmp_path / worker.STATUS_FILE, worker.base_status(envelope, "prepared"))
    owner = subprocess.Popen([sys.executable, worker.__file__, "supervise", "--attempt-dir", str(tmp_path)], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not child.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert child.exists()
        pid = json.loads(child.read_text())
        ticks = worker.process_start_ticks(pid)
        assert ticks is not None
        result = worker.cancel(tmp_path, 30)
        owner.wait(timeout=10)
        assert result["state"] == "cancelled" and result["quiescent"] is True
        assert not worker.process_matches(pid, ticks)
    finally:
        if owner.poll() is None:
            worker.atomic_json(tmp_path / worker.CANCEL_REQUEST_FILE, {"requested_at": worker.utc_now()})
            owner.wait(timeout=45)
