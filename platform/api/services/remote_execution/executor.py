"""Remote attempt staging, durable control, collection, and local finalization."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
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
from .targets import ExecutionTargetError, get_ready_target
from .transport import (
    RemoteConnection,
    RemoteTransportError,
    rsync_from_remote,
    rsync_to_remote,
    run_remote,
)

REMOTE_RUN_PREFIX = "remote:"
TERMINAL_REMOTE_STATES = frozenset({"cancelled", "succeeded", "failed", "lost"})


class RemoteExecutionError(RuntimeError):
    pass


class RemoteCollectionPending(RemoteExecutionError):
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
        raise RemoteExecutionError("Remote runner identity changed after target activation")


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


async def _stage_bundle(
    connection: RemoteConnection,
    bundle: PreparedRemoteBundle,
) -> None:
    await _transfer_plan(connection, bundle.source_transfer)
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
        await _transfer_plan(connection, transfer)
    api_runtime_transfers = [
        transfer
        for transfer in bundle.runtime_transfers
        if "/data/runtime/cm-api-python/releases/" in transfer.remote_destination
    ]
    if len(api_runtime_transfers) != 1:
        raise RemoteExecutionError("Remote package has no unique managed workflow Python runtime")
    api_release_name = PurePosixPath(api_runtime_transfers[0].remote_destination).name
    await run_remote(
        connection,
        ["rm", "-f", f"{api_runtime_transfers[0].remote_destination}/venv/.venv"],
    )
    api_runtime_root = f"{bundle.remote_runtime_dir}/data/runtime/cm-api-python"
    await run_remote(
        connection,
        ["ln", "-sfn", f"releases/{api_release_name}", f"{api_runtime_root}/current"],
    )
    canonical_data_root = str(get_data_root())
    await run_remote(connection, ["mkdir", "-p", canonical_data_root])
    for target_path, canonical_name in (
        (f"{bundle.remote_runtime_dir}/containers", "apptainer"),
        (f"{bundle.remote_runtime_dir}/weights", "weights"),
        (f"{bundle.remote_runtime_dir}/data/runtime", "runtime"),
    ):
        await run_remote(
            connection,
            ["ln", "-sfn", target_path, f"{canonical_data_root}/{canonical_name}"],
        )
    await run_remote(
        connection,
        [
            "mkdir",
            "-p",
            f"{bundle.remote_attempt_dir}/bundle",
            f"{bundle.remote_attempt_dir}/results",
            f"{bundle.remote_attempt_dir}/work",
            f"{bundle.remote_attempt_dir}/apptainer-cache",
        ],
    )
    output_alias = PurePosixPath(bundle.remote_output_alias)
    await run_remote(connection, ["mkdir", "-p", str(output_alias.parent)])
    link_writer = (
        "import os,sys; p,t=sys.argv[1:]; "
        "exists=os.path.lexists(p); "
        "(_ for _ in ()).throw(RuntimeError('remote output path is occupied')) "
        "if exists and not os.path.islink(p) else None; "
        "os.unlink(p) if exists else None; os.symlink(t,p)"
    )
    await run_remote(
        connection,
        [
            "python3",
            "-c",
            link_writer,
            bundle.remote_output_alias,
            f"{bundle.remote_attempt_dir}/results",
        ],
    )
    await rsync_to_remote(connection, bundle.local_attempt_dir, bundle.remote_attempt_dir)
    for transfer in bundle.input_transfers:
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


async def launch_remote_job(
    session: AsyncSession,
    job: Job,
    *,
    command: list[str],
    environment: dict[str, str] | None = None,
    secret_environment: dict[str, str] | None = None,
) -> str:
    if not job.execution_target_id:
        raise RemoteExecutionError("Job has no remote execution target")

    job_id = str(job.id)
    bundle: PreparedRemoteBundle | None = None
    connection: RemoteConnection | None = None
    target: ExecutionTarget | None = None
    worker_start_requested = False
    launch_fenced = False
    requested_attempt_id = str(uuid.uuid4())
    try:
        target = await get_ready_target(session, str(job.execution_target_id))
        connection = RemoteConnection.from_target(target)
        await _verify_remote_runner(connection, target)
        bundle = await asyncio.to_thread(
            prepare_remote_bundle,
            job=job,
            target=target,
            command=command,
            environment=environment,
            attempt_id=requested_attempt_id,
        )
        run_id = f"{REMOTE_RUN_PREFIX}{bundle.attempt_id}"
        job.nextflow_run_id = run_id
        job.remote_attempt_id = bundle.attempt_id
        job.remote_state = "staging"
        job.execution_source_revision = bundle.envelope.source_revision
        job.execution_source_tree = bundle.envelope.source_tree
        job.execution_bundle_sha256 = bundle.envelope_sha256
        provenance = dict(job.provenance or {})
        provenance["remote_execution_receipt"] = _remote_receipt(
            bundle,
            target,
            state="staging",
        )
        job.provenance = provenance
        # Persist the attempt identity and immutable envelope before the first
        # remote side effect. A local restart can now reconcile this attempt.
        await session.commit()
        await asyncio.to_thread(_archive_envelope, bundle)

        await _stage_bundle(connection, bundle)
        await _stage_secret_environment(connection, bundle, secret_environment)
        await run_remote(
            connection,
            _worker_argv(connection, "prepare", bundle.remote_attempt_dir),
            timeout=300,
        )
        # End the staging transaction and reload the authoritative Job before
        # requesting the external start effect. A committed cancellation or
        # requeue must win over this stale launcher session.
        await session.rollback()
        current_job = await session.get(Job, job_id)
        if (
            current_job is None
            or current_job.status != JobStatus.RUNNING.value
            or current_job.queue_status != "running"
            or current_job.nextflow_run_id != run_id
            or current_job.remote_attempt_id != bundle.attempt_id
        ):
            launch_fenced = True
            if current_job is not None:
                current_job.remote_state = "cancelled_before_remote_start"
                provenance = dict(current_job.provenance or {})
                provenance["remote_execution_receipt"] = _remote_receipt(
                    bundle,
                    target,
                    state="cancelled_before_remote_start",
                    error="Authoritative local Job state no longer permits launch",
                )
                current_job.provenance = provenance
                await session.commit()
            try:
                await run_remote(
                    connection,
                    ["rm", "-rf", bundle.remote_attempt_dir],
                    timeout=60,
                )
            finally:
                raise RemoteExecutionError(
                    "Authoritative local Job state no longer permits remote launch"
                )
        job = current_job
        worker_start_requested = True
        response = await run_remote(
            connection,
            _worker_argv(connection, "run", bundle.remote_attempt_dir),
            timeout=60,
        )
        status = _parse_status(response.stdout)
        if (
            status.attempt_id != bundle.attempt_id
            or status.job_id != str(job.id)
            or status.state not in {"running", *TERMINAL_REMOTE_STATES}
        ):
            raise RemoteExecutionError("Remote worker did not publish a valid attempt receipt")

        job.remote_state = status.state
        provenance = dict(job.provenance or {})
        provenance["remote_execution_receipt"] = _remote_receipt(
            bundle,
            target,
            state=status.state,
            started_at=status.started_at,
        )
        job.provenance = provenance
        await session.commit()
        return run_id
    except Exception as exc:
        if launch_fenced:
            raise
        if bundle is not None and connection is not None and target is not None:
            recovered_status: RemoteAttemptStatus | None = None
            if worker_start_requested:
                try:
                    response = await run_remote(
                        connection,
                        _worker_argv(connection, "status", bundle.remote_attempt_dir),
                        timeout=30,
                    )
                    candidate = _parse_status(response.stdout)
                    if (
                        candidate.attempt_id == bundle.attempt_id
                        and candidate.job_id == str(job.id)
                        and candidate.state in {"running", *TERMINAL_REMOTE_STATES}
                    ):
                        recovered_status = candidate
                except Exception:
                    recovered_status = None

            provenance = dict(job.provenance or {})
            if recovered_status is not None:
                job.remote_state = recovered_status.state
                provenance["remote_execution_receipt"] = _remote_receipt(
                    bundle,
                    target,
                    state=recovered_status.state,
                    started_at=recovered_status.started_at,
                    error=str(exc),
                )
                job.provenance = provenance
                await session.commit()
                return f"{REMOTE_RUN_PREFIX}{bundle.attempt_id}"

            if worker_start_requested:
                # The start command may have reached the worker. Keep the Job
                # reconcilable and never replay or erase an uncertain effect.
                job.remote_state = "launch_uncertain"
                provenance["remote_execution_receipt"] = _remote_receipt(
                    bundle,
                    target,
                    state="launch_uncertain",
                    error=str(exc),
                )
                job.provenance = provenance
                await session.commit()
                return f"{REMOTE_RUN_PREFIX}{bundle.attempt_id}"

            job.remote_state = "launch_failed"
            provenance["remote_execution_receipt"] = _remote_receipt(
                bundle,
                target,
                state="launch_failed",
                error=str(exc),
            )
            job.provenance = provenance
            await session.commit()
            try:
                await run_remote(
                    connection,
                    ["rm", "-rf", bundle.remote_attempt_dir],
                    timeout=60,
                )
            except Exception:
                pass
        if isinstance(exc, RemoteExecutionError):
            raise
        if isinstance(exc, (ExecutionTargetError, RemoteBundleError, RemoteTransportError)):
            raise RemoteExecutionError(str(exc)) from exc
        raise
    finally:
        if bundle is not None:
            await asyncio.to_thread(_cleanup_local_bundle, bundle)
        else:
            await asyncio.to_thread(
                shutil.rmtree,
                get_data_root() / "remote-execution" / "staging" / requested_attempt_id,
                True,
            )


def _connection_for_attempt(target: ExecutionTarget, job: Job) -> tuple[RemoteConnection, str]:
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


async def remote_status(session: AsyncSession, job: Job) -> RemoteAttemptStatus:
    if not job.execution_target_id or not job.remote_attempt_id:
        raise RemoteExecutionError("Job has no complete remote attempt identity")
    target = await session.get(ExecutionTarget, str(job.execution_target_id))
    if target is None:
        raise RemoteExecutionError("Remote execution target record is missing")
    connection, attempt_dir = _connection_for_attempt(target, job)
    try:
        response = await run_remote(
            connection,
            _worker_argv(connection, "status", attempt_dir),
            timeout=30,
        )
    except RemoteTransportError as exc:
        raise RemoteExecutionError(str(exc)) from exc
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


async def collect_remote_results(
    session: AsyncSession,
    job: Job,
    status: RemoteAttemptStatus,
) -> RemoteResultManifest:
    target = await session.get(ExecutionTarget, str(job.execution_target_id))
    if target is None:
        raise RemoteCollectionPending("Remote execution target record is missing")
    connection, attempt_dir = _connection_for_attempt(target, job)
    incoming = get_data_root() / "remote-execution" / "incoming" / str(job.remote_attempt_id)
    if incoming.exists():
        shutil.rmtree(incoming)
    incoming.mkdir(parents=True, exist_ok=False)
    try:
        await rsync_from_remote(
            connection,
            f"{attempt_dir}/results",
            incoming,
        )
        manifest = await asyncio.to_thread(_verify_result_package, incoming, job, status)
    except (RemoteTransportError, RemoteExecutionError, OSError) as exc:
        job.remote_state = "remote_finished_results_waiting"
        job.error_message = f"Remote results are waiting for verified return: {str(exc)[:1500]}"
        await session.commit()
        raise RemoteCollectionPending(str(exc)) from exc
    try:
        local_output = Path(str(job.child_output_dir or job.output_dir)).expanduser().resolve()
        local_output.mkdir(parents=True, exist_ok=True)
        for artifact in manifest.artifacts:
            source_path = _safe_result_path(incoming, artifact.relative_path)
            destination_path = _safe_result_path(local_output, artifact.relative_path)
            if destination_path.is_symlink() or any(
                parent != local_output and parent.is_symlink()
                for parent in destination_path.parents
                if parent == local_output or local_output in parent.parents
            ):
                raise RemoteExecutionError("Local result destination contains a symlink")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
    except (RemoteExecutionError, OSError) as exc:
        job.remote_state = "remote_finished_results_waiting"
        job.error_message = f"Remote results are waiting for verified local copy: {str(exc)[:1500]}"
        await session.commit()
        raise RemoteCollectionPending(str(exc)) from exc
    return manifest


async def reconcile_remote_job(session: AsyncSession, job: Job) -> bool:
    """Reconcile one running remote Job. Return true when local state changed."""
    job_id = str(job.id)
    expected_run_id = str(job.nextflow_run_id or "")
    expected_attempt_id = str(job.remote_attempt_id or "")
    status = await remote_status(session, job)
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
    if job.status == JobStatus.CANCELLED.value:
        job.remote_state = "cancelled"
        job.assigned_gpu = None
        job.params = release_scheduler_gpu_assignment(job.params)
        await session.commit()
        return True
    if job.status != JobStatus.RUNNING.value or job.queue_status != "running":
        return False
    if status.state == "prepared":
        target = await session.get(ExecutionTarget, str(job.execution_target_id))
        if target is None:
            raise RemoteExecutionError("Remote execution target record is missing")
        connection, attempt_dir = _connection_for_attempt(target, job)
        await _verify_remote_runner(connection, target)
        response = await run_remote(
            connection,
            _worker_argv(connection, "run", attempt_dir),
            timeout=60,
        )
        status = _parse_status(response.stdout)
        if status.job_id != str(job.id) or status.attempt_id != str(job.remote_attempt_id):
            raise RemoteExecutionError("Resumed remote attempt does not match the BMS Job")
        job.remote_state = status.state
        await session.commit()
        return True
    if status.state not in TERMINAL_REMOTE_STATES:
        changed = job.remote_state != status.state
        job.remote_state = status.state
        if changed:
            await session.commit()
        return changed
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
        await session.commit()
        return True
    manifest = await collect_remote_results(session, job, status)
    if not await _acquire_remote_terminal_fence(session, job):
        return False
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
            job.status = JobStatus.FAILED.value
            job.queue_status = "failed"
            job.completed_at = datetime.utcnow()
            job.remote_state = "returned_contract_drift"
            job.error_message = "REMOTE_RESULT_CONTRACT_IDENTITY_MISMATCH"
            job.assigned_gpu = None
            job.params = release_scheduler_gpu_assignment(job.params)
            await session.commit()
            return True
        if job.model_id == "msa_batch":
            local_output = Path(str(job.child_output_dir or job.output_dir)).expanduser().resolve()
            msa_manifest = local_output / "msa_manifest.json"
            if msa_manifest.is_symlink() or not msa_manifest.is_file():
                job.status = JobStatus.FAILED.value
                job.queue_status = "failed"
                job.remote_state = "returned_ingestion_failed"
                job.error_message = "Remote MSA batch returned no msa_manifest.json"
                job.assigned_gpu = None
                job.params = release_scheduler_gpu_assignment(job.params)
                job.completed_at = job.completed_at or datetime.utcnow()
                await session.commit()
                return True
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
                str(job.child_output_dir or job.output_dir),
                session,
            )
            if not result.completed:
                job.remote_state = "returned_ingestion_failed"
                job.assigned_gpu = None
                job.params = release_scheduler_gpu_assignment(job.params)
                await session.commit()
                return True
            from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job

            schedule_viewer_minimum_analyses_for_job(str(job.id))
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
    await session.commit()
    return True


async def cancel_remote_job(job: Job, *, graceful_timeout_seconds: float = 30.0) -> bool:
    if not job.execution_target_id or not job.remote_attempt_id:
        return False
    async with async_session() as session:
        target = await session.get(ExecutionTarget, str(job.execution_target_id))
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
        except RemoteTransportError:
            return False
        status = _parse_status(response.stdout)
        return status.state == "cancelled"


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
