"""Saved-Job cache prewarm and independent managed image/weight provisioning.

No queue insertion, Job updates, input transfers, inference or automatic resume.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from dataclasses import dataclass, field, replace
import hashlib
import json
from types import SimpleNamespace
import uuid

from sqlalchemy import func, select, update
from database import ExecutionTarget, Job
from .bundle import current_source_identity
from .contracts import (PreloadProgress, ProvisionRequest, ProvisionSelection, CachedArtifactReceipt,
    WorkflowProvisionRequest, WorkflowProvisionSelection)
from .progress import PRELOAD_ACTIVE_PHASES, preload_idle_clause
from .targets import (ExecutionTargetError, INVENTORY_MAX_AGE_SECONDS, get_target,
    inventory_fresh, _target_response, _has_nonterminal_jobs)
from .transport import RemoteConnection


SAFE_FAILURE_REASONS = frozenset({
    "Worker identity or activity changed during preload",
    "Saved recipe changed during preload",
    "Source identity changed during preload",
    "Cache source verification failed",
    "Remote transport timed out",
    "Remote SSH host key changed",
    "Prewarm source identity does not match current committed source",
})


def failure_message(exc, phase):
    if isinstance(exc, asyncio.CancelledError):
        return "Preload interrupted; explicitly retry"
    reason = str(exc)
    if reason in SAFE_FAILURE_REASONS:
        return f"{reason}; explicitly retry"
    return f"Preload failed during {phase}; verify worker and recipe, then explicitly retry"


def recipe_snapshot(job):
    return SimpleNamespace(**{column.key: deepcopy(getattr(job, column.key))
        for column in Job.__table__.columns})


def recipe_digest(job):
    # Digest only, never expose or transfer biological values/provenance.
    values = {key: getattr(job, key, None) for key in (
        "id", "model_id", "mode", "params", "provenance", "output_dir", "child_output_dir",
        "execution_source_revision", "execution_source_tree")}
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def compile_recipe(job, *, native_invocations=None) -> list[str]:
    from services.nextflow import build_job_nextflow_command, _build_msa_batch_command
    output = job.child_output_dir or job.output_dir
    if not output:
        raise ExecutionTargetError("Saved Job has no authoritative output identity")
    if job.model_id == "msa_batch":
        return _build_msa_batch_command(deepcopy(job.params), output)
    return build_job_nextflow_command(job, deepcopy(job.params), output,
        materialize_inputs=False, native_invocations=native_invocations)


def endpoint(target):
    return (target.host, target.port, target.username, target.remote_root, target.host_key_sha256)


@dataclass(frozen=True)
class TargetSnapshot:
    """Only immutable authority needed by hashing, transport and admission."""
    id: str
    host: str
    port: int
    username: str
    remote_root: str
    host_key_sha256: str
    capabilities: dict = field(default_factory=dict)
    managed_inventory: object | None = None

    @classmethod
    def capture(cls, target):
        from .managed_inventory import project_inventory
        return cls(target.id, *endpoint(target), deepcopy(target.capabilities or {}),
                   deepcopy(project_inventory(target)))


def admission_clause(target):
    now = datetime.utcnow()
    return (
        (ExecutionTarget.id == target.id) & ExecutionTarget.active.is_(True)
        & (ExecutionTarget.state == "ready") & ExecutionTarget.leased_job_id.is_(None)
        & preload_idle_clause()
        & ~select(ExecutionTarget.id).where(ExecutionTarget.state == "probing").exists()
        & (ExecutionTarget.host == target.host) & (ExecutionTarget.port == target.port)
        & (ExecutionTarget.username == target.username) & (ExecutionTarget.remote_root == target.remote_root)
        & (ExecutionTarget.host_key_sha256 == target.host_key_sha256)
        & (ExecutionTarget.provider_metadata["inventory"]["status"].as_string() == "complete")
        & ExecutionTarget.provider_metadata["inventory"]["present"].as_boolean().is_(True)
        & ExecutionTarget.provider_metadata["inventory"]["running"].as_boolean().is_(True)
        & (ExecutionTarget.provider_metadata["inventory"]["checked_at"].as_string() >=
            (now - timedelta(seconds=INVENTORY_MAX_AGE_SECONDS)).isoformat())
        & (ExecutionTarget.provider_metadata["inventory"]["checked_at"].as_string() <= now.isoformat())
    )


class PreloadController:
    def __init__(self, session_factory, *, prewarm=None, quiesce=None):
        self.session_factory = session_factory
        self.prewarm = prewarm
        self.quiesce = quiesce
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.closed = False
        self.preview_slots = asyncio.Semaphore(2)

    async def _compile_native(self, selection, http_request, session):
        from schemas import JobCreate
        if selection.kind != "workflow" or isinstance(selection.workflow_request, JobCreate):
            return None
        from services.nextflow import compile_native_workflow_provision_request
        try:
            return await compile_native_workflow_provision_request(selection.workflow_request, http_request, session)
        except ValueError as exc:
            raise ExecutionTargetError("Native runtime preview unavailable; preview again with valid controls") from exc

    async def preview(self, session, target_id, selection, *, http_request=None):
        compiled_plan = await self._compile_native(selection, http_request, session)
        target = TargetSnapshot.capture(await get_target(session, target_id))
        await session.rollback()
        try:
            preview, _ = await self._preview(selection, target, compiled_plan=compiled_plan)
            return preview
        except Exception as exc:
            raise ExecutionTargetError("Runtime preview unavailable; verify reviewed model and managed assets") from exc

    async def _preview(self, selection, target, *, compiled_plan=None):
        from .cache import independent_preview
        # Cancellation must not release a slot while its hashing thread runs.
        async with self.preview_slots:
            kwargs = {'compiled_plan': compiled_plan} if compiled_plan is not None else {}
            task = asyncio.create_task(asyncio.to_thread(independent_preview, selection, target, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except Exception as exc:
                raise ExecutionTargetError("Runtime preview unavailable; verify reviewed model and managed assets") from exc

    async def start(self, session, target_id, request, *, retry_operation_id=None, http_request=None):
        async with self.lock:
            if self.closed:
                raise ExecutionTargetError("Preload service is stopping")
            target = await get_target(session, target_id)
            previous = (target.provider_metadata or {}).get("preload", {})
            if retry_operation_id is not None:
                if (previous.get("operation_id") != retry_operation_id
                        or previous.get("phase") not in {"failed", "cancelled"}
                        or previous.get("recovery_required")):
                    raise ExecutionTargetError("Provision recovery requires confirmed remote quiescence before retry")
            if (not target.active or target.state != "ready" or not inventory_fresh(target)
                    or target.leased_job_id or await _has_nonterminal_jobs(session, target_id)):
                raise ExecutionTargetError("Preload requires an idle attached worker with current inventory")
            target = TargetSnapshot.capture(target)
            independent = isinstance(request, (ProvisionRequest, WorkflowProvisionRequest))
            selection = (WorkflowProvisionSelection(kind="workflow", workflow_request=request.workflow_request)
                if isinstance(request, WorkflowProvisionRequest) else
                ProvisionSelection(kind=request.kind, model_id=request.model_id) if independent else None)
            snapshot, command = None, None
            native_invocation = None
            compiled_plan = None
            if independent:
                compiled_plan = await self._compile_native(selection, http_request, session)
                await session.rollback()
                preview, _ = await self._preview(selection, target, compiled_plan=compiled_plan)
                if preview.preview_sha256 != request.preview_sha256:
                    raise ExecutionTargetError("Provision preview changed; preview again")
                if preview.blockers:
                    raise ExecutionTargetError("Provision preview has unresolved dependency blockers")
                digest = preview.preview_sha256
                revision, tree = await asyncio.to_thread(current_source_identity)
            else:
                job = await session.get(Job, request.job_id)
                if job is None:
                    raise ExecutionTargetError("Saved recipe Job does not exist")
                snapshot = recipe_snapshot(job)
                digest = recipe_digest(snapshot)
                await session.rollback()
                try:
                    revision, tree = await asyncio.to_thread(current_source_identity)
                    if snapshot.execution_source_revision and (snapshot.execution_source_revision,
                            snapshot.execution_source_tree) != (revision, tree):
                        raise ExecutionTargetError("Saved Job source differs from current source; choose a current recipe")
                    native_invocations = []
                    command = await asyncio.to_thread(compile_recipe, snapshot,
                        native_invocations=native_invocations)
                    if len(native_invocations) != 1:
                        raise ExecutionTargetError("Saved Job did not compile one native invocation")
                    native_invocation = native_invocations[0]
                    identity = native_invocation.source_identity
                    if identity is None or (identity.revision, identity.tree) != (revision, tree):
                        raise ExecutionTargetError("Prewarm source identity does not match current committed source")
                except ExecutionTargetError:
                    raise
                except Exception as exc:
                    raise ExecutionTargetError("Saved Job cannot compile a cache recipe on this source") from exc
            operation_id = str(uuid.uuid4())
            now = datetime.utcnow()
            progress = PreloadProgress(operation_id=operation_id, job_id=None if independent else request.job_id, selection=selection,
                source_revision=revision, source_tree=tree, request_sha256=digest,
                phase="checking", message="Checking source and runtime cache", started_at=now, updated_at=now,
                endpoint_sha256=hashlib.sha256(json.dumps(endpoint(target)).encode()).hexdigest(),
                artifact_progress=[dict(r.model_dump(), state="pending") for r in preview.artifacts] if independent else [])
            retry_fence = ([ExecutionTarget.provider_metadata["preload"]["operation_id"].as_string() == retry_operation_id]
                           if retry_operation_id is not None else [])
            admitted = await session.execute(update(ExecutionTarget).where(admission_clause(target), *retry_fence).values(
                provider_metadata=func.json_set(ExecutionTarget.provider_metadata, "$.preload",
                    func.json(progress.model_dump_json()), '$.managed_inventory.refresh_failed',
                    func.json('true'))).execution_options(synchronize_session=False))
            if admitted.rowcount != 1:
                await session.rollback()
                raise ExecutionTargetError("Worker inventory, endpoint, or activity changed; refresh and retry")
            await session.commit()
            connection = RemoteConnection.from_target(target)
            expected_endpoint = endpoint(target)
            self.tasks[operation_id] = asyncio.create_task(self._run(target_id, progress, snapshot,
                command, connection, expected_endpoint,
                native_invocation=native_invocation, compiled_plan=compiled_plan), name=f"preload-{operation_id}")
            response = _target_response(await get_target(session, target_id))
            await session.rollback()
            return response

    async def _publish(self, session, target_id, progress, expected_endpoint=None, managed=None):
        metadata = func.json_set(ExecutionTarget.provider_metadata, "$.preload", func.json(progress.model_dump_json()),
            "$.preload.sequence", func.coalesce(ExecutionTarget.provider_metadata["preload"]["sequence"].as_integer(), 0) + 1)
        if managed is not None:
            metadata = func.json_set(metadata, "$.managed_inventory", func.json(json.dumps(managed)),
                "$.managed_boot_id", managed['observation']['boot_id'])
        if progress.selection is not None and progress.phase == "source_download_ready":
            target = await get_target(session, target_id)
            if expected_endpoint is None or endpoint(target) != expected_endpoint:
                raise ExecutionTargetError("Worker identity or activity changed during preload")
            observed = dict(operation_id=progress.operation_id,
                selection=progress.selection.model_dump(mode="json"), observed_at=progress.updated_at.isoformat(),
                artifacts=[r.model_dump() for r in progress.artifacts],
                endpoint_sha256=hashlib.sha256(json.dumps(endpoint(target)).encode()).hexdigest())
            metadata = func.json_set(metadata, "$.artifact_inventory", func.json(json.dumps(observed)))
        fence = []
        if expected_endpoint is not None:
            host, port, username, root, key = expected_endpoint
            fence = [ExecutionTarget.host == host, ExecutionTarget.port == port,
                ExecutionTarget.username == username, ExecutionTarget.remote_root == root,
                ExecutionTarget.host_key_sha256 == key, ExecutionTarget.active.is_(True),
                ExecutionTarget.state == "ready", ExecutionTarget.leased_job_id.is_(None)]
        if not progress.cancel_requested:
            fence.append(func.coalesce(ExecutionTarget.provider_metadata["preload"]["cancel_requested"].as_boolean(), False).is_(False))
        changed = await session.execute(update(ExecutionTarget).where(
            *fence, ExecutionTarget.id == target_id,
            ExecutionTarget.provider_metadata["preload"]["operation_id"].as_string() == progress.operation_id,
            ExecutionTarget.provider_metadata["preload"]["phase"].as_string().in_(PRELOAD_ACTIVE_PHASES),
        ).values(provider_metadata=metadata).execution_options(synchronize_session=False))
        await session.commit()
        if changed.rowcount != 1:
            raise ExecutionTargetError("Preload operation was superseded")

    async def _run(self, target_id, progress, snapshot, command, connection, expected_endpoint,
                   *, native_invocation=None, compiled_plan=None):
        remote_started = False
        try:
            connection = replace(connection, provision_operation_id=progress.operation_id)
            async def check_fence():
                async with self.session_factory() as session:
                    target = await get_target(session, target_id)
                    inventory = (target.provider_metadata or {}).get("inventory", {})
                    current = (target.provider_metadata or {}).get("preload", {})
                    if current.get("cancel_requested"):
                        raise asyncio.CancelledError()
                    if (not target.active or target.state != "ready" or not inventory_fresh(target)
                            or inventory.get("present") is not True or inventory.get("running") is not True
                            or target.leased_job_id or endpoint(target) != expected_endpoint
                            or current.get("operation_id") != progress.operation_id
                            or current.get("phase") not in PRELOAD_ACTIVE_PHASES):
                        raise ExecutionTargetError("Worker identity or activity changed during preload")
                    if snapshot is not None:
                        job = await session.get(Job, snapshot.id, populate_existing=True)
                        if job is None or recipe_digest(job) != progress.request_sha256:
                            raise ExecutionTargetError("Saved recipe changed during preload")
                if await asyncio.to_thread(current_source_identity) != (progress.source_revision, progress.source_tree):
                    raise ExecutionTargetError("Source identity changed during preload")

            async def publish(event):
                await check_fence()
                # Validate the closed projection; raw stderr/path/command never enters UI.
                updated = progress.model_copy(update={**event, "updated_at": datetime.utcnow()})
                validated = PreloadProgress.model_validate(updated.model_dump())
                if validated.phase not in {"checking", "transferring", "verifying"}:
                    raise ExecutionTargetError("Cache callback supplied a terminal phase")
                progress.phase, progress.artifact, progress.message = validated.phase, validated.artifact, validated.message
                progress.updated_at = validated.updated_at
                progress.artifact_progress = validated.artifact_progress
                async with self.session_factory() as session:
                    await self._publish(session, target_id, progress, expected_endpoint=expected_endpoint)

            await check_fence()
            managed = None
            if progress.selection is not None:
                from .cache import provision_cache
                from .managed_inventory import (manifest_for, helper_call, activate_release,
                    observe_releases, endpoint_digest, saved_manifests, run_native_readiness_check)
                async with self.session_factory() as session:
                    current_target = await get_target(session, target_id)
                    target = TargetSnapshot.capture(current_target)
                    manifests = saved_manifests(current_target)
                preview, entries = await self._preview(progress.selection, target, compiled_plan=compiled_plan)
                if preview.preview_sha256 != progress.request_sha256:
                    raise ExecutionTargetError("Provision preview changed; preview again")
                remote_started = True
                boot = (await helper_call(connection, {'action': 'boot'}, check_fence))['boot_id']
                manifest = manifest_for(progress.selection, entries, (progress.source_revision, progress.source_tree))
                await helper_call(connection, dict(action='admit', manifest=manifest, boot_id=boot), check_fence)
                artifacts = await provision_cache(connection=connection, entries=entries,
                    operation_id=progress.operation_id, progress=publish, check_fence=check_fence)
                await activate_release(connection, manifest, check_fence, publish, boot)
                manifests = [m for m in manifests if m['selection'] != manifest['selection']] + [manifest]
                observed = await observe_releases(connection, manifests, check_fence)
                if observed.critical_runtime_ready and any(
                        a.get('kind') == 'runtime_image' and a['name'] == 'containers/rfantibody.sif'
                        for a in manifest['artifacts']):
                    await publish(dict(phase='verifying', artifact=None,
                        message='Native RFantibody CUDA/DGL preflight; no inference'))
                    observed = await run_native_readiness_check(connection, manifest, check_fence,
                        observed=observed, manifests=manifests)
                if str(observed.boot_id) != boot:
                    raise ExecutionTargetError('Worker identity or activity changed during preload')
                managed = dict(manifests=manifests, observation=observed.model_dump(mode='json'),
                               endpoint_sha256=endpoint_digest(target))
                receipt = dict(source_revision=progress.source_revision, source_tree=progress.source_tree,
                    artifacts=artifacts)
            else:
                prewarm = self.prewarm
                if prewarm is None:
                    from .cache import prewarm_cache
                    prewarm = prewarm_cache
                remote_started = True
                receipt = await prewarm(connection=connection, job=snapshot, command=command,
                    native_invocation=native_invocation,
                    source_revision=progress.source_revision, source_tree=progress.source_tree,
                    operation_id=progress.operation_id, progress=publish, check_fence=check_fence)
            await check_fence()
            if (receipt.get("source_revision"), receipt.get("source_tree")) != (progress.source_revision, progress.source_tree):
                raise ExecutionTargetError("Cache source verification failed")
            progress.artifacts = [CachedArtifactReceipt.model_validate(row) for row in receipt.get("artifacts", [])]
            from .contracts import ProvisionArtifactProgress
            progress.artifact_progress = [ProvisionArtifactProgress(**r.model_dump(), state="verified") for r in progress.artifacts]
            progress.phase = "source_download_ready"
            progress.artifact = None
            progress.message = "Source and cacheable runtime downloads verified; launch still prepares support Python and verifies scientific readiness"
            if progress.selection is not None:
                progress.message = "Selected managed assets activated and inventory refreshed; scientific readiness remains unverified"
            progress.updated_at = datetime.utcnow()
            async with self.session_factory() as session:
                await self._publish(session, target_id, progress, expected_endpoint=expected_endpoint, managed=managed)
        except BaseException as exc:
            quiet = not remote_started or await self._quiescent(connection, progress.operation_id)
            async with self.session_factory() as session:
                current = (await get_target(session, target_id)).provider_metadata.get("preload", {})
                if current.get("operation_id") == progress.operation_id:
                    progress.cancel_requested = bool(current.get("cancel_requested")) or isinstance(exc, asyncio.CancelledError)
                progress.message = failure_message(exc, progress.phase)
                progress.phase = ("cancelled" if progress.cancel_requested else "failed") if quiet else "recovery_blocked"
                progress.recovery_required = not quiet
                if not quiet:
                    progress.message = "Remote provisioning quiescence unconfirmed; ownership retained, explicit recovery required"
                for row in progress.artifact_progress:
                    if row.state != "verified":
                        row.state = "interrupted"
                progress.artifact = None
                progress.updated_at = datetime.utcnow()
                try:
                    await self._publish(session, target_id, progress)
                except ExecutionTargetError:
                    pass
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            self.tasks.pop(progress.operation_id, None)

    async def _quiescent(self, connection, operation_id):
        # The transport owner may supply bounded remote-process/boot evidence.
        # Missing support is a retained ownership fence, not successful cancel.
        hook = self.quiesce
        if hook is None:
            from . import transport
            hook = getattr(transport, "quiesce_provision", None)
        if hook is None:
            return False
        try:
            return await asyncio.wait_for(hook(connection, operation_id), timeout=30) is True
        except (Exception, asyncio.CancelledError):
            return False

    async def cancel(self, session, target_id, operation_id):
        async with self.lock:
            target = await get_target(session, target_id)
            raw = (target.provider_metadata or {}).get("preload", {})
            if raw.get("operation_id") != operation_id:
                raise ExecutionTargetError("Provision operation was superseded")
            progress = PreloadProgress.model_validate(raw)
            if progress.phase not in PRELOAD_ACTIVE_PHASES:
                return _target_response(target)
            connection = RemoteConnection.from_target(target)
            original_endpoint = progress.endpoint_sha256
            current_endpoint = hashlib.sha256(json.dumps(endpoint(target)).encode()).hexdigest()
            progress.cancel_requested = True
            progress.phase = "cancelling"
            progress.message = "Cancellation requested; awaiting remote provisioning quiescence"
            progress.updated_at = datetime.utcnow()
            await self._publish(session, target_id, progress)
            task = self.tasks.get(operation_id)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # The run task normally publishes its own result. After a restart
            # there is no local task; the same explicit action reconciles it.
            await session.rollback()
            target = await get_target(session, target_id)
            latest = (target.provider_metadata or {}).get("preload", {})
            if latest.get("operation_id") != operation_id:
                raise ExecutionTargetError("Provision operation was superseded")
            progress = PreloadProgress.model_validate(latest)
            await session.rollback()
            if progress.phase in PRELOAD_ACTIVE_PHASES:
                quiet = (original_endpoint is not None and original_endpoint == current_endpoint
                         and await self._quiescent(connection, operation_id))
                progress.phase = "cancelled" if quiet else "recovery_blocked"
                progress.recovery_required = not quiet
                progress.message = ("Remote provisioning stopped; verified cache objects retained for explicit retry" if quiet else
                    "Remote provisioning quiescence unconfirmed; ownership retained, explicit recovery required")
                for artifact in progress.artifact_progress:
                    if artifact.state != "verified":
                        artifact.state = "interrupted"
                progress.artifact = None
                progress.updated_at = datetime.utcnow()
                await self._publish(session, target_id, progress)
            response = _target_response(await get_target(session, target_id))
            await session.rollback()
            return response

    async def refresh_inventory(self, session, target_id):
        from .managed_inventory import observe_releases, endpoint_digest, project_inventory, saved_manifests
        async with self.lock:
            if self.closed:
                raise ExecutionTargetError('Preload service is stopping')
            target = await get_target(session, target_id)
            previous = deepcopy((target.provider_metadata or {}).get('managed_inventory', {}))
            manifests = deepcopy(saved_manifests(target))
            operation = (target.provider_metadata or {}).get('preload', {}).get('operation_id')
            if (not target.active or target.state != 'ready' or not inventory_fresh(target)
                    or target.leased_job_id or await _has_nonterminal_jobs(session, target_id)
                    or (target.provider_metadata or {}).get('preload', {}).get('phase') in PRELOAD_ACTIVE_PHASES):
                raise ExecutionTargetError('Inventory refresh requires an idle attached worker with current provider inventory')
            target = TargetSnapshot.capture(target)
            await session.rollback()
            if not isinstance(previous, dict):
                raise ExecutionTargetError('Managed inventory metadata is invalid; provision again')
            if previous.get('manifests') and previous.get('endpoint_sha256') != endpoint_digest(target):
                raise ExecutionTargetError('Managed inventory belongs to a different worker identity; provision again')

            async def fence():
                async with self.session_factory() as check:
                    row = await get_target(check, target_id)
                    if (endpoint(row) != endpoint(target) or not inventory_fresh(row)
                            or not row.active or row.state != 'ready' or row.leased_job_id
                            or (row.provider_metadata or {}).get('preload', {}).get('operation_id') != operation
                            or (row.provider_metadata or {}).get('preload', {}).get('phase') in PRELOAD_ACTIVE_PHASES):
                        raise ExecutionTargetError('Worker identity or activity changed during inventory observation')
            try:
                observed = await observe_releases(RemoteConnection.from_target(target), manifests, fence)
                payload = dict(manifests=manifests, observation=observed.model_dump(mode='json'),
                               endpoint_sha256=endpoint_digest(target))
                value = func.json_set(ExecutionTarget.provider_metadata,
                    '$.managed_inventory', func.json(json.dumps(payload)), '$.managed_boot_id', str(observed.boot_id))
                changed = await session.execute(update(ExecutionTarget).where(admission_clause(target),
                    ExecutionTarget.provider_metadata['preload']['operation_id'].as_string().is_(operation))
                    .values(provider_metadata=value).execution_options(synchronize_session=False))
                if changed.rowcount != 1:
                    await session.rollback()
                    raise ExecutionTargetError('Worker identity or activity changed during inventory observation')
                await session.commit()
                result = project_inventory(await get_target(session, target_id))
                await session.rollback()
                return result
            except BaseException as exc:
                await session.rollback()
                # Failed readback invalidates freshness immediately; retain prior evidence.
                await session.execute(update(ExecutionTarget).where(admission_clause(target),
                    ExecutionTarget.provider_metadata['preload']['operation_id'].as_string().is_(operation))
                    .values(provider_metadata=func.json_set(ExecutionTarget.provider_metadata,
                        '$.managed_inventory.refresh_failed', func.json('true')))
                    .execution_options(synchronize_session=False))
                await session.commit()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ExecutionTargetError('Managed inventory readback failed; prior observation is stale, explicitly retry') from exc

    async def recover(self):
        async with self.session_factory() as session:
            rows = (await session.scalars(select(ExecutionTarget).where(
                ExecutionTarget.provider_metadata["preload"]["phase"].as_string().in_(PRELOAD_ACTIVE_PHASES)))).all()
            for row in rows:
                progress = PreloadProgress.model_validate(row.provider_metadata["preload"])
                # No remote process observation is inferred from API restart.
                progress.phase = "recovery_blocked"
                progress.recovery_required = True
                for artifact in progress.artifact_progress:
                    if artifact.state != "verified":
                        artifact.state = "interrupted"
                progress.message = "Preload interrupted by service restart; remote quiescence unconfirmed, explicit recovery required"
                progress.updated_at = datetime.utcnow()
                try:
                    await self._publish(session, row.id, progress)
                except ExecutionTargetError:
                    # Another owner already finalized/superseded this operation.
                    continue

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
