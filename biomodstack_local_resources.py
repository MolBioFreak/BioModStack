"""Controller-local budgets. Remote execution capacity is never resolved here.

Configuration is a preview; a runtime takes one immutable policy snapshot until
restart. Saving a profile never changes running units or existing admissions.
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path, PurePosixPath
from dataclasses import dataclass
from functools import lru_cache
from collections.abc import Mapping
from typing import cast

GIB = 1024**3


@dataclass(frozen=True)
class LocalCapacity:
    cpu_threads: int
    memory_bytes: int


def _read_capacity_file(path: str | Path) -> str:
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return ""


def _cgroup_directories():
    """Resolve this process's controllers, including bind/cgroup namespace roots.

    Only visible ancestors can be inspected; never escape a controller mount.
    mountinfo paths use octal escapes, unlike /proc/self/cgroup paths.
    """
    memberships = {}
    for line in _read_capacity_file("/proc/self/cgroup").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3:
            for controller in fields[1].split(","):
                memberships[controller] = fields[2]
    for line in _read_capacity_file("/proc/self/mountinfo").splitlines():
        before, sep, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not sep or len(fields) < 6 or len(filesystem) < 3:
            continue
        if filesystem[0] not in {"cgroup", "cgroup2"}:
            continue
        controllers = {""} if filesystem[0] == "cgroup2" else set(filesystem[2].split(","))
        for controller in controllers & memberships.keys():
            def unescape(value):
                return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)
            root = PurePosixPath(unescape(fields[3]))
            mount = Path(unescape(fields[4]))
            member = PurePosixPath(memberships[controller])
            if not member.is_absolute() or ".." in member.parts:
                continue
            try:
                relative = member.relative_to(root)
            except ValueError:
                # A cgroup namespace reports membership relative to its root,
                # while an inherited mount may still expose a host-relative root.
                relative = member.relative_to("/")
            current = mount / relative
            while True:
                yield controller, current
                if current == mount:
                    break
                current = current.parent


def _linux_capacity_bounds(threads: int, total: int) -> tuple[int, int]:
    try:
        affinity = len(os.sched_getaffinity(0))
        if affinity:
            threads = min(threads, affinity)
    except (AttributeError, OSError):
        pass
    for controller, directory in _cgroup_directories():
        if controller in {"", "cpu"}:
            if controller == "":
                quota = _read_capacity_file(directory / "cpu.max").split()
            else:
                quota = [_read_capacity_file(directory / name) for name in
                         ("cpu.cfs_quota_us", "cpu.cfs_period_us")]
            try:
                amount, period = map(int, quota)
                if amount > 0 and period > 0:
                    threads = min(threads, max(1, amount // period))
            except ValueError:
                pass  # Missing, malformed, or unlimited (v2 max / v1 -1).
        if controller in {"", "memory"}:
            name = "memory.max" if controller == "" else "memory.limit_in_bytes"
            try:
                limit = int(_read_capacity_file(directory / name))
                if limit >= 0:
                    # v1's page-rounded LONG_MAX sentinel is above physical RAM;
                    # min also handles it without architecture-specific constants.
                    total = min(total, limit)
            except ValueError:
                pass
    return threads, total


def detect_local_capacity() -> LocalCapacity:
    """Total usable capacity bounded by Linux affinity and cgroup hard limits.

    Never use fluctuating free RAM, memory.current, or soft memory.high limits.
    """
    try:
        threads = os.cpu_count()
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError("Cannot detect local CPU/RAM capacity") from exc
    if not threads or total <= 0:
        raise ValueError("Cannot detect local CPU/RAM capacity")
    if sys.platform == "linux":
        threads, total = _linux_capacity_bounds(threads, total)
        if total <= 0:
            raise ValueError("No usable local RAM capacity within cgroup limit")
    return LocalCapacity(threads, total)


def validate_local_budget(profile: Mapping[str, object]) -> None:
    """Validate stored values without consulting the reader's cgroup.

    Missing fields are allowed for legacy profiles, not committed generations.
    Capacity admission belongs to configured_local_policy, not integrity reads.
    """
    if "local_cpu_threads" in profile:
        threads = profile["local_cpu_threads"]
        if type(threads) is not int or threads < 1:
            raise ValueError("Local CPU budget must be a positive integer")
    if "local_memory_gib" in profile:
        gib = profile["local_memory_gib"]
        try:
            valid = (not isinstance(gib, bool) and isinstance(gib, (int, float))
                     and math.isfinite(gib) and gib > 0 and math.isfinite(gib * GIB)
                     and int(gib * GIB) >= 1)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("Local RAM budget must be finite, positive and at least one byte")


def committed_local_policy(profile: Mapping[str, object]) -> LocalCapacity:
    """Reproduce a frozen generation for integrity/export checks, not admission."""
    validate_local_budget(profile)
    return LocalCapacity(cast(int, profile["local_cpu_threads"]),
                         int(cast(float, profile["local_memory_gib"]) * GIB))


def configured_local_policy(profile: Mapping[str, object] | None = None) -> LocalCapacity:
    """Resolve defaults and admit a new configuration against live capacity."""
    if profile is None:
        from biomodstack_runtime_profile import load_install_profile
        profile = load_install_profile()
    capacity = detect_local_capacity()
    threads = profile.get("local_cpu_threads", math.ceil(capacity.cpu_threads * 0.8))
    gib = profile.get("local_memory_gib", capacity.memory_bytes * 0.75 / GIB)
    if type(threads) is not int or not 1 <= threads <= capacity.cpu_threads:
        raise ValueError(f"Local CPU budget must be 1..{capacity.cpu_threads} logical threads")
    if isinstance(gib, bool) or not isinstance(gib, (int, float)) or not math.isfinite(gib) or not 0 < gib <= capacity.memory_bytes / GIB:
        raise ValueError(f"Local RAM budget must be positive and at most {capacity.memory_bytes / GIB:.3f} GiB")
    memory = int(gib * GIB)
    if memory < 1:
        raise ValueError("Local RAM budget must be at least one byte")
    return LocalCapacity(threads, memory)


@lru_cache(maxsize=1)
def applied_local_policy() -> LocalCapacity:
    """Process snapshot: call at runtime startup, not after each profile save."""
    from biomodstack_runtime_profile import load_install_profile
    profile = load_install_profile()
    validate_local_budget(profile)
    committed = dict(profile)
    if os.getenv("BMS_LOCAL_CPU_THREADS") is not None:
        profile["local_cpu_threads"] = int(os.environ["BMS_LOCAL_CPU_THREADS"])
    if os.getenv("BMS_LOCAL_MEMORY_BYTES") is not None:
        memory = int(os.environ["BMS_LOCAL_MEMORY_BYTES"])
        profile["local_memory_gib"] = memory / GIB
    validate_local_budget(profile)
    capacity = detect_local_capacity()
    # Environment exports can narrow a saved budget, never enlarge it. When no
    # profile is mounted (Compose), exports supply the configured budget.
    defaults = LocalCapacity(math.ceil(capacity.cpu_threads * 0.8), int(capacity.memory_bytes * 0.75))
    threads = min(capacity.cpu_threads,
                  cast(int, committed.get("local_cpu_threads", capacity.cpu_threads)),
                  cast(int, profile.get("local_cpu_threads", defaults.cpu_threads)))
    memory = min(capacity.memory_bytes,
                 int(cast(float, committed.get("local_memory_gib", capacity.memory_bytes / GIB)) * GIB),
                 int(cast(float, profile.get("local_memory_gib", defaults.memory_bytes / GIB)) * GIB))
    return LocalCapacity(threads, memory)
