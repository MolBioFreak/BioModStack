"""Controller-local budgets. Remote execution capacity is never resolved here.

Configuration is a preview; a runtime takes one immutable policy snapshot until
restart. Saving a profile never changes running units or existing admissions.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from collections.abc import Mapping

GIB = 1024**3


@dataclass(frozen=True)
class LocalCapacity:
    cpu_threads: int
    memory_bytes: int


def detect_local_capacity() -> LocalCapacity:
    """Use total OS-usable RAM, never fluctuating available/free RAM."""
    try:
        threads = os.cpu_count()
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError("Cannot detect local CPU/RAM capacity") from exc
    if not threads or total <= 0:
        raise ValueError("Cannot detect local CPU/RAM capacity")
    return LocalCapacity(threads, total)


def configured_local_policy(profile: Mapping[str, object] | None = None) -> LocalCapacity:
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
    if os.getenv("BMS_LOCAL_CPU_THREADS") is not None:
        profile["local_cpu_threads"] = int(os.environ["BMS_LOCAL_CPU_THREADS"])
    if os.getenv("BMS_LOCAL_MEMORY_BYTES") is not None:
        memory = int(os.environ["BMS_LOCAL_MEMORY_BYTES"])
        profile["local_memory_gib"] = memory / GIB
    return configured_local_policy(profile)
