"""Local result-transfer supervisor, launched only by transport.py.

Linux subreaping plus an unreaped group leader pins the group ID until SIGKILL.
No PID discovered from disk is ever signalled. A receipt follows ECHILD, not just
leader exit. Supervisor death (including host service-wide kill) fails closed.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

# Also executable by absolute filename without loading the API/database.
if __package__:
    from .result_generation import durable_json
else:
    from result_generation import durable_json

SCHEMA = "bms.local-result-transport.v1"


def process_identity(pid: int) -> dict:
    # comm can contain spaces and parentheses; stat fields start after its last ).
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start_ticks": int(fields[19]),
            "process_group": int(fields[2]), "session": int(fields[3])}


def supervise(marker: Path, control_fd: int, argv: list[str]) -> int:
    record = json.loads(marker.read_text())
    if record["schema"] != SCHEMA or record["phase"] != "starting":
        raise RuntimeError("Invalid transport launch record")
    if record["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        raise RuntimeError("Transport launch boot changed")
    # PR_SET_CHILD_SUBREAPER: adopted grandchildren remain ours until reaped.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot establish transport subreaper")
    record.update(phase="supervising", supervisor=process_identity(os.getpid()))
    durable_json(marker, record)
    # A controller that died before spawn authorizes no new writer.
    if select.select([control_fd], [], [], 0)[0]:
        record.update(phase="quiescent", quiescence="no-writer")
        durable_json(marker, record)
        return 125
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
    record["writer"] = process_identity(child.pid)
    durable_json(marker, record)
    interrupted = False
    while True:
        # WNOWAIT reserves the child PID/PGID, even after leader exit. Never use
        # Popen.poll() here: reaping before killpg creates a PID reuse race.
        if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
            break
        if select.select([control_fd], [], [], 0.05)[0]:
            interrupted = True
            break
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _, status = os.waitpid(child.pid, 0)
    child.returncode = os.waitstatus_to_exitcode(status)
    # This includes adopted descendants; even an escaped descendant prevents a
    # false receipt. If it never exits, recovery remains explicitly blocked.
    while True:
        try:
            os.waitpid(-1, 0)
        except ChildProcessError:
            break
    record.update(phase="quiescent", quiescence="descendants-reaped")
    durable_json(marker, record)
    if interrupted:
        return 125
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == "__main__":
    sys.exit(supervise(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]))
