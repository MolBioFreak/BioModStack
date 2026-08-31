"""Persistence and activation service for execution-only remote targets."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import ExecutionTarget, Job

from .contracts import (
    DiscoveredExecutionTarget,
    ExecutionTargetActivateRequest,
    ExecutionTargetInventoryResponse,
    ExecutionTargetResponse,
)
from .transport import (
    RemoteConnection,
    RemoteTransportError,
    capture_host_key,
    persist_host_key,
    probe_readiness,
    rsync_to_remote,
    run_remote,
)
from .vast import VastInventoryError, get_owned_instance, list_owned_instances

RUNNING_PROVIDER_STATES = frozenset({"running", "ready"})


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
    return [_target_response(row) for row in rows]


async def get_target(session: AsyncSession, execution_target_id: str) -> ExecutionTarget:
    target = await session.get(ExecutionTarget, execution_target_id)
    if target is None:
        raise ExecutionTargetError("Execution target does not exist")
    return target


async def get_ready_target(session: AsyncSession, execution_target_id: str) -> ExecutionTarget:
    target = await get_target(session, execution_target_id)
    if not target.active or target.state != "ready":
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
    try:
        inventory = await list_owned_instances()
    except VastInventoryError as exc:
        raise ExecutionTargetError(str(exc)) from exc
    if not inventory.available:
        return inventory
    now = datetime.utcnow()
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
        target.name = instance.name
        target.host = instance.host
        target.port = instance.port
        target.username = instance.username or target.username or "root"
        target.capabilities = {
            **dict(target.capabilities or {}),
            **_capabilities(instance),
        }
        target.pricing = _pricing(instance)
        target.provider_metadata = instance.raw
        target.last_seen_at = now
        target.updated_at = now
        if instance.provider_state not in RUNNING_PROVIDER_STATES:
            target.state = "unavailable" if target.active else "discovered"
            target.last_error = f"Provider state is {instance.provider_state}"
    await session.commit()
    return inventory


async def activate_target(
    session: AsyncSession,
    request: ExecutionTargetActivateRequest,
) -> ExecutionTargetResponse:
    try:
        instance = await get_owned_instance(request.provider_instance_id)
    except VastInventoryError as exc:
        raise ExecutionTargetError(str(exc)) from exc
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
    target = await session.get(ExecutionTarget, identifier)
    if target is None:
        target = ExecutionTarget(
            id=identifier,
            provider="vast",
            provider_instance_id=instance.provider_instance_id,
            created_at=now,
        )
        session.add(target)
    target.name = instance.name
    target.state = "probing"
    target.active = False
    target.host = instance.host
    target.port = instance.port
    target.username = request.username or instance.username or "root"
    target.remote_root = request.remote_root
    target.capabilities = _capabilities(instance)
    target.pricing = _pricing(instance)
    target.provider_metadata = instance.raw
    target.last_seen_at = now
    target.updated_at = now
    target.last_error = None
    await session.commit()

    connection = RemoteConnection.from_target(target)
    try:
        host_key_line, fingerprint = await capture_host_key(connection.host, connection.port)
        if target.host_key_sha256 and target.host_key_sha256 != fingerprint:
            raise RemoteTransportError("Remote SSH host key changed since the last activation")
        await persist_host_key(host_key_line, fingerprint)
        probe = await probe_readiness(connection)
        runner = Path(__file__).resolve().parents[2] / "tools" / "bms_remote_worker.py"
        from services.nextflow import resolve_nextflow_executable

        nextflow_launcher = Path(resolve_nextflow_executable())
        await run_remote(connection, ["mkdir", "-p", f"{connection.remote_root}/runner"])
        await rsync_to_remote(
            connection,
            runner,
            f"{connection.remote_root}/runner/bms_remote_worker.py",
            timeout=120,
        )
        await run_remote(
            connection,
            ["chmod", "0755", f"{connection.remote_root}/runner/bms_remote_worker.py"],
        )
        await rsync_to_remote(
            connection,
            nextflow_launcher,
            f"{connection.remote_root}/runner/nextflow",
            timeout=120,
        )
        await run_remote(
            connection,
            ["chmod", "0755", f"{connection.remote_root}/runner/nextflow"],
        )
        runner_sha256 = _sha256_file(runner)
        nextflow_sha256 = _sha256_file(nextflow_launcher)
        remote_hashes = await run_remote(
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
    except (RemoteTransportError, OSError) as exc:
        await session.refresh(target)
        target.state = "unavailable"
        target.active = False
        target.last_error = str(exc)[:2000]
        target.updated_at = datetime.utcnow()
        await session.commit()
        raise ExecutionTargetError(str(exc)) from exc

    await session.execute(
        update(ExecutionTarget)
        .where(ExecutionTarget.id != identifier, ExecutionTarget.active.is_(True))
        .values(active=False, state="inactive", updated_at=datetime.utcnow())
    )
    await session.refresh(target)
    target.host_key_sha256 = fingerprint
    target.capabilities = {
        **dict(target.capabilities or {}),
        "readiness": probe,
        "runner_sha256": runner_sha256,
        "nextflow_launcher_sha256": nextflow_sha256,
    }
    target.state = "ready"
    target.active = True
    target.activated_at = datetime.utcnow()
    target.updated_at = datetime.utcnow()
    target.last_error = None
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
    observed_at = datetime.utcnow().isoformat() + "Z"
    connection = RemoteConnection.from_target(target)
    query = (
        "index,uuid,name,memory.total,memory.used,utilization.gpu,temperature.gpu,"
        "power.draw"
    )
    try:
        response = await run_remote(
            connection,
            [
                "nvidia-smi",
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            timeout=15,
        )
    except RemoteTransportError as exc:
        return {
            "source": "active_vast",
            "available": False,
            "target": _target_response(target).model_dump(mode="json"),
            "observed_at": observed_at,
            "gpus": [],
            "error": str(exc),
        }
    gpus: list[dict[str, Any]] = []
    for raw_line in response.stdout.splitlines():
        parts = [part.strip() for part in raw_line.split(",")]
        if len(parts) != 8:
            continue
        try:
            index = int(parts[0])
            gpus.append(
                {
                    "id": f"{target.id}:gpu:{index}",
                    "execution_target_id": target.id,
                    "index": index,
                    "uuid": parts[1],
                    "name": parts[2],
                    "memory_total_mb": int(float(parts[3])),
                    "memory_used_mb": int(float(parts[4])),
                    "utilization": float(parts[5]),
                    "temperature": _optional_float(parts[6]),
                    "power_draw_w": _optional_float(parts[7]),
                    "controls": {"fan": False, "power": False},
                }
            )
        except ValueError:
            continue
    if not gpus:
        return {
            "source": "active_vast",
            "available": False,
            "target": _target_response(target).model_dump(mode="json"),
            "observed_at": observed_at,
            "gpus": [],
            "error": "Remote GPU telemetry returned no valid GPU rows",
        }
    return {
        "source": "active_vast",
        "available": True,
        "target": _target_response(target).model_dump(mode="json"),
        "observed_at": observed_at,
        "gpus": gpus,
    }


async def active_remote_telemetry(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(ExecutionTarget).where(
            ExecutionTarget.active.is_(True),
            ExecutionTarget.state == "ready",
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        return {"source": "active_vast", "available": False, "target": None, "gpus": []}
    return await remote_target_telemetry(target)
