"""Retained native review artifacts survive worker teardown and new sessions."""
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update

from component_runtime import ComponentRuntime, ResultReference
from database import Design, ExecutionTarget, Job
from services.remote_execution import executor
from services import nextflow
from scripts.open_stage_gate import open_component_gate, component_checkpoint_projection
from test_local_gate_review_rows import store, PDB


def retained_job(tmp_path, scores=True):
    worker = tmp_path / 'retired-worker'
    candidates = worker / 'candidates'
    candidates.mkdir(parents=True)
    (candidates / 'one.pdb').write_text(PDB)
    validation = worker / 'validation'
    validation.mkdir()
    if scores:
        path = validation / 'protenix_v2/one/native/one/seed_0/predictions/one_summary_confidence_sample_0.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(plddt=0.5, ptm=0.0, ranking_score=0.0, has_clash=False)))
    (worker / 'results').mkdir()
    runtime = ComponentRuntime(worker / 'ledger.sqlite', artifact_root=worker / 'results',
        attempt_id='attempt', root_job_id='source', target_id='old-worker', lease_id='lease',
        source_identity=dict(revision='a'*40, tree='b'*40))
    open_component_gate(runtime, job_id='source', stage='post_structure_validation',
        payload={'framework_type': 'protein_local_redesign'}, directories={'candidate': candidates, 'raw': validation})
    checkpoint = component_checkpoint_projection(runtime)[0]
    out = tmp_path / 'controller/results/source'
    retained = out / '.bms-review-import' / checkpoint['checkpoint_sha256']
    for item in checkpoint['artifacts']:
        ref = ResultReference(**item)
        dest = retained / ref.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ref.resolve(runtime.artifact_root), dest)
    (retained / 'checkpoint-review.json').write_text(json.dumps(checkpoint))
    shutil.rmtree(worker)
    return Job(id='source', name='review', model_id='protein_modification_experimental', mode='region_redesign',
        status='awaiting_input', queue_status='completed', awaiting_input=True, awaiting_stage=checkpoint['stage'],
        execution_target_id='old-worker', remote_attempt_id='attempt', output_dir=str(out),
        params=dict(structure_validators=['protenix_v2'], seed=0, interactive_gating=False,
                    custom_native_setting={'zero': 0, 'off': False, 'null': None}),
        provenance={}, decision_history=[], awaiting_payload={'component_checkpoint': checkpoint}), retained


async def no_transport(*args, **kwargs):
    pytest.fail('retained checkpoint must not contact retired worker')


@pytest.mark.asyncio
@pytest.mark.parametrize('score_transfer_fails', [False, True])
async def test_bounded_transport_receives_scores_without_requiring_them(store, tmp_path, monkeypatch, score_transfer_fails):
    from types import SimpleNamespace
    job, retained = retained_job(tmp_path)
    checkpoint = job.awaiting_payload['component_checkpoint']
    worker = tmp_path / 'transport-worker'
    remote_results = worker / 'attempts/attempt/results'
    remote_results.parent.mkdir(parents=True)
    shutil.move(retained, remote_results)
    calls = []
    async def status(connection, argv, **kwargs):
        assert 'status' in argv
        return SimpleNamespace(stdout=executor.RemoteAttemptStatus(job_id='source', attempt_id='attempt',
            state='awaiting_input', boot_id='boot', quiescent=True, checkpoints=[checkpoint]).model_dump_json())
    async def transfer(connection, remote, destination, selected, **kwargs):
        assert remote == str(remote_results)
        assert kwargs['max_file_bytes'] == 64 * 1024 * 1024
        calls.append(selected)
        if score_transfer_fails and any('/validator-scores/' in p for p in selected):
            raise executor.RemoteTransportError('optional fixture transfer failure')
        for relative in selected:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(remote_results / relative, path)
    monkeypatch.setattr(executor, 'run_remote', status)
    monkeypatch.setattr(executor, 'rsync_selected_from_remote', transfer)
    async with store() as session:
        session.add(job)
        session.add(ExecutionTarget(id='old-worker', provider='vast', provider_instance_id='old',
            host='fixture.invalid', port=22, username='root', remote_root=str(worker)))
        await session.commit()
        assert await executor.publish_remote_review_rows(session, job) == 1
        projected = (await session.scalars(select(Design))).one()
        assert projected.ptm == (None if score_transfer_fails else 0.0)
    assert len(calls) == 2
    assert all('/validator-scores/' in p for p in calls[1])
    shutil.rmtree(worker)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    async with store() as session:
        source = await session.get(Job, 'source')
        assert await executor.retrieve_remote_checkpoint_review(session, source, checkpoint) == retained
        assert await executor.publish_remote_review_rows(session, source) == 1
        row = (await session.scalars(select(Design))).one()
        assert row.ptm == (None if score_transfer_fails else 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing-score', 'corrupt-score', 'corrupt-candidate'])
async def test_optional_scores_never_authorize_or_block_pickup(store, tmp_path, monkeypatch, damage):
    job, retained = retained_job(tmp_path)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    checkpoint = job.awaiting_payload['component_checkpoint']
    schema = 'bms.stage-review.candidate.v1' if damage == 'corrupt-candidate' else 'bms.stage-review.validator-scores.v1'
    ref = next(r for r in checkpoint['artifacts'] if r['schema'] == schema)
    path = retained / ref['relative_path']
    if damage == 'missing-score':
        path.unlink()
    else:
        path.write_text('{"plddt":99,"ptm":1}')
    async with store() as session:
        session.add(job)
        await session.commit()
        if damage == 'corrupt-candidate':
            with pytest.raises((ValueError, RuntimeError)):
                await executor.retrieve_remote_checkpoint_review(session, job, checkpoint)
        else:
            assert await executor.retrieve_remote_checkpoint_review(session, job, checkpoint) == retained
            assert await executor.publish_remote_review_rows(session, job) == 1
            row = (await session.scalars(select(Design))).one()
            assert row.ptm is None and row.plddt_overall == 90.0
            assert job.awaiting_input


@pytest.mark.asyncio
@pytest.mark.parametrize('scores', [False, True])
async def test_retained_review_reopens_after_original_worker_removed(store, tmp_path, monkeypatch, scores):
    job, retained = retained_job(tmp_path, scores)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    async with store() as session:
        session.add(job)
        await session.commit()
    async with store() as session:
        source = await session.get(Job, 'source')
        root = await executor.retrieve_remote_checkpoint_review(session, source, source.awaiting_payload['component_checkpoint'])
        assert root == retained
        assert await executor.publish_remote_review_rows(session, source) == 1
    async with store() as verify:
        row = (await verify.scalars(select(Design))).one()
        assert row.ptm == (0.0 if scores else None)
        assert row.plddt_overall == (0.5 if scores else 90.0)
        source = await verify.get(Job, 'source')
        assert source.status == 'awaiting_input' and source.awaiting_input
        if scores:
            assert Path(source.awaiting_payload['validation_root']).is_relative_to(retained)


@pytest.mark.asyncio
@pytest.mark.parametrize('target', [None, 'new-worker'])
async def test_successor_is_selected_native_job_not_old_runtime(store, tmp_path, monkeypatch, target):
    job, retained = retained_job(tmp_path)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    async with store() as session:
        session.add(job)
        session.add(ExecutionTarget(id='new-worker', provider='vast', provider_instance_id='new', active=True,
            state='ready', provider_metadata={'inventory': dict(status='complete', present=True, running=True,
                                                                checked_at=datetime.utcnow().isoformat())}))
        await session.commit()
    async with store() as session:
        source = await session.get(Job, 'source')
        checkpoint = source.awaiting_payload['component_checkpoint']
        selected = [r['relative_path'] for r in checkpoint['artifacts'] if r['schema'] == 'bms.stage-review.candidate.v1']
        request = await executor.prepare_checkpoint_successor(session, source, checkpoint,
            {'selected_artifacts': selected}, target)
        assert request.execution_target_id == target
        assert request.execution_plan_approval is None
        assert request.params['seed'] == 0 and request.params['interactive_gating'] is False
        assert request.params['custom_native_setting'] == job.params['custom_native_setting']
        assert 'resume_source_dir' not in request.params and 'resume_work_dir' not in request.params
        assert Path(request.params['plr_final_candidate_dir'], 'one.pdb').read_text() == PDB
        manifest = json.loads(Path(request.params['selected_input_manifest']).read_text())
        row = (await session.scalars(select(Design))).one()
        assert manifest['designs'][0]['design_id'] == row.id
        assert manifest['designs'][0]['checkpoint_artifact_sha256'] == checkpoint['artifacts'][0]['sha256']
        # Real native compiler, new root. No old Nextflow cache or runtime required.
        from routers.jobs import _execution_plan_preview
        preview = _execution_plan_preview(request)
        assert preview['request']['execution_target_id'] == target
        invocation = nextflow.compile_nextflow_invocation(request.model_id, request.mode, request.params,
                                                          str(tmp_path / 'fresh-successor'), job_id='successor')
        assert invocation.native_parameters['plr_final_candidate_dir'] == request.params['plr_final_candidate_dir']
        assert not any('retired-worker' in str(arg) for arg in invocation.command)
        successor = Job(id='successor', name=request.name, model_id=request.model_id, mode=request.mode,
                        params=request.params, status='queued', queue_status='queued', execution_target_id=target)
        await executor.claim_checkpoint_successor(session, source, successor, request.params['checkpoint_successor'])
        session.add(successor)
        await session.commit()
        # The selected-input transfer's destination is a distinct fresh result root.
        # Exercise the actual final receiver, not a fabricated Design row.
        from services.result_ingester import ingest_loose_files
        fresh = tmp_path / 'fresh-successor'
        received = fresh / 'selected'
        shutil.copytree(Path(request.params['plr_final_candidate_dir']), received)
        successor.output_dir = str(fresh)
        successor.params = {**successor.params, 'plr_final_candidate_dir': str(received)}
        assert await ingest_loose_files(successor.id, fresh, session, current_job=successor) == 1
        await session.commit()
    async with store() as verify:
        source = await verify.get(Job, 'source')
        assert executor.checkpoint_successor_id(source, request.params['checkpoint_successor']) == 'successor'
        final = (await verify.scalars(select(Design).where(Design.job_id == 'successor'))).one()
        assert Path(final.pdb_path).is_relative_to(tmp_path / 'fresh-successor')
        assert final.parent_design_id == row.id
        assert source.status == 'awaiting_input'
        assert len((await verify.scalars(select(Job))).all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['cancel', 'replace', 'duplicate'])
async def test_successor_cas_rejects_independent_writer(store, tmp_path, monkeypatch, change):
    job, _ = retained_job(tmp_path)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    async with store() as session:
        session.add(job)
        await session.commit()
    async with store() as continuation:
        source = await continuation.get(Job, 'source')
        checkpoint = source.awaiting_payload['component_checkpoint']
        decision = {'selected_artifacts': [r['relative_path'] for r in checkpoint['artifacts']
                                            if r['schema'] == 'bms.stage-review.candidate.v1']}
        request = await executor.prepare_checkpoint_successor(continuation, source, checkpoint, decision, None)
        successor = Job(id='loser', execution_target_id=None)
        async with store() as writer:
            current = await writer.get(Job, 'source')
            if change == 'cancel':
                current.queue_status = 'cancelling'
                current.params = {**current.params, 'cancellation_receipt': {'state': 'requested'}}
            elif change == 'replace':
                current.awaiting_payload = {'component_checkpoint': {'checkpoint_id': 'replacement'}}
            else:
                winner = Job(id='winner', name='winner', model_id=job.model_id, mode=job.mode,
                             status='queued', queue_status='queued', params={}, execution_target_id=None)
                await executor.claim_checkpoint_successor(writer, current, winner, request.params['checkpoint_successor'])
                writer.add(winner)
            await writer.commit()
        with pytest.raises(executor.RemoteExecutionError, match='owner changed'):
            await executor.claim_checkpoint_successor(continuation, source, successor, request.params['checkpoint_successor'])
        await continuation.rollback()
    async with store() as verify:
        assert await verify.get(Job, 'loser') is None
        assert len((await verify.scalars(select(Job))).all()) == (2 if change == 'duplicate' else 1)
