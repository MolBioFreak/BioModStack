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
from schemas import BinderRoundRequest, BinderRoundStepReference, JobCreate, SequenceDesignRequest
from services.sequence_round_inputs import REQUEST as SEQUENCE_REQUEST

REQUEST = 'binder_round_request'
PROGRESS = 'binder_round_progress'
STEP = 'binder_round_step'
logger = logging.getLogger(__name__)


def identity(root_id, stage, design_id, target, sample=0, *, model_id=None):
    # Historical v1 children must keep their exact deterministic identity.
    parts = [root_id, stage, design_id, target, sample]
    version = 1
    if model_id is not None:
        parts.append(model_id)
        version = 2
    value = json.dumps(parts, sort_keys=True, separators=(',', ':'))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'bms:binder-round:v{version}:' + value))


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


async def refresh_children(session, root_id, retained):
    """Recover atomic Job insertions before interpreting dependency progress."""
    children = await session.scalars(select(Job).where(
        Job.provenance[STEP]['root_job_id'].as_string() == root_id).execution_options(populate_existing=True))
    for child in children:
        step = retained['steps'].get(child.provenance[STEP]['step_id'])
        if step is not None:
            step.update(job_id=child.id, state=child.status)
            step.pop('error', None)


def round_state(retained):
    states = {s['state'] for s in retained['steps'].values() if not s.get('superseded_by')}
    dependencies = {d['state'] for d in retained.get('dependencies', {}).values()}
    if retained['errors'] or 'error' in states or 'publication_error' in dependencies:
        return 'needs_retry'
    if 'review_required' in states:
        return 'review_required'
    if states - {'completed', 'failed', 'cancelled'} or dependencies & {'awaiting_predecessor', 'awaiting_publication'}:
        return 'running'
    return 'completed_with_errors' if states & {'failed', 'cancelled'} else 'completed'


async def submit_step(session, experiments, step, *, destination, context):
    """One ordinary Job/Project submission boundary for initial and selected rounds."""
    from routers.jobs import submit_selected_child_jobs
    tasks = BackgroundTasks()
    try:
        if step['metadata'].get('route') == 'boltz_api':
            from services.boltz_api_jobs import queue_boltz_api_job
            metadata = step['metadata']
            if not metadata.get('approved_estimate_fingerprint'):
                return {'state': 'review_required', 'review': {
                    'code': 'boltz_api_estimate_approval_required',
                    'model': metadata['provider_model'], 'provider_input': metadata['provider_input']}}, tasks
            child = await queue_boltz_api_job(session, name=step['request']['name'],
                client_request_id=metadata['step_id'], model=metadata['provider_model'],
                provider_input=metadata['provider_input'],
                approved_estimate_fingerprint=metadata['approved_estimate_fingerprint'],
                local_round_step=metadata)
            return {'state': child.status, 'job_id': child.id}, tasks
        response = await submit_selected_child_jobs(
            [JobCreate.model_validate(step['request'])], tasks, session, experiments,
            destination_launch_context_id=destination,
            idempotency_key=step['metadata']['step_id'], response_context=context)
        outcome = {'state': 'queued', 'job_id': response[0].id}
    except HTTPException as exc:
        if exc.status_code == 409 and isinstance(exc.detail, dict) and exc.detail.get('code') == 'remote_prepared_job_review_required':
            outcome = {'state': 'review_required', 'review': exc.detail}
            prepared = exc.detail.get('job_request') or exc.detail.get('job_requests', [None])[0]
            if prepared:
                outcome['request'] = prepared
        else:
            await session.rollback()
            outcome = {'state': 'error', 'error': str(exc.detail)}
    except asyncio.CancelledError:
        await session.rollback()
        await experiments.rollback()
        raise
    except Exception as exc:
        logger.exception('Round submission failed for %s', context)
        await session.rollback()
        outcome = {'state': 'error', 'error': str(exc)}
    return outcome, tasks


async def read_round(session, root_id, *, round_id=None):
    root = await session.get(Job, root_id)
    if root is None:
        raise HTTPException(404, 'Binder round Job not found')
    result = progress_of(root)
    provenance = root.provenance or {}
    envelope = provenance.get(SEQUENCE_REQUEST) or provenance.get(REQUEST)
    if envelope is None:
        result['state'] = 'not_requested'
    elif not envelope['enabled']:
        result['state'] = 'generation_only'
    elif root.status in {'failed', 'cancelled'} and not result['steps']:
        result.update(state=root.status, outcome='no_follow_on')
    result.update(job_id=root.id, binder_round=provenance.get(REQUEST),
                  sequence_design=provenance.get(SEQUENCE_REQUEST),
                  kind='general_sequence_design' if SEQUENCE_REQUEST in provenance else 'binder')
    # Observational readback only, including cancellation/failure before a
    # scheduler tick. Never materialize, submit or compute in GET.
    children = await session.scalars(select(Job).where(Job.provenance[STEP]['root_job_id'].as_string() == root_id))
    for child in children:
        step_id = child.provenance[STEP]['step_id']
        if step_id in result['steps']:
            result['steps'][step_id].update(job_id=child.id, state=child.status)
    from services.binder_selected_round import read_selected
    result['selected_rounds'] = await read_selected(session, root, round_id=round_id)
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
        step = next((r['steps'][reference.step_id] for r in progress.get('selected_rounds', [])
                     if reference.step_id in r['steps']), None)
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
    if root.status == 'cancelled' and not step['metadata'].get('round_id'):
        raise HTTPException(409, 'Binder round was cancelled')
    return step['metadata'], preallocated_id if isinstance(preallocated_id, str) else reference.step_id, None


async def _plan_invocations(session, root, progress, request):
    """Initial outputs enter the same invocation compiler as selected outputs."""
    from schemas import SelectedRoundRequest, ExecutionPolicy
    from services import binder_selected_round as selected
    if 'request' not in progress:
        submitted = SelectedRoundRequest(operation='round', source_job_id=root.id,
            idempotency_key='initial', round_plan=request.round_plan,
            execution_target_id=root.execution_target_id,
            launch_context_id=(root.provenance or {}).get('launch_context_id'),
            execution_policy=ExecutionPolicy.from_params(root.params))
        progress.update(round_id='initial:' + root.id, source_job_id=root.id, root_job_id=root.id,
            submitted_request=submitted.model_dump(mode='json'),
            request=selected.normalize(submitted).model_dump(mode='json'),
            selection={'candidate_refs': [], 'resolved': []}, resolved_outputs={})
    # Publication failures are observations on this source, not empty success.
    dependency = progress.setdefault('dependencies', {}).setdefault('initial-publication', {})
    if dependency.get('state') in {'resolved_outputs', 'valid_empty'} and not progress['errors']:
        await selected.plan(session, root, progress)
        return
    try:
        refs = await selected.output_refs(session, root)
    except FileNotFoundError:
        dependency['state'] = 'awaiting_publication'
        return
    except (ValueError, OSError, HTTPException) as exc:
        dependency.update(state='publication_error', error=str(exc))
        return
    dependency.update(state='resolved_outputs' if refs else 'valid_empty')
    dependency.pop('error', None)
    rows = progress['selection']['resolved']
    for ref in refs:
        key = selected.digest(ref)
        if any(row['ref'] == ref for row in rows):
            continue
        try:
            rows.append(await selected.resolve_ref(session, root, ref, output_format='native'))
            progress['selection']['candidate_refs'].append(ref)
            progress['errors'].pop(key, None)
        except (ValueError, OSError, HTTPException) as exc:
            progress['errors'][key] = str(exc)
    await selected.plan(session, root, progress)


async def _plan(session, root, progress, request, *, retry=False):
    if isinstance(request, SequenceDesignRequest) and request.schema_version == 2:
        return await _plan_invocations(session, root, progress, request)
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
        if metadata.get('round_id'):
            continue
        for design in await session.scalars(select(Design).where(Design.job_id == child.id).order_by(Design.id)):
            sources.append((child, design, metadata['backbone_design_id'], metadata))
    general = isinstance(request, SequenceDesignRequest)
    if general:
        sources = [item for item in sources if item[0].id == root.id]
    targets = None
    for owner, design, backbone_id, prior in sources:
        if design.id in progress['errors'] and not retry:
            continue
        try:
            is_backbone = True if general else requires_design(owner, design)
            stage = 'sequence_design' if is_backbone else 'prediction'
            binder, target = ([], []) if general else (
                candidate_roles(owner, design, request) if prior is None else (
                    list(prior['binder_chains']), list(prior['target_chains'])))
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
            multi = not general and request.schema_version == 2
            stages = [request] if general else (request.design_stages if is_backbone else request.prediction_stages)
            for settings in stages:
                samples = settings.params.get('num_parallel_jobs', 1) or 1
                for target_source in stage_targets:
                    base_request = None
                    binding = {}
                    for sample in range(samples):
                        step_id = identity(root.id, stage, design.id, target_source, sample,
                                model_id=settings.model_id if multi else None)
                        error_key = step_id if multi else design.id
                        if error_key in progress['errors'] and not retry:
                            continue
                        if step_id in progress['steps']:
                            continue
                        try:
                            metadata = {'schema_version': 1, 'step_id': step_id, 'stage': stage,
                                'root_job_id': root.id, 'source_job_id': owner.id, 'source_design_id': design.id,
                                'backbone_design_id': backbone_id,
                                'candidate_key': (prior or {}).get('candidate_key') or
                                    (design.provenance or {}).get('candidate_key') or
                                    (design.provenance or {}).get('producer_candidate_key'),
                                'target_state': target_source.get('name') if target_source else None,
                                'binder_chains': binder, 'target_chains': target, 'sample_index': sample}
                            if multi:
                                metadata.update(model_id=settings.model_id,
                                    designer_model_id=settings.model_id if is_backbone else (prior or {}).get('model_id'))
                            if base_request is None:
                                # Resolve immutable inputs only when this pair needs work.
                                # Outputs/step identity stay private to every child below.
                                if general:
                                    from services.sequence_round_inputs import design_request as general_design_request
                                    base_request, binding = general_design_request(root, owner, design, request)
                                elif is_backbone:
                                    base_request = design_request(root, owner, design, request, binder, target, settings=settings)
                                else:
                                    base_request, binding = prediction_request(root, owner, design, request, binder, target, target_source, settings=settings)
                            child_request = base_request.model_copy(deep=True)
                            metadata.update(deepcopy(binding))
                            child_request.name = f'binder-round-{step_id}'
                            child_request.params['num_parallel_jobs'] = 1
                            child_request.binder_round_step = BinderRoundStepReference(root_job_id=root.id, step_id=step_id)
                            child_request = normalize_job_request(child_request)
                            progress['steps'][step_id] = {'metadata': metadata,
                                'request': child_request.model_dump(mode='json'), 'state': 'prepared', 'job_id': None}
                            progress['errors'].pop(error_key, None)
                        except (ValueError, OSError, HTTPException) as exc:
                            if not multi:
                                raise
                            progress['errors'][error_key] = f'{settings.model_id}: {exc.detail if isinstance(exc, HTTPException) else exc}'
            progress['errors'].pop(design.id, None)
        except (ValueError, OSError, HTTPException) as exc:
            progress['errors'][design.id] = str(exc.detail if isinstance(exc, HTTPException) else exc)


async def reconcile_round(session, experiment_session, root_id, *, retry=False):
    """Recovery tick; generation completion and native result rows are immutable."""
    root = await lock_root(session, root_id)
    envelope = (root.provenance or {}).get(SEQUENCE_REQUEST) or (root.provenance or {}).get(REQUEST)
    if envelope is None:
        await session.rollback()
        return await read_round(session, root_id)
    progress = progress_of(root)
    if not retry and progress['state'] in {'completed', 'completed_with_errors', 'cancelled', 'generation_only'}:
        await session.rollback()
        return await read_round(session, root_id)
    await refresh_children(session, root_id, progress)
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
            root.status if root.status in {'failed', 'cancelled'} else 'waiting_generation')
        save(root, progress)
        await session.commit()
        return await read_round(session, root_id)
    request_type = SequenceDesignRequest if SEQUENCE_REQUEST in (root.provenance or {}) else BinderRoundRequest
    await _plan(session, root, progress, request_type.model_validate(envelope), retry=retry)
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
        outcome, tasks = await submit_step(session, experiment_session, step,
            destination=(root.provenance or {}).get('launch_context_id'),
            context={'root_job_id': root_id, 'binder_round_step_id': step_id})
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
    await refresh_children(session, root_id, progress)
    await _plan(session, root, progress, request_type.model_validate(envelope), retry=retry)
    progress['state'] = round_state(progress)
    save(root, progress)
    await session.commit()
    return await read_round(session, root_id)


async def reconcile_selected(session, experiments, root_id, round_id, *, retry=False):
    from services import binder_round as rounds
    from services.binder_selected_round import KEY, plan, read_selected, output_refs
    root = await lock_root(session, root_id)
    progress = rounds.progress_of(root)
    retained = next((r for r in progress.get(KEY, []) if r['round_id'] == round_id), None)
    if retained is None:
        raise HTTPException(404, 'Selected round not found')
    if not retry and retained['state'] in {'completed', 'completed_with_errors'}:
        await session.rollback()
        root = await session.get(Job, root_id)
        return (await read_selected(session, root, round_id=round_id))[0]
    await rounds.refresh_children(session, root_id, retained)
    if retry:
        retained.pop('outputs_retained', None)
        retained['errors'] = {}
        for old_id, step in list(retained['steps'].items()):
            child = await session.get(Job, step['job_id']) if step.get('job_id') else None
            if step.get('superseded_by') or child is None or child.status not in {'failed', 'cancelled'}:
                continue
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bms:selected-round:retry:' + old_id))
            replacement = deepcopy(step)
            replacement.update(state='prepared', job_id=None, outputs=[])
            for key in ('review', 'error'):
                replacement.pop(key, None)
            replacement['metadata'].update(step_id=identity, retry_of_step_id=old_id, retry_of_job_id=child.id)
            replacement['request'].update(name=f'binder-round-{identity}', launch_context_id=None,
                execution_plan_approval=None, binder_round_step={'root_job_id': root_id, 'step_id': identity})
            for key in ('workflow_adapter', '_global_resource_admission', '_global_dispatch_authority'):
                replacement['request']['params'].pop(key, None)
            step['superseded_by'] = identity
            retained['steps'][identity] = replacement
    await plan(session, root, retained)
    rounds.save(root, progress)
    await session.commit()
    for identity in list(retained['steps']):
        root = await rounds.lock_root(session, root_id)
        progress = rounds.progress_of(root)
        retained = next(r for r in progress[KEY] if r['round_id'] == round_id)
        step = retained['steps'][identity]
        child = await session.scalar(select(Job).where(Job.provenance[rounds.STEP]['root_job_id'].as_string() == root_id,
            Job.provenance[rounds.STEP]['step_id'].as_string() == identity))
        if child:
            step.update(job_id=child.id, state=child.status)
            rounds.save(root, progress)
            await session.commit()
            continue
        if step.get('superseded_by') or step['state'] == 'review_required' or (step['state'] == 'error' and not retry):
            await session.rollback()
            continue
        outcome, tasks = await rounds.submit_step(session, experiments, step,
            destination=retained['request']['launch_context_id'],
            context={'operation': 'round', 'root_job_id': root_id, 'round_id': round_id,
                     'binder_round_step_id': identity, 'source_job_id': retained['source_job_id']})
        root = await rounds.lock_root(session, root_id)
        progress = rounds.progress_of(root)
        retained = next(r for r in progress[KEY] if r['round_id'] == round_id)
        retained['steps'][identity].update(outcome)
        rounds.save(root, progress)
        await session.commit()
        await tasks()
    root = await rounds.lock_root(session, root_id)
    progress = rounds.progress_of(root)
    retained = next(r for r in progress[KEY] if r['round_id'] == round_id)
    await rounds.refresh_children(session, root_id, retained)
    # A predecessor may have completed during submission. Retain its newly
    # expanded successors now; prepared children remain scheduler-recoverable.
    await plan(session, root, retained)
    retained['state'] = rounds.round_state(retained)
    if retained['state'] in {'completed', 'completed_with_errors'}:
        # Freeze leaf output references too, so terminal GET never needs source
        # bytes. Missing leaf publication is an observation, not a new gate.
        for step in retained['steps'].values():
            if not step.get('job_id') or step.get('outputs'):
                continue
            child = await session.get(Job, step['job_id'])
            try:
                step['outputs'] = await output_refs(session, child)
            except (ValueError, OSError, HTTPException) as exc:
                step['publication_error'] = str(exc.detail if isinstance(exc, HTTPException) else exc)
        retained['outputs_retained'] = True
    rounds.save(root, progress)
    await session.commit()
    return (await read_selected(session, root, round_id=round_id))[0]


async def recover_rounds(session_factory, experiment_session_factory):
    """Existing scheduler recovery, including remote-return completion."""
    from sqlalchemy import func, or_
    from services.analysis_autorun import schedule_viewer_minimum_analyses_for_job
    async with session_factory() as session:
        roots = list(await session.scalars(select(Job.id).where(
            Job.status == 'completed', or_(
                Job.provenance[REQUEST]['enabled'].as_boolean().is_(True),
                Job.provenance[SEQUENCE_REQUEST]['enabled'].as_boolean().is_(True)),
            func.coalesce(Job.provenance[PROGRESS]['state'].as_string(), 'pending').not_in(
                ['completed', 'completed_with_errors', 'cancelled', 'generation_only']))))
        predictions = list(await session.scalars(select(Job.id).where(
            Job.status == 'completed', Job.provenance[STEP]['stage'].as_string() == 'prediction')))
    for child_id in predictions:
        try:
            schedule_viewer_minimum_analyses_for_job(child_id)
        except Exception:
            logger.exception('Binder prediction analysis recovery failed for %s', child_id)
    async with session_factory() as session:
        selected = [(job.id, r['round_id']) for job in await session.scalars(select(Job).where(
            Job.provenance[PROGRESS]['selected_rounds'].as_string().is_not(None)))
            for r in progress_of(job).get('selected_rounds', [])
            if r['state'] not in {'completed', 'completed_with_errors'}]
    from services.binder_selected_round import reconcile
    for root_id, round_id in selected:
        try:
            async with session_factory() as session, experiment_session_factory() as experiments:
                await reconcile(session, experiments, root_id, round_id)
        except Exception:
            logger.exception('Selected binder round recovery failed for %s', round_id)
    for root_id in roots:
        try:
            async with session_factory() as session, experiment_session_factory() as experiments:
                await reconcile_round(session, experiments, root_id)
        except Exception:
            logger.exception('Binder round recovery failed for %s', root_id)
