"""Canonical HTTP callers, private persistence, no live execution or transport."""
import hashlib
import json
from pathlib import Path
from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from database import Job, Design, ExecutionTarget, get_session
from experiment_database import get_experiment_session
from routers import jobs, files
from paths import resolve_allowed_path
from services.remote_execution import executor
from test_project_manager_adapters import stores
from test_denovo_source_custody_closeout import registered_source
from test_denovo_portable_checkpoint import retained_job, no_transport


@pytest_asyncio.fixture
async def api(stores, monkeypatch):
    root, experiments, core = stores
    import paths
    roots = {'inputs': root / 'inputs', 'bms_results': root / 'results'}
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(jobs, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: root / 'inputs')
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: root / 'results')
    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    async def core_dep():
        async with core() as session:
            yield session
    async def experiment_dep():
        async with experiments() as session:
            yield session
    app.dependency_overrides[get_session] = core_dep
    app.dependency_overrides[get_experiment_session] = experiment_dep
    class BoundedClient(httpx.AsyncClient):
        async def request(self, *args, **kwargs):
            import asyncio
            return await asyncio.wait_for(super().request(*args, **kwargs), timeout=30)
    async with BoundedClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        yield client, core, root


@pytest.mark.asyncio
async def test_http_exact_source_cold_clone_sparse_and_cleanup(api):
    client, core, root = api
    async with core() as session:
        request, path, stored, raw, owner, artifact = await registered_source(session, root)
    payload = dict(sources=[request.model_dump(mode='json', exclude_none=True)], design_chains='a',
                   seq_method='ligandmpnn', designer_mode='ligand_aware', designer_settings={'temperature': 0.2},
                   validator_settings={'esmfold2': {'seed': 0}}, structure_validators=['esmfold2'],
                   execution_target_id=None, execution_policy={'remote_result_policy': 'manual'})
    response = await client.post('/api/jobs/resequence/from-structures/prepare', json=payload)
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert prepared.get('execution_target_id') is None
    assert prepared.get('execution_policy') == {'remote_result_policy': 'manual'}
    params = prepared['params']
    source = params['resequence_sources'][0]
    assert source['original_sha256'] == hashlib.sha256(stored).hexdigest()
    assert source['source_identity']['owner_job_id'] == 'producer'
    assert source['model_number'] == 7
    assert source['sha256'] == hashlib.sha256(resolve_allowed_path(source['path']).read_bytes()).hexdigest()
    assert params['esmf_seed'] == 0
    template = Path(params['designer_template']).read_bytes()
    async with core() as session:
        await session.delete(await session.get(type(artifact), artifact.id))
        await session.delete(await session.get(Job, owner.id))
        await session.commit()
    path.unlink()
    clone_payload = {**payload, 'sources': [], 'backbone_input_pdbs': params['backbone_input_pdbs'],
        'retained_params': {**params, 'execution_target_id': 'retired', 'resume_work_dir': 'forbidden',
                            'permanent_delete_cleanup': True}, 'validator_settings': {}}
    clone_payload.pop('designer_settings')
    clone = await client.post('/api/jobs/resequence/from-structures/prepare', json=clone_payload)
    assert clone.status_code == 200, clone.text
    copied = clone.json()['params']
    assert Path(copied['designer_template']).read_bytes() == template
    assert not {'esmf_seed', 'execution_target_id', 'resume_work_dir', 'permanent_delete_cleanup'} & copied.keys()
    assert copied['resequence_sources'][0]['original_sha256'] == source['original_sha256']
    reset = await client.post('/api/jobs/resequence/from-structures/prepare', json={**clone_payload, 'designer_settings': {}})
    assert reset.status_code == 200, reset.text
    assert Path(reset.json()['params']['designer_template']).read_bytes() != template
    for body in (prepared, clone.json(), reset.json()):
        receipt = body['params']['resequence_preparation']
        cleaned = await client.post('/api/jobs/resequence/from-structures/cleanup', json={'preparation': receipt})
        assert cleaned.status_code == 200 and cleaned.json()['cleaned'] is True, cleaned.text
        assert not resolve_allowed_path(receipt['path']).exists()


@pytest.mark.asyncio
async def test_http_bad_source_hash_removes_partial_staging(api):
    client, core, root = api
    async with core() as session:
        request, *_ = await registered_source(session, root)
    body = dict(sources=[{**request.model_dump(mode='json', exclude_none=True), 'expected_sha256': '0'*64}], design_chains='a')
    response = await client.post('/api/jobs/resequence/from-structures/prepare', json=body)
    assert response.status_code == 422, response.text
    assert not list((root / 'inputs/design_selections/protein_local_redesign').glob('resequence*'))


@pytest.mark.asyncio
@pytest.mark.parametrize('target', [None, 'new-worker'])
async def test_http_checkpoint_successor_persist_retry_and_native_conflict(api, monkeypatch, target):
    client, core, root = api
    job, retained = retained_job(root)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    # No bundle is dispatched. Other owners' uncommitted overlays are qualified
    # here; source identity capture is the sole packaging-boundary substitute.
    from services.remote_execution import bundle
    import subprocess
    source_identity = tuple(subprocess.check_output(['git', 'rev-parse', ref], text=True).strip()
                            for ref in ('HEAD', 'HEAD^{tree}'))
    monkeypatch.setattr(bundle, 'current_source_identity', lambda: source_identity)
    async with core() as session:
        session.add(job)
        session.add(ExecutionTarget(id='new-worker', provider='vast', provider_instance_id='new', active=True,
            state='ready', provider_metadata={'inventory': dict(status='complete', present=True, running=True,
                checked_at=datetime.utcnow().isoformat())}))
        await session.commit()
    checkpoint = job.awaiting_payload['component_checkpoint']
    body = dict(checkpoint_id=checkpoint['checkpoint_id'], checkpoint_sha256=checkpoint['checkpoint_sha256'],
        checkpoint_decision={'selected_artifacts': [r['relative_path'] for r in checkpoint['artifacts']
                                                    if r['schema'] == 'bms.stage-review.candidate.v1']},
        execution_target_id=target)
    response = await client.post('/api/jobs/source/resume', json=body)
    if target:
        assert response.status_code == 409, response.text
        review = response.json()['detail']
        assert review['code'] == 'remote_prepared_job_review_required', review
        request = review['job_request']
        preview = await client.post('/api/jobs/execution-plan/preview', json=request)
        assert preview.status_code == 200, preview.text
        assert preview.json()['admissible'], preview.text
        request['execution_plan_approval'] = preview.json()['approval_digest']
        response = await client.post('/api/jobs', json=request)
        assert response.status_code == 201, response.text
        new_id = response.json()['id']
        retry_create = await client.post('/api/jobs', json=request)
        assert retry_create.status_code == 201 and retry_create.json()['id'] == new_id, retry_create.text
        changed = {**request, 'params': {**request['params'], 'seed': 999}}
        assert (await client.post('/api/jobs', json=changed)).status_code == 409
    else:
        assert response.status_code == 200, response.text
        new_id = response.json()['new_job_id']
    async with core() as verify:
        successor = await verify.get(Job, new_id)
        assert successor.execution_target_id == target and successor.parent_job_id is None
        assert successor.vram_estimate_mb == 0 and successor.pinned_gpu is None
        assert successor.params['checkpoint_successor']['source_job_id'] == 'source'
        assert 'resume_work_dir' not in successor.params
        source = await verify.get(Job, 'source')
        assert source.decision_history[-1]['new_job_id'] == new_id
    retry = await client.post('/api/jobs/source/resume', json=body)
    assert retry.status_code == 200 and retry.json()['new_job_id'] == new_id, retry.text
    native = await client.post('/api/jobs/source/resume', json={k:v for k,v in body.items() if k != 'execution_target_id'})
    assert native.status_code == 409, native.text
    async with core() as verify:
        assert len((await verify.scalars(select(Job))).all()) == 2


@pytest.mark.asyncio
async def test_http_deleted_cleanup_owner_invisible_but_referenced(api, monkeypatch):
    client, core, root = api
    from services import job_control
    async def unavailable(*args, **kwargs):
        raise executor.RemoteTransportError('fixture offline')
    monkeypatch.setattr(executor, 'run_remote', unavailable)
    out = root / 'results/deleting'; out.mkdir()
    (out / 'retained.txt').write_text('custody')
    async with core() as session:
        session.add(Job(id='deleting', name='deleting', model_id='protein_modification_experimental', mode='region_redesign',
            status='cancelled', queue_status='cancelled', params={}, output_dir=str(out),
            execution_target_id='gone', remote_state='running', remote_attempt_id='attempt'))
        await session.commit()
    response = await client.delete('/api/jobs/deleting/permanent')
    assert response.status_code == 200, response.text
    assert (await client.get('/api/jobs/deleting')).status_code == 404
    assert (await client.get('/api/jobs/deleting/stages')).status_code == 404
    listing = await client.get('/api/jobs', params={'include_children': True})
    assert listing.status_code == 200, listing.text
    assert listing.json()['total'] == 0
    async with core() as session:
        hidden = await session.get(Job, 'deleting')
        assert hidden is not None, 'permanent deletion erased outstanding stop custody'
        assert hidden.params['permanent_delete_cleanup'] is True
        assert str(out) in await jobs._load_remaining_output_dir_refs(session, set())
    assert (out / 'retained.txt').exists()
    # Existing cleanup owner drains the same hidden row after stop completion.
    async with core() as session:
        hidden = await session.get(Job, 'deleting')
        hidden.remote_state = 'cancelled'
        hidden.params = {**hidden.params, 'cancellation_receipt': {'state': 'completed', 'remote_stop_verified': True}}
        await session.commit()
        assert await job_control.finalize_permanent_delete_cleanup(session, 'deleting')
    async with core() as verify:
        assert await verify.get(Job, 'deleting') is None
    assert not out.exists()


async def prepare_plain(client, core, root):
    from test_local_gate_review_rows import PDB
    path = root / 'inputs/plain.pdb'
    path.write_text(PDB)
    response = await client.post('/api/jobs/resequence/from-structures/prepare', json=dict(
        sources=[{'path': 'inputs/plain.pdb'}], design_chains='A', seq_method='mpnn',
        structure_validators=['esmfold2'], validator_settings={'esmfold2': {'seed': 0}}))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_http_prepared_submission_preserves_custody_and_strips_client_marker(api):
    client, core, root = api
    prepared = await prepare_plain(client, core, root)
    prepared['params']['permanent_delete_cleanup'] = True
    response = await client.post('/api/jobs', json=prepared)
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    assert (await client.get('/api/jobs/' + identifier)).status_code == 200
    async with core() as verify:
        job = await verify.get(Job, identifier)
        assert 'permanent_delete_cleanup' not in job.params
        assert job.params['resequence_sources'] == prepared['params']['resequence_sources']
        assert job.params['esmf_seed'] == 0
    response = await client.post('/api/jobs/resequence/from-structures/cleanup',
                                json={'preparation': prepared['params']['resequence_preparation']})
    assert response.status_code == 200 and response.json()['cleaned'] is False
    assert Path(prepared['params']['backbone_input_pdbs']).is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize('commit', [True, False])
async def test_canonical_uncommitted_submission_holds_source_until_transaction_end(api, commit):
    import asyncio
    from fastapi import BackgroundTasks
    from schemas import JobCreate
    client, core, root = api
    prepared = await prepare_plain(client, core, root)
    async with core() as session:
        response = await jobs.create_job(JobCreate.model_validate(prepared), BackgroundTasks(), session, _commit=False)
        cleanup = asyncio.create_task(client.post('/api/jobs/resequence/from-structures/cleanup',
            json={'preparation': prepared['params']['resequence_preparation']}))
        await asyncio.sleep(0.05)
        assert not cleanup.done(), 'cleanup raced an uncommitted canonical claimant'
        if commit:
            await session.commit()
        else:
            await session.rollback()
        cleaned = await cleanup
        assert cleaned.status_code == 200 and cleaned.json()['cleaned'] is (not commit), cleaned.text
    async with core() as verify:
        assert (await verify.get(Job, response.id) is not None) is commit


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['request', 'approval', 'cancel'])
async def test_http_checkpoint_successor_rejects_stale_request_or_cancel(api, monkeypatch, change):
    client, core, root = api
    job, _ = retained_job(root)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    from services.remote_execution import bundle
    import subprocess
    identity = tuple(subprocess.check_output(['git', 'rev-parse', ref], text=True).strip()
                     for ref in ('HEAD', 'HEAD^{tree}'))
    monkeypatch.setattr(bundle, 'current_source_identity', lambda: identity)
    async with core() as session:
        session.add(job)
        session.add(ExecutionTarget(id='new-worker', provider='vast', provider_instance_id='new', active=True,
            state='ready', provider_metadata={'inventory': dict(status='complete', present=True, running=True,
                checked_at=datetime.utcnow().isoformat())}))
        await session.commit()
    checkpoint = job.awaiting_payload['component_checkpoint']
    body = dict(checkpoint_id=checkpoint['checkpoint_id'], checkpoint_sha256=checkpoint['checkpoint_sha256'],
        checkpoint_decision={'selected_artifacts': [r['relative_path'] for r in checkpoint['artifacts']
                                                    if r['schema'] == 'bms.stage-review.candidate.v1']},
        execution_target_id='new-worker')
    prepared = await client.post('/api/jobs/source/resume', json=body)
    assert prepared.status_code == 409, prepared.text
    request = prepared.json()['detail']['job_request']
    preview = await client.post('/api/jobs/execution-plan/preview', json=request)
    assert preview.status_code == 200 and preview.json()['admissible'], preview.text
    request['execution_plan_approval'] = preview.json()['approval_digest']
    if change == 'request':
        request['params']['seed'] = 999
    elif change == 'approval':
        request['execution_plan_approval'] = '0' * 64
    else:
        cancelled = await client.delete('/api/jobs/source')
        assert cancelled.status_code == 200, cancelled.text
    response = await client.post('/api/jobs', json=request)
    assert response.status_code == 409, response.text
    async with core() as verify:
        assert len((await verify.scalars(select(Job))).all()) == 1
        source = await verify.get(Job, 'source')
        assert source.decision_history == []
        if change == 'cancel':
            assert source.status == 'cancelled'


@pytest.mark.asyncio
async def test_native_resume_refresh_cannot_reuse_a_checkpoint_consumed_by_http_successor(api, monkeypatch):
    client, core, root = api
    job, _ = retained_job(root)
    checkpoint = job.awaiting_payload['component_checkpoint']
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    async with core() as session:
        session.add(job)
        await session.commit()
    async with core() as native:
        stale = await native.get(Job, 'source')
        decision = {'selected_artifacts': [r['relative_path'] for r in checkpoint['artifacts']
                                          if r['schema'] == 'bms.stage-review.candidate.v1']}
        response = await client.post('/api/jobs/source/resume', json=dict(
            checkpoint_id=checkpoint['checkpoint_id'], checkpoint_sha256=checkpoint['checkpoint_sha256'],
            checkpoint_decision=decision, execution_target_id=None))
        assert response.status_code == 200, response.text
        assert stale.decision_history == []
        with pytest.raises(executor.RemoteExecutionError, match='already continued as a successor'):
            await executor.request_remote_checkpoint_resume(native, stale, checkpoint, decision)
        # A native owner whose admission began before the successor also loses
        # its actual publication CAS, rather than overwriting consumed history.
        assert not await executor._publish_remote_transition(native, stale,
            {'status': 'running', 'awaiting_input': False}, require_lease=False, checkpoint_history=[])
    async with core() as verify:
        source = await verify.get(Job, 'source')
        assert source.status == 'awaiting_input' and source.awaiting_input
        assert source.decision_history[-1]['new_job_id'] == response.json()['new_job_id']
