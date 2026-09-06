"""Persistence and activation service for execution-only remote targets."""
from __future__ import annotations

import asyncio
import logging
import hashlib
import json
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
    return bool(target.active and target.state == "ready" and inventory_fresh(target)
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


def _target_response(target: ExecutionTarget) -> ExecutionTargetResponse:
    return ExecutionTargetResponse(
        setup=(target.provider_metadata or {}).get("setup"),
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
        capabilities=dict(target.capabilities or {}),
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
    if any(not inventory_fresh(row) for row in rows) or (
        not rows and (_empty_inventory_checked_at is None or
        (datetime.utcnow() - _empty_inventory_checked_at).total_seconds() > INVENTORY_MAX_AGE_SECONDS)
    ):
        raise ExecutionTargetError("Vast inventory is unknown or expired; placement is unavailable")
    return [_target_response(row) for row in rows if row.provider_metadata["inventory"].get("present") is True]


async def get_target(session: AsyncSession, execution_target_id: str) -> ExecutionTarget:
    target = await session.get(ExecutionTarget, execution_target_id, populate_existing=True)
    if target is None:
        raise ExecutionTargetError("Execution target does not exist")
    return target


async def get_ready_target(session: AsyncSession, execution_target_id: str) -> ExecutionTarget:
    target = await get_target(session, execution_target_id)
    if not target_eligible(target):
        raise ExecutionTargetError("Execution target is not active and ready")
    return target


async def _has_nonterminal_jobs(session: AsyncSession, execution_target_id: str) -> bool:
    job_id = await session.scalar(
        select(Job.id)
        .where(
            Job.execution_target_id == execution_target_id,
            Job.status.notin_(("completed", "failed", "cancelled", "canceled")),
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
            if self.tasks:
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
    active_target = await session.scalar(
        select(ExecutionTarget).where(
            ExecutionTarget.active.is_(True),
            ExecutionTarget.id != identifier,
        )
    )
    if active_target is not None and await _has_nonterminal_jobs(session, str(active_target.id)):
        raise ExecutionTargetError(
            "The active execution target has nonterminal Jobs and cannot be replaced"
        )
    admitted = await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == identifier,
        ExecutionTarget.leased_job_id.is_(None),
        ExecutionTarget.state != "probing",
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
        await set_setup(session, target, "transferring", "Transferring verified runner and Nextflow; slow links may take up to one hour", expected_started_at=started_at)
        runner = Path(__file__).resolve().parents[2] / "tools" / "bms_remote_worker.py"
        from services.nextflow import resolve_nextflow_executable, resolve_nextflow_version

        nextflow_launcher = Path(resolve_nextflow_executable())
        await checked_io(run_remote, connection, ["mkdir", "-p", f"{connection.remote_root}/runner"])
        await checked_io(rsync_to_remote,
            connection,
            runner,
            f"{connection.remote_root}/runner/bms_remote_worker.py",
            timeout=3600,
        )
        await checked_io(run_remote,
            connection,
            ["chmod", "0755", f"{connection.remote_root}/runner/bms_remote_worker.py"],
        )
        await checked_io(rsync_to_remote,
            connection,
            nextflow_launcher,
            f"{connection.remote_root}/runner/nextflow",
            timeout=3600,
        )
        await checked_io(run_remote,
            connection,
            ["chmod", "0755", f"{connection.remote_root}/runner/nextflow"],
        )
        runner_sha256 = _sha256_file(runner)
        nextflow_sha256 = _sha256_file(nextflow_launcher)
        remote_hashes = await checked_io(run_remote,
            connection,
            [
                "sha256sum",
                f"{connection.remote_root}/runner/bms_remote_worker.py",
                f"{connection.remote_root}/runner/nextflow",
            ],
        )
        observed_hashes = [
            line.split()[0]
            for line in remote_hashes.stdout.splitlines()
            if line.strip()
        ]
        if observed_hashes != [runner_sha256, nextflow_sha256]:
            raise RemoteTransportError("Remote runner transfer failed integrity verification")
        await set_setup(session, target, "verifying", "Verifying Nextflow and CUDA inside a pinned container", expected_started_at=started_at)
        expected_version = resolve_nextflow_version()
        version = await checked_verification(
            ["env", "NXF_OFFLINE=true", f"NXF_VER={expected_version}", f"{connection.remote_root}/runner/nextflow", "-version"],
            "Pinned Nextflow version verification failed", timeout=120)
        if f"version {expected_version}" not in version.stdout:
            raise RemoteTransportError("Pinned Nextflow version verification failed")
        cuda = await checked_verification([
            "apptainer", "exec", "--nv",
            "docker://python@sha256:97983fa8cc88343512862c62307159a82261c3528dc025f79e5a3f7af43e50b4",
            "python", "-c", "import ctypes; c=ctypes.CDLL('libcuda.so.1'); assert c.cuInit(0)==0; n=ctypes.c_int(); assert c.cuDeviceGetCount(ctypes.byref(n))==0 and n.value>0; print('BMS_CUDA_OK')",
        ], "CUDA container verification failed", timeout=3600)
        if "BMS_CUDA_OK" not in cuda.stdout.splitlines():
            raise RemoteTransportError("CUDA container verification failed")
    except (RemoteTransportError, OSError) as exc:
        await session.refresh(target)
        phase = (target.provider_metadata or {}).get("setup", {}).get("phase", "checking")
        safe = BOOTSTRAP_ERRORS | {
            "Remote transport timed out", "Remote runner transfer failed integrity verification",
            "Pinned Nextflow version verification failed", "CUDA container verification failed",
            "Remote SSH host key changed since the last activation", "Remote SSH host key changed",
            "Unable to read the remote SSH host key", "Remote readiness probe returned invalid output",
            "Remote readiness probe is incomplete",
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

    nonterminal = select(Job.id).where(
        Job.execution_target_id == ExecutionTarget.id,
        Job.status.notin_(("completed", "failed", "cancelled", "canceled")),
    ).exists()
    await session.execute(
        update(ExecutionTarget)
        .where(ExecutionTarget.id != identifier, ExecutionTarget.active.is_(True),
               ExecutionTarget.leased_job_id.is_(None), ~nonterminal)
        .values(active=False, state="inactive", updated_at=datetime.utcnow())
    )
    if await session.scalar(select(ExecutionTarget.id).where(
        ExecutionTarget.id != identifier, ExecutionTarget.active.is_(True)).limit(1)):
        await session.rollback()
        raise ExecutionTargetError("The active execution target acquired work during attachment; retry when idle")
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
            provider_metadata={**dict(target.provider_metadata or {}), "setup": {
                **(target.provider_metadata or {}).get("setup", {}), "phase": "ready",
                "message": "Remote worker ready; analytics available", "updated_at": now.isoformat()}},
            host_key_sha256=fingerprint,
            capabilities={**dict(target.capabilities or {}), "readiness": probe,
                          "runner_sha256": runner_sha256, "nextflow_launcher_sha256": nextflow_sha256},
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
    target.active = False
    target.state = "inactive"
    target.updated_at = datetime.utcnow()
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


async def active_remote_telemetry(session: AsyncSession, since: str | None = None) -> dict[str, Any]:
    result = await session.execute(
        select(ExecutionTarget).where(
            ExecutionTarget.active.is_(True),
            ExecutionTarget.state == "ready",
        )
    )
    target = result.scalar_one_or_none()
    from .telemetry import remote_telemetry
    return remote_telemetry.read(target, since)
