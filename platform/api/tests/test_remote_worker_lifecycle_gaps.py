"""Offline process regressions for durable worker status publication."""
import subprocess
import sys

from tools import bms_remote_worker as worker


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
