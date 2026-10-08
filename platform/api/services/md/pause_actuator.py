from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, JobArtifact, MdAttemptSegment, MdCheckpoint, MdReplicaRun, MdRun
from services.nextflow import cancel_nextflow_job

from .state import MdStateError, accept_checkpoint, finalize_pause, request_pause

CancelWorker = Callable[[str], Awaitable[bool]]


async def _cancel_md_worker(nextflow_run_id: str) -> bool:
    return await cancel_nextflow_job(nextflow_run_id, graceful_timeout_seconds=120.0)


def _receipt_exists(roots: list[Path], *, minimum_mtime_ns: int = 0) -> bool:
    return any(
        path.is_file() and path.stat().st_mtime_ns >= minimum_mtime_ns
        for root in roots
        for path in root.rglob("md-checkpoint-receipt.json")
    )


async def _wait_for_receipt(
    roots: list[Path],
    *,
    minimum_mtime_ns: int = 0,
    timeout_seconds: float = 125.0,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if _receipt_exists(roots, minimum_mtime_ns=minimum_mtime_ns):
            return True
        await asyncio.sleep(0.1)
    return _receipt_exists(roots, minimum_mtime_ns=minimum_mtime_ns)


def _checkpoint_roots(child: Job) -> list[Path]:
    candidates: list[str] = []
    if isinstance(child.params, dict):
        resume_output = child.params.get("md_resume_output_dir")
        if isinstance(resume_output, str) and resume_output:
            candidates.append(resume_output)
    if child.stage_work_dir:
        stage_root = Path(str(child.stage_work_dir)).expanduser().resolve(strict=True)
        replica_index = child.params.get("md_replica_index", 0) if isinstance(child.params, dict) else 0
        replica_root = stage_root / f"replica_{int(replica_index)}"
        if replica_root.is_dir() and not replica_root.is_symlink():
            candidates.append(str(replica_root))
        candidates.append(str(stage_root))
    roots: list[Path] = []
    for candidate in candidates:
        root = Path(candidate).expanduser().resolve(strict=True)
        if root not in roots:
            roots.append(root)
    return roots


def _pause_boundary(
    root: Path,
    *,
    idempotency_key: str,
    reuse_existing: bool,
) -> int:
    path = root / ".bms-pause-boundary.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MdStateError("MD_PAUSE_ACTUATION_FAILED", "pause boundary is unreadable") from exc
        if payload.get("idempotency_key") == idempotency_key:
            return path.stat().st_mtime_ns
        if reuse_existing:
            raise MdStateError("MD_PAUSE_ACTUATION_FAILED", "pause boundary belongs to another request")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump({"idempotency_key": idempotency_key}, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.stat().st_mtime_ns


def _checkpoint_receipt(
    roots: list[Path],
    *,
    minimum_mtime_ns: int = 0,
) -> tuple[dict, Path, str]:
    receipts: list[tuple[Path, Path]] = []
    for root in roots:
        receipts.extend(
            (root, path)
            for path in root.rglob("md-checkpoint-receipt.json")
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime_ns >= minimum_mtime_ns
        )
    unique: dict[Path, tuple[Path, Path]] = {}
    for root, path in receipts:
        unique.setdefault(path.resolve(), (root, path))
    if len(unique) != 1:
        raise MdStateError(
            "MD_PAUSE_CHECKPOINT_INVALID",
            "MD_PAUSE_ACTUATION_FAILED: expected exactly one checkpoint receipt",
        )
    root, receipt_path = next(iter(unique.values()))
    if receipt_path.is_symlink():
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint receipt is a symlink")
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: invalid checkpoint receipt") from exc
    if payload.get("schema") != "bms.md.checkpoint-receipt.v1":
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: unsupported checkpoint receipt")
    raw_path = payload.get("checkpoint_path")
    if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint path must be relative")
    checkpoint = (receipt_path.parent / raw_path).resolve(strict=True)
    try:
        checkpoint.relative_to(root)
        relative_path = checkpoint.relative_to(receipt_path.parent).as_posix()
    except ValueError as exc:
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint escapes worker root") from exc
    if checkpoint.is_symlink() or not checkpoint.is_file():
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint is not a regular file")
    data = checkpoint.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if payload.get("sha256") != digest or payload.get("bytes") != len(data):
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint bytes do not match receipt")
    if not isinstance(payload.get("step"), int) or payload["step"] < 0:
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint step is invalid")
    if not isinstance(payload.get("time_ps"), (int, float)) or payload["time_ps"] < 0:
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint time is invalid")
    return payload, checkpoint, relative_path


def _snapshot_checkpoint(
    *,
    checkpoint_path: Path,
    source_relative_path: str,
    segment_id: str,
    sha256: str,
) -> tuple[Path, str, Path]:
    output_root = checkpoint_path
    for _part in Path(source_relative_path).parts:
        output_root = output_root.parent
    logical_path = f".bms-checkpoints/{segment_id}/{sha256}.cpt"
    destination = output_root / logical_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = checkpoint_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "checkpoint changed before durable snapshot")
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != data:
            raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "durable checkpoint snapshot conflicts")
        return destination, logical_path, output_root
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o444)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination, logical_path, output_root


async def _active_segment(session: AsyncSession, replica_id: str) -> MdAttemptSegment:
    segments = list((await session.scalars(
        select(MdAttemptSegment)
        .where(MdAttemptSegment.replica_run_id == replica_id)
        .order_by(MdAttemptSegment.segment_index.desc())
    )).all())
    if not segments:
        raise MdStateError("MD_PAUSE_ACTUATION_FAILED", "active replica has no execution segment")
    return segments[0]


async def _register_checkpoint(
    session: AsyncSession,
    *,
    run: MdRun,
    replica: MdReplicaRun,
    segment: MdAttemptSegment,
    child: Job,
    payload: dict,
    checkpoint_path: Path,
    relative_path: str,
) -> MdCheckpoint:
    if payload.get("execution_plan_sha256") != segment.execution_plan_sha256:
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: execution plan differs")
    if payload.get("compatibility_key") != segment.compatibility_key:
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: compatibility key differs")

    checkpoint_path, relative_path, output_root = _snapshot_checkpoint(
        checkpoint_path=checkpoint_path,
        source_relative_path=relative_path,
        segment_id=segment.id,
        sha256=str(payload["sha256"]),
    )

    existing = await session.scalar(select(MdCheckpoint).where(
        MdCheckpoint.segment_id == segment.id,
        MdCheckpoint.logical_role == "checkpoint",
        MdCheckpoint.relative_path == relative_path,
    ))
    if existing is None:
        checkpoint = await accept_checkpoint(
            session,
            segment_id=segment.id,
            logical_role="checkpoint",
            relative_path=relative_path,
            sha256=str(payload["sha256"]),
            bytes_=int(payload["bytes"]),
            step=int(payload["step"]),
            time_ps=float(payload["time_ps"]),
            compatibility_key=segment.compatibility_key,
        )
    else:
        checkpoint = existing
        if (
            checkpoint.sha256 != payload["sha256"]
            or checkpoint.bytes != payload["bytes"]
            or checkpoint.step != payload["step"]
            or checkpoint.time_ps != float(payload["time_ps"])
            or not checkpoint.accepted
        ):
            raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint replay differs")

    artifact = await session.scalar(select(JobArtifact).where(
        JobArtifact.owner_job_id == child.id,
        JobArtifact.attempt == replica.attempt,
        JobArtifact.logical_path == relative_path,
    ))
    provenance = {
        "schema": "bms.md.artifact-provenance.v1",
        "md_job_id": run.job_id,
        "replica_run_id": replica.id,
        "segment_id": segment.id,
        "checkpoint_id": checkpoint.id,
        "semantic_role": "checkpoint",
        "replica_output_dir": str(output_root),
        "sources": [],
    }
    if artifact is None:
        session.add(JobArtifact(
            id=str(uuid.uuid4()),
            owner_job_id=child.id,
            attempt=replica.attempt,
            logical_path=relative_path,
            storage_path=str(checkpoint_path),
            sha256=checkpoint.sha256,
            bytes=checkpoint.bytes,
            media_type="application/octet-stream",
            provenance=provenance,
        ))
    elif (
        artifact.sha256 != checkpoint.sha256
        or artifact.bytes != checkpoint.bytes
        or artifact.provenance != provenance
    ):
        raise MdStateError("MD_PAUSE_CHECKPOINT_INVALID", "MD_PAUSE_ACTUATION_FAILED: checkpoint artifact replay differs")
    await session.flush()
    return checkpoint


async def pause_running_md_run(
    session: AsyncSession,
    *,
    job_id: str,
    expected_version: int,
    idempotency_key: str,
    cancel_worker: CancelWorker = _cancel_md_worker,
) -> MdRun:
    run = await session.get(MdRun, job_id)
    if run is None:
        raise MdStateError("MD_RUN_NOT_FOUND", "MD run was not found")
    if run.phase == "paused":
        return await request_pause(
            session, job_id=job_id, expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    recovering_checkpointing = run.phase == "checkpointing"
    replicas = list((await session.scalars(select(MdReplicaRun).where(
        MdReplicaRun.md_job_id == job_id,
        MdReplicaRun.active.is_(True),
    ))).all())
    allowed_replica_states = (
        {"running", "completed", "paused"}
        if run.phase == "checkpointing"
        else {"running", "completed"}
    )
    if not replicas or any(replica.state not in allowed_replica_states for replica in replicas):
        raise MdStateError("MD_PAUSE_UNAVAILABLE", "pause requires active replicas to be running, paused, or completed")

    run = await request_pause(
        session, job_id=job_id, expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    await session.flush()

    for replica in replicas:
        if replica.state in {"completed", "paused"}:
            continue
        if replica.engine != "gromacs" or not replica.child_job_id:
            raise MdStateError("MD_PAUSE_ACTUATION_FAILED", "only tracked GROMACS replica workers can be paused")
        child = await session.get(Job, replica.child_job_id)
        if child is None or not child.nextflow_run_id or not child.stage_work_dir:
            raise MdStateError("MD_PAUSE_ACTUATION_FAILED", "running replica worker identity is incomplete")
        segment = await _active_segment(session, replica.id)
        roots = _checkpoint_roots(child)
        receipt_roots = roots[:1]
        boundary_mtime_ns = _pause_boundary(
            receipt_roots[0],
            idempotency_key=idempotency_key,
            reuse_existing=recovering_checkpointing,
        )
        stopped = await cancel_worker(str(child.nextflow_run_id))
        receipt_exists = _receipt_exists(
            receipt_roots,
            minimum_mtime_ns=boundary_mtime_ns,
        )
        if stopped and not receipt_exists:
            receipt_exists = await _wait_for_receipt(
                receipt_roots,
                minimum_mtime_ns=boundary_mtime_ns,
            )
        if not receipt_exists:
            detail = (
                "replica worker stopped without a validated checkpoint receipt"
                if stopped
                else "replica worker did not stop and has no validated checkpoint receipt"
            )
            raise MdStateError(
                "MD_PAUSE_ACTUATION_FAILED",
                f"MD_PAUSE_ACTUATION_FAILED: {detail}",
            )
        payload, checkpoint_path, relative_path = _checkpoint_receipt(
            receipt_roots,
            minimum_mtime_ns=boundary_mtime_ns,
        )
        await _register_checkpoint(
            session,
            run=run,
            replica=replica,
            segment=segment,
            child=child,
            payload=payload,
            checkpoint_path=checkpoint_path,
            relative_path=relative_path,
        )
        segment.state = "paused"
        segment.end_step = int(payload["step"])
        segment.end_time_ps = float(payload["time_ps"])
        replica.state = "paused"
        child.status = "paused"
        child.queue_status = "paused"
        child.paused = True
        child.assigned_gpu = None
        child.error_message = None

    parent = await session.get(Job, job_id)
    if parent is not None:
        parent.status = "paused"
        parent.queue_status = "paused"
        parent.paused = True
        parent.assigned_gpu = None
        parent.error_message = None
    await session.flush()
    return await finalize_pause(
        session,
        job_id=job_id,
        expected_version=run.state_version,
        idempotency_key=f"{idempotency_key}:complete",
    )
