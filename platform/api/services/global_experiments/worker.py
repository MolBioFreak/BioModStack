"""Managed single-owner dispatcher and lifecycle reconciler for global experiments."""
from __future__ import annotations

import asyncio
import fcntl
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from experiment_models import (
    ExperimentDispatchOutbox,
    ExperimentRunControlCommand,
    ExperimentRunGroup,
)
from experiment_services import ExistingJobMaterializer, dispatch_pending_outbox, reconcile_run_group
from services.global_experiments.launch_contexts import (
    publish_consumed_launch_context_bindings,
    recover_stale_typed_launch_context_claims,
)
from services.ngs_molbio_connector import connector_health, process_command_once, process_outbox_once
from services.ngs_molbio_run_control import process_run_control_command_once


_ACTIVE_GROUP_STATES = {
    "dispatch_pending",
    "partially_dispatched",
    "dispatched",
    "queued",
    "running",
}
_RUN_CONTROL_STATUSES = ("pending", "leased", "retryable", "applied", "conflicted")
_TYPED_CLAIM_STALE_AFTER = timedelta(minutes=5)

_global_worker: "GlobalExperimentWorker | None" = None


def install_global_experiment_worker(worker: "GlobalExperimentWorker | None") -> None:
    global _global_worker
    _global_worker = worker


def global_experiment_worker() -> "GlobalExperimentWorker | None":
    return _global_worker


class GlobalExperimentWorker:
    """Bounded dispatcher/reconciler guarded by a process-wide filesystem lease."""

    def __init__(
        self,
        experiment_session_factory: async_sessionmaker[AsyncSession],
        core_session_factory: async_sessionmaker[AsyncSession],
        molbio_ngs_session_factory: async_sessionmaker[AsyncSession],
        *,
        database_path: Path,
        poll_interval: float = 2.0,
        dispatch_batch_size: int = 10,
        reconcile_batch_size: int = 25,
    ) -> None:
        self._experiment_sessions = experiment_session_factory
        self._core_sessions = core_session_factory
        self._molbio_ngs_sessions = molbio_ngs_session_factory
        self._poll_interval = poll_interval
        self._dispatch_batch_size = dispatch_batch_size
        self._reconcile_batch_size = reconcile_batch_size
        self._lock_path = Path(f"{database_path}.dispatcher.lock")
        self._lock_handle: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
        self.lease_state = "unacquired"
        self.last_successful_sweep: str | None = None
        self.failure_count = 0
        self.last_error: str | None = None
        self.last_sweep_counts = {
            "run_control_before_dispatch": 0,
            "run_control_before_reconcile": 0,
            "typed_claim_recovery": 0,
            "typed_binding_publications": 0,
            "typed_binding_publication_conflicts": 0,
            "dispatched": 0,
            "reconciled": 0,
            "connector_commands": 0,
            "connector_events": 0,
        }

    def _acquire_owner_lease(self) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            self.lease_state = "standby"
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(self.worker_id)
        handle.flush()
        os.fsync(handle.fileno())
        self._lock_handle = handle
        self.lease_state = "owner"
        return True

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="global-experiment-dispatcher")

    def ensure_dispatcher_available(self) -> bool:
        if self.lease_state == "owner" and self._lock_handle is not None:
            return True
        if self._acquire_owner_lease():
            return True
        # A non-blocking lock conflict is direct evidence of another live owner.
        return self.lease_state == "standby"

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None
        if self._lock_handle is not None:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None
        self.lease_state = "released"

    async def _run(self) -> None:
        while not self._stopping.is_set():
            if self._lock_handle is None:
                try:
                    self._acquire_owner_lease()
                except OSError as exc:
                    self.lease_state = "unavailable"
                    self.failure_count += 1
                    self.last_error = str(exc)[:512]
                if self._lock_handle is None:
                    try:
                        await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                    except asyncio.TimeoutError:
                        pass
                    continue
            try:
                await self.run_once()
            except Exception as exc:
                self.failure_count += 1
                self.last_error = str(exc)[:512]
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass

    async def _process_run_control_batch(self) -> int:
        processed_count = 0
        for _ in range(self._dispatch_batch_size):
            async with self._experiment_sessions() as experiment_session:
                async with self._core_sessions() as core_session:
                    processed = await process_run_control_command_once(
                        experiment_session,
                        core_session,
                        worker_id=self.worker_id,
                    )
            if processed == 0:
                break
            processed_count += processed
        return processed_count

    async def _recover_stale_typed_claim_batch(self) -> tuple[int, int, int]:
        async with self._experiment_sessions() as experiment_session:
            async with self._core_sessions() as core_session:
                report = await recover_stale_typed_launch_context_claims(
                    experiment_session,
                    core_session,
                    claimed_before=datetime.now(timezone.utc) - _TYPED_CLAIM_STALE_AFTER,
                    limit=max(1, min(self._dispatch_batch_size, 100)),
                )
                await experiment_session.commit()
                binding_report = await publish_consumed_launch_context_bindings(
                    experiment_session,
                    core_session,
                    priority_context_ids=tuple(report["consumed_launch_context_ids"]),
                    limit=max(1, min(self._dispatch_batch_size, 100)),
                )
        return (
            int(report["scanned_count"]),
            int(binding_report["published_count"]),
            int(binding_report["conflict_count"]),
        )

    async def run_once(self) -> dict[str, int]:
        if self.lease_state != "owner":
            return {
                "run_control_before_dispatch": 0,
                "run_control_before_reconcile": 0,
                "typed_claim_recovery": 0,
                "typed_binding_publications": 0,
                "typed_binding_publication_conflicts": 0,
                "dispatched": 0,
                "reconciled": 0,
                "connector_commands": 0,
                "connector_events": 0,
            }
        run_control_before_dispatch = await self._process_run_control_batch()
        typed_claim_recovery, typed_binding_publications, typed_binding_publication_conflicts = (
            await self._recover_stale_typed_claim_batch()
        )
        connector_commands = 0
        connector_events = 0
        for _ in range(self._dispatch_batch_size):
            async with self._experiment_sessions() as experiment_session:
                async with self._molbio_ngs_sessions() as domain_session:
                    processed = await process_command_once(
                        experiment_session, domain_session, worker_id=self.worker_id
                    )
            if processed == 0:
                break
            connector_commands += processed
        for _ in range(self._dispatch_batch_size):
            async with self._experiment_sessions() as experiment_session:
                async with self._molbio_ngs_sessions() as domain_session:
                    processed = await process_outbox_once(
                        experiment_session, domain_session, worker_id=self.worker_id
                    )
            if processed == 0:
                break
            connector_events += processed
        dispatched = 0
        for _ in range(self._dispatch_batch_size):
            async with self._experiment_sessions() as experiment_session:
                async with self._core_sessions() as core_session:
                    try:
                        claimed = await dispatch_pending_outbox(
                            experiment_session,
                            ExistingJobMaterializer(core_session),
                            core_session=core_session,
                            lease_owner=self.worker_id,
                        )
                    except Exception as exc:
                        self.failure_count += 1
                        self.last_error = str(exc)[:512]
                        break
            if claimed == 0:
                break
            dispatched += claimed

        run_control_before_reconcile = await self._process_run_control_batch()
        async with self._experiment_sessions() as discovery_session:
            group_ids = list(
                (
                    await discovery_session.execute(
                        select(ExperimentRunGroup.resource_id)
                        .where(ExperimentRunGroup.state.in_(_ACTIVE_GROUP_STATES))
                        .order_by(ExperimentRunGroup.updated_at, ExperimentRunGroup.resource_id)
                        .limit(self._reconcile_batch_size)
                    )
                ).scalars()
            )
        reconciled = 0
        for group_id in group_ids:
            async with self._experiment_sessions() as experiment_session:
                async with self._core_sessions() as core_session:
                    try:
                        group = await experiment_session.get(ExperimentRunGroup, group_id)
                        if group is None:
                            continue
                        before = (group.state, group.generation)
                        await reconcile_run_group(
                            experiment_session,
                            core_session,
                            group.workspace_id,
                            group.resource_id,
                        )
                        await experiment_session.commit()
                        await experiment_session.refresh(group)
                        if before != (group.state, group.generation):
                            reconciled += 1
                    except Exception as exc:
                        await experiment_session.rollback()
                        self.failure_count += 1
                        self.last_error = str(exc)[:512]
        self.last_successful_sweep = datetime.now(timezone.utc).isoformat()
        if self.last_error is not None and self.failure_count == 0:
            self.last_error = None
        sweep_counts = {
            "run_control_before_dispatch": run_control_before_dispatch,
            "run_control_before_reconcile": run_control_before_reconcile,
            "typed_claim_recovery": typed_claim_recovery,
            "typed_binding_publications": typed_binding_publications,
            "typed_binding_publication_conflicts": typed_binding_publication_conflicts,
            "dispatched": dispatched,
            "reconciled": reconciled,
            "connector_commands": connector_commands,
            "connector_events": connector_events,
        }
        self.last_sweep_counts = sweep_counts
        return sweep_counts

    async def health_snapshot(self, session: AsyncSession | None = None) -> dict[str, Any]:
        if session is None:
            async with self._experiment_sessions() as managed_session:
                return await self.health_snapshot(managed_session)
        active_session = session
        pending_count = int(
            (
                await active_session.execute(
                    select(func.count(ExperimentDispatchOutbox.id)).where(
                        ExperimentDispatchOutbox.status.in_({"pending", "dispatching"})
                    )
                )
            ).scalar_one()
        )
        oldest = (
            await active_session.execute(
                select(func.min(ExperimentDispatchOutbox.created_at)).where(
                    ExperimentDispatchOutbox.status.in_({"pending", "dispatching"})
                )
            )
        ).scalar_one()
        durable_failures = int(
            (
                await active_session.execute(
                    select(func.count(ExperimentDispatchOutbox.id)).where(
                        ExperimentDispatchOutbox.status == "failed"
                    )
                )
            ).scalar_one()
        )
        run_control_status_counts: dict[str, int] = {}
        for command_status in _RUN_CONTROL_STATUSES:
            run_control_status_counts[command_status] = int(
                (
                    await active_session.execute(
                        select(func.count(ExperimentRunControlCommand.command_id)).where(
                            ExperimentRunControlCommand.status == command_status
                        )
                    )
                ).scalar_one()
            )
        run_control_total_count = int(
            (
                await active_session.execute(
                    select(func.count(ExperimentRunControlCommand.command_id))
                )
            ).scalar_one()
        )
        run_control_unknown_count = max(
            0,
            run_control_total_count - sum(run_control_status_counts.values()),
        )
        oldest_age = None
        if isinstance(oldest, str):
            try:
                created = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
                oldest_age = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
            except ValueError:
                oldest_age = None
        async with self._molbio_ngs_sessions() as domain_session:
            connector = await connector_health(active_session, domain_session)
        return {
            "schema": "bms.experiment.worker-health.v1",
            "worker_id": self.worker_id,
            "lease_state": self.lease_state,
            "single_owner": self.lease_state == "owner",
            "last_successful_sweep": self.last_successful_sweep,
            "pending_count": pending_count,
            "oldest_pending_age_seconds": oldest_age,
            "failure_count": durable_failures + self.failure_count,
            "last_error": self.last_error,
            "last_sweep_counts": dict(self.last_sweep_counts),
            "run_control": {
                "total_count": run_control_total_count,
                "actionable_count": sum(
                    run_control_status_counts[state]
                    for state in ("pending", "leased", "retryable")
                ),
                "status_counts": run_control_status_counts,
                "unknown_status_count": run_control_unknown_count,
            },
            "ngs_molbio_connector": connector,
        }


__all__ = [
    "GlobalExperimentWorker",
    "global_experiment_worker",
    "install_global_experiment_worker",
]
