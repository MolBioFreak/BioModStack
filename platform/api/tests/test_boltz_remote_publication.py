"""Terminal GPU lease cleanup must not invalidate sealed native Boltz output."""
import copy
import json

import pytest
from sqlalchemy import select

from database import Design
from services.boltz_scientific_persistence import _verified_publication
from services.execution_ownership import attach_scheduler_gpu_assignment, release_scheduler_gpu_assignment
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication, job, setup


def launched_job(tmp_path, *, user_gpu=None):
    publication(tmp_path, sequence=True)
    path = tmp_path / 'fixture_launch.json'
    params = json.loads(path.read_text())
    if user_gpu is not None:
        params['gpu_id'] = user_gpu
    path.write_text(json.dumps(attach_scheduler_gpu_assignment(params, 0)))
    current = job(tmp_path)
    current.execution_target_id = 'vast:test'
    current.remote_attempt_id = 'remote-attempt'
    current.provenance = dict(current.provenance,
        remote_execution_assignment={'execution_target_id': 'vast:test', 'gpu_index': 0},
        remote_execution_receipt={'execution_target_id': 'vast:test', 'attempt_id': 'remote-attempt',
                                  'generation': 0})
    return current


@pytest.mark.asyncio
@pytest.mark.parametrize('user_gpu', [None, 3])
async def test_publish_after_remote_release_preserves_seal_and_consumer(tmp_path, user_gpu):
    current = launched_job(tmp_path, user_gpu=user_gpu)
    sealed = copy.deepcopy(current.provenance['boltz_launch_authority'])
    before, receipt = _verified_publication(current, tmp_path)
    current.params = release_scheduler_gpu_assignment(current.params)
    released = copy.deepcopy(current.params)
    after, returned = _verified_publication(current, tmp_path)
    assert after == before and returned == receipt
    assert current.params == released
    assert current.provenance['boltz_launch_authority'] == sealed
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session) == 1
            row = (await session.execute(select(Design))).scalar_one()
            assert row.name == next(iter(before))
            assert row.pdb_path == before[row.name]['artifacts']['structure']['path']
            assert await ingest_job_results('job', str(tmp_path), session) == 0
            # Selected scientific reads use the same real verifier after publish.
            selected, _ = _verified_publication(current, tmp_path, document=row.name)
            assert selected[row.name] == before[row.name]
            assert current.params == released
            assert current.provenance['boltz_launch_authority'] == sealed
    finally:
        await engine.dispose()


@pytest.mark.parametrize('damage', [
    'science', 'input', 'user_gpu', 'gpu_assignment', 'target', 'remote_attempt',
    'retry', 'job', 'model', 'mode', 'root', 'workflow', 'task', 'output',
])
def test_released_request_does_not_excuse_true_mismatches(tmp_path, damage):
    current = launched_job(tmp_path)
    current.params = release_scheduler_gpu_assignment(current.params)
    if damage == 'science':
        current.params['boltz_sampling_steps'] = 201
    elif damage == 'input':
        current.params['sequence_input'] = 'CCC'
    elif damage == 'user_gpu':
        current.params['gpu_id'] = 0
    elif damage == 'gpu_assignment':
        current.provenance['remote_execution_assignment']['gpu_index'] = 1
    elif damage == 'target':
        current.execution_target_id = 'vast:other'
    elif damage == 'remote_attempt':
        current.remote_attempt_id = 'other'
    elif damage == 'retry':
        current.retry_count = 1
    elif damage == 'job':
        current.id = 'other'
    elif damage == 'model':
        current.model_id = 'other'
    elif damage == 'mode':
        current.mode = 'complex'
    elif damage == 'root':
        current.output_dir = str(tmp_path / 'other')
    elif damage in {'workflow', 'task'}:
        path = (tmp_path / 'scientific/boltz_workflow_inventory.json' if damage == 'workflow'
                else tmp_path / 'scientific/boltz/sample/boltz_task_binding.json')
        value = json.loads(path.read_text())
        value['attempt'] = 1
        path.write_text(json.dumps(value))
    elif damage == 'output':
        path = next((tmp_path / 'scientific/boltz/sample/predictions').glob('*.pdb'))
        path.write_text('changed coordinates')
    with pytest.raises(ValueError):
        _verified_publication(current, tmp_path)


def test_remote_generation_is_not_scientific_retry(tmp_path):
    current = launched_job(tmp_path)
    current.params = release_scheduler_gpu_assignment(current.params)
    current.provenance['remote_execution_receipt']['generation'] = 1
    assert _verified_publication(current, tmp_path)[0]
    current.retry_count = 1
    with pytest.raises(ValueError, match='job/input binding changed'):
        _verified_publication(current, tmp_path)
