from __future__ import annotations

import logging
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Generic, Iterator, TextIO, TypeVar

CORE_RUNTIME_MODE_ENV = "BMS_CORE_RUNTIME_MODE"
DEPLOYMENT_ADMISSION_LOCK_ENV = "BMS_DEPLOYMENT_ADMISSION_LOCK"
DEFAULT_DEPLOYMENT_ADMISSION_LOCK = (
    Path.home() / ".local" / "state" / "biomodstack" / "deployment-admission.lock"
)
logger = logging.getLogger(__name__)


class WorkflowAdmissionBlocked(RuntimeError):
    """A managed Development cutover currently owns the admission fence."""


T = TypeVar("T")


class WorkflowMutationLease(Generic[T]):
    """A transferable shared lock held for detached mutation work."""

    def __init__(self, lock: TextIO) -> None:
        self._lock: TextIO | None = lock

    def close(self) -> None:
        lock, self._lock = self._lock, None
        if lock is None:
            return
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


def acquire_workflow_mutation_lease() -> WorkflowMutationLease[Any]:
    lock_path = Path(
        os.getenv(DEPLOYMENT_ADMISSION_LOCK_ENV, str(DEFAULT_DEPLOYMENT_ADMISSION_LOCK))
    ).expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise WorkflowAdmissionBlocked(
            "BioModStack Development deployment is in progress; retry after coherence is restored"
        ) from exc
    return WorkflowMutationLease(lock)


async def run_with_workflow_mutation_lease(
    lease: WorkflowMutationLease[T],
    operation: Awaitable[T],
) -> T:
    try:
        return await operation
    finally:
        lease.close()


@contextmanager
def workflow_mutation_admission() -> Iterator[None]:
    lease = acquire_workflow_mutation_lease()
    try:
        yield
    finally:
        lease.close()


TRUE_STRINGS = {"1", "true", "yes", "on"}
FALSE_STRINGS = {"0", "false", "no", "off"}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_STRINGS:
            return True
        if normalized in FALSE_STRINGS:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def core_runtime_mode_enabled() -> bool:
    raw_value = os.getenv(CORE_RUNTIME_MODE_ENV)
    if raw_value is None:
        return False

    normalized = raw_value.strip().lower()
    if not normalized:
        return False
    if normalized in TRUE_STRINGS:
        return True
    if normalized in FALSE_STRINGS:
        return False

    logger.warning(
        "Invalid %s=%r; defaulting to enabled for safety in core-runtime mode.",
        CORE_RUNTIME_MODE_ENV,
        raw_value,
    )
    return True


def workflow_launches_allowed() -> bool:
    from services.workflow_adapter import workflow_adapter_enabled

    if workflow_adapter_enabled():
        return True
    return not core_runtime_mode_enabled()


def workflow_launch_block_detail(action: str = "launch workflows") -> str:
    normalized_action = str(action or "launch workflows").strip()
    return (
        f"Cannot {normalized_action} while BioModStack is running in core-runtime container mode without a configured "
        "host-native workflow adapter. This container runtime only owns the web/control-plane surface; configure "
        "BMS_WORKFLOW_ADAPTER_URL or use the host-native BioModStack runtime for workflow launches, resumes, and resubmits."
    )


def assert_workflow_launch_allowed(action: str = "launch workflows") -> None:
    if not workflow_launches_allowed():
        raise RuntimeError(workflow_launch_block_detail(action))
