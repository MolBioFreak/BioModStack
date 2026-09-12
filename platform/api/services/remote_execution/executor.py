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
from .targets import ExecutionTargetError, get_ready_target
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
    if connection.runtime_binding:
        return connection.runtime_binding["paths"]["runner"]
    return f"{connection.remote_root}/runner/bms_remote_worker.py"


def _worker_argv(
    connection: RemoteConnection,
    command: str,
    attempt_dir: str,
    *extra: str,
) -> list[str]:
    return [
        connection.runtime_binding["paths"]["python"] if connection.runtime_binding else "python3",
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
    paths = [f"{connection.remote_root}/runner/bms_remote_worker.py",
             f"{connection.remote_root}/runner/nextflow"]
    if connection.runtime_binding:
        binding = connection.runtime_binding
        keys = ("runner", "nextflow") + (("python",) if "python" in binding["sha256"] else ())
        expected = [binding["sha256"][key] for key in keys]
        paths = [binding["paths"][key] for key in keys]
    if any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in expected
    ):
        raise RemoteExecutionError("Execution target has no verified runner identity")
    response = await run_remote(
        connection,
        ["sha256sum", *paths],
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
    attached_binding = (target.capabilities or {}).get("critical_runtime_binding")
    if attached_binding:
        generation.append(ExecutionTarget.capabilities["critical_runtime_binding"]["release_sha256"].as_string()
                          == attached_binding["release_sha256"])
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
    context_identity = None
    context_destination = bundle.remote_attempt_dir + '/bundle/inputs/component-context.json'
    for transfer in bundle.input_transfers:
        if transfer.remote_destination == context_destination:
            context = json.loads(transfer.source.read_bytes())
            context_identity = {key: context[key] for key in (
                'root_job_id', 'attempt_id', 'target_id', 'lease_id', 'source_identity',
                'plan_sha256', 'artifact_root')}
            context_identity['external_services'] = context['execution_plan']['metadata']['external_services']
    local_roots = [local for local, remote in bundle.envelope.path_map.items()
                   if remote == bundle.envelope.output_directory]
    if len(local_roots) != 1:
        raise RemoteExecutionError('Remote envelope lacks its exact controller result root')
    return {
        "schema": "bms.remote-execution-receipt.v1",
        "local_result_root": local_roots[0],
        "component_context_identity": context_identity,
        "resource_execution": (bundle.envelope.resource_monitor or {}).get('execution'),
        "generation": 0,
        "state": state,
        "attempt_id": bundle.attempt_id,
        "execution_target_id": str(target.id),
        "provider": str(target.provider),
        "provider_instance_id": str(target.provider_instance_id),
        "ssh_host": str(target.host),
        "ssh_port": target.port,
        "ssh_username": str(target.username),
        "lease_acquired_at": target.lease_acquired_at.isoformat() if target.lease_acquired_at else None,
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
        "critical_runtime_binding": capabilities.get("critical_runtime_binding"),
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


def _attempt_lease_predicates(job: Job) -> list:
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    raw = receipt.get("lease_acquired_at")
    if raw is None:
        return []  # Retained legacy attempts still use job/attempt CAS.
    try:
        epoch = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise RemoteExecutionError("Invalid persisted attempt lease epoch") from exc
    return [ExecutionTarget.lease_acquired_at == epoch]


async def _publish_remote_transition(
    session: AsyncSession, job: Job, values: dict[str, Any], *, release_lease: bool = False, require_lease: bool = True,
) -> bool:
    """CAS the complete attempt/claim snapshot; never autoflush a stale owner."""
    lease_authority = [select(ExecutionTarget.id).where(
        ExecutionTarget.id == job.execution_target_id,
        ExecutionTarget.leased_job_id == str(job.id),
        *_attempt_lease_predicates(job),
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
        merged.update(state=status.state, boot_id=status.boot_id, quiescent=status.quiescent,
                      generation=status.generation, native_output_directory=status.native_output_directory,
                      started_at=status.started_at.isoformat())
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
    session: AsyncSession, job: Job, *, command: list[str], native_invocation,
    environment: dict[str, str] | None = None,
    secret_environment: dict[str, str] | None = None,
) -> str:
    with _controller_attempt_guard(str(job.id)) as owned:
        if not owned:
            raise RemoteExecutionError("Remote attempt already has an active controller")
        return await _launch_remote_job_owned(session, job, command=command,
            native_invocation=native_invocation,
            environment=environment, secret_environment=secret_environment)


def _resource_monitor_contract(job, bundle, invocation, admission):
    """Project an existing target reservation into native resource evidence.

    This issues no allocation and claims no hard containment. Only native
    contracts which require a usage receipt request the shared worker observer.
    """
    if job.model_id != 'nanopore':
        return None
    from services.resource_usage_evidence import (
        build_resource_admission_handoff, build_dispatch_materialization_authority,
        materialize_scheduler_dispatch_authority,
    )
    resources = json.loads(bundle.envelope.environment['BMS_TARGET_RESOURCES'])
    assignment = (job.provenance or {}).get('remote_execution_assignment') or {}
    devices = admission['devices']
    if not assignment.get('lease_id') or len(devices) > 1:
        raise RemoteExecutionError('Native resource evidence requires its exact single-device or CPU reservation')
    device = devices[0] if devices else {}
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    payload_sha256 = hashlib.sha256(canonical({
        'requested': json.loads(invocation.requested_json),
        'effective': invocation.native_parameters,
        'plan_sha256': invocation.execution_plan.plan_sha256,
        'command': list(invocation.command),
    })).hexdigest()
    handoff = build_resource_admission_handoff(
        admission_id=hashlib.sha256(canonical(admission)).hexdigest(),
        run_attempt_id=bundle.attempt_id, canonical_job_id=str(job.id),
        preparation_id=invocation.execution_plan.plan_sha256,
        cpu_threads=resources['required']['cpus'], dram_bytes=resources['required']['memory_bytes'],
        gpu_index=device.get('gpu_index'), gpu_uuid=device.get('gpu_uuid'),
        policy_source='services.remote_execution.targets.selected_plan_target_resources',
        policy_version=resources['schema'], owner=str(job.execution_target_id),
        lease_token=assignment['lease_id'], source_revision=bundle.envelope.source_revision,
        source_tree=bundle.envelope.source_tree,
    )
    dispatch = build_dispatch_materialization_authority(payload_sha256=payload_sha256, handoff=handoff)
    if device:
        dispatch = materialize_scheduler_dispatch_authority(dispatch, handoff=handoff,
            gpu_index=device['gpu_index'], gpu_uuid=device['gpu_uuid'])
    # First execution of this newly issued immutable attempt. Owner observations
    # are supplied later by the authenticated supervisor, never by this builder.
    return {'params': {'_global_resource_admission': handoff, '_global_dispatch_authority': dispatch},
            'execution': {'generation': 1, 'attempt': 1, 'attempt_id': bundle.attempt_id}}


async def _launch_remote_job_owned(
    session: AsyncSession,
    job: Job,
    *,
    command: list[str],
    native_invocation,
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
            native_invocation=native_invocation,
            environment=environment, attempt_id=requested_attempt_id,
        )
        from .targets import admit_target_resources
        from .bundle import bind_resource_admission
        resources = json.loads(bundle.envelope.environment['BMS_TARGET_RESOURCES'])
        required = resources['required']
        admission = await admit_target_resources(target, required_cpus=required['cpus'],
            required_memory_bytes=required['memory_bytes'], required_scratch_bytes=required['scratch_bytes'],
            gpu_ids=resources['gpu_ids'], minimum_gpu_memory_mb=resources.get('minimum_gpu_memory_mb', 0))
        resource_monitor = _resource_monitor_contract(job, bundle, native_invocation, admission)
        bundle = await asyncio.to_thread(bind_resource_admission, bundle, admission, resource_monitor=resource_monitor)
        resources = json.loads(bundle.envelope.environment['BMS_TARGET_RESOURCES'])
        run_id = f"{REMOTE_RUN_PREFIX}{bundle.attempt_id}"
        provenance = dict(job.provenance or {})
        provenance['remote_execution_assignment'] = {
            **provenance.get('remote_execution_assignment', {}), 'resources': resources}
        provenance["remote_execution_receipt"] = _remote_receipt(bundle, target, state="staging")
        if not await _publish_remote_transition(session, job, {
            "nextflow_run_id": run_id, "remote_attempt_id": bundle.attempt_id,
            "remote_state": "staging", "execution_source_revision": bundle.envelope.source_revision,
            "execution_source_tree": bundle.envelope.source_tree,
            "execution_bundle_sha256": bundle.envelope_sha256, "provenance": provenance,
            "params": {**dict(job.params or {}), **(resource_monitor["params"] if resource_monitor else {})},
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
        await session.refresh(target)
        # Declared bundle storage is now materialized, not a second pending copy.
        # Its original capacity receipt remains sealed in the execution envelope.
        fresh_admission = await admit_target_resources(target, required_cpus=required['cpus'],
            required_memory_bytes=required['memory_bytes'], required_scratch_bytes=0,
            gpu_ids=resources['gpu_ids'], minimum_gpu_memory_mb=resources.get('minimum_gpu_memory_mb', 0))
        if fresh_admission['devices'] != admission['devices']:
            raise RemoteExecutionError('Target physical devices changed during staging')
        provenance = dict(job.provenance or {})
        provenance['remote_execution_assignment'] = {**assignment,
            'resources': {**resources, 'admission': fresh_admission}}
        # Publish fresh admission and start intent under one attempt/source/lease
        # CAS; no external work needs an intermediate provenance-only commit.
        if not await _publish_remote_transition(session, job, {
            "remote_state": "launch_requested", "provenance": provenance,
        }):
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
    connection = RemoteConnection.from_target(target)
    receipt = (
        dict(job.provenance.get("remote_execution_receipt") or {})
        if isinstance(job.provenance, dict)
        else {}
    )
    for key, actual in (("execution_target_id", target.id),
                        ("provider_instance_id", target.provider_instance_id),
                        ("ssh_host", connection.host), ("ssh_port", connection.port),
                        ("ssh_username", connection.username)):
        if receipt.get(key) is not None and str(receipt[key]) != str(actual):
            raise RemoteExecutionError("Existing attempt endpoint identity changed; explicit resolution required")
    receipt_attempt = str(receipt.get("attempt_id") or "")
    if receipt_attempt and receipt_attempt != str(job.remote_attempt_id):
        raise RemoteExecutionError("Persisted receipt belongs to another attempt")
    receipt_root = str(receipt.get("remote_root") or "").rstrip("/")
    receipt_attempt_dir = str(receipt.get("remote_attempt_dir") or "")
    if receipt_attempt == str(job.remote_attempt_id) and receipt_root and receipt_attempt_dir:
        expected_attempt_dir = f"{receipt_root}/attempts/{job.remote_attempt_id}"
        if receipt_attempt_dir != expected_attempt_dir:
            raise RemoteExecutionError("Persisted remote attempt path is invalid")
        binding = receipt.get('critical_runtime_binding')
        if binding is None and receipt.get('runner_sha256') and receipt.get('nextflow_launcher_sha256'):
            # Retained pre-generation attempts still own these pinned legacy
            # bytes; reattachment cannot substitute the current target hashes.
            binding = dict(paths=dict(runner=receipt_root + '/runner/bms_remote_worker.py',
                nextflow=receipt_root + '/runner/nextflow', python='python3'),
                sha256=dict(runner=receipt['runner_sha256'], nextflow=receipt['nextflow_launcher_sha256']))
        connection = RemoteConnection(
            target_id=connection.target_id,
            host=connection.host,
            port=connection.port,
            username=connection.username,
            remote_root=receipt_root,
            # Control the original pinned generation, never a later attachment.
            # A legacy receipt intentionally keeps its legacy runner path.
            runtime_binding=binding,
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
                *_attempt_lease_predicates(job),
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
            *_attempt_lease_predicates(job),
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


async def retry_component_execution(session: AsyncSession, job: Job, *, component_id: str,
                                    operation_id: str, actor: str, failure_code: str) -> dict:
    """Explicit native retry outbox; one original root, target and durable edge."""
    if not all(isinstance(value, str) and value.strip() for value in
               (component_id, operation_id, actor, failure_code)):
        raise RemoteExecutionError('Component retry requires immutable operation identity')
    with _controller_attempt_guard(str(job.id)) as owned:
        if not owned:
            raise RemoteExecutionError('Component attempt controller is busy; replay the same operation')
        await session.refresh(job)
        child = await session.get(Job, component_id)
        if child is not None:
            if child.parent_job_id != str(job.id) or child.execution_target_id != job.execution_target_id:
                raise RemoteExecutionError('Retry child does not belong to this retained target/root')
            component_id = (child.provenance or {}).get('component_id') or component_id
        intent = dict(component_id=component_id, operation_id=operation_id,
                      actor=actor, failure_code=failure_code)
        provenance = dict(job.provenance or {})
        previous = provenance.get('component_retry') or {}
        history = dict(provenance.get('component_retry_history') or {})
        saved = previous if previous.get('operation_id') == operation_id else history.get(operation_id)
        if saved and any(saved.get(key) != value for key, value in intent.items()):
            raise RemoteExecutionError('Immutable component retry operation conflicts')
        if saved:
            intent = dict(saved)
        elif previous:
            if job.status != 'failed' or previous.get('state') not in {'accepted', 'queued'}:
                raise RemoteExecutionError('Another component retry is still pending')
            history[previous['operation_id']] = previous
        if provenance.get('component_checkpoint_resume'):
            raise RemoteExecutionError('A pending review continuation cannot be bypassed by retry')
        if not job.execution_target_id:
            return await _queue_local_component_retry(session, job, intent, history, saved)
        if not saved:
            if job.status != 'failed':
                raise RemoteExecutionError('Component retry requires a failed retained root')
            receipt = provenance.get('remote_execution_receipt') or {}
            context = receipt.get('component_context_identity') or provenance.get('assignment_context') or {}
            if (context.get('root_job_id') != str(job.id)
                    or context.get('attempt_id') != job.remote_attempt_id
                    or context.get('target_id') != job.execution_target_id
                    or context.get('source_identity') != {'revision': job.execution_source_revision,
                                                         'tree': job.execution_source_tree}
                    or not context.get('lease_id') or not receipt.get('boot_id')):
                raise RemoteExecutionError('Original remote component context/lease/source is unavailable')
            intent.update(attempt_id=str(job.remote_attempt_id), original_lease_id=context['lease_id'],
                boot_id=receipt['boot_id'], predecessor_generation=receipt.get('generation', 0),
                continuation_lease_id=uuid.uuid4().hex, state='pending')
            if not await _publish_remote_transition(session, job, {'provenance': dict(provenance,
                    component_retry=intent, component_retry_history=history)}, require_lease=False):
                raise RemoteExecutionError('Component retry intent changed before control')
        return await _retry_remote_component_owned(session, job, intent)


async def _queue_local_component_retry(session, job, intent, history, saved):
    import sys
    from paths import get_code_root
    code_root = str(get_code_root())
    if code_root not in sys.path:
        sys.path.insert(0, code_root)
    from scripts.lib.component_adapter import runtime_from_environment
    from scripts.bms_md.spawn_replicas import prepare_replica_retry

    provenance = dict(job.provenance or {})
    path = Path(str(provenance.get('component_context_path') or ''))
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise RemoteExecutionError('Local retry requires its retained context.json')
    context = json.loads(path.read_bytes())
    from component_runtime import SourceIdentity
    from dataclasses import asdict
    if (context.get('source_identity') != asdict(SourceIdentity.from_checkout(Path(code_root)))
            or not Path(context['ledger_path']).is_file() or context['root_job_id'] != str(job.id)
            or context['target_id'] != 'local'):
        raise RemoteExecutionError('Local retry ledger/root/target conflicts')
    runtime = runtime_from_environment(str(path))
    prior = runtime.retry_status(intent['operation_id'])
    if prior is not None:
        if (not saved or prior['component_id'] != intent['component_id']
                or prior['actor'] != intent['actor'] or prior['attempt_id'] != context['attempt_id']
                or prior['continuation_lease_id'] != intent['continuation_lease_id']):
            raise RemoteExecutionError('Local retry replay ownership conflicts')
        return prior
    if saved:
        if intent.get('attempt_id') != context['attempt_id']:
            raise RemoteExecutionError('Local retry pending attempt conflicts')
        return dict(child_job_id=intent['child_job_id'], attempt_id=intent['attempt_id'],
                    target_id='local', state='queued', operation_id=intent['operation_id'])
    state = runtime.root_state() or {}
    if (job.status != 'failed' or state.get('state') != 'failed' or not state.get('quiescent')
            or state.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()):
        raise RemoteExecutionError('Local retry requires same-boot failed quiescent root')
    replacement, _ = prepare_replica_retry(runtime, component_id=intent['component_id'],
        operation_id=intent['operation_id'], failure_code=intent['failure_code'])
    gpu = runtime.context['resources'].get('gpu_id')
    if type(gpu) is not int or gpu < 0:
        raise RemoteExecutionError('Local MD retry has no retained physical GPU assignment')
    intent.update(attempt_id=context['attempt_id'], continuation_lease_id=uuid.uuid4().hex,
                  predecessor_run_id=job.nextflow_run_id,
                  child_job_id=replacement.component_id, state='queued')
    # No ledger authorization here: normal scheduler readmission sets running and
    # reacquires this GPU before _compile_local_component_retry authorizes it.
    if not await _publish_remote_transition(session, job, {
            'provenance': dict(provenance, component_retry=intent, component_retry_history=history),
            'status': 'queued', 'queue_status': 'queued', 'paused': False,
            'awaiting_input': False, 'awaiting_stage': None, 'awaiting_payload': {},
            'pinned_gpu': gpu, 'assigned_gpu': None, 'nextflow_run_id': None, 'started_at': None,
            'params': release_scheduler_gpu_assignment(job.params),
            'completed_at': None, 'error_message': None,
        }, require_lease=False):
        raise RemoteExecutionError('Local component retry changed before scheduler admission')
    return dict(child_job_id=replacement.component_id, attempt_id=context['attempt_id'],
                target_id='local', state='queued', operation_id=intent['operation_id'])


async def _observe_component_retry(session, job, intent):
    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    context = receipt.get('component_context_identity') or (job.provenance or {}).get('assignment_context') or {}
    if (intent['attempt_id'] != str(job.remote_attempt_id)
            or receipt.get('source_revision') != job.execution_source_revision
            or receipt.get('source_tree') != job.execution_source_tree
            or receipt.get('execution_envelope_sha256') != job.execution_bundle_sha256
            or context.get('root_job_id') != str(job.id)
            or context.get('target_id') != job.execution_target_id
            or context.get('attempt_id') != intent['attempt_id']
            or context.get('lease_id') != intent['original_lease_id']
            or receipt.get('boot_id') != intent['boot_id']):
        raise RemoteExecutionError('Retry belongs to another remote source/attempt/lease')
    target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
    if target is None:
        raise RemoteExecutionError('Retained retry target is missing')
    connection, attempt_dir = _connection_for_attempt(target, job)
    await session.commit()
    await _verify_remote_runner(connection, target)
    args = ['--attempt-id', intent['attempt_id'], '--expected-boot-id', intent['boot_id'],
        '--lease-id', intent['original_lease_id'], '--component-id', intent['component_id'],
        '--operation-id', intent['operation_id'], '--actor', intent['actor']]
    response = await run_remote(connection,
        _worker_argv(connection, 'component-retry-status', attempt_dir, *args), timeout=60)
    payload = json.loads(response.stdout)
    status = RemoteAttemptStatus.model_validate(payload['worker_status'])
    if (status.job_id != str(job.id) or status.attempt_id != intent['attempt_id']
            or status.boot_id != intent['boot_id']):
        raise RemoteExecutionError('Remote retry observation identity conflicts')
    edge = payload['retry']
    if edge is not None:
        if any(edge.get(key) != intent[key] for key in
               ('component_id', 'operation_id', 'actor', 'attempt_id', 'continuation_lease_id')):
            raise RemoteExecutionError('Remote durable retry operation conflicts')
        if (edge.get('target_id') != str(job.execution_target_id)
                or edge.get('generation') != intent['predecessor_generation'] + 1):
            raise RemoteExecutionError('Remote durable retry target/generation conflicts')
    return target, connection, attempt_dir, args, edge, status


async def _accept_component_retry(session, job, intent, edge, status):
    # A ledger edge can precede the status/launch claim. Observation alone never
    # launches it or interprets the former generation's terminal status as new.
    if (status.generation != edge['generation']
            or status.continuation_lease_id != edge['continuation_lease_id']):
        return False
    if (status.plan_sha256 != edge['plan_sha256']
            or status.native_output_directory != edge['parent_snapshot']['output_dir']):
        raise RemoteExecutionError('Retry current native plan/output differs from durable edge')
    if (job.status != 'running' or job.queue_status not in {'running', 'cancelling'}
            or job.remote_state not in {'component_retry_requested', 'component_retry_uncertain'}):
        return False
    provenance = dict(job.provenance or {})
    if (provenance.get('component_retry') or {}).get('operation_id') != intent['operation_id']:
        return False  # An historical operation read must never rewind the root.
    receipt = dict(provenance.get('remote_execution_receipt') or {})
    receipt.update(generation=edge['generation'], plan_sha256=edge['plan_sha256'],
        native_output_directory=status.native_output_directory,
        continuation_lease_id=edge['continuation_lease_id'])
    for key in ('terminal_status', 'result_manifest_sha256', 'received_manifest_sha256',
                'published_output_dir', 'completed_at', 'exit_code'):
        receipt.pop(key, None)
    provenance.update(remote_execution_receipt=receipt,
        component_retry=dict(intent, state='accepted', child_job_id=edge['child_job_id']))
    accepted = await _publish_remote_transition(session, job, {
        'provenance': provenance, 'remote_state': 'cancelling' if job.queue_status == 'cancelling' else 'running',
        'status': 'running', 'queue_status': job.queue_status, 'completed_at': None, 'error_message': None,
        'awaiting_input': False, 'awaiting_stage': None, 'awaiting_payload': {},
    })
    if not accepted:
        raise RemoteExecutionError('Retry publication lost its current attempt/lease fence')
    return True


async def _retry_remote_component_owned(session, job, intent: dict[str, Any]):
    job_id = str(job.id)
    target, connection, attempt_dir, args, edge, status = await _observe_component_retry(session, job, intent)
    if edge is not None:
        if intent.get('state') == 'accepted':
            return edge  # Pure replay must not reopen terminal/imported science.
        if await _accept_component_retry(session, job, intent, edge, status):
            return edge
        if (job.provenance or {}).get('component_retry', {}).get('operation_id') != intent['operation_id']:
            return edge
    if edge is None and intent.get('state') not in {'requested', 'uncertain'}:
        receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
        if (status.state != 'failed' or not status.quiescent
                or status.generation != intent['predecessor_generation']
                or status.continuation_lease_id != receipt.get('continuation_lease_id')):
            raise RemoteExecutionError('Retry predecessor is not the owned failed quiescent generation')
        from .targets import admit_target_resources
        target = await get_ready_target(session, str(job.execution_target_id))
        connection, attempt_dir = _connection_for_attempt(target, job)
        resources = dict((job.provenance or {}).get('remote_execution_assignment', {}).get('resources') or {})
        required = resources['required']
        admission = await admit_target_resources(target, required_cpus=required['cpus'],
            required_memory_bytes=required['memory_bytes'], required_scratch_bytes=required.get('scratch_bytes', 0),
            gpu_ids=resources['gpu_ids'], minimum_gpu_memory_mb=resources.get('minimum_gpu_memory_mb', 0))
        if admission['devices'] != resources.get('admission', {}).get('devices'):
            raise RemoteExecutionError('Retry physical target devices changed')
        resources['admission'] = admission
        epoch = datetime.utcnow()
        keys = ('host', 'port', 'username', 'remote_root', 'host_key_sha256')
        claimed = await session.execute(update(ExecutionTarget).where(
            ExecutionTarget.id == target.id, ExecutionTarget.leased_job_id.is_(None),
            ExecutionTarget.active.is_(True), ExecutionTarget.state == 'ready',
            *(getattr(ExecutionTarget, key) == getattr(target, key) for key in keys),
        ).values(leased_job_id=str(job.id), lease_acquired_at=epoch).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            await session.rollback()
            raise RemoteExecutionError('Retry target capacity is already reserved')
        intent = dict(intent, state='requested', resources=resources)
        provenance = dict(job.provenance or {})
        receipt = dict(provenance.get('remote_execution_receipt') or {},
            lease_acquired_at=epoch.isoformat(), continuation_lease_id=intent['continuation_lease_id'],
            generation=intent['predecessor_generation'] + 1)
        provenance.update(component_retry=intent, remote_execution_receipt=receipt)
        if not await _publish_remote_transition(session, job, {
                'provenance': provenance, 'status': 'running', 'queue_status': 'running',
                'remote_state': 'component_retry_requested', 'completed_at': None,
                'awaiting_input': False, 'awaiting_stage': None, 'awaiting_payload': {},
            }, require_lease=False):
            raise RemoteExecutionError('Retry ownership changed during resource reacquisition')
    else:
        # Explicit retry of the SAME operation (not an automatic science retry).
        # Even an absent edge may have a delayed command: reuse its issued lease
        # and resources; the worker's durable operation/start claim fences spawn.
        if job.status != 'running' or job.queue_status != 'running':
            raise RemoteExecutionError('Retry control no longer owns a runnable root')
        resources = edge['resources'] if edge is not None else intent['resources']
        if not await _publish_remote_transition(session, job, {'remote_state': 'component_retry_requested'}):
            raise RemoteExecutionError('Retry continuation lease is no longer owned')
    try:
        response = await run_remote(connection, _worker_argv(connection, 'component-retry', attempt_dir,
            *args, '--failure-code', intent['failure_code'],
            '--continuation-lease-id', intent['continuation_lease_id'],
            '--resource-admission-json', json.dumps(resources, sort_keys=True, separators=(',', ':'))), timeout=60)
        payload = json.loads(response.stdout)
        # Read the worker's durable edge again through the same identity checks;
        # never treat a transport response alone as replacement authorization.
        _, _, _, _, edge, status = await _observe_component_retry(session, job, intent)
        if edge is None or payload.get('child_job_id') != edge['child_job_id']:
            raise RemoteExecutionError('Retry did not return its durable replacement')
        if not await _accept_component_retry(session, job, intent, edge, status):
            raise RemoteExecutionError('Retry generation handoff is still unresolved')
        return edge
    except (RemoteTransportError, RemoteExecutionError, ValueError, KeyError) as exc:
        await session.rollback()
        current = await session.get(Job, job_id, populate_existing=True)
        if (current is not None and current.remote_attempt_id == intent['attempt_id']
                and current.status == 'running' and current.queue_status == 'running'
                and current.remote_state in {'component_retry_requested', 'component_retry_uncertain'}
                and (current.provenance or {}).get('component_retry', {}).get('operation_id') == intent['operation_id']):
            provenance = dict(current.provenance or {}, component_retry=dict(intent, state='uncertain'))
            await _publish_remote_transition(session, current, {
                'provenance': provenance, 'remote_state': 'component_retry_uncertain'})
        raise RemoteExecutionError('Retry start is unresolved; original lease retained: ' + str(exc)) from exc


async def request_remote_checkpoint_resume(session: AsyncSession, job: Job, checkpoint: dict, decision: dict) -> dict:
    with _controller_attempt_guard(str(job.id)) as owned:
        if not owned:
            raise RemoteExecutionError('Checkpoint controller operation is already active')
        return await _request_remote_checkpoint_resume_owned(session, job, checkpoint, decision)


async def _request_remote_checkpoint_resume_owned(session: AsyncSession, job: Job, checkpoint: dict, decision: dict) -> dict:
    """Commit the explicit operation before transport; acceptance owns generation."""
    if (job.provenance or {}).get('remote_checkpoint_operation', {}).get('state') in {'requested', 'uncertain'}:
        raise RemoteExecutionError('Checkpoint decision is unresolved; reconcile the original operation')
    if not job.awaiting_input or job.status != 'awaiting_input' or job.queue_status == 'cancelling':
        raise RemoteExecutionError('Checkpoint review is not awaiting an explicit decision')
    from .targets import get_ready_target
    import uuid
    target = await get_ready_target(session, str(job.execution_target_id))
    connection, attempt_dir = _connection_for_attempt(target, job)
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    context = receipt.get('component_context_identity') or (job.provenance or {}).get('assignment_context') or {}
    if (not receipt.get("boot_id") or checkpoint.get("attempt_id") != job.remote_attempt_id
            or checkpoint.get("target_id") != job.execution_target_id or not checkpoint.get("lease_id")
            or context.get('lease_id') != checkpoint['lease_id']
            or context.get('attempt_id') != str(job.remote_attempt_id)
            or context.get('target_id') != str(job.execution_target_id)
            or context.get('root_job_id') != str(job.id)
            or receipt.get('source_revision') != job.execution_source_revision
            or receipt.get('source_tree') != job.execution_source_tree
            or receipt.get('execution_envelope_sha256') != job.execution_bundle_sha256):
        raise RemoteExecutionError("Checkpoint attempt/boot/lease identity is incomplete")
    from .targets import admit_target_resources
    resources = (job.provenance or {}).get('remote_execution_assignment', {}).get('resources') or {}
    # The worker compiles the actual edge. Request a fresh capacity observation,
    # not admission against an obsolete predecessor's CPU/RAM/scratch budget.
    admission = await admit_target_resources(target, required_cpus=1,
        required_memory_bytes=1, required_scratch_bytes=0, gpu_ids=resources['gpu_ids'], minimum_gpu_memory_mb=resources.get('minimum_gpu_memory_mb', 0))
    if admission['devices'] != resources.get('admission', {}).get('devices'):
        raise RemoteExecutionError('Checkpoint physical target devices changed')
    continuation_lease = uuid.uuid4().hex
    epoch = datetime.utcnow()
    identity_fields = ("host", "port", "username", "remote_root", "host_key_sha256")
    claimed = await session.execute(update(ExecutionTarget).where(
        ExecutionTarget.id == target.id, ExecutionTarget.leased_job_id.is_(None),
        ExecutionTarget.active.is_(True), ExecutionTarget.state == "ready",
        *(getattr(ExecutionTarget, key) == getattr(target, key) for key in identity_fields),
    ).values(leased_job_id=str(job.id), lease_acquired_at=epoch).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await session.rollback()
        raise RemoteExecutionError("Checkpoint worker capacity is already reserved")
    provenance = dict(job.provenance or {})
    binding = dict(operation_id=uuid.uuid4().hex, attempt_id=str(job.remote_attempt_id),
        boot_id=receipt['boot_id'], original_lease_id=checkpoint['lease_id'],
        checkpoint_id=checkpoint['checkpoint_id'], checkpoint_sha256=checkpoint['checkpoint_sha256'],
        decision=decision, continuation_lease_id=continuation_lease, resource_admission=admission)
    provenance['remote_checkpoint_operation'] = dict(binding=binding, state='requested',
        predecessor_receipt=dict(receipt), checkpoint=checkpoint)
    receipt.update(lease_acquired_at=epoch.isoformat())
    provenance["remote_execution_receipt"] = receipt
    # Target claim above and full existing job/source/attempt CAS below commit
    # together. Its prior lease epoch no longer applies to this fresh lease.
    if not await _publish_remote_transition(session, job, {
        "status": "running", "queue_status": "running", "remote_state": "checkpoint_resume_requested",
        "awaiting_input": False, "provenance": provenance,
    }, require_lease=False):
        raise RemoteExecutionError("Checkpoint owner changed during resource reacquisition")
    await _recover_remote_checkpoint(session, job)
    intent = (job.provenance or {}).get('remote_checkpoint_operation') or {}
    if intent.get('state') == 'rejected':
        raise RemoteExecutionError('Checkpoint continuation rejected: ' + intent['error'])
    return dict(job_id=str(job.id), checkpoint_id=checkpoint["checkpoint_id"],
                execution_target_id=str(job.execution_target_id),
                state='continuing' if intent.get('state') == 'accepted' else 'pending')


async def _recover_remote_checkpoint(session: AsyncSession, job: Job) -> bool:
    """Observe/replay ONLY the already committed explicit operation, before status CAS."""
    if job.status != 'running' or job.queue_status not in {'running', 'cancelling'}:
        return False
    intent = (job.provenance or {}).get('remote_checkpoint_operation') or {}
    binding = intent.get('binding') or {}
    receipt = dict((job.provenance or {}).get('remote_execution_receipt') or {})
    context = receipt.get('component_context_identity') or (job.provenance or {}).get('assignment_context') or {}
    if (binding.get('attempt_id') != str(job.remote_attempt_id)
            or binding.get('boot_id') != receipt.get('boot_id')
            or context.get('lease_id') != binding.get('original_lease_id')
            or context.get('target_id') != str(job.execution_target_id)
            or context.get('root_job_id') != str(job.id)
            or context.get('attempt_id') != str(job.remote_attempt_id)
            or receipt.get('source_revision') != job.execution_source_revision
            or receipt.get('source_tree') != job.execution_source_tree
            or receipt.get('execution_envelope_sha256') != job.execution_bundle_sha256):
        raise RemoteExecutionError('Checkpoint operation source/attempt/lease binding conflicts')
    if not await _publish_remote_transition(session, job, {'remote_state': job.remote_state}):
        return False
    target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
    connection, attempt_dir = _connection_for_attempt(target, job)
    await session.commit()
    args = ['--attempt-id', binding['attempt_id'], '--expected-boot-id', binding['boot_id'],
        '--lease-id', binding['original_lease_id'], '--checkpoint-id', binding['checkpoint_id'],
        '--checkpoint-sha256', binding['checkpoint_sha256'], '--operation-id', binding['operation_id'],
        '--decision-json', json.dumps(binding['decision'], sort_keys=True),
        '--continuation-lease-id', binding['continuation_lease_id'],
        '--resource-admission-json', json.dumps(binding['resource_admission'], sort_keys=True)]
    try:
        if job.queue_status == 'cancelling':
            await cancel_remote_job(job, guard_owned=True)
        response = await run_remote(connection, _worker_argv(connection, 'checkpoint-status', attempt_dir, *args), timeout=60)
        payload = json.loads(response.stdout)
        operation, observed = payload['operation'], RemoteAttemptStatus.model_validate(payload['worker_status'])
        if (observed.job_id != str(job.id) or observed.attempt_id != binding['attempt_id']
                or observed.boot_id != binding['boot_id']
                or (operation is not None and operation.get('binding') != binding)):
            raise RemoteExecutionError('Checkpoint operation observation conflicts')
        if job.queue_status == 'cancelling':
            if observed.state in TERMINAL_REMOTE_STATES and observed.quiescent:
                return await _finish_remote_cancellation(session, job, observed)
            return False
        if operation is None or (operation['state'] == 'accepted' and
                (observed.generation != operation['generation'] or observed.state == 'prepared')):
            # CAS again after observation: cancellation or a successor forbids replay.
            if not await _publish_remote_transition(session, job, {'remote_state': job.remote_state}):
                return False
            response = await run_remote(connection, _worker_argv(connection, 'checkpoint-resume', attempt_dir, *args), timeout=60)
            payload = json.loads(response.stdout)
            operation, observed = payload['operation'], RemoteAttemptStatus.model_validate(payload['worker_status'])
            if (operation is None or operation.get('binding') != binding
                    or observed.job_id != str(job.id) or observed.attempt_id != binding['attempt_id']
                    or observed.boot_id != binding['boot_id']):
                raise RemoteExecutionError('Checkpoint continuation receipt identity mismatch')
        provenance = dict(job.provenance or {})
        if operation['state'] == 'rejected':
            predecessor = intent['predecessor_receipt']
            if (observed.state != 'awaiting_input' or not observed.quiescent
                    or observed.generation != predecessor.get('generation', 0)
                    or observed.continuation_lease_id != predecessor.get('continuation_lease_id')
                    or intent['checkpoint'] not in observed.checkpoints):
                raise RemoteExecutionError('Checkpoint rejection lacks quiescent predecessor proof')
            provenance.update(remote_execution_receipt=dict(predecessor),
                remote_checkpoint_operation=dict(intent, state='rejected', error=operation['error']))
            return await _publish_remote_transition(session, job, {'provenance': provenance,
                'status': 'awaiting_input', 'queue_status': 'completed', 'remote_state': 'awaiting_input',
                'awaiting_input': True}, release_lease=True)
        edge = operation['edge']
        if (operation['generation'] != intent['predecessor_receipt'].get('generation', 0) + 1
                or observed.generation != operation['generation']
                or observed.continuation_lease_id != binding['continuation_lease_id']
                or observed.plan_sha256 != edge['plan_sha256']
                or observed.native_output_directory != edge['parent_snapshot']['output_dir']):
            raise RemoteExecutionError('Checkpoint accepted generation binding conflicts')
        if observed.state == 'prepared':
            return False  # Keep the replay lane until a supervisor owns the generation.
        receipt.update(generation=observed.generation, continuation_lease_id=observed.continuation_lease_id,
            plan_sha256=observed.plan_sha256, native_output_directory=observed.native_output_directory)
        provenance.update(remote_execution_receipt=receipt, remote_checkpoint_operation=dict(intent, state='accepted'))
        return await _publish_remote_transition(session, job, {'remote_state': 'running',
            'provenance': provenance, 'awaiting_input': False, 'awaiting_stage': None, 'awaiting_payload': {}})
    except (RemoteTransportError, RemoteExecutionError, ValueError, KeyError) as exc:
        # Publish against the original snapshot, never a refreshed successor.
        await _publish_remote_transition(session, job, {'remote_state': 'checkpoint_resume_uncertain'})
        raise RemoteExecutionError('Checkpoint start is unresolved; original lease retained: ' + str(exc)) from exc


async def retrieve_remote_checkpoint_review(session: AsyncSession, job: Job, checkpoint: dict) -> Path:
    """Explicit selected-file transfer only; no terminal result import or release."""
    import tempfile
    from component_runtime import ResultReference
    observed = await remote_status(session, job)
    if (observed.state != 'awaiting_input' or not observed.quiescent
            or checkpoint not in observed.checkpoints):
        raise RemoteExecutionError('Review checkpoint is no longer the quiescent owned observation')
    refs = [ResultReference(**item) for item in checkpoint['artifacts']]
    if not refs or sum(ref.size_bytes for ref in refs) > 64 * 1024 * 1024:
        raise RemoteExecutionError('Declared review set is empty or exceeds the bounded 64 MiB review transfer')
    target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
    connection, attempt_dir = _connection_for_attempt(target, job)
    output = Path(job.output_dir)
    if not output.is_absolute():
        output = get_data_root() / output
    review_root = output / '.bms-review-import'
    review_root.mkdir(parents=True, exist_ok=True)
    destination = review_root / checkpoint['checkpoint_sha256']
    if destination.exists():
        for ref in refs:
            ref.resolve(destination)
        return destination
    with tempfile.TemporaryDirectory(prefix='.incoming-', dir=review_root) as temporary:
        incoming = Path(temporary)
        await rsync_selected_from_remote(connection, attempt_dir + '/results', incoming,
            [ref.relative_path for ref in refs], max_file_bytes=64 * 1024 * 1024, timeout=300)
        for ref in refs:
            ref.resolve(incoming)
        current = await remote_status(session, job)
        if current.attempt_id != observed.attempt_id or current.boot_id != observed.boot_id or checkpoint not in current.checkpoints:
            raise RemoteExecutionError('Checkpoint changed during explicit review transfer')
        # Registration is an immutable native artifact projection, not scientific completion.
        (incoming / 'checkpoint-review.json').write_text(json.dumps(checkpoint, sort_keys=True))
        if destination.exists():
            for ref in refs:
                ref.resolve(destination)
        else:
            os.rename(incoming, destination)
    return destination


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
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    prior_boot = receipt.get("boot_id")
    if status.generation != receipt.get("generation", 0):
        raise RemoteExecutionError("Component generation observation is stale; retain attempt ownership")
    if receipt.get("continuation_lease_id") and status.continuation_lease_id != receipt["continuation_lease_id"]:
        raise RemoteExecutionError("Checkpoint generation observation is stale; retain lease")
    if prior_boot and status.boot_id != prior_boot and status.state not in TERMINAL_REMOTE_STATES:
        raise RemoteExecutionError("Attempt boot epoch changed without lost/terminal reconciliation")
    return status


def _safe_result_path(root: Path, relative_path: str, *, allow_root: bool = False) -> Path:
    relative = PurePosixPath(relative_path)
    if (relative.is_absolute() or ".." in relative.parts
            or (not relative.parts and not (allow_root and relative_path == '.'))):
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
        or manifest.generation != status.generation
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
        or manifest.generation != status.generation
    ):
        raise RemoteExecutionError("Remote result manifest identity does not match the BMS Job")
    if len(manifest.artifacts) > MAX_RESULT_ARTIFACTS:
        raise RemoteExecutionError("Remote result manifest exceeds the artifact-count limit")
    from .result_generation import checked, prepare_transfer

    checked(incoming)
    prepare_transfer(incoming)
    if incoming.exists():
        saved = checked(incoming / "result-manifest.json")
        if saved.exists() and saved.read_bytes() != manifest_bytes:
            raise RemoteExecutionError("Retained result staging manifest identity changed")
        if not saved.exists() and any(incoming.iterdir()):
            raise RemoteExecutionError("Retained result staging has no manifest identity")
    else:
        incoming.mkdir(parents=True, exist_ok=False)
    # Reject unsafe/undeclared retained files. Reclaim only declared incomplete
    # files under the matching manifest; complete files survive explicit retry.
    declared = {a.relative_path: a for a in manifest.artifacts}
    for path in incoming.rglob("*"):
        checked(path)
        if path.is_file() and path != incoming / "result-manifest.json":
            relative = path.relative_to(incoming).as_posix()
            if relative not in declared:
                # rsync's unrenamed temporary file can survive machine death.
                # Only reclaim its exact declared sibling pattern, never relax
                # the final complete-package inventory/hash check.
                import re

                temporary_for = any(
                    path.parent == incoming / Path(name).parent
                    and re.fullmatch(r"\." + re.escape(Path(name).name) + r"\.[A-Za-z0-9]{6}", path.name)
                    for name in declared
                )
                if temporary_for:
                    path.unlink()
                else:
                    raise RemoteExecutionError("Retained result staging contains undeclared files")
    missing = []
    for artifact in manifest.artifacts:
        path = _safe_result_path(incoming, artifact.relative_path)
        if path.exists():
            if path.is_file() and path.stat().st_size == artifact.size_bytes and _sha256_file(path) == artifact.sha256:
                continue
            if not path.is_file():
                raise RemoteExecutionError("Retained artifact is not a regular file")
            path.unlink()
        missing.append(artifact)
    total_bytes = sum(int(artifact.size_bytes) for artifact in manifest.artifacts)
    configured_limit = int(os.environ.get("BMS_REMOTE_MAX_RETURN_BYTES", DEFAULT_MAX_RESULT_BYTES))
    free_budget = max(0, shutil.disk_usage(incoming.parent).free - RESULT_DISK_RESERVE_BYTES)
    missing_bytes = sum(int(artifact.size_bytes) for artifact in missing)
    if total_bytes > configured_limit or missing_bytes + len(manifest_bytes) > free_budget:
        raise RemoteExecutionError("Remote result package exceeds the local return-byte budget")
    # A deterministic sibling temp cannot pollute the declared payload inventory.
    from .result_generation import move

    temporary = checked(incoming.with_name(incoming.name + ".manifest-tmp"))
    with temporary.open("wb") as handle:
        handle.write(manifest_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    move(temporary, incoming / "result-manifest.json")
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
    from .result_generation import staging_path

    incoming = staging_path(job, str(status.result_manifest_sha256))
    incoming.parent.mkdir(parents=True, exist_ok=True)
    remote_results_dir = f"{attempt_dir}/results"
    transfer_authority = (_pull_identity(job), job.status, job.queue_status, job.provenance)
    await session.commit()
    try:
        manifest = await _fetch_result_manifest(
            connection,
            remote_results_dir,
            incoming,
            job,
            status,
        )
        missing = [artifact for artifact in manifest.artifacts
                   if not _safe_result_path(incoming, artifact.relative_path).is_file()]
        if missing:
            from .result_generation import begin_transfer, end_transfer

            await session.refresh(job)
            if ((_pull_identity(job), job.status, job.queue_status, job.provenance) != transfer_authority
                    or job.queue_status == 'cancelling'):
                raise RemoteExecutionError('Result transfer authority changed before writer launch')
            await session.commit()
            begin_transfer(incoming)
            try:
                await rsync_selected_from_remote(
                    connection,
                    remote_results_dir,
                    incoming,
                    [artifact.relative_path for artifact in missing],
                    max_file_bytes=max(int(artifact.size_bytes) for artifact in missing),
                )
            except (RemoteTransportError, asyncio.CancelledError):
                # Durable result transport must prove descendant quiescence.
                # A timeout/exception alone cannot authorize marker removal;
                # end_transfer retains the fence if its receipt is missing.
                end_transfer(incoming)
                raise
            else:
                end_transfer(incoming)
        manifest = await asyncio.to_thread(_verify_result_package, incoming, job, status)
    except (RemoteTransportError, RemoteExecutionError, OSError, ValueError) as exc:
        raise RemoteCollectionPending(str(exc)) from exc
    return manifest, incoming


def _publish_result_generation(job: Job, incoming: Path) -> tuple[Path, Path | None]:
    from .result_generation import publish

    return publish(job, incoming)


async def _recover_result_generation(session, job):
    """Repair only under both the controller lock and current DB write authority."""
    from .result_generation import journal_path, recover

    if not journal_path(job).exists():
        return False
    identity = _pull_identity(job)
    await session.refresh(job)
    if _pull_identity(job) != identity:
        raise RemoteExecutionError("Result publication recovery authority changed")
    fence = await session.execute(update(Job).where(
        Job.id == job.id, Job.remote_attempt_id == job.remote_attempt_id,
        Job.nextflow_run_id == job.nextflow_run_id,
        Job.execution_target_id == job.execution_target_id,
        Job.execution_source_revision == job.execution_source_revision,
        Job.execution_source_tree == job.execution_source_tree,
        Job.execution_bundle_sha256 == job.execution_bundle_sha256,
        Job.status == job.status, Job.queue_status == job.queue_status,
        Job.remote_state == job.remote_state, Job.provenance == job.provenance,
        Job.output_dir == job.output_dir, Job.child_output_dir == job.child_output_dir,
    ).values(provenance=job.provenance))
    if fence.rowcount != 1:
        await session.rollback()
        raise RemoteExecutionError("Result publication recovery lost current DB fence")
    repaired = await asyncio.to_thread(recover, job)
    await session.commit()
    return repaired


async def _finish_remote_cancellation(session: AsyncSession, job: Job, status=None) -> bool:
    if job.remote_attempt_id and (status is None or not getattr(status, "quiescent", False)):
        return False
    if not await cancel_local_result_transfer(job, guard_owned=True):
        return False
    provenance = dict(job.provenance or {})
    if status is not None:
        receipt = dict(provenance.get("remote_execution_receipt") or {})
        receipt.update(state=status.state, boot_id=status.boot_id, quiescent=status.quiescent, exit_code=status.exit_code,
            generation=status.generation, plan_sha256=status.plan_sha256,
            native_output_directory=status.native_output_directory, terminal_status=status.model_dump(mode='json'),
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


# Retain controller tasks independently of browser/HTTP request lifetime.
_result_return_tasks: set[asyncio.Task] = set()


def automatic_result_return_enabled(job: Job) -> bool:
    """Only the persisted per-job opt-in authorizes successful result retrieval.

    Missing/legacy/invalid settings fail closed to manual. Failure diagnostics
    and interrupted transfers never inherit this authorization.
    """
    params = job.params if isinstance(job.params, dict) else {}
    return params.get("remote_result_policy") == "automatic"


async def reconcile_remote_job(session: AsyncSession, job: Job, *, background_tasks=None) -> bool:
    job_id = str(job.id)
    with _controller_attempt_guard(job_id) as owned:
        if not owned:
            return False
        changed = await _reconcile_remote_job_owned(session, job)
    # Release the observation guard before entering the same reservation lane
    # used by manual pulls. Refresh persisted authority, including revocation.
    job = await session.get(Job, job_id, populate_existing=True)
    if job is None or job.remote_state != "results_available" or not automatic_result_return_enabled(job):
        return changed
    from fastapi import BackgroundTasks

    tasks = background_tasks if background_tasks is not None else BackgroundTasks()
    admitted = await request_remote_result_pull(session, job, tasks, automatic=True)
    if admitted and background_tasks is None:
        task = asyncio.create_task(tasks())
        _result_return_tasks.add(task)
        task.add_done_callback(_result_return_tasks.discard)
    return changed or bool(admitted)


async def _service_remote_external_inputs(session, job, status) -> None:
    """Deliver declared external-stage data; scientific execution stays on worker."""
    from .result_generation import checked, prepare_transfer, begin_transfer
    from component_runtime import canonical_bytes
    from services.model_msa_handoff import (prepare_generated_msa_on_controller,
        await_controller_service_operation, generated_msa_service_supported)
    receipt = dict((job.provenance or {}).get('remote_execution_receipt') or {})
    authority = receipt.get('component_context_identity') or {}
    selected = [row for row in authority.get('external_services', []) if generated_msa_service_supported(row)]
    if not selected or status.state != 'running':
        return
    target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
    if target is None:
        raise RemoteExecutionError('External service target is unavailable')
    connection, attempt_dir = _connection_for_attempt(target, job)
    snapshot = canonical_bytes({key: getattr(job, key) for key in
        ('status', 'queue_status', 'remote_attempt_id', 'nextflow_run_id', 'execution_target_id',
         'execution_source_revision', 'execution_source_tree', 'execution_bundle_sha256', 'params')})
    lease_epoch = receipt.get('lease_acquired_at')

    async def check_fence():
        await session.refresh(job)
        current_target = (await session.execute(select(ExecutionTarget).where(
            ExecutionTarget.id == str(job.execution_target_id), ExecutionTarget.leased_job_id == str(job.id),
            *_attempt_lease_predicates(job)))).scalar_one_or_none()
        current = canonical_bytes({key: getattr(job, key) for key in
            ('status', 'queue_status', 'remote_attempt_id', 'nextflow_run_id', 'execution_target_id',
             'execution_source_revision', 'execution_source_tree', 'execution_bundle_sha256', 'params')})
        current_receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
        if (current != snapshot or (job.status, job.queue_status) != ('running', 'running')
                or current_receipt.get('component_context_identity') != authority
                or current_target is None or not lease_epoch
                or current_receipt.get('lease_acquired_at') != lease_epoch):
            raise asyncio.CancelledError('External service owner changed')

    await check_fence()
    args = ['--attempt-id', authority['attempt_id'], '--expected-boot-id', status.boot_id,
            '--lease-id', authority['lease_id'], '--plan-sha256', authority['plan_sha256']]
    response = await run_remote(connection, _worker_argv(connection, 'external-service-status', attempt_dir, *args), timeout=60)
    observed = json.loads(response.stdout.strip().splitlines()[-1])
    await check_fence()
    if observed.get('artifact_root') != authority['artifact_root']:
        raise RemoteExecutionError('External service artifact custody conflicts')
    for request in observed['requests']:
        if request.get('service') not in selected:
            raise RemoteExecutionError('External service provider/settings differ from selected plan')
        if any(request.get(key) != authority.get(key) for key in
               ('attempt_id', 'root_job_id', 'target_id', 'lease_id', 'source_identity', 'plan_sha256')):
            raise RemoteExecutionError('External service request belongs to a foreign attempt')
        # Use the existing attempt staging/transfer fence across controller death.
        # No old source tree is reclaimed until its transport proves quiescence.
        from component_runtime import digest
        if request.get('request_id') != digest({k: v for k, v in request.items() if k != 'request_id'}):
            raise RemoteExecutionError('External service request digest conflicts')
        staging = checked(get_data_root() / 'remote-execution' / 'staging' / ('msa-' + request['request_id']))
        staging.parent.mkdir(parents=True, exist_ok=True)
        prepare_transfer(staging)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        package = staging / 'prepared'
        try:
            sha = await prepare_generated_msa_on_controller(request, package, check_fence)
        except Exception:
            await check_fence()
            await run_remote(connection, _worker_argv(connection, 'external-service-deliver', attempt_dir,
                *args, '--request-id', request['request_id'], '--failed'), timeout=60)
            raise
        if sha is None:
            return
        await check_fence()
        destination = str(PurePosixPath(authority['artifact_root']) / 'external-services' / request['request_id'] / sha)
        await run_remote(connection, ['mkdir', '-p', destination])
        begin_transfer(staging)
        transfer = asyncio.create_task(rsync_to_remote(connection, package, destination, delete=False,
                                                       ownership_directory=staging))
        await await_controller_service_operation(transfer, check_fence)
        await check_fence()
        # Worker rechecks cancellation, request/settings/native identities and
        # every alignment digest before publishing its sole ledger receipt.
        await run_remote(connection, _worker_argv(connection, 'external-service-deliver', attempt_dir,
            *args, '--request-id', request['request_id'], '--manifest-sha256', sha), timeout=60)
        prepare_transfer(staging)
        shutil.rmtree(staging)


async def _reconcile_remote_job_owned(session: AsyncSession, job: Job) -> bool:
    """Reconcile one running remote Job. Return true when local state changed."""
    job_id = str(job.id)
    await _recover_result_generation(session, job)
    if job.remote_state in {'checkpoint_resume_requested', 'checkpoint_resume_uncertain'}:
        return await _recover_remote_checkpoint(session, job)
    pending = (job.provenance or {}).get('component_retry') or {}
    if job.remote_state in {'component_retry_requested', 'component_retry_uncertain'}:
        # Reconcile the durable operation BEFORE ordinary generation comparison.
        # Never auto-authorize a replacement or repeat a scientific start.
        _, _, _, _, edge, observed = await _observe_component_retry(session, job, pending)
        if edge is None:
            return False
        return await _accept_component_retry(session, job, pending, edge, observed)
    if job.status in {"completed", "failed", "cancelled"}:
        changed = await _recover_diagnostic_return(session, job)
        target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
        if target is None or target.leased_job_id != str(job.id) or not job.remote_attempt_id:
            return changed
        observed = await remote_status(session, job)
        if observed.state in TERMINAL_REMOTE_STATES and observed.quiescent:
            released = await _publish_remote_transition(session, job,
                {"remote_state": job.remote_state}, release_lease=True)
            return released or changed
        return changed
    if job.remote_state == "returning":
        if (job.status, job.queue_status) != ("running", "running"):
            return False
        return await _pull_failure(session, job, "Result pull interrupted; choose Retry pull")
    if job.awaiting_stage == "remote_results":
        return False
    expected_run_id = str(job.nextflow_run_id or "")
    expected_attempt_id = str(job.remote_attempt_id or "")
    expected_target_id = str(job.execution_target_id or "")
    expected_authority = (job.execution_source_revision, job.execution_source_tree,
                          job.execution_bundle_sha256, job.provenance)
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
        or str(job.execution_target_id or "") != expected_target_id
        or expected_authority != (job.execution_source_revision, job.execution_source_tree,
                                  job.execution_bundle_sha256, job.provenance)
    ):
        return False
    if job.status in {"cancelled", "completed", "failed"}:
        return False
    if job.queue_status == "cancelling":
        if status.state in TERMINAL_REMOTE_STATES and status.quiescent:
            return await _finish_remote_cancellation(session, job, status)
        # Delivery is not completion. Keep the original attempt/lease until a
        # subsequent owned observation proves all writers stopped.
        if not await _publish_remote_transition(session, job, {'remote_state': 'cancelling'}):
            return False
        await cancel_remote_job(job, guard_owned=True)
        status = await remote_status(session, job)
        if status.state in TERMINAL_REMOTE_STATES and status.quiescent:
            return await _finish_remote_cancellation(session, job, status)
        return False
    if status.state in TERMINAL_REMOTE_STATES and not getattr(status, "quiescent", False):
        return False
    if status.state == "awaiting_input":
        if job.remote_state in {"checkpoint_resume_requested", "checkpoint_resume_uncertain"}:
            return False  # An in-flight control command can still arrive; retain its lease.
        if not status.quiescent or not status.checkpoints:
            return False
        receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
        context = receipt.get('component_context_identity') or (job.provenance or {}).get('assignment_context') or {}
        if not status.boot_id:
            raise RemoteExecutionError('Remote review checkpoint has no observed boot identity')
        for checkpoint in status.checkpoints:
            if (checkpoint.get("attempt_id") != expected_attempt_id
                    or checkpoint.get("target_id") != expected_target_id
                    or not checkpoint.get('lease_id')
                    or (context.get('lease_id') and checkpoint['lease_id'] != context['lease_id'])
                    or not checkpoint.get("checkpoint_sha256") or not checkpoint.get("artifacts")):
                raise RemoteExecutionError("Remote review checkpoint identity is incomplete or foreign")
        if job.remote_state == "awaiting_input" and job.awaiting_input:
            return False
        receipt = dict((job.provenance or {}).get('remote_execution_receipt') or {})
        receipt.update(boot_id=status.boot_id, generation=status.generation,
            continuation_lease_id=status.continuation_lease_id, plan_sha256=status.plan_sha256,
            native_output_directory=status.native_output_directory,
            started_at=status.started_at.isoformat() if status.started_at else None)
        return await _publish_remote_transition(session, job, {
            "provenance": dict(job.provenance or {}, remote_execution_receipt=receipt),
            "status": "awaiting_input", "queue_status": "completed", "remote_state": "awaiting_input",
            "awaiting_input": True, "awaiting_stage": status.checkpoints[0]["stage"],
            "awaiting_payload": {"component_checkpoint": status.checkpoints[0],
                                 "component_checkpoints": status.checkpoints},
            "assigned_gpu": None,
            "params": release_scheduler_gpu_assignment(job.params),
        }, release_lease=True)
    if (job.status, job.queue_status) not in {("running", "running"), ("queued", "preparing")}:
        return False
    if status.state == "prepared":
        if status.continuation_lease_id:
            return False  # Checkpoint command already owns the sole supervisor spawn.
        if job.remote_state in {"launch_requested", "launch_uncertain"}:
            # A prior start effect is ambiguous. Observation/cancellation only;
            # a prepared snapshot is not proof the command never arrived.
            return False
        target = await session.get(ExecutionTarget, str(job.execution_target_id), populate_existing=True)
        if target is None:
            raise RemoteExecutionError("Remote execution target record is missing")
        connection, attempt_dir = _connection_for_attempt(target, job)
        await _verify_launch_runner(session, job, connection, target)
        from .targets import admit_target_resources
        resources = (job.provenance or {}).get('remote_execution_assignment', {}).get('resources') or {}
        required = resources.get('required') or {}
        if not required or not resources.get('admission'):
            raise RemoteExecutionError('Prepared recovery lacks retained resource admission')
        admission = await admit_target_resources(target, required_cpus=required['cpus'],
            required_memory_bytes=required['memory_bytes'], required_scratch_bytes=required.get('scratch_bytes', 0),
            gpu_ids=resources['gpu_ids'], minimum_gpu_memory_mb=resources.get('minimum_gpu_memory_mb', 0))
        if admission['devices'] != resources['admission'].get('devices'):
            raise RemoteExecutionError('Prepared recovery physical target devices changed')
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
        await _service_remote_external_inputs(session, job, status)
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
    receipt.update(state=status.state, boot_id=status.boot_id, quiescent=status.quiescent, exit_code=status.exit_code,
        generation=status.generation, plan_sha256=status.plan_sha256,
        native_output_directory=status.native_output_directory,
        terminal_status=status.model_dump(mode='json'),
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


async def request_remote_result_pull(session, job, background_tasks, *, automatic=False):
    """Reserve transfer before responding; keep the process lock through completion."""
    if automatic and (job.remote_state != "results_available" or not automatic_result_return_enabled(job)):
        return False
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
        if automatic or job.remote_state == "returning":
            return False
        raise RemoteExecutionError("Remote attempt controller is busy")
    scheduled = False
    try:
        # The caller's ORM snapshot is not policy authority. A concurrent
        # revocation/cancellation before reservation must win without transfer.
        identity = _pull_identity(job)
        await session.refresh(job)
        if _pull_identity(job) != identity:
            if automatic:
                return False
            raise RemoteExecutionError("Remote result attempt changed; refresh the Job")
        if automatic and (job.remote_state != "results_available"
                          or not automatic_result_return_enabled(job)
                          or (job.status, job.queue_status) != ("awaiting_input", "completed")):
            return False
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
        return True
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
                await _recover_result_generation(session, job)
                receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
                received_digest = receipt.get("received_manifest_sha256")
                received = None
                if received_digest:
                    # These are already-authorized, fully received bytes. Import
                    # retry must not depend on a still-running worker/provider.
                    if (received_digest != receipt.get("result_manifest_sha256")
                            or receipt.get("state") != "succeeded" or receipt.get("exit_code") != 0
                            or receipt.get("attempt_id") != identity["attempt_id"]
                            or receipt.get("execution_target_id") != identity["execution_target_id"]
                            or receipt.get("source_revision") != identity["source_revision"]
                            or receipt.get("source_tree") != identity["source_tree"]
                            or receipt.get("execution_envelope_sha256") != identity["execution_envelope_sha256"]):
                        raise RemoteExecutionError("Received result authority no longer matches this attempt")
                    from .result_generation import staging_path, prepare_transfer

                    incoming = staging_path(job, received_digest)
                    prepare_transfer(incoming)
                    status = _retained_terminal_status(job, receipt)
                    try:
                        manifest = await asyncio.to_thread(_verify_result_package, incoming, job, status)
                        received = (status, manifest, incoming)
                    except (RemoteExecutionError, OSError, ValueError):
                        # A later loss/corruption needs the ordinary authorized
                        # transfer repair. Never trust the receipt over bytes.
                        receipt.pop("received_manifest_sha256", None)
                        if not await _publish_remote_transition(session, job, {
                            "provenance": dict(job.provenance or {}, remote_execution_receipt=receipt),
                        }, require_lease=False):
                            return
                if received is None:
                    await _prove_pull_endpoint(session, job)
                    status = await remote_status(session, job)
                    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
                    if (status.state != "succeeded" or status.exit_code != 0
                            or not status.result_manifest_sha256
                            or status.result_manifest_sha256 != receipt.get("result_manifest_sha256")):
                        raise RemoteExecutionError("Remote terminal result identity changed")
                    manifest, incoming = await collect_remote_results(session, job, status)
                else:
                    status, manifest, incoming = received
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if (job is None or _pull_identity(job) != identity or job.remote_state != "returning"
                        or job.status != "running" or job.queue_status != "running"
                        or dict((job.provenance or {}).get("remote_execution_receipt") or {}) != receipt):
                    return
                if receipt.get("received_manifest_sha256") != status.result_manifest_sha256:
                    receipt = dict(receipt, received_manifest_sha256=status.result_manifest_sha256,
                                   terminal_status=status.model_dump(mode='json'), generation=status.generation,
                                   boot_id=status.boot_id, plan_sha256=status.plan_sha256,
                                   native_output_directory=status.native_output_directory,
                                   returned_artifact_count=len(manifest.artifacts))
                    # Keep received-but-unimported distinct even if native import
                    # rolls back. Reuse the existing attempt CAS and journal.
                    if not await _publish_remote_transition(session, job, {
                        "provenance": dict(job.provenance or {}, remote_execution_receipt=receipt),
                    }, require_lease=False):
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
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if job is not None and _pull_identity(job) == identity:
                    await _recover_result_generation(session, job)
            except Exception as exc:
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if (job is not None and _pull_identity(job) == identity
                        and job.remote_state == "returning"
                        and (job.status, job.queue_status) == ("running", "running")):
                    await _recover_result_generation(session, job)
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


def _diagnostic_destination(job_id, identity, digest):
    root = get_data_root() / "remote-execution" / "diagnostics"
    destination = _safe_result_path(root, "/".join((job_id, identity["attempt_id"], digest)))
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise RemoteExecutionError("Diagnostic destination traverses a symlink")
    return destination


async def _diagnostic_write_fence(session, job, identity):
    with session.no_autoflush:
        fence = await session.execute(update(Job).where(
            Job.id == str(job.id), Job.status == job.status,
            Job.queue_status == job.queue_status, Job.remote_state == job.remote_state,
            Job.nextflow_run_id == f"remote:{identity['attempt_id']}",
            Job.remote_attempt_id == identity['attempt_id'],
            Job.execution_target_id == identity['execution_target_id'],
            Job.execution_source_revision == identity['source_revision'],
            Job.execution_source_tree == identity['source_tree'],
            Job.execution_bundle_sha256 == identity['execution_envelope_sha256'],
            Job.provenance == job.provenance,
        ).values(provenance=job.provenance))
    if fence.rowcount != 1:
        await session.rollback()
        return False
    return True


async def _project_remote_diagnostics(session, job, status, manifest, destination):
    relative, _, _ = _native_result_view(job, status, destination, manifest)
    if (relative / '.bms-components.json').as_posix() not in {
            artifact.relative_path for artifact in manifest.artifacts}:
        return  # A pre-runtime failure can seal logs without executed children.
    expected = _component_projection_context(job, status, relative, manifest)
    if expected is not None:
        from services.result_ingester import ingest_component_projection
        await ingest_component_projection(job, str(destination), session, expected_context=expected)
    # Lineage only: never invoke native success or finalize_component_projection.


async def _recover_diagnostic_return(session, job):
    """Recover an abandoned archive claim locally, never pull failed science.

    Called only with the controller guard. An immutable manifest-bound directory
    is the publication journal: a rename before DB commit can be verified and
    adopted without worker connectivity. Missing/corrupt archives remain explicit
    retry, preserving every byte and the original scientific terminal history.
    """
    job_id = str(job.id)
    record = dict((job.provenance or {}).get("remote_diagnostics") or {})
    if job.status not in {"failed", "cancelled"} or record.get("state") != "returning":
        return False
    identity = _pull_identity(job)
    receipt = dict((job.provenance or {}).get("remote_execution_receipt") or {})
    digest = receipt.get("result_manifest_sha256")
    recovered = dict(record, state="failed", output_dir=None,
                     error="Diagnostic pull interrupted; choose Retry diagnostics")
    try:
        if not _diagnostic_authority(job, identity, digest):
            raise RemoteExecutionError("Diagnostic return identity changed; refresh the Job")
        status = _retained_terminal_status(job, receipt)
        if status.state not in TERMINAL_REMOTE_STATES or not digest:
            raise RemoteExecutionError("Diagnostic terminal manifest authority is missing")
        destination = _diagnostic_destination(str(job.id), identity, digest)
        if destination.exists():
            manifest = await asyncio.to_thread(_verify_result_package, destination, job, status)
            if not await _diagnostic_write_fence(session, job, identity):
                return False
            await _project_remote_diagnostics(session, job, status, manifest, destination)
            recovered.update(state="returned", output_dir=str(destination), error=None)
    except Exception as exc:
        await session.rollback()
        job = await session.get(Job, job_id, populate_existing=True)
        if not _diagnostic_authority(job, identity, digest, receipt):
            return False
        recovered["error"] = f"Diagnostic recovery blocked: {exc}"[:1500]
    # CAS includes the receipt/policy/lifecycle snapshot observed before hashing;
    # a newer attempt, cancellation or diagnostic request cannot be overwritten.
    return await _publish_remote_transition(session, job, {
        "provenance": dict(job.provenance or {}, remote_diagnostics=recovered),
    }, require_lease=False)


async def _run_requested_diagnostics(job_id, identity, digest, guard, receipt_snapshot=None):
    incoming = None
    try:
        async with async_session() as session:
            try:
                job = await session.get(Job, job_id)
                if not _diagnostic_authority(job, identity, digest, receipt_snapshot):
                    return
                destination = _diagnostic_destination(job_id, identity, digest)
                receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
                if destination.exists():
                    status = _retained_terminal_status(job, receipt)
                    manifest = await asyncio.to_thread(_verify_result_package, destination, job, status)
                else:
                    await _prove_pull_endpoint(session, job, diagnostics=True)
                    status = await remote_status(session, job)
                    if (status.state not in TERMINAL_REMOTE_STATES
                            or status.result_manifest_sha256 != digest
                            or status.state != receipt.get('state') or status.exit_code != receipt.get('exit_code')):
                        raise RemoteExecutionError('Remote diagnostic terminal identity changed')
                    manifest, incoming = await collect_remote_results(session, job, status)
                await session.rollback()
                job = await session.get(Job, job_id, populate_existing=True)
                if not _diagnostic_authority(job, identity, digest, receipt_snapshot):
                    return
                # Acquire a write fence before filesystem publication; never touch
                # scientific outputs, lifecycle timestamps or any successor lease.
                if not await _diagnostic_write_fence(session, job, identity):
                    return
                destination = _diagnostic_destination(job_id, identity, digest)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    await asyncio.to_thread(_verify_result_package, destination, job, status)
                else:
                    if incoming is None:
                        raise RemoteExecutionError('Verified diagnostic archive disappeared before publication')
                    os.replace(incoming, destination)
                await _project_remote_diagnostics(session, job, status, manifest, destination)
                provenance = dict(job.provenance or {})
                provenance['remote_execution_receipt'] = dict(receipt,
                    terminal_status=status.model_dump(mode='json'), generation=status.generation,
                    boot_id=status.boot_id, plan_sha256=status.plan_sha256,
                    native_output_directory=status.native_output_directory)
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
        # Failed/cancelled publication retains verified staging for explicit retry.
        # In particular, never reclaim bytes on a missing supervisor receipt.
        guard.__exit__(None, None, None)


def _retained_terminal_status(job, receipt):
    """Restore authenticated control evidence, never infer owner from result JSON."""
    saved = receipt.get('terminal_status')
    if saved is None:
        # Retained generation-zero non-native attempts predate the full snapshot.
        saved = dict(job_id=str(job.id), attempt_id=str(job.remote_attempt_id),
            **{key: receipt[key] for key in ('state', 'exit_code', 'completed_at',
                'result_manifest_sha256', 'boot_id', 'quiescent', 'generation',
                'plan_sha256', 'native_output_directory', 'continuation_lease_id',
                'supervisor_pid', 'supervisor_start_ticks', 'control_group') if key in receipt})
    status = RemoteAttemptStatus.model_validate(saved)
    if (status.job_id != str(job.id) or status.attempt_id != str(job.remote_attempt_id)
            or status.state not in TERMINAL_REMOTE_STATES
            or status.state != receipt.get('state') or status.exit_code != receipt.get('exit_code')
            or status.result_manifest_sha256 != receipt.get('result_manifest_sha256')
            or status.generation != receipt.get('generation', 0)
            or status.boot_id != receipt.get('boot_id')
            or status.plan_sha256 != receipt.get('plan_sha256')
            or status.native_output_directory != receipt.get('native_output_directory')
            or status.continuation_lease_id != receipt.get('continuation_lease_id')):
        raise RemoteExecutionError('Retained terminal control authority conflicts')
    return status


def _native_result_view(job, status, artifact_root, manifest):
    """Select the current native collector without discarding retained children."""
    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    remote_root = PurePosixPath(receipt['remote_attempt_dir']) / 'results'
    if status.generation and not status.native_output_directory:
        raise RemoteExecutionError('Current continuation has no authenticated native output root')
    native = PurePosixPath(status.native_output_directory or str(remote_root))
    if not native.is_absolute() or '..' in native.parts:
        raise RemoteExecutionError('Native result root is not a contained attempt path')
    try:
        relative = native.relative_to(remote_root)
    except ValueError as exc:
        raise RemoteExecutionError('Native result root leaves its immutable attempt') from exc
    root = _safe_result_path(artifact_root, relative.as_posix(), allow_root=True)
    records = []
    for artifact in manifest.artifacts:
        try:
            local = PurePosixPath(artifact.relative_path).relative_to(relative)
        except ValueError:
            continue
        records.append(artifact.model_copy(update={'relative_path': local.as_posix()}))
    # This is a native path projection of authenticated entries, not a new
    # transport manifest or any changed scientific bytes/digests.
    return relative, root, manifest.model_copy(update={'artifacts': records})


def retained_result_view(job, *, diagnostics: bool = False):
    """One job-scoped authority for current logs and manifest-listed history.

    Validate the small sealed manifest here; hash only the selected artifact on
    read, rather than rehashing large scientific outputs on every logs request.
    """
    from .result_generation import checked, identity, output_path

    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    status = _retained_terminal_status(job, receipt)
    digest = status.result_manifest_sha256
    if not digest:
        raise RemoteExecutionError('No returned terminal manifest')
    if diagnostics:
        record = (job.provenance or {}).get('remote_diagnostics') or {}
        root = _diagnostic_destination(str(job.id), _pull_identity(job), digest)
        if (job.status not in {'failed', 'cancelled'} or record.get('state') != 'returned'
                or record.get('identity') != _pull_identity(job)
                or record.get('result_manifest_sha256') != digest
                or record.get('output_dir') != str(root)):
            raise RemoteExecutionError('No current returned diagnostic archive')
    else:
        root = output_path(job)
        if (job.provenance or {}).get('remote_result_generation') != identity(job, digest):
            raise RemoteExecutionError('No committed current result generation')
    manifest_path = checked(root / 'result-manifest.json')
    if manifest_path.stat().st_size > MAX_RESULT_MANIFEST_BYTES:
        raise RemoteExecutionError('Returned manifest exceeds bounded size')
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RemoteExecutionError('Returned manifest identity changed')
    manifest = RemoteResultManifest.model_validate_json(raw)
    if (manifest.job_id != str(job.id) or manifest.attempt_id != str(job.remote_attempt_id)
            or manifest.source_revision != str(job.execution_source_revision)
            or manifest.source_tree != str(job.execution_source_tree)
            or manifest.execution_envelope_sha256 != str(job.execution_bundle_sha256)
            or manifest.generation != status.generation or manifest.exit_code != status.exit_code
            or not status.quiescent or len(manifest.artifacts) > MAX_RESULT_ARTIFACTS):
        raise RemoteExecutionError('Returned manifest belongs to another attempt/generation')
    relative, native, _ = _native_result_view(job, status, root, manifest)
    if not diagnostics and receipt.get('published_output_dir') != str(native):
        raise RemoteExecutionError('Published native generation root conflicts')
    return root, relative, manifest, status


def retained_result_file(root, manifest, relative_path):
    """Return only an immutable manifest member; caller cannot select a root."""
    from .result_generation import checked

    artifact = next((a for a in manifest.artifacts if a.relative_path == relative_path), None)
    if artifact is None:
        raise RemoteExecutionError('Artifact is not listed in the returned manifest')
    path = checked(_safe_result_path(root, relative_path))
    if (not path.is_file() or path.stat().st_size != artifact.size_bytes
            or _sha256_file(path) != artifact.sha256):
        raise RemoteExecutionError('Returned artifact integrity changed')
    return path


def _component_projection_context(job, status, relative, manifest):
    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    expected = receipt.get('component_context_identity')
    if expected is None:
        return None
    path = (relative / '.bms-components.json').as_posix()
    if path not in {artifact.relative_path for artifact in manifest.artifacts}:
        raise RemoteExecutionError('Selected component projection is absent from the authenticated return')
    plan_sha256 = status.plan_sha256
    if (not plan_sha256
            or (receipt.get('plan_sha256') and plan_sha256 != receipt['plan_sha256'])
            or (status.generation == 0 and plan_sha256 != expected['plan_sha256'])):
        raise RemoteExecutionError('Current component plan identity is missing or conflicts')
    return dict(expected, current_plan_sha256=plan_sha256, generation=status.generation,
                projection_relative_path=path)


def _record_native_resource_execution(job, status):
    """Bind optional observations to authenticated ownership, never receipt data."""
    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    issued = receipt.get('resource_execution')
    if (not isinstance(issued, dict) or issued.get('attempt_id') != job.remote_attempt_id
            or not status.quiescent or not status.boot_id or not status.control_group
            or not status.supervisor_pid or not status.supervisor_start_ticks):
        return False
    observed = dict(issued, boot_id=status.boot_id, supervisor_pid=status.supervisor_pid,
        supervisor_start_ticks=status.supervisor_start_ticks, control_group=status.control_group)
    observed['quiescent'] = True
    params = dict(job.params or {})
    attempts = list(params.get('execution_attempts') or [])
    same = [row for row in attempts if row.get('attempt_id') == issued['attempt_id']
            and row.get('generation') == issued.get('generation') and row.get('attempt') == issued.get('attempt')]
    if same and same != [observed]:
        return False
    if not same:
        attempts.append(observed)
    params['execution_attempts'] = attempts
    job.params = params
    return True


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
        from services.remote_stage_receipts import validate_remote_stage_receipts
        from .result_generation import output_path

        relative, native_incoming, native_manifest = _native_result_view(job, status, incoming, manifest)
        projection_context = _component_projection_context(job, status, relative, manifest)
        canonical_root = output_path(job)
        job.child_output_dir = str(_safe_result_path(canonical_root, relative.as_posix(), allow_root=True))
        if job.model_id == 'molecular_dynamics' and job.mode != 'simulate':
            raise RemoteExecutionError('Remote MD root lacks its native orchestration contract')
        ngs_completion_path = None
        if job.model_id == 'nanopore':
            from services.ont_ngs_completion import validate_and_prepare_remote_ont_completion

            resource_receipt = None
            if _record_native_resource_execution(job, status):
                resource_path = native_incoming / '.bms-resource-usage.json'
                try:
                    if (not resource_path.is_symlink() and resource_path.is_file()
                            and resource_path.stat().st_size <= 256 * 1024):
                        resource_receipt = json.loads(resource_path.read_bytes())
                except (OSError, ValueError):
                    # Optional observations cannot decide scientific completion.
                    # The native owner still rejects unbound/malformed telemetry.
                    resource_receipt = None
            completion = await validate_and_prepare_remote_ont_completion(job,
                attempt_id=expected_attempt_id, output_root=native_incoming,
                manifest=native_manifest, resource_usage_receipt=resource_receipt)
            ngs_completion_path = completion['completion_path']
        else:
            # Domain-native NGS validation above owns its receipts and lifecycle;
            # generic stage receipts never substitute for that completion proof.
            validate_remote_stage_receipts(output_root=native_incoming, job_id=str(job.id),
                attempt_id=expected_attempt_id, manifest=native_manifest)
        try:
            local_artifact_root, previous_generation = await asyncio.to_thread(
                _publish_result_generation, job, incoming)
        except (OSError, RemoteExecutionError) as exc:
            raise RemoteCollectionPending(str(exc)) from exc
        local_output = _safe_result_path(local_artifact_root, relative.as_posix(), allow_root=True)
        if projection_context is not None:
            from services.result_ingester import ingest_component_projection
            await ingest_component_projection(job, str(local_artifact_root), session,
                expected_context=projection_context)
        receipt["published_output_dir"] = str(local_output)
        receipt["previous_generation_quarantine"] = (
            str(previous_generation) if previous_generation is not None else None
        )
        provenance = dict(job.provenance or {}, remote_execution_receipt=receipt)
        job.provenance = provenance
        from services.remote_stage_receipts import apply_remote_stage_receipts

        # Native MD/NGS completion owns lifecycle; other consumers use the
        # same authenticated stage-format adapter as local execution.
        if job.model_id not in {"molecular_dynamics", "nanopore"}:
            await apply_remote_stage_receipts(
                session=session, job=job, attempt_id=expected_attempt_id,
                output_root=local_output, manifest=native_manifest,
            )
        await session.flush()
        if job.model_id == "nanopore" and ngs_completion_path != "shared_native_import":
            job.completed_at = job.completed_at or datetime.utcnow()
        elif job.model_id == "msa_batch":
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
                else ("failed", "failed", "ingested") if result.integrity_state == "no_candidates"
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
                from services.nextflow import maybe_trigger_mutation_seed_refinement

                schedule_viewer_minimum_analyses_for_job(job_id)
                await maybe_trigger_mutation_seed_refinement(job, session)
            return True
        if job.model_id in {'molecular_dynamics', 'nanopore'} and job.status == 'completed':
            from services.result_state_integrity import finalize_component_projection
            await finalize_component_projection(job, session)
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


async def cancel_local_result_transfer(job: Job, *, timeout: float = 5.0, guard_owned: bool = False) -> bool:
    """Stop the manifest-addressed controller writer; absence alone is not proof.

    Called after durable cancellation intent. Recovery already holding the
    controller guard passes guard_owned=True. A live producer must either expose
    its supervisor or release that guard before absence can mean quiescence.
    """
    from .result_generation import staging_path, prepare_transfer
    from .transport import cancel_owned_transfer

    receipt = (job.provenance or {}).get('remote_execution_receipt') or {}
    digest = receipt.get('result_manifest_sha256')
    if not digest:
        return True  # no terminal manifest has authorized a result download
    try:
        incoming = staging_path(job, digest)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            stopped = await cancel_owned_transfer(incoming, timeout=max(0.01, deadline - asyncio.get_running_loop().time()))
            if stopped is True:
                return True
            if guard_owned:
                prepare_transfer(incoming)
                return True
            with _controller_attempt_guard(str(job.id)) as owned:
                if owned:
                    prepare_transfer(incoming)
                    return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            # Bounded startup/guard handoff, not polling scientific Job state.
            await asyncio.sleep(0.05)
    except (OSError, ValueError):
        return False


async def cancel_remote_job(job: Job, *, graceful_timeout_seconds: float = 30.0, guard_owned: bool = False) -> bool:
    if not job.execution_target_id or not job.remote_attempt_id:
        return False
    local_quiescent = await cancel_local_result_transfer(job, timeout=min(5.0, graceful_timeout_seconds), guard_owned=guard_owned)
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
        # Terminal state alone is not proof all attempt writers stopped.
        return local_quiescent and status.state in TERMINAL_REMOTE_STATES and getattr(status, "quiescent", False)


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
