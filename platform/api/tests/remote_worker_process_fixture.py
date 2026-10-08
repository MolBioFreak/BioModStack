"""Offline supervisor subprocess fixture. NEVER production resource evidence.

Only bridge process/publication tests point their supervisor launcher here.
The production OwnedBoundary and enrollment gate have separate contract tests
and require separately authorized live manager qualification.
"""
import os
from pathlib import Path
import subprocess
import sys

# A detached test subprocess starts with the tests directory as sys.path[0].
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bms_remote_worker as worker


class FixtureBoundary:
    def __init__(self, attempt_id, limits):
        self.unit = "bms-attempt-" + attempt_id + ".service"
        self.limits = limits
        self.identity = {"unit": self.unit, "invocation_id": "d" * 32,
            "control_group": "/sys/fs/cgroup/system.slice/" + self.unit + "/science", "inode": 99,
            "machine_id": Path("/etc/machine-id").read_text().strip(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}

    def create(self, *args):
        return self.identity

    def sample(self):
        return {"cpu_usage_usec": 1, "memory_peak_bytes": 1, "pids_peak": 1,
            "memory_events": {"oom_kill": 0}, "populated": 0,
            "cpu_max": [self.limits["cpu_threads"] * 100000, 100000],
            "memory_max_bytes": self.limits["dram_bytes"], "swap_max_bytes": 0}

    def quiesce(self):
        return self.sample()

    def close(self):
        pass


def spawn_fixture(boundary, envelope, environment, log):
    return subprocess.Popen(envelope["command"], cwd=envelope["working_directory"], env=environment,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


if __name__ == "__main__":
    worker.OwnedBoundary = FixtureBoundary
    worker.spawn_owned = spawn_fixture
    raise SystemExit(worker.main())
