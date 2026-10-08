"""Explicit cache-only operations using an authoritative saved Job as recipe.

No queue insertion, Job updates, input transfers, or automatic restart/resume.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
import uuid

from sqlalchemy import func, select, update
from database import ExecutionTarget, Job
from .bundle import current_source_identity
from .contracts import PreloadProgress
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


def compile_recipe(job):
    from services.nextflow import build_job_nextflow_command, _build_msa_batch_command
    output = job.child_output_dir or job.output_dir
    if not output:
        raise ExecutionTargetError("Saved Job has no authoritative output identity")
    if job.model_id == "msa_batch":
        return _build_msa_batch_command(deepcopy(job.params), output)
    return build_job_nextflow_command(job, deepcopy(job.params), output)


def endpoint(target):
    return (target.host, target.port, target.username, target.remote_root, target.host_key_sha256)


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
    def __init__(self, session_factory, *, prewarm=None):
        self.session_factory = session_factory
        self.prewarm = prewarm
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.closed = False

    async def start(self, session, target_id, request):
        async with self.lock:
            if self.closed:
                raise ExecutionTargetError("Preload service is stopping")
            target = await get_target(session, target_id)
            if (not target.active or target.state != "ready" or not inventory_fresh(target)
                    or target.leased_job_id or await _has_nonterminal_jobs(session, target_id)):
                raise ExecutionTargetError("Preload requires an idle attached worker with current inventory")
            job = await session.get(Job, request.job_id)
            if job is None:
                raise ExecutionTargetError("Saved recipe Job does not exist")
            snapshot = recipe_snapshot(job)
            digest = recipe_digest(snapshot)
            try:
                revision, tree = await asyncio.to_thread(current_source_identity)
                if snapshot.execution_source_revision and (snapshot.execution_source_revision,
                        snapshot.execution_source_tree) != (revision, tree):
                    raise ExecutionTargetError("Saved Job source differs from current source; choose a current recipe")
                command = await asyncio.to_thread(compile_recipe, snapshot)
            except ExecutionTargetError:
                raise
            except Exception as exc:
                raise ExecutionTargetError("Saved Job cannot compile a cache recipe on this source") from exc
            operation_id = str(uuid.uuid4())
            now = datetime.utcnow()
            progress = PreloadProgress(operation_id=operation_id, job_id=request.job_id,
                source_revision=revision, source_tree=tree, request_sha256=digest,
                phase="checking", message="Checking source and runtime cache", started_at=now, updated_at=now)
            admitted = await session.execute(update(ExecutionTarget).where(admission_clause(target)).values(
                provider_metadata=func.json_set(ExecutionTarget.provider_metadata, "$.preload",
                    func.json(progress.model_dump_json()))).execution_options(synchronize_session=False))
            if admitted.rowcount != 1:
                await session.rollback()
                raise ExecutionTargetError("Worker inventory, endpoint, or activity changed; refresh and retry")
            await session.commit()
            connection = RemoteConnection.from_target(target)
            expected_endpoint = endpoint(target)
            self.tasks[operation_id] = asyncio.create_task(self._run(target_id, progress, snapshot,
                command, connection, expected_endpoint), name=f"preload-{operation_id}")
            await session.refresh(target)
            return _target_response(target)

    async def _publish(self, session, target_id, progress):
        changed = await session.execute(update(ExecutionTarget).where(
            ExecutionTarget.id == target_id,
            ExecutionTarget.provider_metadata["preload"]["operation_id"].as_string() == progress.operation_id,
            ExecutionTarget.provider_metadata["preload"]["phase"].as_string().in_(PRELOAD_ACTIVE_PHASES),
        ).values(provider_metadata=func.json_set(ExecutionTarget.provider_metadata, "$.preload",
            func.json(progress.model_dump_json()))).execution_options(synchronize_session=False))
        await session.commit()
        if changed.rowcount != 1:
            raise ExecutionTargetError("Preload operation was superseded")

    async def _run(self, target_id, progress, snapshot, command, connection, expected_endpoint):
        try:
            async with self.session_factory() as session:
                async def check_fence():
                    await session.rollback()
                    target = await get_target(session, target_id)
                    inventory = (target.provider_metadata or {}).get("inventory", {})
                    current = (target.provider_metadata or {}).get("preload", {})
                    if (not target.active or target.state != "ready" or not inventory_fresh(target)
                            or inventory.get("present") is not True or inventory.get("running") is not True
                            or target.leased_job_id or endpoint(target) != expected_endpoint
                            or current.get("operation_id") != progress.operation_id
                            or current.get("phase") not in PRELOAD_ACTIVE_PHASES):
                        raise ExecutionTargetError("Worker identity or activity changed during preload")
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
                    if validated.phase not in PRELOAD_ACTIVE_PHASES:
                        raise ExecutionTargetError("Cache callback supplied a terminal phase")
                    progress.phase, progress.artifact, progress.message = validated.phase, validated.artifact, validated.message
                    progress.updated_at = validated.updated_at
                    await self._publish(session, target_id, progress)

                await check_fence()
                prewarm = self.prewarm
                if prewarm is None:
                    from .cache import prewarm_cache
                    prewarm = prewarm_cache
                receipt = await prewarm(connection=connection, job=snapshot, command=command,
                    source_revision=progress.source_revision, source_tree=progress.source_tree,
                    operation_id=progress.operation_id, progress=publish, check_fence=check_fence)
                await check_fence()
                if (receipt.get("source_revision"), receipt.get("source_tree")) != (progress.source_revision, progress.source_tree):
                    raise ExecutionTargetError("Cache source verification failed")
                progress.phase = "source_download_ready"
                progress.artifact = None
                progress.message = "Source and cacheable runtime downloads verified; launch still prepares support Python and verifies scientific readiness"
                progress.updated_at = datetime.utcnow()
                await self._publish(session, target_id, progress)
        except BaseException as exc:
            async with self.session_factory() as session:
                progress.message = failure_message(exc, progress.phase)
                progress.phase = "failed"
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

    async def recover(self):
        async with self.session_factory() as session:
            rows = (await session.scalars(select(ExecutionTarget).where(
                ExecutionTarget.provider_metadata["preload"]["phase"].as_string().in_(PRELOAD_ACTIVE_PHASES)))).all()
            for row in rows:
                progress = PreloadProgress.model_validate(row.provider_metadata["preload"])
                progress.phase = "failed"
                progress.message = "Preload interrupted by service restart; explicitly retry"
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
