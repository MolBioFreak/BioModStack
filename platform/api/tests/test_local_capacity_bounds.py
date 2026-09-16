"""Synthetic Linux capacity fixtures; no host cgroup assumptions or writes."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import biomodstack_local_resources as resources

GIB = resources.GIB


@pytest.fixture
def linux(monkeypatch):
    files = {}
    monkeypatch.setattr(resources.sys, "platform", "linux")
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(resources.os, "sysconf", lambda name: 32 * GIB if name == "SC_PHYS_PAGES" else 1)
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: set(range(32)))
    monkeypatch.setattr(resources, "_read_capacity_file", lambda path: files.get(str(path), ""))
    return files


def v2(files, member="/", root="/", mount="/cg"):
    files["/proc/self/cgroup"] = f"0::{member}"
    files["/proc/self/mountinfo"] = f"21 20 0:28 {root} {mount} ro - cgroup2 cgroup rw"


def test_container_budget_and_explicit_validation(linux):
    v2(linux)
    linux.update({"/cg/cpu.max": "400000 100000", "/cg/memory.max": str(4 * GIB)})
    assert resources.detect_local_capacity() == resources.LocalCapacity(4, 4 * GIB)
    assert resources.configured_local_policy({}) == resources.LocalCapacity(4, 3 * GIB)
    assert resources.configured_local_policy({"local_cpu_threads": 2, "local_memory_gib": 2}) == resources.LocalCapacity(2, 2 * GIB)
    for profile in ({"local_cpu_threads": 5}, {"local_memory_gib": 5}):
        with pytest.raises(ValueError):
            resources.configured_local_policy(profile)


@pytest.mark.parametrize("quota,expected", [("390000 100000", 3), ("50000 100000", 1), ("max 100000", 32), ("-1 100000", 32), ("0 100000", 32), ("400000 0", 32), ("broken", 32)])
def test_quota_floor_and_unlimited(linux, quota, expected):
    v2(linux)
    linux["/cg/cpu.max"] = quota
    assert resources.detect_local_capacity().cpu_threads == expected


def test_visible_ancestors_and_soft_limits(linux):
    v2(linux, "/parent/child")
    linux.update({"/cg/parent/child/cpu.max": "max 100000", "/cg/parent/cpu.max": "250000 100000",
                  "/cg/parent/child/memory.max": str(8 * GIB), "/cg/memory.max": str(3 * GIB),
                  "/cg/parent/child/memory.high": "1", "/cg/parent/child/memory.current": "1"})
    assert resources.detect_local_capacity() == resources.LocalCapacity(2, 3 * GIB)


@pytest.mark.parametrize("root,member", [("/host/group", "/host/group/child"), ("/host/group", "/child"), ("/", "/child")])
def test_mount_roots_namespace_and_escaped_mount(linux, root, member):
    v2(linux, member, root, r"/custom\040mount")
    linux["/custom mount/child/cpu.max"] = "400000 100000"
    linux["/custom mount/memory.max"] = str(4 * GIB)
    linux["/memory.max"] = "1"  # Never walk beyond the mount.
    assert resources.detect_local_capacity() == resources.LocalCapacity(4, 4 * GIB)


def test_v1_split_and_combined_controllers(linux):
    linux["/proc/self/cgroup"] = "2:cpu,cpuacct:/group/child\n3:memory:/group/child"
    linux["/proc/self/mountinfo"] = ("21 20 0:28 /group /cpu ro - cgroup cgroup rw,cpu,cpuacct\n"
                                          "22 20 0:29 / /mem ro - cgroup cgroup rw,memory")
    linux.update({"/cpu/child/cpu.cfs_quota_us": "-1", "/cpu/child/cpu.cfs_period_us": "100000",
                  "/cpu/cpu.cfs_quota_us": "350000", "/cpu/cpu.cfs_period_us": "100000",
                  "/mem/group/child/memory.limit_in_bytes": "9223372036854771712",
                  "/mem/group/memory.limit_in_bytes": str(5 * GIB)})
    assert resources.detect_local_capacity() == resources.LocalCapacity(3, 5 * GIB)


@pytest.mark.parametrize("value", ["max", "", "bad", "-1", "9223372036854771712"])
def test_unlimited_or_unreadable_memory(linux, value):
    v2(linux)
    linux["/cg/memory.max"] = value
    assert resources.detect_local_capacity().memory_bytes == 32 * GIB


def test_affinity_bounds_and_missing_proc(linux, monkeypatch):
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: {3, 7})
    assert resources.detect_local_capacity().cpu_threads == 2
    v2(linux)
    linux["/cg/cpu.max"] = "800000 100000"
    assert resources.detect_local_capacity().cpu_threads == 2


def test_unavailable_affinity_and_malformed_mounts(linux, monkeypatch):
    def unavailable(pid):
        raise OSError("unsupported")
    monkeypatch.setattr(resources.os, "sched_getaffinity", unavailable)
    linux["/proc/self/mountinfo"] = "bad\n1 2 - cgroup2\n1 2 3 / /cg rw - tmpfs tmpfs rw"
    assert resources.detect_local_capacity() == resources.LocalCapacity(32, 32 * GIB)


def test_nonlinux_ignores_proc(linux, monkeypatch):
    monkeypatch.setattr(resources.sys, "platform", "darwin")
    v2(linux)
    linux["/cg/cpu.max"] = "100000 100000"
    assert resources.detect_local_capacity().cpu_threads == 32


def test_snapshot_not_changed_by_new_capacity(linux, monkeypatch):
    import biomodstack_runtime_profile as profile
    monkeypatch.setattr(profile, "load_install_profile", lambda: {})
    monkeypatch.delenv("BMS_LOCAL_CPU_THREADS", raising=False)
    monkeypatch.delenv("BMS_LOCAL_MEMORY_BYTES", raising=False)
    resources.applied_local_policy.cache_clear()
    try:
        v2(linux)
        linux.update({"/cg/cpu.max": "400000 100000", "/cg/memory.max": str(4 * GIB)})
        snapshot = resources.applied_local_policy()
        linux["/cg/cpu.max"] = "200000 100000"
        assert resources.applied_local_policy() is snapshot
        assert resources.configured_local_policy({}).cpu_threads == 2
    finally:
        resources.applied_local_policy.cache_clear()


def test_zero_memory_is_not_unlimited(linux):
    v2(linux)
    linux["/cg/memory.max"] = "0"
    with pytest.raises(ValueError, match="No usable local RAM"):
        resources.detect_local_capacity()


def test_namespace_outside_visible_root_does_not_escape(linux):
    v2(linux, "/../outside")
    linux["/outside/cpu.max"] = "100000 100000"
    assert resources.detect_local_capacity().cpu_threads == 32


def test_read_failure_is_optional(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("unreadable")
    monkeypatch.setattr(Path, "read_text", denied)
    assert resources._read_capacity_file("/not-readable") == ""
