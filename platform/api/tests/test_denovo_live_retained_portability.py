"""Opt-in replay of hash-verified live artifacts; never native inference.

Run with BMS_LIVE_PORTABILITY_EVIDENCE pointing to a private immutable capture.
The completed live Jobs are NOT mutated: checkpoint cases reconstruct their
historical review boundary only inside the isolated fixture SQLite database.
"""
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from database import Job, Design, ExecutionTarget
from services.remote_execution import executor
from services import nextflow
from test_project_manager_adapters import stores
from test_denovo_api_final import api
from test_denovo_portable_checkpoint import no_transport

LANES = ['mpnn', 'fampnn', 'ligand', 'caliby', 'proton']


def capture(lane):
    location = os.environ.get('BMS_LIVE_PORTABILITY_EVIDENCE')
    if not location:
        pytest.skip('requires independently captured live artifact evidence')
    base = Path(location)
    live = json.loads((base / (lane + '-job.json')).read_text())
    gate = json.loads((base / lane / 'gate.json').read_text())
    assert live['status'] == 'completed' and live['remote_state'] == 'ingested'
    cp = {**gate['checkpoint'], 'checkpoint_sha256': gate['checkpoint_sha256'],
          'stage': 'post_structure_validation'}
    for ref in cp['artifacts']:
        data = (base / lane / ref['relative_path']).read_bytes()
        assert len(data) == ref['size_bytes']
        assert hashlib.sha256(data).hexdigest() == ref['sha256']
    return base / lane, live, cp


def write_result(lane, kind, result):
    base = Path(os.environ['BMS_LIVE_PORTABILITY_EVIDENCE'])
    (base / f'{lane}-{kind}-result.json').write_text(json.dumps(result, indent=2))


@pytest.mark.asyncio
@pytest.mark.parametrize('lane', LANES)
async def test_live_bytes_cold_second_round(api, monkeypatch, lane):
    client, core, root = api
    base, live, cp = capture(lane)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    refs = [r for r in cp['artifacts'] if r['schema'] == 'bms.stage-review.candidate.v1']
    chosen = refs[0]
    path = root / 'inputs' / Path(chosen['relative_path']).name
    shutil.copyfile(base / chosen['relative_path'], path)
    # Exact candidate bytes retain MODEL/author/residue/insertion fields.
    assert hashlib.sha256(path.read_bytes()).hexdigest() == chosen['sha256']
    body = dict(sources=[{'path': 'inputs/' + path.name, 'expected_sha256': chosen['sha256']}],
                design_chains=live['params'].get('design_chains', 'A'),
                seq_method=live['params']['seq_method'], structure_validators=['protenix_v2'],
                execution_target_id=None)
    # No producer Job or Design exists in this private database.
    response = await client.post('/api/jobs/resequence/from-structures/prepare', json=body)
    assert response.status_code == 200, response.text
    first = response.json()
    source = first['params']['resequence_sources'][0]
    assert source['original_sha256'] == chosen['sha256']
    path.unlink()
    clone = {**body, 'sources': [], 'backbone_input_pdbs': first['params']['backbone_input_pdbs'],
             'retained_params': first['params']}
    response = await client.post('/api/jobs/resequence/from-structures/prepare', json=clone)
    assert response.status_code == 200, response.text
    second = response.json()
    assert second['params']['resequence_sources'][0]['original_sha256'] == chosen['sha256']
    preview = await client.post('/api/jobs/execution-plan/preview', json=second)
    assert preview.status_code == 200 and preview.json()['admissible'], preview.text
    async with core() as session:
        assert not (await session.scalars(select(Job))).all()
    write_result(lane, 'cold', dict(source=source, request=second, preview=preview.json(),
                                   original_candidate_deleted=True, producer_records=0))


@pytest.mark.asyncio
@pytest.mark.parametrize('lane', LANES)
@pytest.mark.parametrize('target', [None, 'new-worker'])
async def test_live_checkpoint_successor_materialization(api, monkeypatch, lane, target):
    client, core, root = api
    base, live, cp = capture(lane)
    monkeypatch.setattr(executor, 'run_remote', no_transport)
    out = root / 'results' / live['id']
    retained = out / '.bms-review-import' / cp['checkpoint_sha256']
    for ref in cp['artifacts']:
        dest = retained / ref['relative_path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base / ref['relative_path'], dest)
    (retained / 'checkpoint-review.json').write_text(json.dumps(cp))
    async with core() as session:
        session.add(Job(id=live['id'], name='ISOLATED historical live checkpoint',
            model_id=live['model_id'], mode=live['mode'], status='awaiting_input', queue_status='completed',
            awaiting_input=True, awaiting_stage=cp['stage'], execution_target_id=live['execution_target_id'],
            remote_attempt_id=cp['attempt_id'], output_dir=str(out), params=live['params'],
            provenance={}, decision_history=[], awaiting_payload={'component_checkpoint': cp}))
        session.add(ExecutionTarget(id='new-worker', provider='vast', provider_instance_id='new', active=True,
            state='ready', provider_metadata={'inventory': dict(status='complete', present=True, running=True,
            checked_at=datetime.utcnow().isoformat())}))
        await session.commit()
    refs = [r for r in cp['artifacts'] if r['schema'] == 'bms.stage-review.candidate.v1']
    body = dict(checkpoint_id=cp['checkpoint_id'], checkpoint_sha256=cp['checkpoint_sha256'],
        checkpoint_decision={'selected_artifacts': [r['relative_path'] for r in refs]}, execution_target_id=target)
    response = await client.post('/api/jobs/' + live['id'] + '/resume', json=body)
    if target:
        assert response.status_code == 409, response.text
        request = response.json()['detail']['job_request']
        preview = await client.post('/api/jobs/execution-plan/preview', json=request)
        assert preview.status_code == 200 and preview.json()['admissible'], preview.text
        request['execution_plan_approval'] = preview.json()['approval_digest']
        response = await client.post('/api/jobs', json=request)
        assert response.status_code == 201, response.text
        successor_id = response.json()['id']
    else:
        assert response.status_code == 200, response.text
        successor_id = response.json()['new_job_id']
    async with core() as session:
        successor = await session.get(Job, successor_id)
        assert successor.execution_target_id == target
        selected = Path(successor.params['plr_final_candidate_dir'])
        for ref in refs:
            assert hashlib.sha256((selected / Path(ref['relative_path']).name).read_bytes()).hexdigest() == ref['sha256']
        assert 'resume_work_dir' not in successor.params and 'resume_source_dir' not in successor.params
        invocation = nextflow.compile_nextflow_invocation(successor.model_id, successor.mode,
            successor.params, str(root / 'fresh-native'), job_id=successor_id)
    # Use ordinary cleanup: successor-owned selected bytes must be preserved,
    # not destroyed by an arbitrary recursive unlink of another owner's files.
    deletion = await client.delete('/api/jobs/' + live['id'] + '/permanent')
    assert deletion.status_code == 200, deletion.text
    from services.job_control import finalize_permanent_delete_cleanup
    async with core() as session:
        source = await session.get(Job, live['id'])
        source.remote_state = 'cancelled'
        source.params = {**source.params, 'cancellation_receipt': {
            'state': 'completed', 'remote_stop_verified': True}}
        await session.commit()
        assert await finalize_permanent_delete_cleanup(session, live['id'])
    # Original replayed checkpoint bytes are no longer necessary; only the
    # successor's sealed selection, preserved by normal cleanup, is consumed.
    for ref in cp['artifacts']:
        original = retained / ref['relative_path']
        if original.exists():
            original.unlink()
    assert not any((retained / ref['relative_path']).exists() for ref in cp['artifacts'])
    async with core() as session:
        successor = await session.get(Job, successor_id)
        assert await session.get(Job, live['id']) is None
        assert Path(successor.source_selection_manifest_path).is_file()
        destination = Path(successor.output_dir) / 'final_candidates'
        shutil.copytree(selected, destination)
        from services.result_ingester import ingest_loose_files
        assert await ingest_loose_files(successor.id, Path(successor.output_dir), session, current_job=successor) == len(refs)
        await session.commit()
    async with core() as session:
        rows = (await session.scalars(select(Design).where(Design.job_id == successor_id))).all()
        assert len(rows) == len(refs)
        assert all(Path(r.pdb_path).is_relative_to(destination) for r in rows)
        assert all(r.parent_design_id for r in rows)
    write_result(lane, 'local' if target is None else 'new-target', dict(
        checkpoint_id=cp['checkpoint_id'], checkpoint_sha256=cp['checkpoint_sha256'],
        successor_id=successor_id, target=target, designs=len(rows),
        original_private_producer_deleted=True, native_parameters=invocation.native_parameters,
        resume_body=body, candidate_sha256=[r['sha256'] for r in refs]))
