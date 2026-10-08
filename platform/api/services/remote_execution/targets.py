"""Persistence and activation service for execution-only remote targets."""
from __future__ import annotations

import asyncio
import logging
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import ExecutionTarget, Job

from .contracts import (
    DiscoveredExecutionTarget,
    ExecutionTargetActivateRequest,
    ExecutionTargetInventoryResponse,
    ExecutionTargetResponse,
)
from .transport import (
    BOOTSTRAP_ERRORS,
    RemoteConnection,
    RemoteTransportError,
    capture_host_key,
    persist_host_key,
    probe_readiness,
    rsync_to_remote,
    run_remote,
)
from .vast import VastInventoryError, list_owned_instances
from .progress import preload_active, preload_idle_clause

RUNNING_PROVIDER_STATES = frozenset({"running", "ready"})
INVENTORY_MAX_AGE_SECONDS = 120
_empty_inventory_checked_at: datetime | None = None
_inventory_refresh_lock = asyncio.Lock()


def inventory_fresh(target: ExecutionTarget) -> bool:
    inventory = (target.provider_metadata or {}).get("inventory", {})
    try:
        age = (datetime.utcnow() - datetime.fromisoformat(inventory["checked_at"])).total_seconds()
        return inventory.get("status") == "complete" and 0 <= age <= INVENTORY_MAX_AGE_SECONDS
    except (KeyError, ValueError, TypeError):
        return False


def target_eligible(target: ExecutionTarget) -> bool:
    inventory = (target.provider_metadata or {}).get("inventory", {})
    return bool(target.active and target.state == "ready" and not preload_active(target) and inventory_fresh(target)
                and inventory.get("present") is True and inventory.get("running") is True)


async def invalidate_vast_inventory(session: AsyncSession) -> None:
    """Invalidate current knowledge, never historical presence or attempt evidence."""
    global _empty_inventory_checked_at
    _empty_inventory_checked_at = None
    for row in (await session.scalars(select(ExecutionTarget).where(ExecutionTarget.provider == "vast"))).all():
        metadata = dict(row.provider_metadata or {})
        metadata["inventory"] = {**metadata.get("inventory", {}), "status": "unknown"}
        row.provider_metadata = metadata
    await session.commit()


async def run_vast_inventory_refresh(session_factory, stop: asyncio.Event, *, wait=None) -> None:
    """API-lifespan-owned refresh; no provider mutations and no GET side effects."""
    async with session_factory() as session:
        await invalidate_vast_inventory(session)
    while not stop.is_set():
        try:
            async with session_factory() as session:
                await refresh_vast_targets(session)
        except Exception:
            logging.getLogger(__name__).warning("Vast inventory refresh unavailable")
        if wait is not None:
            await wait(60)
        else:
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass


class ExecutionTargetError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_id(provider: str, provider_instance_id: str) -> str:
    return f"{provider}:{provider_instance_id}"


def observed_artifact_inventory(target):
    """Last explicit verified download, not discovery of all installed science."""
    import json
    from datetime import timezone
    from pydantic import ValidationError
    from .contracts import ObservedArtifactInventory
    metadata = target.provider_metadata
    if not isinstance(metadata, dict):
        return None
    stored = metadata.get("artifact_inventory")
    if not isinstance(stored, dict) or not stored:
        return None
    raw = dict(stored)
    binding = raw.pop("endpoint_sha256", None)
    identity = (target.host, target.port, target.username, target.remote_root, target.host_key_sha256)
    try:
        observation = ObservedArtifactInventory.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        # Optional historical observations must not break otherwise healthy
        # target listings. GET neither repairs metadata nor probes the worker.
        return None
    # Freshness is derived, never trusted from the stored projection.
    observation.state = "stale"
    observed = observation.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    current = metadata.get("preload")
    if not isinstance(current, dict):
        return observation
    if (0 <= age <= INVENTORY_MAX_AGE_SECONDS and inventory_fresh(target)
            and current.get("operation_id") == observation.operation_id
            and current.get("phase") == "source_download_ready"
            and target.active and target.state == "ready"
            and binding == hashlib.sha256(json.dumps(identity).encode()).hexdigest()):
        observation.state = "download_verified"
    return observation


def _target_response(target: ExecutionTarget) -> ExecutionTargetResponse:
    return ExecutionTargetResponse(
        artifact_inventory=observed_artifact_inventory(target),
        setup=(target.provider_metadata or {}).get("setup"),
        preload=(target.provider_metadata or {}).get("preload"),
        progress=(target.provider_metadata or {}).get("progress") if target.leased_job_id else None,
        id=str(target.id),
        provider="vast",
        provider_instance_id=str(target.provider_instance_id),
        name=target.name,
        state=cast(
            Literal["discovered", "probing", "ready", "unavailable", "inactive"],
            str(target.state),
        ),
        active=bool(target.active),
        host=target.host,
        port=target.port,
        username=target.username,
        remote_root=str(target.remote_root),
        host_key_sha256=target.host_key_sha256,
        capabilities={**dict(target.capabilities or {}), "scheduling": {
            "policy": "exclusive_target", "max_concurrent_root_attempts": 1,
            "new_work_ready": target_eligible(target) and not target.leased_job_id,
            "inventory_fresh": inventory_fresh(target),
            "leased_job_id": target.leased_job_id,
        }},
        pricing=dict(target.pricing or {}),
        last_error=target.last_error,
        last_seen_at=target.last_seen_at,
        activated_at=target.activated_at,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


def _pricing(instance: DiscoveredExecutionTarget) -> dict[str, Any]:
    return {
        "currency": "USD",
        "hourly_rate": instance.hourly_rate_usd,
        "provider_started_at": (
            instance.started_at.isoformat() if instance.started_at is not None else None
        ),
        "billing_continues_after_deactivation": True,
    }


def _capabilities(instance: DiscoveredExecutionTarget) -> dict[str, Any]:
    return {
        "gpu_name": instance.gpu_name,
        "gpu_count": instance.gpu_count,
        "gpu_vram_mb": instance.gpu_vram_mb,
        "provider_verified": instance.verified,
    }


def _optional_float(value: str) -> float | None:
    normalized = value.strip().lower()
    if normalized in {"", "n/a", "na", "[not supported]"}:
        return None
    return float(value)


async def list_targets(session: AsyncSession) -> list[ExecutionTargetResponse]:
    rows = (
        await session.execute(
            select(ExecutionTarget).order_by(
                ExecutionTarget.active.desc(),
                ExecutionTarget.updated_at.desc(),
            )
        )
    ).scalars().all()
    if (
        not rows and (_empty_inventory_checked_at is None or
        (datetime.utcnow() - _empty_inventory_checked_at).total_seconds() > INVENTORY_MAX_AGE_SECONDS)
    ):
        raise ExecutionTargetError("Vast inventory is unknown or expired; placement is unavailable")
    # A stale/unreachable member cannot hide the rest of the fleet or retained
    # ownership. Per-target admission remains fail-closed in get_ready_target.
    return [_target_response(row) for row in rows if row.active or row.leased_job_id
            or (row.provider_metadata or {}).get("inventory", {}).get("present") is True]


async def get_target(session: AsyncSession, execution_target_id: str) -> ExecutionTarget:
    """Identity lookup for observation/control; not new-work admission authority."""
    target = await session.get(ExecutionTarget, execution_target_id, populate_existing=True)
    if target is None:
        raise ExecutionTargetError("Execution target does not exist")
    return target


async def submission_target_fields(
    session: AsyncSession, execution_target_id: str | None, *, parent_job: Job | None = None,
) -> dict[str, Any]:
    """Shared typed-submission placement; no GPU index or scientific mutation.

    Admission is separate from existing-attempt control. Computational follow-ons
    retain their parent's target and immutable source, never fall back to Local.
    """
    target_id = execution_target_id
    if target_id is not None and (not isinstance(target_id, str) or not target_id.strip() or len(target_id) > 160):
        raise ExecutionTargetError("execution_target_id must be a nonempty target identity or null")
    if parent_job is not None and target_id != parent_job.execution_target_id:
        raise ExecutionTargetError("Child execution_target_id must match the parent Job")
    fields = {"execution_target_id": target_id}
    if target_id is None:
        return fields
    target = await session.get(ExecutionTarget, target_id, populate_existing=True)
    if target is None or not target_eligible(target):
        raise ExecutionTargetError("execution_target_id is not an active ready execution target")
    if parent_job is not None:
        revision, tree = parent_job.execution_source_revision, parent_job.execution_source_tree
        if not revision or not tree:
            raise ExecutionTargetError("Remote parent Job is missing its immutable source identity")
    else:
        from .bundle import current_source_identity
        revision, tree = current_source_identity()
    fields.update(execution_source_revision=revision, execution_source_tree=tree)
    return fields


async def get_ready_target(session: AsyncSession, execution_target_id: str) -> ExecutionTarget:
    """New-work admission only; existing attempts use their persisted control binding."""
    target = await get_target(session, execution_target_id)
    if not target_eligible(target):
        raise ExecutionTargetError("Execution target is not active and ready")
    return target


def blocking_job_clause():
    # Finished remote work awaiting an explicit local pull owns no worker lease.
    pending_return = (
        (func.coalesce(Job.awaiting_stage, "") == "remote_results")
        & func.coalesce(Job.remote_state, "").in_(("results_available", "result_pull_failed", "returning"))
    )
    return Job.status.notin_(("completed", "failed", "cancelled", "canceled")) & ~pending_return


async def _has_nonterminal_jobs(session: AsyncSession, execution_target_id: str) -> bool:
    job_id = await session.scalar(
        select(Job.id)
        .where(
            Job.execution_target_id == execution_target_id,
            blocking_job_clause(),
        )
        .limit(1)
    )
    return job_id is not None


async def refresh_vast_targets(session: AsyncSession) -> ExecutionTargetInventoryResponse:
    # Fetch and publication are one ordered operation shared by Discover/attach/lifespan.
    async with _inventory_refresh_lock:
        return await _refresh_vast_targets(session)


async def _refresh_vast_targets(session: AsyncSession) -> ExecutionTargetInventoryResponse:
    global _empty_inventory_checked_at
    try:
        inventory = await list_owned_instances()
    except VastInventoryError as exc:
        await invalidate_vast_inventory(session)
        raise ExecutionTargetError(str(exc)) from exc
    if not inventory.available:
        await invalidate_vast_inventory(session)
        return inventory
    now = datetime.utcnow()
    present_ids = {target_id("vast", instance.provider_instance_id) for instance in inventory.instances}
    for row in (await session.scalars(select(ExecutionTarget).where(ExecutionTarget.provider == "vast"))).all():
        if row.id not in present_ids:
            row.provider_metadata = {**dict(row.provider_metadata or {}), "inventory": {
                "status": "complete", "present": False, "running": False, "checked_at": now.isoformat()}}
            row.active = False
            row.state = "inactive"
            row.last_error = "Absent from complete owned Vast inventory"
    _empty_inventory_checked_at = now
    for instance in inventory.instances:
        identifier = target_id("vast", instance.provider_instance_id)
        target = await session.get(ExecutionTarget, identifier)
        if target is None:
            target = ExecutionTarget(
                id=identifier,
                provider="vast",
                provider_instance_id=instance.provider_instance_id,
                state="discovered",
                active=False,
                created_at=now,
                updated_at=now,
            )
            session.add(target)
            target.host = instance.host
            target.port = instance.port
            target.username = instance.username or "root"
        endpoint_changed = (target.host, target.port) != (instance.host, instance.port)
        if target not in session.new:
            # Evaluate the lease in the endpoint write itself, not a stale ORM read.
            unleased = ExecutionTarget.leased_job_id.is_(None)
            await session.execute(update(ExecutionTarget).where(ExecutionTarget.id == identifier).values(
                host=case((unleased, instance.host), else_=ExecutionTarget.host),
                port=case((unleased, instance.port), else_=ExecutionTarget.port),
                username=case((unleased, func.coalesce(ExecutionTarget.username, instance.username or "root")),
                              else_=ExecutionTarget.username),
            ).execution_options(synchronize_session=False))
            await session.refresh(target)
        target.name = instance.name
        if endpoint_changed and target.host is not None:
            target.active = False
            target.state = "unavailable"
            target.last_error = "Provider SSH endpoint changed; attachment required"
        target.capabilities = {
            **dict(target.capabilities or {}),
            **_capabilities(instance),
        }
        target.pricing = _pricing(instance)
        target.provider_metadata = {**dict(target.provider_metadata or {}), "inventory": {
            "status": "complete", "present": True,
            "running": instance.provider_state in RUNNING_PROVIDER_STATES, "checked_at": now.isoformat()}}
        target.last_seen_at = now
        target.updated_at = now
        if instance.provider_state not in RUNNING_PROVIDER_STATES:
            target.state = "unavailable" if target.active else "discovered"
            target.last_error = f"Provider state is {instance.provider_state}"
    await session.commit()
    return inventory


SETUP_ACTIVE_PHASES = ("checking", "installing", "transferring", "verifying")


async def fail_setup(session, identifier, started_at, message: str) -> None:
    """End only this setup attempt; retain inventory-owned state and errors."""
    probing = ExecutionTarget.state == "probing"
    now = datetime.utcnow()
    await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == identifier, ExecutionTarget.leased_job_id.is_(None),
        ExecutionTarget.provider_metadata["setup"]["started_at"].as_string() == started_at,
        or_(probing, ExecutionTarget.provider_metadata["setup"]["phase"].as_string().in_(SETUP_ACTIVE_PHASES)),
    ).values(
        provider_metadata=func.json_set(ExecutionTarget.provider_metadata,
            "$.setup.phase", "failed", "$.setup.message", message, "$.setup.updated_at", now.isoformat()),
        state=case((probing, "unavailable"), else_=ExecutionTarget.state),
        active=case((probing, False), else_=ExecutionTarget.active),
        last_error=case((probing, message), else_=ExecutionTarget.last_error),
        updated_at=now,
    ).execution_options(synchronize_session=False))
    await session.commit()


async def set_setup(session, target, phase: str, message: str, *, expected_started_at=None) -> None:
    await session.refresh(target)
    now = datetime.utcnow().isoformat()
    previous = (target.provider_metadata or {}).get("setup", {})
    setup = {"phase": phase, "message": message, "started_at": previous.get("started_at", now), "updated_at": now}
    predicate = ExecutionTarget.id == target.id
    if expected_started_at is not None:
        predicate = predicate & (ExecutionTarget.provider_metadata["setup"]["started_at"].as_string() == expected_started_at)
    published = await session.execute(update(ExecutionTarget).where(predicate).values(
        provider_metadata=func.json_set(ExecutionTarget.provider_metadata, "$.setup", func.json(json.dumps(setup)))
    ).execution_options(synchronize_session=False))
    if published.rowcount != 1:
        await session.rollback()
        raise ExecutionTargetError("Setup attempt was replaced; current attempt preserved")
    await session.commit()
    await session.refresh(target)


class AttachmentController:
    """One lifespan-owned installer; requests never own its sessions or task."""
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.closed = False

    async def attach(self, session, request):
        async with self.lock:
            if self.closed:
                raise ExecutionTargetError("Attachment service is stopping")
            if target_id("vast", request.provider_instance_id) in self.tasks:
                raise ExecutionTargetError("An attachment is already in progress")
            result = await begin_activation(session, request)
            if result.setup is None or result.setup.started_at is None:
                raise ExecutionTargetError("Setup admission did not publish an attempt identity")
            task = asyncio.create_task(self._run(result.id, result.setup.started_at.isoformat()), name=f"attach-{result.id}")
            self.tasks[result.id] = task
            return result

    async def _run(self, identifier, started_at):
        try:
            async with self.session_factory() as session:
                await finish_activation(session, identifier)
        except BaseException as exc:
            async with self.session_factory() as session:
                row = await get_target(session, identifier)
                phase = (row.provider_metadata or {}).get("setup", {}).get("phase", "checking")
                message = f"Remote setup interrupted during {phase}; retry Attach" if isinstance(exc, asyncio.CancelledError) else f"Remote setup failed during {phase}; retry Attach"
                if isinstance(exc, ExecutionTargetError):
                    message = str(exc)
                await fail_setup(session, identifier, started_at, message)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self.tasks.pop(identifier, None)

    async def recover(self):
        async with self.session_factory() as session:
            rows = (await session.scalars(select(ExecutionTarget).where(
                or_(ExecutionTarget.state == "probing",
                    ExecutionTarget.provider_metadata["setup"]["phase"].as_string().in_(SETUP_ACTIVE_PHASES)),
                ExecutionTarget.leased_job_id.is_(None)))).all()
            for row in rows:
                message = "Remote setup interrupted by service restart; retry Attach"
                await fail_setup(session, row.id, (row.provider_metadata or {}).get("setup", {}).get("started_at"), message)

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def activate_target(session, request):
    """Internal synchronous entrypoint retained for fenced service callers."""
    result = await begin_activation(session, request)
    return await finish_activation(session, result.id)


async def begin_activation(
    session: AsyncSession,
    request: ExecutionTargetActivateRequest,
) -> ExecutionTargetResponse:
    inventory = await refresh_vast_targets(session)
    instance = next((item for item in inventory.instances
                     if item.provider_instance_id == request.provider_instance_id), None)
    if not inventory.available or instance is None:
        raise ExecutionTargetError("Selected Vast instance is absent or inventory is unavailable")
    existing = await session.get(ExecutionTarget, target_id("vast", request.provider_instance_id))

    if existing is not None and existing.leased_job_id:
        raise ExecutionTargetError("Execution target has an active attempt lease; cannot reattach")
    if instance.provider_state not in RUNNING_PROVIDER_STATES:
        raise ExecutionTargetError(
            f"Vast instance must be running before activation; current state is {instance.provider_state}"
        )
    if not instance.host or not instance.port:
        raise ExecutionTargetError("Vast instance has no SSH endpoint")
    now = datetime.utcnow()
    identifier = target_id("vast", instance.provider_instance_id)

    admitted = await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == identifier,
        ExecutionTarget.leased_job_id.is_(None),
        ExecutionTarget.state != "probing",
        preload_idle_clause(),

        ExecutionTarget.provider_metadata["inventory"]["status"].as_string() == "complete",
        ExecutionTarget.provider_metadata["inventory"]["present"].as_boolean().is_(True),
        ExecutionTarget.provider_metadata["inventory"]["running"].as_boolean().is_(True),
        ExecutionTarget.host == instance.host,
        ExecutionTarget.port == instance.port,
        ExecutionTarget.provider_metadata["inventory"]["checked_at"].as_string() >=
            (now - timedelta(seconds=INVENTORY_MAX_AGE_SECONDS)).isoformat(),
        ExecutionTarget.provider_metadata["inventory"]["checked_at"].as_string() <= now.isoformat(),
    ).values(
        name=instance.name, state="probing", active=False, host=instance.host, port=instance.port,
        username=request.username or instance.username or "root", remote_root=request.remote_root,
        capabilities=_capabilities(instance), pricing=_pricing(instance),
        last_seen_at=now, updated_at=now, last_error=None,
        provider_metadata=func.json_set(ExecutionTarget.provider_metadata, "$.setup", func.json(json.dumps({
            "phase": "checking", "message": "Checking remote worker compatibility",
            "started_at": now.isoformat(), "updated_at": now.isoformat(),
        }))),
    ).execution_options(synchronize_session=False))
    if admitted.rowcount != 1:
        await session.rollback()
        raise ExecutionTargetError("Vast inventory or lease changed before attachment")
    await session.commit()
    target = await get_target(session, identifier)

    await session.refresh(target)
    return _target_response(target)


async def finish_activation(session: AsyncSession, identifier: str) -> ExecutionTargetResponse:
    target = await get_target(session, identifier)
    connection = RemoteConnection.from_target(target)
    started_at = (target.provider_metadata or {}).get("setup", {}).get("started_at")

    async def checked_io(operation, *args, **kwargs):
        await session.refresh(target)
        if (not inventory_fresh(target)
                or target.provider_metadata["inventory"].get("present") is not True
                or target.provider_metadata["inventory"].get("running") is not True

                or target.state != "probing" or target.leased_job_id
                or (target.provider_metadata or {}).get("setup", {}).get("started_at") != started_at
                or (target.host, target.port, target.username, target.remote_root) !=
                   (connection.host, connection.port, connection.username, connection.remote_root)):
            raise ExecutionTargetError("Vast inventory or endpoint changed during attachment")
        return await operation(*args, **kwargs)

    async def checked_verification(argv, failure: str, *, timeout: int):
        try:
            return await checked_io(run_remote, connection, argv, timeout=timeout)
        except RemoteTransportError as exc:
            if str(exc) in {"Remote transport timed out", "Remote SSH host key changed"}:
                raise
            raise RemoteTransportError(failure) from exc

    try:
        host_key_line, fingerprint = await checked_io(capture_host_key, connection.host, connection.port)
        if target.host_key_sha256 and target.host_key_sha256 != fingerprint:
            raise RemoteTransportError("Remote SSH host key changed since the last activation")
        await persist_host_key(host_key_line, fingerprint)
        script = Path(__file__).with_name("bootstrap_worker.sh").read_bytes()
        await checked_io(run_remote, connection, ["bash", "-s", "--", "check", connection.remote_root], input_bytes=script, timeout=60)
        await set_setup(session, target, "installing", "Installing missing worker tools (up to 30 minutes)", expected_started_at=started_at)
        await checked_io(run_remote, connection, ["bash", "-s", "--", "install", connection.remote_root], input_bytes=script, timeout=3600)
        probe = await checked_io(probe_readiness, connection)
        from . import critical_runtime, managed_inventory, cache
        import tempfile
        async def fence():
            async def noop():
                pass
            await checked_io(noop)
        async def progress(event):
            await fence()
            await set_setup(session, target, "transferring", event['message'], expected_started_at=started_at)
        with tempfile.TemporaryDirectory(prefix='bms-critical-') as staging:
            projection = asyncio.create_task(asyncio.to_thread(critical_runtime.project_runtime,
                connection.remote_root, Path(staging)))
            try:
                manifest, artifacts = await asyncio.shield(projection)
            except asyncio.CancelledError:
                # Finish this local writer before TemporaryDirectory removes its
                # files. Cancellation never leaves a thread writing into cleanup.
                while not projection.done():
                    try:
                        await asyncio.shield(projection)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not projection.cancelled():
                    projection.exception()
                raise
            boot_response = await managed_inventory.helper_call(connection, dict(action='boot'), fence)
            boot = boot_response['boot_id']
            admission = await managed_inventory.helper_call(connection,
                dict(action='admit', manifest=manifest, boot_id=boot), fence)
            if admission['boot_id'] != boot:
                raise RemoteTransportError("Critical runtime boot identity changed")
            await cache._cache_artifacts(connection=connection, artifacts=artifacts,
                operation_id=str(uuid.uuid4()), progress=progress, check_fence=fence)
        binding = critical_runtime.runtime_binding(connection.remote_root, manifest)
        await set_setup(session, target, "verifying", "Verifying critical runtime and CUDA", expected_started_at=started_at)
        cuda = await checked_verification([
            "apptainer", "exec", "--nv",
            "docker://python@sha256:97983fa8cc88343512862c62307159a82261c3528dc025f79e5a3f7af43e50b4",
            "python", "-c", "import ctypes; c=ctypes.CDLL('libcuda.so.1'); assert c.cuInit(0)==0; n=ctypes.c_int(); assert c.cuDeviceGetCount(ctypes.byref(n))==0 and n.value>0; print('BMS_CUDA_OK')",
        ], "CUDA container verification failed", timeout=3600)
        if "BMS_CUDA_OK" not in cuda.stdout.splitlines():
            raise RemoteTransportError("CUDA container verification failed")
        # Check the real container/driver boundary before replacing the active
        # release; a failed CUDA probe must preserve the previous generation.
        critical_release = await managed_inventory.activate_release(
            connection, manifest, fence, progress, boot)
        manifests = [m for m in managed_inventory.saved_manifests(target)
                     if m["selection"] != manifest["selection"]] + [manifest]
        readback = await managed_inventory.observe_releases(connection, manifests, fence)
        if str(readback.boot_id) != boot or not readback.critical_runtime_ready:
            raise RemoteTransportError("Critical runtime boot identity changed")
    except (RemoteTransportError, OSError, ValueError) as exc:
        await session.refresh(target)
        phase = (target.provider_metadata or {}).get("setup", {}).get("phase", "checking")
        safe = BOOTSTRAP_ERRORS | {
            "Remote transport timed out", "Remote runner transfer failed integrity verification",
            "Pinned Nextflow version verification failed", "CUDA container verification failed",
            "Remote SSH host key changed since the last activation", "Remote SSH host key changed",
            "Unable to read the remote SSH host key", "Remote readiness probe returned invalid output",
            "Remote readiness probe is incomplete",
            "Pinned Nextflow framework JAR is unavailable",
            "Critical runtime boot identity changed",
        }
        message = str(exc) if str(exc) in safe else f"Remote setup failed during {phase}; retry Attach"
        setup = {**(target.provider_metadata or {}).get("setup", {}), "phase": "failed",
                 "message": message, "updated_at": datetime.utcnow().isoformat()}
        await session.execute(update(ExecutionTarget).where(
            ExecutionTarget.id == identifier, ExecutionTarget.state == "probing",
            ExecutionTarget.provider_metadata["setup"]["started_at"].as_string() == started_at,
            ExecutionTarget.leased_job_id.is_(None), ExecutionTarget.host == connection.host,
            ExecutionTarget.port == connection.port, ExecutionTarget.username == connection.username,
            ExecutionTarget.remote_root == connection.remote_root,
        ).values(state="unavailable", active=False, last_error=message, updated_at=datetime.utcnow(),
            provider_metadata=func.json_set(ExecutionTarget.provider_metadata, "$.setup", func.json(json.dumps(setup)))
        ).execution_options(synchronize_session=False))
        await session.commit()
        raise ExecutionTargetError(message) from exc


    await session.refresh(target)
    # Publish readiness with an atomic current-inventory predicate, never ORM
    # autoflush of an old ready projection after network I/O.
    now = datetime.utcnow()
    published = await session.execute(
        update(ExecutionTarget).where(
            ExecutionTarget.id == identifier,
            ExecutionTarget.state == "probing",
            ExecutionTarget.provider_metadata["setup"]["started_at"].as_string() == started_at,
            ExecutionTarget.leased_job_id.is_(None),
            ExecutionTarget.host == connection.host,
            ExecutionTarget.port == connection.port,
            ExecutionTarget.username == connection.username,
            ExecutionTarget.remote_root == connection.remote_root,
            ExecutionTarget.provider_metadata["inventory"]["status"].as_string() == "complete",
            ExecutionTarget.provider_metadata["inventory"]["present"].as_boolean().is_(True),
            ExecutionTarget.provider_metadata["inventory"]["running"].as_boolean().is_(True),
            ExecutionTarget.provider_metadata["inventory"]["checked_at"].as_string() >=
                (now - timedelta(seconds=INVENTORY_MAX_AGE_SECONDS)).isoformat(),
            ExecutionTarget.provider_metadata["inventory"]["checked_at"].as_string() <= now.isoformat(),
        ).values(
            state="ready", active=True, activated_at=now, updated_at=now, last_error=None,
            provider_metadata={**dict(target.provider_metadata or {}),
                "managed_boot_id": boot,
                "critical_runtime_manifest": manifest,
                "managed_inventory": dict(manifests=manifests, observation=readback.model_dump(mode="json"),
                    endpoint_sha256=hashlib.sha256(json.dumps((connection.host, connection.port,
                        connection.username, connection.remote_root, fingerprint)).encode()).hexdigest()),
                "critical_runtime_endpoint_sha256": hashlib.sha256(json.dumps((connection.host, connection.port,
                    connection.username, connection.remote_root, fingerprint)).encode()).hexdigest(),
                "setup": {
                **(target.provider_metadata or {}).get("setup", {}), "phase": "ready",
                "message": "Remote worker ready; analytics available", "updated_at": now.isoformat()}},
            host_key_sha256=fingerprint,
            capabilities={**dict(target.capabilities or {}), "readiness": probe,
                          "critical_runtime": critical_release.model_dump(mode="json"),
                          "critical_runtime_binding": binding},
        ).execution_options(synchronize_session=False)
    )
    if published.rowcount != 1:
        await session.rollback()
        raise ExecutionTargetError("Vast inventory or endpoint changed during attachment")
    await session.commit()
    await session.refresh(target)
    return _target_response(target)


async def deactivate_target(
    session: AsyncSession,
    execution_target_id: str,
) -> ExecutionTargetResponse:
    target = await get_target(session, execution_target_id)
    if await _has_nonterminal_jobs(session, execution_target_id):
        raise ExecutionTargetError(
            "Execution target has nonterminal Jobs and cannot be detached"
        )
    changed = await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == execution_target_id, ExecutionTarget.leased_job_id.is_(None),
        ExecutionTarget.state != "probing", preload_idle_clause(),
    ).values(active=False, state="inactive", updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        await session.rollback()
        raise ExecutionTargetError("Worker has active setup, preload, or execution; cannot detach")
    await session.commit()
    await session.refresh(target)
    return _target_response(target)


async def remote_target_telemetry(target: ExecutionTarget) -> dict[str, Any]:
    """Admission reads only fresh, identity-bound cached VRAM; never polls SSH."""
    from .telemetry import remote_telemetry
    sample = remote_telemetry.read(target, include_history=False)
    if any(gpu.get('memory_total_mb') is None or gpu.get('memory_used_mb') is None
           or gpu.get('memory_total_mb', 0) <= 0
           for gpu in sample['gpus']):
        sample.update(available=False, error='Remote VRAM readings unavailable')
    return sample


async def admit_target_resources(
    target: ExecutionTarget, *, required_cpus: int, required_memory_bytes: int,
    required_scratch_bytes: int, gpu_ids: list[int], minimum_gpu_memory_mb: int = 0,
) -> dict[str, Any]:
    """Bind compiled requirements to fresh target capacity, without changing science.

    The launch/compiler owner supplies the aggregate plan and bundle budget;
    Nextflow must enforce the returned CPU/RAM ceiling within this root lease.
    Descendants subdivide this reservation, never reserve another target.
    """
    requirements = {"cpus": required_cpus, "memory_bytes": required_memory_bytes,
                    "scratch_bytes": required_scratch_bytes}
    if any(type(value) is not int or value < 0 for value in requirements.values()):
        raise ExecutionTargetError("Compiled resource requirements must be nonnegative integers")
    if not required_cpus or not required_memory_bytes:
        raise ExecutionTargetError("Compiled CPU and memory requirements must be explicit")
    sample = await remote_target_telemetry(target)
    if not sample.get("available"):
        raise ExecutionTargetError(sample.get("error") or "Fresh target capacity is unavailable")
    cpu, ram, disk = (sample.get(key) or {} for key in ("cpu", "ram", "disk"))
    observed = {"cpus": cpu.get("allocated_cores"),
                "memory_bytes": (ram["limit_bytes"] - ram["used_bytes"]
                                 if ram.get("limit_bytes") is not None and ram.get("used_bytes") is not None else None),
                "scratch_bytes": disk.get("free_bytes")}
    for key, required in requirements.items():
        available = observed[key]
        if not isinstance(available, (int, float)) or available < required:
            raise ExecutionTargetError(f"Target {key} capacity {available} cannot satisfy {required}")
    if disk.get("path") != target.remote_root:
        raise ExecutionTargetError("Scratch observation does not belong to the target work root")
    if type(minimum_gpu_memory_mb) is not int or minimum_gpu_memory_mb < 0:
        raise ExecutionTargetError("Compiled GPU physical capacity floor must be a nonnegative integer")
    devices = {row["index"]: row for row in sample.get("gpus", [])}
    if len(set(gpu_ids)) != len(gpu_ids) or any(i not in devices or not devices[i].get("uuid") for i in gpu_ids):
        raise ExecutionTargetError("Physical GPU identity is unavailable for the target assignment")
    if minimum_gpu_memory_mb and (not gpu_ids or any(
        devices[i].get("memory_total_mb", 0) < minimum_gpu_memory_mb for i in gpu_ids
    )):
        raise ExecutionTargetError(f"Assigned GPU physical capacity is below {minimum_gpu_memory_mb} MB")
    return {"schema": "bms.target-resource-admission.v1", "execution_target_id": str(target.id),
            "policy": "exclusive_target", "observed_at": sample.get("observed_at"),
            "required": requirements, "available": observed,
            "minimum_gpu_memory_mb": minimum_gpu_memory_mb,
            "devices": [{"gpu_index": i, "gpu_uuid": devices[i]["uuid"]} for i in gpu_ids]}


def selected_plan_target_resources(target, plan, *, gpu_ids, scratch_bytes) -> dict[str, Any]:
    """Pure native requirement projection for synchronous bundle preparation.

    The shared adapter serializes compute tasks across root and descendants with
    one inherited slot. Native waiters retain CPU/RAM while children compute:
    reserve their conservative sum (one fork per declared authority) in addition
    to the compute maximum. No coordinator may consume GPU compute capacity.
    """
    import re
    from biomodstack_local_resources import GIB

    if isinstance(plan, dict):
        # Retained authenticated contexts use the same resource projection as
        # in-process compiler plans; do not recompile science to renew admission.
        from types import SimpleNamespace
        metadata = plan['metadata']
        components = tuple(SimpleNamespace(**dict(row,
            resources_json=json.dumps(row['resources_json'])))
            for key in ('static_components', 'dynamic_templates') for row in metadata[key])
    else:
        components = (*plan.metadata.static_components, *plan.metadata.dynamic_templates)
    if not components:
        raise ExecutionTargetError("Selected plan has no native resource declarations")
    cpus = memory = required_gpus = minimum_gpu_memory_mb = 0
    coordinators = {}
    declarations = []
    for component in components:
        try:
            resource = json.loads(component.resources_json)
            cpu = resource["cpus"]["value"]
            raw_memory = resource["memory"]["value"]
            # Nextflow MemoryUnit uses binary units even for its GB spelling.
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB)",
                                 str(raw_memory).strip(), re.IGNORECASE)
            if type(cpu) is not int or cpu <= 0 or match is None:
                raise ValueError("unresolved CPU/memory value")
            from decimal import Decimal
            scale = {"B": 1, "KB": GIB // (1024 * 1024), "MB": GIB // 1024,
                     "GB": GIB, "TB": GIB * 1024}[match[2].upper()]
            size = Decimal(match[1]) * scale
            if size <= 0 or size != int(size):
                raise ValueError("invalid native memory quantity")
            gpu = resource.get("gpu")
            count = gpu["count"] if gpu is not None else 0
            if type(count) is not int or count < 0:
                raise ValueError("unresolved GPU count")
            floor = gpu.get("minimum_gpu_memory_mb", 0) if gpu else 0
            if type(floor) is not int or floor < 0:
                raise ValueError("unresolved physical GPU capacity floor")
            minimum_gpu_memory_mb = max(minimum_gpu_memory_mb, floor)
            role = resource.get('execution_role', 'compute')
            if role not in {'compute', 'coordinator'}:
                raise ValueError('unknown native execution role')
            if role == 'coordinator':
                from native_components import NATIVE_COORDINATORS
                if (component.authority not in NATIVE_COORDINATORS or count
                        or resource.get('max_forks') != 1 or component.expansion_authority):
                    raise ValueError('coordinator exemption requires a bounded native waiter')
                previous = coordinators.get(component.authority, (0, 0))
                coordinators[component.authority] = (max(previous[0], cpu), max(previous[1], int(size)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionTargetError(
                f"Native resource declaration unresolved: {component.component_key}: {exc}"
            ) from exc
        declarations.append({"component_key": component.component_key, "cpus": cpu,
            "memory_bytes": int(size), "gpu_count": count, "native_policy": resource})
        if role == 'compute':
            cpus, memory, required_gpus = max(cpus, cpu), max(memory, int(size)), max(required_gpus, count)
    coordinator_budget = {'cpus': sum(row[0] for row in coordinators.values()),
                          'memory_bytes': sum(row[1] for row in coordinators.values())}
    compute_budget = {'cpus': cpus, 'memory_bytes': memory}
    cpus += coordinator_budget['cpus']
    memory += coordinator_budget['memory_bytes']
    if len(gpu_ids) < required_gpus:
        raise ExecutionTargetError("Selected plan GPU requirement exceeds assigned target devices")
    return {"schema": "bms.selected-plan-target-resources.v1",
            "execution_target_id": str(target.id), "policy": "exclusive_target",
            "required": {"cpus": cpus, "memory_bytes": memory, "scratch_bytes": scratch_bytes},
            "gpu_ids": list(gpu_ids), "components": declarations,
            "compute": compute_budget, "coordinator_overlap": coordinator_budget,
            "minimum_gpu_memory_mb": minimum_gpu_memory_mb,
            "admission_required": True}


async def active_remote_telemetry(
    session: AsyncSession, since: str | None = None,
    execution_target_id: str | None = None,
) -> dict[str, Any]:
    query = select(ExecutionTarget).where(
        ExecutionTarget.active.is_(True), ExecutionTarget.state == "ready",
    )
    if execution_target_id is not None:
        query = query.where(ExecutionTarget.id == execution_target_id)
    # Preserve the single-worker API, but never choose an arbitrary fleet member.
    candidates = (await session.execute(query.limit(2))).scalars().all()
    target = candidates[0] if len(candidates) == 1 else None
    from .telemetry import remote_telemetry
    value = remote_telemetry.read(target, since)
    if len(candidates) > 1:
        value['error'] = 'Select an execution target to view fleet telemetry'
    return value
