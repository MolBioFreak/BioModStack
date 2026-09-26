"""Durable initial binder rounds on existing Jobs and Project submission owners.

A retained plan precedes submission. Child insertion binds its step provenance in
that same core transaction. Reconciliation repairs a crash between insertion and
progress bookkeeping by reading that provenance. No numerical result lives here.
"""
from __future__ import annotations

from copy import deepcopy
import asyncio
import json
import logging
import uuid

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select, update

from database import Design, Job
from schemas import BinderRoundRequest, BinderRoundStepReference, JobCreate

REQUEST = 'binder_round_request'
PROGRESS = 'binder_round_progress'
STEP = 'binder_round_step'
logger = logging.getLogger(__name__)


def identity(root_id, stage, design_id, target, sample=0):
    value = json.dumps([root_id, stage, design_id, target, sample], sort_keys=True, separators=(',', ':'))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms:binder-round:v1:' + value))


async def lock_root(session, root_id):
    # Same transaction as the following read/write; SQLite obtains its writer
    # lock and PostgreSQL locks this row. No independent nested transaction.
    await session.execute(update(Job).where(Job.id == root_id).values(provenance=Job.provenance))
    root = await session.get(Job, root_id, populate_existing=True)
    if root is None:
        raise HTTPException(404, 'Binder round Job not found')
    return root


def progress_of(root):
    return deepcopy((root.provenance or {}).get(PROGRESS) or
                    {'schema_version': 1, 'state': 'pending', 'steps': {}, 'errors': {}})


def save(root, progress):
    root.provenance = {**(root.provenance or {}), PROGRESS: deepcopy(progress)}


async def read_round(session, root_id):
    root = await session.get(Job, root_id)
    if root is None:
        raise HTTPException(404, 'Binder round Job not found')
    result = progress_of(root)
    if REQUEST not in (root.provenance or {}):
        result['state'] = 'not_requested'
    elif not root.provenance[REQUEST]['enabled']:
        result['state'] = 'generation_only'
    result.update(job_id=root.id, binder_round=(root.provenance or {}).get(REQUEST))
    # Observational readback only, including cancellation/failure before a
    # scheduler tick. Never materialize, submit or compute in GET.
    children = await session.scalars(select(Job).where(Job.provenance[STEP]['root_job_id'].as_string() == root_id))
    for child in children:
        step_id = child.provenance[STEP]['step_id']
        if step_id in result['steps']:
            result['steps'][step_id].update(job_id=child.id, state=child.status)
    return result


async def bind_step(session, request, preallocated_id=None, *, project_bound=False):
    """Validate the opaque retained request at ordinary create/remote approval."""
    reference = request.binder_round_step
    if reference is None:
        return None, preallocated_id, None
    root = await lock_root(session, reference.root_job_id)
    progress = progress_of(root)
    step = progress['steps'].get(reference.step_id)
    if step is None:
        raise HTTPException(409, 'Binder round preparation is unavailable')
    expected = JobCreate.model_validate(step['request']).model_dump(mode='json')
    actual = request.model_dump(mode='json')
    for key in ('execution_plan_approval',):
        expected.pop(key, None)
        actual.pop(key, None)
    if project_bound:
        # The existing Project adapter validates the added workflow/resource
        # binding. All retained scientific keys still have to agree here.
        expected.pop('launch_context_id', None)
        actual.pop('launch_context_id', None)
        for key in ('workflow_adapter', '_global_resource_admission', '_global_dispatch_authority'):
            expected['params'].pop(key, None)
            actual['params'].pop(key, None)
    if expected != actual:
        raise HTTPException(409, 'Binder round retained request changed')
    existing = await session.scalar(select(Job).where(
        Job.provenance[STEP]['root_job_id'].as_string() == root.id,
        Job.provenance[STEP]['step_id'].as_string() == reference.step_id))
    if existing is not None:
        return step['metadata'], existing.id, existing
    if root.status == 'cancelled':
        raise HTTPException(409, 'Binder round was cancelled')
    return step['metadata'], preallocated_id if isinstance(preallocated_id, str) else reference.step_id, None


async def _plan(session, root, progress, request, *, retry=False):
    from services.binder_diagnostic_selection import declared_targets
    from services.binder_round_inputs import candidate_roles, requires_design, design_request, prediction_request
    from routers.jobs import normalize_job_request
    sources = [(root, design, design.id, None) for design in await session.scalars(
        select(Design).where(Design.job_id == root.id).order_by(Design.id))]
    # Only the exact sequence-design owners in this round contribute descendants.
    for child in await session.scalars(select(Job).where(
            Job.provenance[STEP]['root_job_id'].as_string() == root.id,
            Job.provenance[STEP]['stage'].as_string() == 'sequence_design', Job.status == 'completed')):
        metadata = child.provenance[STEP]
        for design in await session.scalars(select(Design).where(Design.job_id == child.id).order_by(Design.id)):
            sources.append((child, design, metadata['backbone_design_id'], metadata))
    targets = None
    for owner, design, backbone_id, prior in sources:
        if design.id in progress['errors'] and not retry:
            continue
        try:
            is_backbone = requires_design(owner, design)
            stage = 'sequence_design' if is_backbone else 'prediction'
            binder, target = candidate_roles(owner, design, request) if prior is None else (
                list(prior['binder_chains']), list(prior['target_chains']))
            if stage == 'prediction' and targets is None:
                targets = await declared_targets(root, session)
                if not targets:
                    raise ValueError('No independently declared target input is available')
            stage_targets = [None] if is_backbone else targets
            if not is_backbone:
                # Native CSV exports belong to explicit producing candidates;
                # do not cross-predict unrelated batch rows or cropped states.
                stage_targets = [t for t in stage_targets if not t.get('source_design_ids')
                                 or backbone_id in t['source_design_ids']]
                if not stage_targets:
                    raise ValueError('No independently declared target input is available for this candidate')
            settings = request.sequence_design if is_backbone else request.prediction
            samples = settings.params.get('num_parallel_jobs', 1) or 1
            for target_source in stage_targets:
                for sample in range(samples):
                    step_id = identity(root.id, stage, design.id, target_source, sample)
                    if step_id in progress['steps']:
                        continue
                    metadata = {'schema_version': 1, 'step_id': step_id, 'stage': stage,
                        'root_job_id': root.id, 'source_job_id': owner.id, 'source_design_id': design.id,
                        'backbone_design_id': backbone_id,
                        'candidate_key': (prior or {}).get('candidate_key') or
                            (design.provenance or {}).get('candidate_key') or
                            (design.provenance or {}).get('producer_candidate_key'),
                        'target_state': target_source.get('name') if target_source else None,
                        'binder_chains': binder, 'target_chains': target, 'sample_index': sample}
                    if is_backbone:
                        child_request = design_request(root, owner, design, request, binder, target)
                    else:
                        child_request, binding = prediction_request(root, owner, design, request, binder, target, target_source)
                        metadata.update(binding)
                    child_request.name = f'binder-round-{step_id}'
                    child_request.params['num_parallel_jobs'] = 1
                    child_request.binder_round_step = BinderRoundStepReference(root_job_id=root.id, step_id=step_id)
                    child_request = normalize_job_request(child_request)
                    progress['steps'][step_id] = {'metadata': metadata,
                        'request': child_request.model_dump(mode='json'), 'state': 'prepared', 'job_id': None}
            progress['errors'].pop(design.id, None)
        except (ValueError, OSError, HTTPException) as exc:
            progress['errors'][design.id] = str(exc.detail if isinstance(exc, HTTPException) else exc)


async def reconcile_round(session, experiment_session, root_id, *, retry=False):
    """Recovery tick; generation completion and native result rows are immutable."""
    from routers.jobs import submit_selected_child_jobs
    root = await lock_root(session, root_id)
    envelope = (root.provenance or {}).get(REQUEST)
    if envelope is None:
        await session.rollback()
        return await read_round(session, root_id)
    progress = progress_of(root)
    if not retry and progress['state'] in {'completed', 'completed_with_errors', 'cancelled', 'generation_only'}:
        await session.rollback()
        return await read_round(session, root_id)
    if retry:
        # Explicit retries create new ordinary Jobs from the retained settings;
        # failed/cancelled observations and their source generation stay intact.
        for old_id, old in list(progress['steps'].items()):
            if old.get('superseded_by'):
                continue
            child = await session.get(Job, old['job_id']) if old.get('job_id') else None
            if child is None or child.status not in {'failed', 'cancelled'}:
                continue
            new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms:binder-round:retry:' + old_id))
            replacement = deepcopy(old)
            replacement.update(state='prepared', job_id=None)
            replacement.pop('review', None)
            replacement.pop('error', None)
            replacement['metadata'].update(step_id=new_id, retry_of_job_id=child.id, retry_of_step_id=old_id)
            replacement['request'].update(name=f'binder-round-{new_id}', launch_context_id=None,
                execution_plan_approval=None, binder_round_step={'root_job_id': root_id, 'step_id': new_id})
            for key in ('workflow_adapter', '_global_resource_admission', '_global_dispatch_authority'):
                replacement['request']['params'].pop(key, None)
            progress['steps'][new_id] = replacement
            old['superseded_by'] = new_id
    if not envelope['enabled'] or root.status != 'completed':
        progress['state'] = 'generation_only' if not envelope['enabled'] else (
            'cancelled' if root.status == 'cancelled' else 'waiting_generation')
        save(root, progress)
        await session.commit()
        return await read_round(session, root_id)
    await _plan(session, root, progress, BinderRoundRequest.model_validate(envelope), retry=retry)
    save(root, progress)
    # Retained input snapshots and requested settings survive a failed submit or
    # cancellation. The existing submission owner is responsible for commits.
    await session.commit()
    for step_id in list(progress['steps']):
        root = await lock_root(session, root_id)
        progress = progress_of(root)
        step = progress['steps'][step_id]
        existing = await session.scalar(select(Job).where(
            Job.provenance[STEP]['root_job_id'].as_string() == root_id,
            Job.provenance[STEP]['step_id'].as_string() == step_id))
        if existing is not None:
            step.update(job_id=existing.id, state=existing.status)
            save(root, progress)
            await session.commit()
            continue
        if root.status != 'completed' or step['state'] == 'review_required' or (step['state'] == 'error' and not retry):
            await session.rollback()
            continue
        tasks = BackgroundTasks()
        try:
            responses = await submit_selected_child_jobs(
                [JobCreate.model_validate(step['request'])], tasks, session, experiment_session,
                destination_launch_context_id=(root.provenance or {}).get('launch_context_id'),
                idempotency_key=step_id,
                response_context={'root_job_id': root_id, 'binder_round_step_id': step_id})
            outcome = {'state': 'queued', 'job_id': responses[0].id}
        except HTTPException as exc:
            if exc.status_code == 409 and isinstance(exc.detail, dict) and exc.detail.get('code') == 'remote_prepared_job_review_required':
                outcome = {'state': 'review_required', 'review': exc.detail}
                retained = exc.detail.get('job_request') or exc.detail.get('job_requests', [None])[0]
                if retained:
                    outcome['request'] = retained
            else:
                outcome = {'state': 'error', 'error': str(exc.detail)}
                await session.rollback()
        except asyncio.CancelledError:
            await session.rollback()
            await experiment_session.rollback()
            raise
        except Exception as exc:
            logger.exception('Binder round submission failed for %s', step_id)
            outcome = {'state': 'error', 'error': str(exc)}
            await session.rollback()
        root = await lock_root(session, root_id)
        progress = progress_of(root)
        progress['steps'][step_id].update(outcome)
        save(root, progress)
        await session.commit()
        # Canonical Jobs are scheduler-visible already. Preserve any ordinary
        # owner's post-commit background work when invoked outside an HTTP route.
        await tasks()
    root = await lock_root(session, root_id)
    progress = progress_of(root)
    states = {item['state'] for item in progress['steps'].values() if not item.get('superseded_by')}
    if progress['errors'] or 'error' in states:
        progress['state'] = 'needs_retry'
    elif 'review_required' in states:
        progress['state'] = 'review_required'
    elif states & {'prepared', 'queued', 'running', 'awaiting_input'}:
        progress['state'] = 'running'
    elif states & {'failed', 'cancelled'}:
        progress['state'] = 'completed_with_errors'
    else:
        progress['state'] = 'completed'
    save(root, progress)
    await session.commit()
    return await read_round(session, root_id)


async def recover_rounds(session_factory, experiment_session_factory):
    """Existing scheduler recovery, including remote-return completion."""
    from sqlalchemy import func
    from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job
    async with session_factory() as session:
        roots = list(await session.scalars(select(Job.id).where(
            Job.status == 'completed', Job.provenance[REQUEST]['enabled'].as_boolean().is_(True),
            func.coalesce(Job.provenance[PROGRESS]['state'].as_string(), 'pending').not_in(
                ['completed', 'completed_with_errors', 'cancelled', 'generation_only']))))
        predictions = list(await session.scalars(select(Job.id).where(
            Job.status == 'completed', Job.provenance[STEP]['stage'].as_string() == 'prediction')))
    for child_id in predictions:
        try:
            schedule_viewer_minimum_analyses_for_job(child_id)
        except Exception:
            logger.exception('Binder prediction analysis recovery failed for %s', child_id)
    for root_id in roots:
        try:
            async with session_factory() as session, experiment_session_factory() as experiments:
                await reconcile_round(session, experiments, root_id)
        except Exception:
            logger.exception('Binder round recovery failed for %s', root_id)
