"""Controller-local CPU and DRAM placement accounting backed by canonical Job rows (R35).

Replaces the retired experiments.db resource_admissions / resource_admission_policy
copy. Active local Jobs in biomodstack.db are the single durable source of truth:
capacity survives restarts and releases automatically when Jobs leave running state.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from biomodstack_local_resources import GIB, applied_local_policy

ACTIVE_LOCAL_QUEUE_STATUSES = ("running", "cancelling", "canceling")
DEFAULT_LOCAL_CPU_THREADS = 1
DEFAULT_LOCAL_DRAM_BYTES = 1 * GIB


class LocalResourceBudgetExceeded(ValueError):
    """Raised when a local job request exceeds the applied host CPU/DRAM policy."""

    def __init__(self, message: str, *, code: str = "resource_admission_denied") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LocalResourceRequest:
    cpu_threads: int
    dram_bytes: int


def _normalize_params(params: Any) -> dict[str, Any]:
    if isinstance(params, Mapping):
        return dict(params)
    if isinstance(params, str) and params.strip():
        try:
            parsed = json.loads(params)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LocalResourceBudgetExceeded(f"{field} must be a positive integer")
    return value


def _positive_bytes(value: Any, *, field: str, scale: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise LocalResourceBudgetExceeded(f"{field} must be a positive number")
    return max(1, int(value * scale))


def extract_job_resource_request(
    params: Any,
    *,
    execution_target_id: str | None = None,
) -> LocalResourceRequest:
    """Extract controller-local CPU threads and DRAM bytes for a Job.

    Remote jobs (execution_target_id set) consume zero local controller budget.
    """
    if execution_target_id:
        return LocalResourceRequest(cpu_threads=0, dram_bytes=0)
    normalized = _normalize_params(params)
    resources = normalized.get("resources")
    res_map = resources if isinstance(resources, Mapping) else {}

    if "cpu_threads" in res_map and res_map["cpu_threads"] is not None:
        cpu_threads = _positive_int(res_map["cpu_threads"], field="resources.cpu_threads")
    elif "cpus" in res_map and res_map["cpus"] is not None:
        cpu_threads = _positive_int(res_map["cpus"], field="resources.cpus")
    elif "threads" in res_map and res_map["threads"] is not None:
        cpu_threads = _positive_int(res_map["threads"], field="resources.threads")
    else:
        cpu_threads = DEFAULT_LOCAL_CPU_THREADS

    if "dram_bytes" in res_map and res_map["dram_bytes"] is not None:
        dram_bytes = _positive_int(res_map["dram_bytes"], field="resources.dram_bytes")
    elif "memory_bytes" in res_map and res_map["memory_bytes"] is not None:
        dram_bytes = _positive_int(res_map["memory_bytes"], field="resources.memory_bytes")
    elif "dram_gib" in res_map and res_map["dram_gib"] is not None:
        dram_bytes = _positive_bytes(res_map["dram_gib"], field="resources.dram_gib", scale=GIB)
    elif "memory_gib" in res_map and res_map["memory_gib"] is not None:
        dram_bytes = _positive_bytes(res_map["memory_gib"], field="resources.memory_gib", scale=GIB)
    else:
        dram_bytes = DEFAULT_LOCAL_DRAM_BYTES

    return LocalResourceRequest(cpu_threads=cpu_threads, dram_bytes=dram_bytes)


def validate_local_resource_request(
    params: Any,
    *,
    execution_target_id: str | None = None,
) -> LocalResourceRequest:
    """Validate that a single local job fits within the host local policy."""
    req = extract_job_resource_request(params, execution_target_id=execution_target_id)
    if execution_target_id:
        return req
    policy = applied_local_policy()
    if req.cpu_threads > policy.cpu_threads or req.dram_bytes > policy.memory_bytes:
        raise LocalResourceBudgetExceeded(
            f"request ({req.cpu_threads} CPU threads, {req.dram_bytes} bytes DRAM) "
            f"exceeds host local policy ({policy.cpu_threads} CPU threads, {policy.memory_bytes} bytes DRAM)"
        )
    return req


def aggregate_local_resource_usage(running_jobs: Iterable[Any]) -> LocalResourceRequest:
    """Sum CPU threads and DRAM bytes across active local Jobs."""
    used_cpu = 0
    used_dram = 0
    for job in running_jobs:
        if getattr(job, "execution_target_id", None):
            continue
        try:
            req = extract_job_resource_request(getattr(job, "params", None))
        except LocalResourceBudgetExceeded:
            req = LocalResourceRequest(DEFAULT_LOCAL_CPU_THREADS, DEFAULT_LOCAL_DRAM_BYTES)
        used_cpu += req.cpu_threads
        used_dram += req.dram_bytes
    return LocalResourceRequest(cpu_threads=used_cpu, dram_bytes=used_dram)


async def active_local_resource_usage(session: AsyncSession) -> LocalResourceRequest:
    """Load active local Jobs from biomodstack.db and return aggregate CPU/DRAM usage."""
    from database import Job

    result = await session.execute(
        select(Job)
        .execution_options(populate_existing=True)
        .where(
            Job.execution_target_id.is_(None),
            Job.queue_status.in_(ACTIVE_LOCAL_QUEUE_STATUSES),
        )
    )
    return aggregate_local_resource_usage(result.scalars().all())


def fits_local_resource_budget(
    used: LocalResourceRequest,
    requested: LocalResourceRequest,
) -> bool:
    """Return True iff adding `requested` to `used` stays within `applied_local_policy()`."""
    if requested.cpu_threads == 0 and requested.dram_bytes == 0:
        return True
    policy = applied_local_policy()
    return (
        used.cpu_threads + requested.cpu_threads <= policy.cpu_threads
        and used.dram_bytes + requested.dram_bytes <= policy.memory_bytes
    )


__all__ = [
    "ACTIVE_LOCAL_QUEUE_STATUSES",
    "DEFAULT_LOCAL_CPU_THREADS",
    "DEFAULT_LOCAL_DRAM_BYTES",
    "LocalResourceBudgetExceeded",
    "LocalResourceRequest",
    "active_local_resource_usage",
    "aggregate_local_resource_usage",
    "extract_job_resource_request",
    "fits_local_resource_budget",
    "validate_local_resource_request",
]
