"""Remote attempt staging, durable control, collection, and local finalization."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import ExecutionTarget, Job, async_session
from paths import get_data_root
from schemas import JobStatus
from services.execution_ownership import release_scheduler_gpu_assignment
from services import stage_reporting

from .bundle import (
    PreparedRemoteBundle,
    RemoteBundleError,
    TransferPlan,
    prepare_remote_bundle,
    resolve_job_result_contract,
)
from .contracts import RemoteAttemptStatus, RemoteResultManifest
from .targets import ExecutionTargetError, get_ready_target, target_eligible
from .transport import (
    RemoteConnection,
    RemoteTransportError,
    rsync_selected_from_remote,
    rsync_to_remote,
    run_remote,
)

REMOTE_RUN_PREFIX = "remote:"
TERMINAL_REMOTE_STATES = frozenset({"cancelled", "succeeded", "failed", "lost"})
MAX_RESULT_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RESULT_ARTIFACTS = 100_000
DEFAULT_MAX_RESULT_BYTES = 1024 * 1024 * 1024 * 1024
RESULT_DISK_RESERVE_BYTES = 10 * 1024 * 1024 * 1024


class RemoteExecutionError(RuntimeError):
    pass


class RemoteRunnerIntegrityError(RemoteExecutionError):
    pass


class RemoteCollectionPending(RemoteExecutionError):
    pass


class RemoteStagingIncomplete(RemoteExecutionError):
    pass


def is_remote_run_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(REMOTE_RUN_PREFIX)


def _remote_attempt_id(run_id: str) -> str:
    value = str(run_id or "")
    if not is_remote_run_id(value):
        raise RemoteExecutionError("Run identity is not a remote attempt")
    attempt_id = value[len(REMOTE_RUN_PREFIX) :]
    if not attempt_id or "/" in attempt_id or ".." in attempt_id:
        raise RemoteExecutionError("Remote attempt identity is invalid")
    return attempt_id


def _worker_path(connection: RemoteConnection) -> str:
    return f"{connection.remote_root}/runner/bms_remote_worker.py"


def _worker_argv(
    connection: RemoteConnection,
    command: str,
    attempt_dir: str,
    *extra: str,
) -> list[str]:
    return [
        "python3",
        _worker_path(connection),
        command,
        "--attempt-dir",
        attempt_dir,
        *extra,
    ]


async def _verify_remote_runner(
    connection: RemoteConnection,
    target: ExecutionTarget,
) -> None:
    capabilities = target.capabilities if isinstance(target.capabilities, dict) else {}
    expected = [
        str(capabilities.get("runner_sha256") or ""),
        str(capabilities.get("nextflow_launcher_sha256") or ""),
    ]
    if any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in expected
    ):
        raise RemoteExecutionError("Execution target has no verified runner identity")
    response = await run_remote(
        connection,
        [
            "sha256sum",
            f"{connection.remote_root}/runner/bms_remote_worker.py",
            f"{connection.remote_root}/runner/nextflow",
        ],
        timeout=30,
    )
    observed = [line.split()[0] for line in response.stdout.splitlines() if line.strip()]
    if observed != expected:
        raise RemoteRunnerIntegrityError(
            "Remote runner identity changed after target activation; integrity verification failed; retry Attach"
        )


async def _verify_launch_runner(session, job, connection, target) -> None:
    # Freeze the observed attachment and claim before remote I/O. Inventory
    # refreshes may continue; a newer attachment/lease/claim must never be poisoned.
    generation = [
        ExecutionTarget.id == target.id,
        ExecutionTarget.active.is_(True), ExecutionTarget.state == "ready",
        ExecutionTarget.activated_at == target.activated_at,
        ExecutionTarget.provider_metadata["setup"]["started_at"].as_string() ==
            (target.provider_metadata or {}).get("setup", {}).get("started_at"),
        ExecutionTarget.host == target.host, ExecutionTarget.port == target.port,
        ExecutionTarget.username == target.username, ExecutionTarget.remote_root == target.remote_root,
        ExecutionTarget.capabilities["runner_sha256"].as_string() == (target.capabilities or {}).get("runner_sha256"),
        ExecutionTarget.capabilities["nextflow_launcher_sha256"].as_string() == (target.capabilities or {}).get("nextflow_launcher_sha256"),
        ExecutionTarget.leased_job_id == str(job.id),
        ExecutionTarget.lease_acquired_at == target.lease_acquired_at,
    ]
    claim = select(Job.id).where(
        Job.id == str(job.id), Job.status == job.status, Job.queue_status == job.queue_status,
        Job.execution_target_id == job.execution_target_id, Job.nextflow_run_id == job.nextflow_run_id,
        Job.remote_attempt_id == job.remote_attempt_id, Job.remote_state == job.remote_state,
        Job.provenance == job.provenance, Job.params == job.params,
    ).exists()
    await session.commit()
    try:
        await _verify_remote_runner(connection, target)
    except RemoteRunnerIntegrityError as exc:
        with session.no_autoflush:
            await session.execute(update(ExecutionTarget).where(*generation, claim).values(
                active=False, state="unavailable", last_error=str(exc), updated_at=datetime.utcnow(),
            ).execution_options(synchronize_session=False))
        await session.commit()
        # Lease release and job terminalization remain with the existing attempt CAS.
        raise


def _parse_status(payload: str) -> RemoteAttemptStatus:
    try:
        raw = json.loads(payload.strip().splitlines()[-1])
        return RemoteAttemptStatus.model_validate(raw)
    except (IndexError, json.JSONDecodeError, ValueError) as exc:
        raise RemoteExecutionError("Remote worker returned an invalid attempt status") from exc


async def _mkdir_for_transfer(
    connection: RemoteConnection,
    transfer: TransferPlan,
) -> None:
    destination = PurePosixPath(transfer.remote_destination)
    directory = destination if transfer.source.is_dir() else destination.parent
    await run_remote(connection, ["mkdir", "-p", str(directory)])


async def _transfer_plan(
    connection: RemoteConnection,
    transfer: TransferPlan,
) -> None:
    await _mkdir_for_transfer(connection, transfer)
    await rsync_to_remote(connection, transfer.source, transfer.remote_destination)


async def _noop_staging(*args, **kwargs):
    pass


async def _stage_bundle(
    connection: RemoteConnection,
    bundle: PreparedRemoteBundle,
    progress=_noop_staging,
    check_fence=_noop_staging,
) -> None:
    from .cache import stage_cached_bundle

    await stage_cached_bundle(connection=connection, bundle=bundle,
                              progress=progress, check_fence=check_fence)
    await check_fence()
    await run_remote(
        connection,
        [
            "mkdir",
            "-p",
            f"{bundle.remote_runtime_dir}/containers",
            f"{bundle.remote_runtime_dir}/weights",
            f"{bundle.remote_runtime_dir}/data/runtime/cm-api-python/releases",
        ],
    )
    for transfer in bundle.runtime_transfers:
        if transfer.remote_destination.rstrip('/') == f"{bundle.remote_runtime_dir}/support-python":
            await check_fence()
            await _transfer_plan(connection, transfer)
    await run_remote(
        connection,
        [
            "mkdir",
            "-p",
            f"{bundle.remote_attempt_dir}/bundle",
            f"{bundle.remote_attempt_dir}/results",
            f"{bundle.remote_attempt_dir}/work",
            f"{bundle.remote_attempt_dir}/apptainer-cache",
            f"{bundle.remote_attempt_dir}/msa-cache",
            f"{bundle.remote_attempt_dir}/data",
        ],
    )
    await rsync_to_remote(
        connection,
        bundle.local_attempt_dir,
        bundle.remote_attempt_dir,
        delete=False,
    )
    for transfer in bundle.input_transfers:
        await check_fence()
        await _transfer_plan(connection, transfer)
    await run_remote(
        connection,
        [
            "ln",
            "-sfn",
            bundle.remote_source_dir,
            f"{bundle.remote_attempt_dir}/bundle/source",
        ],
    )
    await run_remote(
        connection,
        [
            "ln",
            "-sfn",
            bundle.remote_runtime_dir,
            f"{bundle.remote_attempt_dir}/bundle/runtime",
        ],
    )


async def _stage_secret_environment(
    connection: RemoteConnection,
    bundle: PreparedRemoteBundle,
    secret_environment: dict[str, str] | None,
) -> None:
    secrets = dict(secret_environment or {})
    if not secrets:
        return
    if set(secrets) != {stage_reporting.ENV_TOKEN_KEY}:
        raise RemoteExecutionError("Remote attempt secret environment contains an unsupported key")
    if any(not isinstance(value, str) or not value for value in secrets.values()):
        raise RemoteExecutionError("Remote attempt secret environment is invalid")
    payload = json.dumps(secrets, sort_keys=True, separators=(",", ":")).encode("utf-8")
    secret_path = f"{bundle.remote_attempt_dir}/secret-env.json"
    writer = (
        "import os,sys; p=sys.argv[1]; data=sys.stdin.buffer.read(); "
        "fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); "
        "os.write(fd,data); os.fsync(fd); os.close(fd)"
    )
    await run_remote(
        connection,
        ["python3", "-c", writer, secret_path],
        input_bytes=payload,
    )


def _archive_envelope(bundle: PreparedRemoteBundle) -> None:
    envelope_root = get_data_root() / "remote-execution" / "envelopes"
    envelope_root.mkdir(parents=True, exist_ok=True)
    source = bundle.local_attempt_dir / "execution-envelope.json"
    destination = envelope_root / f"{bundle.attempt_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _cleanup_local_bundle(bundle: PreparedRemoteBundle) -> None:
    shutil.rmtree(bundle.local_attempt_dir.parent, ignore_errors=True)


def _remote_receipt(
    bundle: PreparedRemoteBundle,
    target: ExecutionTarget,
    *,
    state: str,
    started_at: datetime | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    capabilities = target.capabilities if isinstance(target.capabilities, dict) else {}
    return {
        "schema": "bms.remote-execution-receipt.v1",
        "state": state,
        "attempt_id": bundle.attempt_id,
        "execution_target_id": str(target.id),
        "provider": str(target.provider),
        "provider_instance_id": str(target.provider_instance_id),
        "ssh_host": str(target.host),
        "ssh_port": target.port,
        "ssh_username": str(target.username),
        "remote_root": str(target.remote_root),
        "remote_attempt_dir": bundle.remote_attempt_dir,
        "source_revision": bundle.envelope.source_revision,
        "source_tree": bundle.envelope.source_tree,
        "source_archive_sha256": bundle.envelope.source_archive_sha256,
        "execution_envelope_sha256": bundle.envelope_sha256,
        "runtime_identity_sha256": bundle.runtime_identity_sha256,
        "expected_result_contract_sha256": hashlib.sha256(
            json.dumps(
                bundle.envelope.expected_result_contract,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "runner_sha256": capabilities.get("runner_sha256"),
        "nextflow_launcher_sha256": capabilities.get("nextflow_launcher_sha256"),
        "command_sha256": hashlib.sha256(
            json.dumps(
                bundle.envelope.command,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "started_at": started_at.isoformat() if started_at else None,
        "error": error[:1500] if error else None,
    }


async def _publish_remote_transition(
    session: AsyncSession, job: Job, values: dict[str, Any], *, release_lease: bool = False, require_lease: bool = True,
) -> bool:
    """CAS the complete attempt/claim snapshot; never autoflush a stale owner."""
    lease_authority = [select(ExecutionTarget.id).where(
        ExecutionTarget.id == job.execution_target_id,
        ExecutionTarget.leased_job_id == str(job.id),
    ).exists()]
    if not require_lease:
        lease_authority = []
    with session.no_autoflush:
        result = await session.execute(
            update(Job).where(
                *lease_authority,
                Job.id == str(job.id),
                Job.status == job.status,
                Job.queue_status == job.queue_status,
                Job.execution_target_id == job.execution_target_id,
                Job.nextflow_run_id == job.nextflow_run_id,
                Job.remote_attempt_id == job.remote_attempt_id,
                Job.remote_state == job.remote_state,
                Job.provenance == job.provenance,
                Job.params == job.params,
                Job.execution_source_revision == job.execution_source_revision,
                Job.execution_source_tree == job.execution_source_tree,
                Job.execution_bundle_sha256 == job.execution_bundle_sha256,
                Job.awaiting_payload == job.awaiting_payload,
            ).values(**values).execution_options(synchronize_session=False)
        )
    if result.rowcount != 1:
        await session.rollback()
        return False
    if release_lease:
        await _release_remote_target_lease(session, job)
    # Discard the stale ORM projection before commit can flush it.
    session.expire(job)
    await session.commit()
    await session.refresh(job)
    return True


async def fail_remote_prestart(session: AsyncSession, job: Job, error: str) -> bool:
    if (job.status != "queued" or job.queue_status != "preparing"
            or job.remote_state not in {"preparing", "staging"}):
        return False
    return await _publish_remote_transition(session, job, {
        "status": "failed", "queue_status": "failed", "remote_state": "launch_failed",
        "error_message": error[:2000], "completed_at": datetime.utcnow(),
        "assigned_gpu": None, "params": release_scheduler_gpu_assignment(job.params),
    }, release_lease=True)


async def _publish_started_receipt(
    session: AsyncSession, job: Job, status: RemoteAttemptStatus,
    receipt: dict[str, Any] | None = None,
) -> bool:
    identity = (str(job.id), job.execution_target_id, job.remote_attempt_id, job.nextflow_run_id)
    assignment = dict((job.provenance or {}).get("remote_execution_assignment") or {})
    for _ in range(8):
        await session.rollback()
        current = await session.get(Job, identity[0], populate_existing=True)
        if (current is None or
                (str(current.id), current.execution_target_id, current.remote_attempt_id, current.nextflow_run_id) != identity or
                dict((current.provenance or {}).get("remote_execution_assignment") or {}) != assignment or
                (current.status, current.queue_status) not in {("queued", "preparing"), ("running", "running")}):
            return False
        provenance = dict(current.provenance or {})
        merged = dict(provenance.get("remote_execution_receipt") or {})
        merged.update(receipt or {})
        merged.update(state=status.state, started_at=status.started_at.isoformat())
        provenance["remote_execution_receipt"] = merged
        if await _publish_remote_transition(session, current, {
            "status": "running", "queue_status": "running", "started_at": status.started_at,
            "remote_state": status.state, "provenance": provenance,
        }):
            return True
    return False


@contextmanager
def _controller_attempt_guard(job_id: str):
    """Exclude active producers/reconcilers across local controller processes.

    The kernel releases ownership on process death. Keep the lock file in place:
    unlinking it could give competing controllers different lock inodes.
    Cancellation remains on its separate interrupt lane.
    """
    root = get_data_root() / "remote-execution" / "controller-locks"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(str(job_id).encode()).hexdigest()
    fd = os.open(root / f"{key}.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


async def launch_remote_job(
    session: AsyncSession, job: Job, *, command: list[str],
    environment: dict[str, str] | None = None,
    secret_environment: dict[str, str] | None = None,
) -> str:
    with _controller_attempt_guard(str(job.id)) as owned:
        if not owned:
            raise RemoteExecutionError("Remote attempt already has an active controller")
        return await _launch_remote_job_owned(session, job, command=command,
            environment=environment, secret_environment=secret_environment)


async def _launch_remote_job_owned(
    session: AsyncSession,
    job: Job,
    *,
    command: list[str],
    environment: dict[str, str] | None = None,
    secret_environment: dict[str, str] | None = None,
) -> str:
    if (not job.execution_target_id or job.status != "queued" or job.queue_status != "preparing"
            or job.remote_state != "preparing" or job.remote_attempt_id or job.nextflow_run_id):
        raise RemoteExecutionError("Remote launch requires a fresh durable preparing claim")
    bundle: PreparedRemoteBundle | None = None
    requested_attempt_id = str(uuid.uuid4())
    start_requested = False
    fenced = False
    try:
        target = await get_ready_target(session, str(job.execution_target_id))
        connection = RemoteConnection.from_target(target)
        await _verify_launch_runner(session, job, connection, target)
        bundle = await asyncio.to_thread(
            prepare_remote_bundle, job=job, target=target, command=command,
            environment=environment, attempt_id=requested_attempt_id,
        )
        run_id = f"{REMOTE_RUN_PREFIX}{bundle.attempt_id}"
        provenance = dict(job.provenance or {})
        provenance["remote_execution_receipt"] = _remote_receipt(bundle, target, state="staging")
        if not await _publish_remote_transition(session, job, {
            "nextflow_run_id": run_id, "remote_attempt_id": bundle.attempt_id,
            "remote_state": "staging", "execution_source_revision": bundle.envelope.source_revision,
            "execution_source_tree": bundle.envelope.source_tree,
            "execution_bundle_sha256": bundle.envelope_sha256, "provenance": provenance,
        }):
            fenced = True
            raise RemoteExecutionError("Remote preparing claim was superseded")
        await asyncio.to_thread(_archive_envelope, bundle)
        job_id, target_id, attempt_id = str(job.id), str(job.execution_target_id), bundle.attempt_id
        lease_epoch = target.lease_acquired_at
        assignment = dict((job.provenance or {}).get("remote_execution_assignment") or {})

        async def check_fence():
            nonlocal fenced
            async with async_session() as authority:
                current = await authority.get(Job, job_id)
                owner = await authority.get(ExecutionTarget, target_id)
                valid = (current is not None and owner is not None
                    and (current.status, current.queue_status, current.remote_state) == ("queued", "preparing", "staging")
                    and current.remote_attempt_id == attempt_id
                    and current.nextflow_run_id == run_id and current.execution_target_id == target_id
                    and owner.leased_job_id == job_id and owner.lease_acquired_at == lease_epoch
                    and dict((current.provenance or {}).get("remote_execution_assignment") or {}) == assignment)
            if not valid:
                fenced = True
                raise RemoteExecutionError("Remote staging attempt or lease was superseded")

        async def progress(event):
            from .progress import publish_job_progress
            await check_fence()
            async with async_session() as authority:
                current = await authority.get(Job, job_id)
                if not await publish_job_progress(authority, current, **event):
                    raise RemoteExecutionError("Remote staging progress lost its lease")

        # CAS refresh opens a read transaction; close it before any SSH work.
        await session.commit()
        await check_fence()
        await _stage_bundle(connection, bundle, progress, check_fence)
        await check_fence()
        # File-based remote stage receipts need no workstation callback credential.
        await run_remote(connection, _worker_argv(connection, "prepare", bundle.remote_attempt_dir), timeout=300)
        await check_fence()
        if not await _publish_remote_transition(session, job, {"remote_state": "launch_requested"}):
            fenced = True
            raise RemoteExecutionError("Remote start claim was superseded")
        await session.commit()
        start_requested = True
        try:
            response = await run_remote(connection, _worker_argv(connection, "run", bundle.remote_attempt_dir), timeout=60)
            status = _parse_status(response.stdout)
            if (status.job_id != str(job.id) or status.attempt_id != bundle.attempt_id
                    or status.state not in {"running", *TERMINAL_REMOTE_STATES}
                    or status.started_at is None):
                raise RemoteExecutionError("Remote worker did not publish a valid started receipt")
        except Exception as exc:
            # Start may have arrived. Never release its lease or declare failure.
            await _publish_remote_transition(session, job, {
                "remote_state": "launch_uncertain", "error_message": str(exc)[:1500],
            })
            return run_id
        provenance = dict(job.provenance or {})
        provenance["remote_execution_receipt"] = _remote_receipt(
            bundle, target, state=status.state, started_at=status.started_at,
        )
        published = await _publish_started_receipt(
            session, job, status, provenance["remote_execution_receipt"],
        )
        if not published:
            # Only this immutable old attempt is stopped; no successor DB writes.
            await run_remote(connection, _worker_argv(connection, "cancel", bundle.remote_attempt_dir), timeout=60)
        return run_id
    except Exception as exc:
        if not fenced and not start_requested:
            await fail_remote_prestart(session, job, str(exc))
        if isinstance(exc, RemoteExecutionError):
            raise
        raise RemoteExecutionError(str(exc)) from exc
    finally:
        if bundle is not None:
            await asyncio.to_thread(_cleanup_local_bundle, bundle)
        else:
            await asyncio.to_thread(shutil.rmtree,
                get_data_root() / "remote-execution" / "staging" / requested_attempt_id, True)


def _connection_for_attempt(target: ExecutionTarget, job: Job) -> tuple[RemoteConnection, str]:
    if not target_eligible(target):
        raise RemoteExecutionError("Remote inventory is absent, unknown or expired; attempt evidence is retained")
    connection = RemoteConnection.from_target(target)
    receipt = (
        dict(job.provenance.get("remote_execution_receipt") or {})
        if isinstance(job.provenance, dict)
        else {}
    )
    receipt_attempt = str(receipt.get("attempt_id") or "")
    receipt_root = str(receipt.get("remote_root") or "").rstrip("/")
    receipt_attempt_dir = str(receipt.get("remote_attempt_dir") or "")
    if receipt_attempt == str(job.remote_attempt_id) and receipt_root and receipt_attempt_dir:
        expected_attempt_dir = f"{receipt_root}/attempts/{job.remote_attempt_id}"
        if receipt_attempt_dir != expected_attempt_dir:
            raise RemoteExecutionError("Persisted remote attempt path is invalid")
        connection = RemoteConnection(
            target_id=connection.target_id,
            host=connection.host,
            port=connection.port,
            username=connection.username,
            remote_root=receipt_root,
        )
        return connection, receipt_attempt_dir
    return connection, f"{connection.remote_root}/attempts/{job.remote_attempt_id}"


async def _acquire_remote_terminal_fence(session: AsyncSession, job: Job) -> bool:
    """Acquire the caller transaction's terminal publication authority."""

    result = await session.execute(
        update(Job)
        .where(
            Job.id == str(job.id),
            Job.status == JobStatus.RUNNING.value,
            Job.queue_status == "running",
            Job.execution_target_id == job.execution_target_id,
            Job.remote_state == job.remote_state,
            select(ExecutionTarget.id).where(
                ExecutionTarget.id == job.execution_target_id,
                ExecutionTarget.leased_job_id == str(job.id),
            ).exists(),
            Job.nextflow_run_id == job.nextflow_run_id,
            Job.remote_attempt_id == job.remote_attempt_id,
        )
        .values(remote_state="validating_return")
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return False
    job.remote_state = "validating_return"
    return True


async def _release_remote_target_lease(session: AsyncSession, job: Job) -> None:
    target_id = str(job.execution_target_id or "").strip()
    if not target_id:
        return
    await session.execute(
        update(ExecutionTarget)
        .where(
            ExecutionTarget.id == target_id,
            ExecutionTarget.leased_job_id == str(job.id),
        )
        .values(
            leased_job_id=None,
            lease_acquired_at=None,
            updated_at=datetime.utcnow(),
        )
        .execution_options(synchronize_session=False)
    )


def _preparation_expired(job: Job) -> bool:
    assignment = dict((job.provenance or {}).get("remote_execution_assignment") or {})
    raw = assignment.get("claimed_at")
    try:
        origin = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None) if raw else None
    except ValueError:
        origin = None
    # Legacy rows use their persisted age, never a new clock on every poll.
    origin = origin or job.started_at or job.created_at
    grace = max(0, int(os.environ.get("BMS_REMOTE_STAGING_RECOVERY_GRACE_SECONDS", "900")))
    return origin is None or (datetime.utcnow() - origin).total_seconds() >= grace


async def remote_status(session: AsyncSession, job: Job) -> RemoteAttemptStatus:
    if not job.execution_target_id or not job.remote_attempt_id:
        raise RemoteExecutionError("Job has no complete remote attempt identity")
    target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
    if target is None:
        raise RemoteExecutionError("Remote execution target record is missing")
    connection, attempt_dir = _connection_for_attempt(target, job)
    await session.commit()
    try:
        response = await run_remote(
            connection,
            _worker_argv(connection, "status", attempt_dir),
            timeout=30,
        )
    except RemoteTransportError as exc:
        if str(job.remote_state or "") != "staging":
            raise RemoteExecutionError(str(exc)) from exc
        try:
            response = await run_remote(
                connection,
                _worker_argv(connection, "prepare", attempt_dir),
                timeout=300,
            )
        except RemoteTransportError as prepare_exc:
            if _preparation_expired(job):
                raise RemoteStagingIncomplete(
                    "Remote attempt did not reach a durable prepared receipt"
                ) from prepare_exc
            raise RemoteExecutionError(
                "Remote attempt staging is not ready for restart recovery"
            ) from prepare_exc
    status = _parse_status(response.stdout)
    if status.job_id != str(job.id) or status.attempt_id != str(job.remote_attempt_id):
        raise RemoteExecutionError("Remote attempt status does not match the BMS Job")
    return status


def _safe_result_path(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RemoteExecutionError("Returned artifact path escapes the result package")
    path = root.joinpath(*relative.parts)
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise RemoteExecutionError("Returned artifact path escapes the result package")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_result_package(
    incoming: Path,
    job: Job,
    status: RemoteAttemptStatus,
) -> RemoteResultManifest:
    manifest_path = incoming / "result-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RemoteExecutionError("Remote result manifest is missing")
    manifest_sha256 = _sha256_file(manifest_path)
    if manifest_sha256 != status.result_manifest_sha256:
        raise RemoteExecutionError("Remote result manifest hash does not match terminal status")
    try:
        manifest = RemoteResultManifest.model_validate_json(manifest_path.read_bytes())
    except ValueError as exc:
        raise RemoteExecutionError("Remote result manifest is invalid") from exc
    if (
        manifest.job_id != str(job.id)
        or manifest.attempt_id != str(job.remote_attempt_id)
        or manifest.source_revision != str(job.execution_source_revision)
        or manifest.source_tree != str(job.execution_source_tree)
        or manifest.execution_envelope_sha256 != str(job.execution_bundle_sha256)
    ):
        raise RemoteExecutionError("Remote result manifest identity does not match the BMS Job")
    declared: set[str] = set()
    for artifact in manifest.artifacts:
        path = _safe_result_path(incoming, artifact.relative_path)
        if path.is_symlink() or not path.is_file():
            raise RemoteExecutionError(f"Returned artifact is missing: {artifact.relative_path}")
        if path.stat().st_size != artifact.size_bytes or _sha256_file(path) != artifact.sha256:
            raise RemoteExecutionError(f"Returned artifact hash mismatch: {artifact.relative_path}")
        declared.add(artifact.relative_path)
    actual: set[str] = set()
    for path in incoming.rglob("*"):
        if path.is_symlink():
            raise RemoteExecutionError("Remote result package contains a symlink")
        if path.is_file() and path != manifest_path:
            actual.add(path.relative_to(incoming).as_posix())
    if actual != declared:
        raise RemoteExecutionError("Remote result package contains undeclared or missing files")
    return manifest


async def _fetch_result_manifest(
    connection: RemoteConnection,
    remote_results_dir: str,
    incoming: Path,
    job: Job,
    status: RemoteAttemptStatus,
) -> RemoteResultManifest:
    manifest_path = f"{remote_results_dir.rstrip('/')}/result-manifest.json"
    reader = (
        "import pathlib,sys; "
        "p=pathlib.Path(sys.argv[1]); n=int(sys.argv[2]); "
        "b=p.read_bytes(); "
        "(_ for _ in ()).throw(RuntimeError('manifest too large')) if len(b)>n else None; "
        "sys.stdout.buffer.write(b)"
    )
    response = await run_remote(
        connection,
        ["python3", "-c", reader, manifest_path, str(MAX_RESULT_MANIFEST_BYTES)],
        timeout=30,
    )
    manifest_bytes = response.stdout.encode("utf-8")
    if not manifest_bytes or len(manifest_bytes) > MAX_RESULT_MANIFEST_BYTES:
        raise RemoteExecutionError("Remote result manifest exceeds the bounded size")
    if hashlib.sha256(manifest_bytes).hexdigest() != status.result_manifest_sha256:
        raise RemoteExecutionError("Remote result manifest hash does not match terminal status")
    try:
        manifest = RemoteResultManifest.model_validate_json(manifest_bytes)
    except ValueError as exc:
        raise RemoteExecutionError("Remote result manifest is invalid") from exc
    if (
        manifest.job_id != str(job.id)
        or manifest.attempt_id != str(job.remote_attempt_id)
        or manifest.source_revision != str(job.execution_source_revision)
        or manifest.source_tree != str(job.execution_source_tree)
        or manifest.execution_envelope_sha256 != str(job.execution_bundle_sha256)
    ):
        raise RemoteExecutionError("Remote result manifest identity does not match the BMS Job")
    if len(manifest.artifacts) > MAX_RESULT_ARTIFACTS:
        raise RemoteExecutionError("Remote result manifest exceeds the artifact-count limit")
    total_bytes = sum(int(artifact.size_bytes) for artifact in manifest.artifacts)
    configured_limit = int(os.environ.get("BMS_REMOTE_MAX_RETURN_BYTES", DEFAULT_MAX_RESULT_BYTES))
    free_budget = max(0, shutil.disk_usage(incoming.parent).free - RESULT_DISK_RESERVE_BYTES)
    if total_bytes > min(configured_limit, free_budget):
        raise RemoteExecutionError("Remote result package exceeds the local return-byte budget")
    incoming.mkdir(parents=True, exist_ok=False)
    (incoming / "result-manifest.json").write_bytes(manifest_bytes)
    return manifest


async def collect_remote_results(
    session: AsyncSession,
    job: Job,
    status: RemoteAttemptStatus,
) -> tuple[RemoteResultManifest, Path]:
    target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
    if target is None:
        raise RemoteCollectionPending("Remote execution target record is missing")
    connection, attempt_dir = _connection_for_attempt(target, job)
    local_output = Path(str(job.child_output_dir or job.output_dir)).expanduser()
    if any(part.is_symlink() for part in (local_output, *local_output.parents)):
        raise RemoteExecutionError("Remote result destination traverses a symlink")
    local_output = local_output.resolve()
    incoming = local_output.parent / f".{local_output.name}.remote-incoming" / str(job.remote_attempt_id)
    incoming = incoming.with_name(incoming.name + "-" + uuid.uuid4().hex)
    incoming.parent.mkdir(parents=True, exist_ok=True)
    remote_results_dir = f"{attempt_dir}/results"
    await session.commit()
    try:
        manifest = await _fetch_result_manifest(
            connection,
            remote_results_dir,
            incoming,
            job,
            status,
        )
        if manifest.artifacts:
            await rsync_selected_from_remote(
                connection,
                remote_results_dir,
                incoming,
                [artifact.relative_path for artifact in manifest.artifacts],
                max_file_bytes=max(int(artifact.size_bytes) for artifact in manifest.artifacts),
            )
        manifest = await asyncio.to_thread(_verify_result_package, incoming, job, status)
    except (RemoteTransportError, RemoteExecutionError, OSError, ValueError) as exc:
        raise RemoteCollectionPending(str(exc)) from exc
    return manifest, incoming


def _publish_result_generation(job: Job, incoming: Path) -> tuple[Path, Path | None]:
    """Atomically make one verified attempt the only visible result generation."""
    manifest_path = incoming / "result-manifest.json"
    local_output = Path(str(job.child_output_dir or job.output_dir)).expanduser().resolve()
    local_output.parent.mkdir(parents=True, exist_ok=True)
    if incoming.stat().st_dev != local_output.parent.stat().st_dev:
        raise RemoteExecutionError("Remote result staging and Job output are on different filesystems")
    backup: Path | None = None
    if local_output.exists():
        backup = (
            local_output.parent
            / ".bms-remote-quarantine"
            / str(job.id)
            / str(job.remote_attempt_id)
            / ("previous-" + uuid.uuid4().hex)
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        os.replace(local_output, backup)
    try:
        os.replace(incoming, local_output)
    except Exception:
        if backup is not None and backup.exists() and not local_output.exists():
            os.replace(backup, local_output)
        raise
    return local_output, backup


async def _finish_remote_cancellation(session: AsyncSession, job: Job, status=None) -> bool:
    provenance = dict(job.provenance or {})
    if status is not None:
        receipt = dict(provenance.get("remote_execution_receipt") or {})
        receipt.update(state=status.state, exit_code=status.exit_code,
            result_manifest_sha256=status.result_manifest_sha256, error=status.error,
            completed_at=status.completed_at.isoformat() if status.completed_at else None)
        provenance["remote_execution_receipt"] = receipt
    params = release_scheduler_gpu_assignment(job.params)
    cancellation_receipt = dict(params.get("cancellation_receipt") or {})
    cancellation_receipt.update(
        {
            "schema": "bms.workflow-cancellation.v1",
            "state": "completed",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "run_identity": str(job.nextflow_run_id or ""),
        }
    )
    params["cancellation_receipt"] = cancellation_receipt
    return await _publish_remote_transition(session, job, {
        "status": "cancelled", "queue_status": "cancelled", "remote_state": "cancelled",
        "params": params, "provenance": provenance, "paused": False, "assigned_gpu": None,
        "completed_at": job.completed_at or datetime.utcnow(),
        "error_message": job.error_message or "Cancelled by user",
        "awaiting_input": False, "awaiting_stage": None, "awaiting_payload": {},
        "current_stage": None, "stage_progress": None, "retry_count": 0,
    }, release_lease=True)


async def reconcile_remote_job(session: AsyncSession, job: Job) -> bool:
    with _controller_attempt_guard(str(job.id)) as owned:
        if not owned:
            return False
        return await _reconcile_remote_job_owned(session, job)


async def _reconcile_remote_job_owned(session: AsyncSession, job: Job) -> bool:
    """Reconcile one running remote Job. Return true when local state changed."""
    job_id = str(job.id)
    if job.status in {"completed", "failed", "cancelled"}:
        return False
    if job.remote_state == "returning":
        if (job.status, job.queue_status) != ("running", "running"):
            return False
        return await _pull_failure(session, job, "Result pull interrupted; choose Retry pull")
    if job.awaiting_stage == "remote_results":
        return False
    expected_run_id = str(job.nextflow_run_id or "")
    expected_attempt_id = str(job.remote_attempt_id or "")
    expected_target_id = str(job.execution_target_id or "")
    if not expected_run_id or not expected_attempt_id:
        if job.queue_status == "cancelling" and not expected_run_id and not expected_attempt_id:
            return await _finish_remote_cancellation(session, job)
        if job.queue_status == "preparing" and _preparation_expired(job):
            return await fail_remote_prestart(session, job, "Remote preparation expired before durable attempt identity")
        return False
    try:
        status = await remote_status(session, job)
    except RemoteStagingIncomplete as exc:
        return await fail_remote_prestart(session, job, str(exc))
    # Remote I/O can outlive a concurrent operator action. End the current read
    # transaction and reload local authority before any resume or publication.
    await session.rollback()
    current_job = await session.get(Job, job_id)
    if current_job is None:
        return False
    job = current_job
    if (
        str(job.nextflow_run_id or "") != expected_run_id
        or str(job.remote_attempt_id or "") != expected_attempt_id
    ):
        return False
    if job.status in {"cancelled", "completed", "failed"}:
        return False
    if job.queue_status == "cancelling":
        if status.state in TERMINAL_REMOTE_STATES:
            return await _finish_remote_cancellation(session, job, status)
        if status.state not in TERMINAL_REMOTE_STATES:
            return await _publish_remote_transition(session, job, {"remote_state": status.state})
    if (job.status, job.queue_status) not in {("running", "running"), ("queued", "preparing")}:
        return False
    if status.state == "prepared":
        if job.remote_state in {"launch_requested", "launch_uncertain"}:
            # A prior start effect is ambiguous. Observation/cancellation only;
            # a prepared snapshot is not proof the command never arrived.
            return False
        target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
        if target is None:
            raise RemoteExecutionError("Remote execution target record is missing")
        connection, attempt_dir = _connection_for_attempt(target, job)
        await _verify_launch_runner(session, job, connection, target)
        if not await _publish_remote_transition(session, job, {"remote_state": "launch_requested"}):
            return False
        response = await run_remote(
            connection,
            _worker_argv(connection, "run", attempt_dir),
            timeout=60,
        )
        status = _parse_status(response.stdout)
        if status.job_id != str(job.id) or status.attempt_id != str(job.remote_attempt_id):
            raise RemoteExecutionError("Resumed remote attempt does not match the BMS Job")
        if status.state not in {"running", *TERMINAL_REMOTE_STATES} or status.started_at is None:
            raise RemoteExecutionError("Resumed remote attempt has no valid started receipt")
        return await _publish_started_receipt(session, job, status)
    if job.queue_status == "preparing":
        if status.started_at is None or status.state not in {"running", *TERMINAL_REMOTE_STATES}:
            return False
        return await _publish_started_receipt(session, job, status)
    if status.state not in TERMINAL_REMOTE_STATES:
        if status.activity is not None:
            from .progress import publish_job_progress

            await publish_job_progress(session, job, phase="running", artifact=None,
                message="Running " + status.activity.stage, activity=status.activity)
        if job.remote_state == status.state:
            return False
        return await _publish_remote_transition(session, job, {"remote_state": status.state})
    if not status.result_manifest_sha256:
        if not await _acquire_remote_terminal_fence(session, job):
            return False
        job.status = JobStatus.FAILED.value
        job.queue_status = "failed"
        job.remote_state = "failed_integrity"
        job.error_message = (
            status.error
            or "Remote attempt reached terminal state without a terminal artifact manifest"
        )
        job.assigned_gpu = None
        job.params = release_scheduler_gpu_assignment(job.params)
        job.completed_at = job.completed_at or datetime.utcnow()
        provenance = dict(job.provenance or {})
        receipt = dict(provenance.get("remote_execution_receipt") or {})
        receipt.update(
            {
                "state": status.state,
                "completed_at": status.completed_at.isoformat() if status.completed_at else None,
                "exit_code": status.exit_code,
                "result_manifest_sha256": None,
                "integrity_failure": "missing_terminal_artifact_manifest",
            }
        )
        provenance["remote_execution_receipt"] = receipt
        job.provenance = provenance
        if str(job.model_id or "").lower() == "conformational_mapping":
            from services.conformational_mapping.persistence import terminalize_failed_request_for_job

            await terminalize_failed_request_for_job(session, job_id=str(job.id))
        if str(job.model_id or "").lower() == "protein_local_redesign":
            from services.rfd3_local_redesign import terminalize_failed_request_for_job

            await terminalize_failed_request_for_job(
                session,
                job_id=str(job.id),
                exit_code=status.exit_code or 1,
            )
        await _release_remote_target_lease(session, job)
        await session.commit()
        return True
    provenance = dict(job.provenance or {})
    receipt = dict(provenance.get("remote_execution_receipt") or {})
    receipt.update(state=status.state, exit_code=status.exit_code,
        completed_at=status.completed_at.isoformat() if status.completed_at else None,
        result_manifest_sha256=status.result_manifest_sha256, error=status.error)
    provenance["remote_execution_receipt"] = receipt
    if status.state != "succeeded" or status.exit_code != 0:
        return await _publish_remote_transition(session, job, {
            "status": "failed", "queue_status": "failed", "remote_state": "failed",
            "provenance": provenance,
            "error_message": status.error or f"Remote workflow exited with code {status.exit_code}",
            "assigned_gpu": None, "params": release_scheduler_gpu_assignment(job.params),
            "completed_at": datetime.utcnow(),
        }, release_lease=True)
    return await _publish_remote_transition(session, job, {
        "status": "awaiting_input", "queue_status": "completed",
        "awaiting_input": True, "awaiting_stage": "remote_results",
        "awaiting_payload": _pull_identity(job), "remote_state": "results_available",
        "provenance": provenance, "assigned_gpu": None,
        "params": release_scheduler_gpu_assignment(job.params), "error_message": None,
    }, release_lease=True)


def _pull_identity(job):
    return {
        "schema": "bms.remote-result-pull.v1",
        "attempt_id": str(job.remote_attempt_id),
        "execution_target_id": str(job.execution_target_id),
        "source_revision": str(job.execution_source_revision),
        "source_tree": str(job.execution_source_tree),
        "execution_envelope_sha256": str(job.execution_bundle_sha256),
    }


async def _pull_failure(session, job, message):
    return await _publish_remote_transition(session, job, {
        "status": "awaiting_input", "queue_status": "completed",
        "awaiting_input": True, "awaiting_stage": "remote_results",
        "awaiting_payload": _pull_identity(job), "remote_state": "result_pull_failed",
        "error_message": str(message)[:1500],
    }, require_lease=False)


async def request_remote_result_pull(session, job, background_tasks):
    """Reserve transfer before responding; keep the process lock through completion."""
    if (not job.execution_target_id or not job.remote_attempt_id
            or not all(isinstance(value, str) and value.strip() and value != "None"
                       for value in (job.execution_source_revision, job.execution_source_tree,
                                     job.execution_bundle_sha256))
            or job.nextflow_run_id != f"remote:{job.remote_attempt_id}"):
        raise RemoteExecutionError("Job has no current remote result attempt with complete source identity")
    guard = _controller_attempt_guard(str(job.id))
    owned = guard.__enter__()
    if not owned:
        guard.__exit__(None, None, None)
        if job.remote_state == "returning":
            return
        raise RemoteExecutionError("Remote attempt controller is busy")
    scheduled = False
    try:
        if (job.status not in {"awaiting_input", "running"}
                or job.remote_state not in {"results_available", "result_pull_failed", "returning"}
                or job.awaiting_stage != "remote_results"
                or not job.awaiting_input or job.awaiting_payload != _pull_identity(job)):
            raise RemoteExecutionError("Job is not awaiting an explicit result pull")
        if not await _publish_remote_transition(session, job, {
            "status": "running", "queue_status": "running", "remote_state": "returning",
            "error_message": None,
        }, require_lease=False):
            raise RemoteExecutionError("Remote result attempt changed; refresh the Job")
        background_tasks.add_task(_run_requested_pull, str(job.id), _pull_identity(job), guard)
        scheduled = True
    finally:
        if not scheduled:
            guard.__exit__(None, None, None)


async def _prove_pull_endpoint(session, job, *, diagnostics=False):
    from .vast import get_owned_instance
    from .targets import RUNNING_PROVIDER_STATES

    target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
    if target is None or target.provider != "vast":
        raise RemoteExecutionError("Result source target is unavailable")
    identity = _pull_identity(job)
    receipt_snapshot = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    lifecycle = (job.status, job.queue_status, job.remote_state)
    endpoint = (target.host, target.port, target.username, target.provider_instance_id)
    await session.commit()
    instance = await get_owned_instance(str(target.provider_instance_id))
    await session.refresh(job)
    await session.refresh(target)
    valid_lifecycle = (job.status in {"failed", "cancelled"} if diagnostics else
                       (job.status, job.queue_status, job.remote_state) == ("running", "running", "returning"))
    if (_pull_identity(job) != identity or not valid_lifecycle
            or (job.status, job.queue_status, job.remote_state) != lifecycle
            or dict((job.provenance or {}).get("remote_execution_receipt") or {}) != receipt_snapshot
            or (target.host, target.port, target.username, target.provider_instance_id) != endpoint):
        raise RemoteExecutionError("Result source changed during provider verification")
    if (instance.provider_state not in RUNNING_PROVIDER_STATES
            or (instance.host, instance.port) != (target.host, target.port)):
        raise RemoteExecutionError("Current provider endpoint does not match the result source")
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    if (receipt.get("attempt_id") != str(job.remote_attempt_id)
            or receipt.get("execution_target_id") != str(target.id)
            or (receipt.get("provider"), receipt.get("provider_instance_id"),
                receipt.get("ssh_host"), receipt.get("ssh_port"), receipt.get("ssh_username")) != (
                str(target.provider), str(target.provider_instance_id),
                str(target.host), target.port, str(target.username))
            or receipt.get("source_revision") != job.execution_source_revision
            or receipt.get("source_tree") != job.execution_source_tree
            or receipt.get("execution_envelope_sha256") != job.execution_bundle_sha256):
        raise RemoteExecutionError("Persisted result source identity is inconsistent")
    connection, _ = _connection_for_attempt(target, job)
    await session.commit()
    await _verify_remote_runner(connection, target)


async def _run_requested_pull(job_id, identity, guard):
    try:
        async with async_session() as session:
            try:
                job = await session.get(Job, job_id)
                if job is None or _pull_identity(job) != identity or job.remote_state != "returning":
                    return
                await _prove_pull_endpoint(session, job)
                status = await remote_status(session, job)
                receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
                if (status.state != "succeeded" or status.exit_code != 0
                        or not status.result_manifest_sha256
                        or status.result_manifest_sha256 != receipt.get("result_manifest_sha256")):
                    raise RemoteExecutionError("Remote terminal result identity changed")
                manifest, incoming = await collect_remote_results(session, job, status)
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if (job is None or _pull_identity(job) != identity or job.remote_state != "returning"
                        or job.status != "running" or job.queue_status != "running"):
                    return
                fence = await session.execute(update(Job).where(
                    Job.id == job_id, Job.status == "running", Job.queue_status == "running",
                    Job.remote_state == "returning", Job.remote_attempt_id == identity["attempt_id"],
                    Job.execution_target_id == identity["execution_target_id"],
                    Job.execution_source_revision == identity["source_revision"],
                    Job.execution_source_tree == identity["source_tree"],
                    Job.execution_bundle_sha256 == identity["execution_envelope_sha256"],
                    Job.provenance == job.provenance, Job.params == job.params,
                ).values(awaiting_input=False, awaiting_stage=None, awaiting_payload={}))
                if fence.rowcount != 1:
                    await session.rollback()
                    return
                # Hold the write fence across publication and receipt application.
                await session.refresh(job)
                await _finalize_pulled_results(session, job, status, manifest, incoming)
            except Exception as exc:
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if (job is not None and _pull_identity(job) == identity
                        and job.remote_state == "returning"
                        and (job.status, job.queue_status) == ("running", "running")):
                    await _pull_failure(session, job, f"Result pull failed: {exc}")
    finally:
        guard.__exit__(None, None, None)


async def request_remote_diagnostic_pull(session, job, background_tasks):
    """Explicit failed-attempt archive pull; never reclassifies scientific history."""
    identity = _pull_identity(job)
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    digest = receipt.get("result_manifest_sha256")
    if (job.status not in {"failed", "cancelled"} or not job.execution_target_id
            or not job.remote_attempt_id or job.nextflow_run_id != f"remote:{job.remote_attempt_id}"
            or any(not value or value == "None" for key, value in identity.items())
            or not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)):
        raise RemoteExecutionError("No integrity-bound terminal remote diagnostics are available")
    prior = dict((job.provenance or {}).get("remote_diagnostics") or {})
    if prior.get("state") == "returned" and prior.get("identity") == identity and prior.get("result_manifest_sha256") == digest:
        return
    guard = _controller_attempt_guard(str(job.id))
    if not guard.__enter__():
        guard.__exit__(None, None, None)
        if prior.get("state") == "returning" and prior.get("identity") == identity:
            return
        raise RemoteExecutionError("Remote attempt controller is busy")
    scheduled = False
    try:
        record = dict(state="returning", identity=identity, result_manifest_sha256=digest,
                      error=None, output_dir=None)
        provenance = dict(job.provenance or {}, remote_diagnostics=record)
        if not await _publish_remote_transition(session, job, {"provenance": provenance}, require_lease=False):
            raise RemoteExecutionError("Remote diagnostic attempt changed; refresh the Job")
        background_tasks.add_task(_run_requested_diagnostics, str(job.id), identity, digest, guard, receipt)
        scheduled = True
    finally:
        if not scheduled:
            guard.__exit__(None, None, None)


def _diagnostic_authority(job, identity, digest, receipt_snapshot=None):
    if job is None or job.status not in {"failed", "cancelled"} or _pull_identity(job) != identity:
        return False
    record = (job.provenance or {}).get("remote_diagnostics") or {}
    receipt = (job.provenance or {}).get("remote_execution_receipt") or {}
    return (record.get("state") == "returning" and record.get("identity") == identity
            and record.get("result_manifest_sha256") == digest
            and receipt.get("result_manifest_sha256") == digest
            and job.nextflow_run_id == f"remote:{identity['attempt_id']}"
            and (receipt_snapshot is None or receipt == receipt_snapshot))


async def _run_requested_diagnostics(job_id, identity, digest, guard, receipt_snapshot=None):
    incoming = None
    try:
        async with async_session() as session:
            try:
                job = await session.get(Job, job_id)
                if not _diagnostic_authority(job, identity, digest, receipt_snapshot):
                    return
                await _prove_pull_endpoint(session, job, diagnostics=True)
                status = await remote_status(session, job)
                receipt = (job.provenance or {}).get("remote_execution_receipt") or {}
                if (status.state not in TERMINAL_REMOTE_STATES
                        or status.result_manifest_sha256 != digest
                        or status.state != receipt.get("state") or status.exit_code != receipt.get("exit_code")):
                    raise RemoteExecutionError("Remote diagnostic terminal identity changed")
                manifest, incoming = await collect_remote_results(session, job, status)
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if not _diagnostic_authority(job, identity, digest, receipt_snapshot):
                    return
                # Acquire a write fence before filesystem publication; never touch
                # scientific outputs, lifecycle timestamps or any successor lease.
                fence = await session.execute(update(Job).where(
                    Job.id == job_id, Job.status == job.status,
                    Job.queue_status == job.queue_status, Job.remote_state == job.remote_state,
                    Job.nextflow_run_id == f"remote:{identity['attempt_id']}",
                    Job.remote_attempt_id == identity["attempt_id"],
                    Job.execution_target_id == identity["execution_target_id"],
                    Job.execution_source_revision == identity["source_revision"],
                    Job.execution_source_tree == identity["source_tree"],
                    Job.execution_bundle_sha256 == identity["execution_envelope_sha256"],
                    Job.provenance == job.provenance,
                ).values(provenance=job.provenance))
                if fence.rowcount != 1:
                    await session.rollback()
                    return
                root = get_data_root() / "remote-execution" / "diagnostics"
                relative = "/".join((job_id, identity["attempt_id"], digest))
                destination = _safe_result_path(root, relative)
                if any(p.is_symlink() for p in (destination, *destination.parents)):
                    raise RemoteExecutionError("Diagnostic destination traverses a symlink")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    await asyncio.to_thread(_verify_result_package, destination, job, status)
                else:
                    os.replace(incoming, destination)
                provenance = dict(job.provenance or {})
                provenance["remote_diagnostics"] = dict(state="returned", identity=identity,
                    result_manifest_sha256=digest, output_dir=str(destination), error=None)
                job.provenance = provenance
                await session.commit()
            except Exception as exc:
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if _diagnostic_authority(job, identity, digest, receipt_snapshot):
                    provenance = dict(job.provenance or {})
                    provenance["remote_diagnostics"] = dict(state="failed", identity=identity,
                        result_manifest_sha256=digest, output_dir=None, error=str(exc)[:1500])
                    await _publish_remote_transition(session, job, {"provenance": provenance}, require_lease=False)
    finally:
        try:
            if incoming is not None:
                await asyncio.to_thread(shutil.rmtree, incoming, True)
        finally:
            guard.__exit__(None, None, None)


async def _finalize_pulled_results(session, job, status, manifest, incoming):
    job_id = str(job.id)
    expected_attempt_id = str(job.remote_attempt_id)
    expected_run_id = str(job.nextflow_run_id)
    expected_target_id = str(job.execution_target_id)
    provenance = dict(job.provenance or {})
    receipt = dict(provenance.get("remote_execution_receipt") or {})
    receipt.update(
        {
            "state": status.state,
            "completed_at": status.completed_at.isoformat() if status.completed_at else None,
            "exit_code": status.exit_code,
            "result_manifest_sha256": status.result_manifest_sha256,
            "returned_artifact_count": len(manifest.artifacts),
        }
    )
    provenance["remote_execution_receipt"] = receipt
    job.provenance = provenance

    if status.state == "succeeded" and status.exit_code == 0:
        expected_contract_sha256 = str(
            receipt.get("expected_result_contract_sha256") or ""
        )
        current_contract = resolve_job_result_contract(job)
        current_contract_sha256 = hashlib.sha256(
            json.dumps(current_contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if expected_contract_sha256 != current_contract_sha256:
            raise RemoteExecutionError("REMOTE_RESULT_CONTRACT_IDENTITY_MISMATCH")
        try:
            local_output, previous_generation = await asyncio.to_thread(
                _publish_result_generation,
                job,
                incoming,
            )
        except (OSError, RemoteExecutionError) as exc:
            raise RemoteCollectionPending(str(exc)) from exc
        receipt["published_output_dir"] = str(local_output)
        receipt["previous_generation_quarantine"] = (
            str(previous_generation) if previous_generation is not None else None
        )
        provenance["remote_execution_receipt"] = receipt
        job.provenance = provenance
        from services.remote_stage_receipts import apply_remote_stage_receipts

        await apply_remote_stage_receipts(
            session=session, job=job, attempt_id=expected_attempt_id,
            output_root=local_output, manifest=manifest,
        )
        await session.flush()
        if job.model_id == "msa_batch":
            msa_manifest = local_output / "msa_manifest.json"
            if msa_manifest.is_symlink() or not msa_manifest.is_file():
                raise RemoteExecutionError("Remote MSA batch returned no msa_manifest.json")
            from services.nextflow import apply_msa_manifest_to_child_jobs

            await apply_msa_manifest_to_child_jobs(
                session,
                str(job.id),
                str(msa_manifest),
            )
            job.status = JobStatus.COMPLETED.value
            job.queue_status = "completed"
            job.msa_manifest_path = str(msa_manifest)
            job.completed_at = job.completed_at or datetime.utcnow()
        elif job.awaiting_input:
            job.status = JobStatus.AWAITING_INPUT.value
            job.queue_status = "completed"
        elif job.model_id == "molecular_dynamics" and job.mode == "simulate":
            from services.md.completion import validate_and_finalize_md_job

            await validate_and_finalize_md_job(job, session)
        else:
            from services.result_state_integrity import finalize_successful_job

            result = await finalize_successful_job(
                job,
                str(local_output),
                session,
            )
            # The ingester/finalizer may have committed and yielded to an
            # operator retry or cancellation. Discard every old ORM delta.
            await session.rollback()
            job = await session.get(Job, job_id, populate_existing=True)
            expected_terminal = (
                ("completed", "completed", "ingested") if result.completed
                else ("failed", "failed", "returned_ingestion_failed")
            )
            if (job is None or str(job.remote_attempt_id or "") != expected_attempt_id
                    or str(job.nextflow_run_id or "") != expected_run_id
                    or str(job.execution_target_id or "") != expected_target_id
                    or (job.status, job.queue_status, job.remote_state) != expected_terminal):
                return False
            # The finalizer committed the terminal state and lease release
            # atomically. Consume that result without claiming its old lease.
            if result.completed:
                from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job

                schedule_viewer_minimum_analyses_for_job(job_id)
            return True
        job.remote_state = "ingested"
        job.error_message = None
    else:
        job.status = JobStatus.FAILED.value
        job.queue_status = "failed"
        job.remote_state = "failed"
        if status.state == "cancelled":
            job.error_message = "REMOTE_CANCELLED_WITHOUT_LOCAL_CANCELLATION_RECEIPT"
        else:
            job.error_message = status.error or f"Remote workflow exited with code {status.exit_code}"
        if str(job.model_id or "").lower() == "conformational_mapping":
            from services.conformational_mapping.persistence import terminalize_failed_request_for_job

            await terminalize_failed_request_for_job(session, job_id=str(job.id))
        if str(job.model_id or "").lower() == "protein_local_redesign":
            from services.rfd3_local_redesign import terminalize_failed_request_for_job

            await terminalize_failed_request_for_job(
                session,
                job_id=str(job.id),
                exit_code=status.exit_code or 1,
            )
    job.assigned_gpu = None
    job.params = release_scheduler_gpu_assignment(job.params)
    job.completed_at = job.completed_at or datetime.utcnow()
    await _release_remote_target_lease(session, job)
    await session.commit()
    return True


async def cancel_remote_job(job: Job, *, graceful_timeout_seconds: float = 30.0) -> bool:
    if not job.execution_target_id or not job.remote_attempt_id:
        return False
    async with async_session() as session:
        target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
        if target is None:
            return False
        try:
            connection, attempt_dir = _connection_for_attempt(target, job)
            response = await run_remote(
                connection,
                _worker_argv(
                    connection,
                    "cancel",
                    attempt_dir,
                    "--timeout-seconds",
                    str(max(1.0, graceful_timeout_seconds)),
                ),
                timeout=max(45.0, graceful_timeout_seconds + 20.0),
            )
        except (RemoteTransportError, RemoteExecutionError):
            return False
        status = _parse_status(response.stdout)
        if status.job_id != str(job.id) or status.attempt_id != str(job.remote_attempt_id):
            return False
        # A terminal worker is stopped. It cannot revoke durable local intent.
        return status.state in TERMINAL_REMOTE_STATES


async def cancel_remote_run_id(
    nextflow_run_id: str,
    *,
    graceful_timeout_seconds: float = 30.0,
) -> bool:
    attempt_id = _remote_attempt_id(nextflow_run_id)
    async with async_session() as session:
        result = await session.execute(
            select(Job).where(
                Job.remote_attempt_id == attempt_id,
                Job.nextflow_run_id == nextflow_run_id,
            )
        )
        job = result.scalar_one_or_none()
        if job is None:
            return False
    return await cancel_remote_job(job, graceful_timeout_seconds=graceful_timeout_seconds)
