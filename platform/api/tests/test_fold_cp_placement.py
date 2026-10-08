"""Fold-CP placement through admission, native compiler and real scheduler/SQL.

Target telemetry and launch leaves are fixtures; no worker or GPU execution.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy import update
from database import Job, ExecutionTarget
import routers.jobs as jobs
from services.nextflow import build_nextflow_command
import services.gpu_orchestrator as scheduler
from test_multiworker_scheduling import workers


@pytest.mark.parametrize('ids', [None, '', '0', '0,1', '0,1,2', '0,0,0,0'])
def test_requested_cp4_is_not_reduced_by_admission_or_native_compiler(ids, tmp_path):
    params = dict(sequence='MKTIIALSYIFCLVFADYKDDDDA', bcp_size_cp=4,
                  bcp_gpu_ids=ids, run_frustrampnn=False)
    with pytest.raises(HTTPException) as error:
        jobs._normalize_boltz_cp_params_for_validation('boltz_cp_experimental', params)
    assert error.value.status_code == 422
    assert 'cannot be reduced' in error.value.detail
    with pytest.raises(ValueError, match='cannot be reduced'):
        build_nextflow_command('boltz_cp_experimental', 'design', params, str(tmp_path))
    assert params['bcp_size_cp'] == 4


@pytest.mark.parametrize('key', ['pinned_gpus', 'bcp_gpu_ids', 'gpu_ids'])
def test_four_device_admission_and_native_argv_preserve_science(key, tmp_path):
    params = dict(sequence='MKTIIALSYIFCLVFADYKDDDDA', bcp_size_cp=4,
                  boltz_num_samples=1, boltz_sampling_steps=200, run_frustrampnn=False)
    params[key] = [0, 1, 2, 3] if key == 'pinned_gpus' else '0,1,2,3'
    normalized = jobs._normalize_boltz_cp_params_for_validation('boltz_cp_experimental', params)
    assert (normalized['gpu_ids'], normalized['size_cp']) == ('0,1,2,3', 4)
    command = build_nextflow_command('boltz_cp_experimental', 'design', params, str(tmp_path))
    for flag, value in [('bcp_gpu_ids', '0,1,2,3'), ('bcp_size_cp', '4'),
                        ('bcp_diffusion_samples', '1'), ('bcp_sampling_steps', '200')]:
        assert command[command.index('--'+flag)+1] == value


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['auto', 'one', 'four', 'canonical', 'unavailable', 'missing', 'capacity'])
async def test_remote_cycle_cp4_never_claims_one_gpu(workers, monkeypatch, case):
    from services.remote_execution import targets
    params = dict(bcp_size_cp=4, run_frustrampnn=False)
    if case != 'auto':
        params['gpu_ids' if case == 'canonical' else 'bcp_gpu_ids'] = '0' if case == 'one' else '0,1,2,3'
    async with workers() as session:
        await session.execute(update(Job).values(paused=True))
        job = await session.get(Job, 'job-2')
        job.paused = False
        job.model_id, job.mode, job.params, job.vram_estimate_mb = 'boltz_cp_experimental', 'design', params, 6000
        target = await session.get(ExecutionTarget, 'vast:2')
        target.capabilities = dict(gpu_count=4)
        await session.commit()
    monkeypatch.setattr(scheduler, 'read_scheduler_config', lambda: {'global': {'enabled': True}})
    async def telemetry(target):
        assert target.id == 'vast:2'
        return dict(available=case != 'unavailable', observed_at='fixture', gpus=[
            dict(index=i, uuid=f'GPU-remote-{i}', memory_total_mb=16384,
                 memory_used_mb=12000 if case == 'capacity' and i == 3 else 0)
            for i in range(3 if case == 'missing' else 4)])
    monkeypatch.setattr(targets, 'remote_target_telemetry', telemetry)
    launched = []
    async def launch(**kwargs):
        launched.append(kwargs)
    await scheduler.GPUOrchestrator(workers, lambda: [], launch)._process_cycle()
    async with workers() as session:
        job = await session.get(Job, 'job-2')
        target = await session.get(ExecutionTarget, 'vast:2')
        if case in ('four', 'canonical'):
            assert len(launched) == 1
            assignment = job.provenance['remote_execution_assignment']
            assert assignment['gpu_indices'] == [0, 1, 2, 3]
            assert len(assignment['admission_snapshot']['devices']) == 4
            assert launched[0]['params']['bcp_size_cp'] == 4
            assert target.leased_job_id == job.id
        else:
            assert not launched
            assert target.leased_job_id is None
            assert job.queue_status == 'queued'
            assert job.remote_state == ('waiting_remote_gpu' if case in ('auto', 'one') else
                                        'waiting_remote_telemetry' if case == 'unavailable' else 'waiting_remote_capacity')
