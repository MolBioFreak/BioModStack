"""Scratch SQLite native preparation/replay; no inference or queue submission."""
import copy
import json

import pytest
from sqlalchemy import func, select

import experiment_services as owners
from experiment_models import (
    ExperimentAggregateHead, ExperimentLaunchContext, ExperimentRevision,
    ExperimentWorkflowDraft, ExperimentWorkflowPreparation, ExperimentWorkflowPlanAuthority,
    ExperimentWorkflowSetupContext,
)
from schemas import JobCreate
from services.global_experiments import workflow_setups
from test_project_workflow_setups import setup_store, _project


async def setup_native(factory, tmp_path):
    target = tmp_path / 'target.fasta'
    target.write_text('>target\n' + 'A' * 60 + '\n')
    request = JobCreate(name='Native BC2', model_id='bindcraft2', mode='campaign', params={
        'bc2_preview_digest': owners.sha256_text('inert fixture preview; not GPU acceptance'),
        'bindcraft2_settings': {'max_trajectories': 1, 'modality': ['binder'],
            'targets': [{'name': 'target', 'target_path': str(target)}]},
    }).model_dump(mode='json')
    async with factory() as db:
        project = await _project(db)
        setup = await workflow_setups.create_workflow_setup(db, project_id=project.id,
            relationship_kind='primary', global_experiment_id=None, experiment_name='BC2',
            experiment_objective='Design', domain_kind='protein_in_silico',
            capability_id='protein.native.bindcraft2.campaign', idempotency_key='create')
        await workflow_setups.save_workflow_setup_draft(db, project_id=project.id,
            setup_context_id=setup['setup_context_id'], expected_generation=0,
            draft={'native_job_request': request, 'collapsed': False, 'nullable': None},
            idempotency_key='save')
        await db.commit()
    return setup, request


async def prepare(factory, setup, key, generation=1):
    async with factory() as db:
        result = await workflow_setups.prepare_workflow_setup_launch(db,
            project_id=setup['project_id'], setup_context_id=setup['setup_context_id'],
            expected_generation=generation, idempotency_key=key)
        await db.commit()
        return result


def snapshot(row):
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}


@pytest.mark.asyncio
async def test_repeat_native_prepare_reuses_revision_and_preserves_immutable_requests(setup_store, tmp_path, monkeypatch):
    from datetime import timedelta
    from services.global_experiments import launch_contexts
    setup, request = await setup_native(setup_store, tmp_path)
    first = await prepare(setup_store, setup, 'first')
    async with setup_store() as db:
        old = await db.get(ExperimentWorkflowPreparation, first['preparation_id'])
        old_snapshot = snapshot(old)
        context_snapshot = snapshot(await db.get(ExperimentLaunchContext, first['launch_context_id']))
        assert json.loads(old.scheduler_payload_json)['params']['bindcraft2_settings'] == request['params']['bindcraft2_settings']
        workflow_id = first['diagnostics']['workflow_id']
        authority_snapshot = snapshot(await db.get(ExperimentWorkflowPlanAuthority, workflow_id))
    after_expiry = launch_contexts._parse_timestamp(context_snapshot['expires_at']) + timedelta(seconds=1)
    monkeypatch.setattr(launch_contexts, '_now', lambda: after_expiry)
    second = await prepare(setup_store, setup, 'second')
    assert second['generation'] == first['generation'] == 1
    assert second['launch_context_id'] != first['launch_context_id']
    assert second['preparation_id'] != first['preparation_id']
    assert await prepare(setup_store, setup, 'first') == first
    assert await prepare(setup_store, setup, 'second') == second
    async with setup_store() as db:
        old = await db.get(ExperimentWorkflowPreparation, first['preparation_id'])
        new = await db.get(ExperimentWorkflowPreparation, second['preparation_id'])
        assert snapshot(old) == old_snapshot
        assert snapshot(await db.get(ExperimentLaunchContext, first['launch_context_id'])) == context_snapshot
        assert snapshot(await db.get(ExperimentWorkflowPlanAuthority, workflow_id)) == authority_snapshot
        assert old.workflow_revision_id == new.workflow_revision_id
        assert old.scheduler_payload_json == new.scheduler_payload_json
        await owners.validate_preparation_authority(db, old)
        await owners.validate_preparation_authority(db, new)
        old_context = await db.get(ExperimentLaunchContext, first['launch_context_id'])
        new_context = await db.get(ExperimentLaunchContext, second['launch_context_id'])
        with pytest.raises(launch_contexts.LaunchContextError) as expired:
            launch_contexts._ensure_live(old_context)
        assert expired.value.code == 'launch_context_expired'
        launch_contexts._ensure_live(new_context)
        bound_request = JobCreate.model_validate({**json.loads(new.scheduler_payload_json),
            'launch_context_id': new_context.launch_context_id})
        assert await launch_contexts.validate_prepared_child_job_request(db, new_context, bound_request)
        head = await db.get(ExperimentAggregateHead, workflow_id)
        assert head.head_generation == 1
        assert await db.scalar(select(func.count()).select_from(ExperimentRevision).where(ExperimentRevision.subject_id == workflow_id)) == 1
        assert await db.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 2
        assert await db.scalar(select(func.count()).select_from(ExperimentLaunchContext)) == 2


@pytest.mark.asyncio
async def test_edited_native_science_cannot_silently_replay_old_plan(setup_store, tmp_path):
    setup, request = await setup_native(setup_store, tmp_path)
    first = await prepare(setup_store, setup, 'first')
    edited = copy.deepcopy(request)
    edited['params']['bindcraft2_settings']['max_trajectories'] = 2
    async with setup_store() as db:
        await workflow_setups.save_workflow_setup_draft(db, project_id=setup['project_id'],
            setup_context_id=setup['setup_context_id'], expected_generation=1,
            draft={'native_job_request': edited}, idempotency_key='edit')
        await db.commit()
    with pytest.raises(owners.RevisionConflict, match='workflow setup generation'):
        await prepare(setup_store, setup, 'stale')
    with pytest.raises(owners.IdempotencyConflict, match='immutable stored authority'):
        await prepare(setup_store, setup, 'changed', generation=2)
    assert await prepare(setup_store, setup, 'first') == first
    async with setup_store() as db:
        detail = await workflow_setups.get_workflow_setup(db, project_id=setup['project_id'], setup_context_id=setup['setup_context_id'])
        assert detail['draft']['native_job_request'] == edited
        assert await db.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 1


@pytest.mark.asyncio
async def test_same_key_different_generation_conflicts(setup_store, tmp_path):
    setup, _ = await setup_native(setup_store, tmp_path)
    await prepare(setup_store, setup, 'first')
    with pytest.raises(owners.IdempotencyConflict):
        await prepare(setup_store, setup, 'first', generation=2)


@pytest.mark.asyncio
async def test_cached_setup_cannot_prepare_over_concurrent_scientific_edit(setup_store, tmp_path):
    setup, request = await setup_native(setup_store, tmp_path)
    async with setup_store() as stale:
        cached = await stale.get(ExperimentWorkflowSetupContext, setup['setup_context_id'])
        assert cached.generation == 1
        edited = copy.deepcopy(request)
        edited['params']['bindcraft2_settings']['max_trajectories'] = 2
        async with setup_store() as editor:
            await workflow_setups.save_workflow_setup_draft(editor, project_id=setup['project_id'],
                setup_context_id=setup['setup_context_id'], expected_generation=1,
                draft={'native_job_request': edited}, idempotency_key='concurrent-edit')
            await editor.commit()
        assert cached.generation == 1  # Deliberately stale identity map.
        with pytest.raises(owners.RevisionConflict, match='expected 1, current 2'):
            await workflow_setups.prepare_workflow_setup_launch(stale,
                project_id=setup['project_id'], setup_context_id=setup['setup_context_id'],
                expected_generation=1, idempotency_key='stale-prepare')
        await stale.rollback()
    fresh = await prepare(setup_store, setup, 'fresh-prepare', generation=2)
    async with setup_store() as db:
        preparation = await db.get(ExperimentWorkflowPreparation, fresh['preparation_id'])
        assert json.loads(preparation.scheduler_payload_json)['params']['bindcraft2_settings'] == edited['params']['bindcraft2_settings']
        assert await db.scalar(select(func.count()).select_from(ExperimentWorkflowPreparation)) == 1


@pytest.mark.asyncio
async def test_revision_reuse_cas_rejects_real_second_writer_head_edit(setup_store, tmp_path):
    setup, _ = await setup_native(setup_store, tmp_path)
    first = await prepare(setup_store, setup, 'first')
    workflow_id = first['diagnostics']['workflow_id']
    async with setup_store() as stale:
        head = await stale.get(ExperimentAggregateHead, workflow_id)
        draft = await stale.scalar(select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == workflow_id))
        original_revision_id = draft.base_revision_id
        assert head.head_generation == 1
        async with setup_store() as editor:
            current_draft = await editor.scalar(select(ExperimentWorkflowDraft).where(ExperimentWorkflowDraft.workflow_id == workflow_id))
            payload = json.loads(current_draft.canonical_payload)
            payload['scheduler']['name'] = 'Concurrent editor name'
            await owners.save_workflow_draft(editor, workflow_id, payload, expected_generation=current_draft.generation)
            successor = await owners.save_workflow_revision(editor, workflow_id, expected_head_generation=1)
            successor_id = successor.resource_id
            await editor.commit()
        assert head.head_generation == 1 and draft.base_revision_id == original_revision_id
        with pytest.raises(owners.RevisionConflict, match='expected 1, current 2') as raised:
            await owners.save_workflow_revision(stale, workflow_id, expected_head_generation=1,
                reuse_current_revision=True)
        assert raised.value.__cause__ is None  # Actual CAS failure, not a duplicate INSERT.
        await stale.rollback()
    async with setup_store() as db:
        head = await db.get(ExperimentAggregateHead, workflow_id)
        assert head.head_generation == 2 and head.current_revision_id == successor_id
        assert await db.scalar(select(func.count()).select_from(ExperimentRevision).where(ExperimentRevision.subject_id == workflow_id)) == 2


@pytest.mark.asyncio
async def test_mutable_workflow_scientific_edit_creates_distinct_revision(setup_store):
    async with setup_store() as db:
        project = await _project(db)
        setup = await workflow_setups.create_workflow_setup(db, project_id=project.id,
            relationship_kind='primary', global_experiment_id=None, experiment_name='Fold',
            experiment_objective='Predict', domain_kind='protein_in_silico',
            capability_id='protein.structure_prediction.esmfold2', idempotency_key='create')
        await workflow_setups.save_workflow_setup_draft(db, project_id=project.id,
            setup_context_id=setup['setup_context_id'], expected_generation=0,
            draft={'sequence': 'MQIFVK'}, idempotency_key='save')
        await db.commit()
    first = await prepare(setup_store, setup, 'first')
    async with setup_store() as db:
        await workflow_setups.save_workflow_setup_draft(db, project_id=setup['project_id'],
            setup_context_id=setup['setup_context_id'], expected_generation=1,
            draft={'sequence': 'AAAAAA'}, idempotency_key='edit')
        await db.commit()
    second = await prepare(setup_store, setup, 'second', generation=2)
    async with setup_store() as db:
        old = await db.get(ExperimentWorkflowPreparation, first['preparation_id'])
        new = await db.get(ExperimentWorkflowPreparation, second['preparation_id'])
        assert old.workflow_revision_id != new.workflow_revision_id
        assert json.loads(old.scheduler_payload_json)['params']['sequence'] == 'MQIFVK'
        assert json.loads(new.scheduler_payload_json)['params']['sequence'] == 'AAAAAA'
        await owners.validate_preparation_authority(db, old)
        await owners.validate_preparation_authority(db, new)
