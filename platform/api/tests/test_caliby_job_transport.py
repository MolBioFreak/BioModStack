"""Actual scratch Jobs/preview/compiler custody; no model or worker execution."""
from datetime import datetime
import json
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
import pytest
from sqlalchemy import select

from component_runtime import SourceIdentity
from database import ExecutionTarget, Job
from routers import jobs
from schemas import JobCreate
from services.caliby_native import read_prepared_request
from services.nextflow import compile_job_nextflow_invocation
from services.remote_execution import bundle
from test_boltzgen_generation_launch import admission, target  # noqa: F401


def request(mode, target, execution_target_id=None):
    state = {'state_id': 'source', 'path': str(target)}
    params = ({'ensembles': [{'ensemble_id': 'ensemble', 'states': [state]}],
               'omit_aas': [], 'verbose': False}
              if mode == 'ensemble_design' else {'structures': [state]})
    params.update(num_workers=0, scn_step_scale=0.)
    return JobCreate(name='caliby-transport', model_id='caliby_experimental', mode=mode,
                     params=params, execution_target_id=execution_target_id)


def assert_invocation(job, prepared):
    invocation = compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert invocation.entrypoint == 'workflows/caliby_native.nf'
    assert invocation.command[invocation.command.index('--caliby_request_dir') + 1] == str(prepared)
    assert invocation.native_parameters['num_workers'] == 0
    assert invocation.native_parameters['scn_step_scale'] == 0.
    plan = invocation.execution_plan
    assert plan.complete, plan.blockers
    assert {node.component_key for node in plan.metadata.static_components} == {'RunCalibyNative'}
    selected = {item.relative_path for item in plan.metadata.dependencies if item.kind == 'weights'}
    expected = 'caliby_packer_010' if job.mode == 'sidechain_pack' else 'soluble_caliby_v1'
    assert selected == {f'caliby/model_params/caliby/{expected}.ckpt'}
    assert json.loads(plan.metadata.result_contract_json)['schema'] == 'bms.caliby-native-results.v1'
    return invocation


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ensemble_design', 'sidechain_pack'])
async def test_local_job_persists_prepared_science_and_compiles_offline(admission, target, mode):
    submitted = request(mode, target)
    tasks = BackgroundTasks()
    response = await jobs._create_job(submitted, tasks, admission)
    admission.expire_all()
    job = await admission.get(Job, response.id)
    prepared = Path(job.params['caliby_request_dir'])
    snapshot = read_prepared_request(mode, job.params, prepared)
    assert snapshot['requested']['num_workers'] == 0
    assert (prepared / snapshot['sources'][0]['path']).read_bytes() == target.read_bytes()
    target.unlink()
    assert_invocation(job, prepared)
    # Queued callbacks are deliberately not executed: this is transport evidence.


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ensemble_design', 'sidechain_pack'])
async def test_remote_review_reuses_exact_retained_tree_after_source_removed(
        admission, target, mode, monkeypatch):
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: target.parent)
    selected = ExecutionTarget(id='vast:caliby-fixture', provider='vast', provider_instance_id='caliby-fixture',
        active=True, state='ready', capabilities={
            'gpu_count': 1,
            'critical_runtime_binding': {'paths': {'python': '/fixture/runtime/python/bin/python',
                'nextflow': '/fixture/runtime/bin/nextflow'}, 'environment': {}}},
        provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
            'status': 'complete', 'present': True, 'running': True}})
    admission.add(selected)
    await admission.commit()
    identity = SourceIdentity.from_checkout(Path(__file__).resolve().parents[3])
    # This fixture tests input custody, not dirty-tree release admission.
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: (identity.revision, identity.tree))
    with pytest.raises(HTTPException) as review:
        await jobs._create_job(request(mode, target, selected.id), BackgroundTasks(), admission)
    assert review.value.status_code == 409
    assert review.value.detail['code'] == 'remote_prepared_job_review_required'
    assert list(await admission.scalars(select(Job.id))) == []
    prepared_body = review.value.detail['job_request']
    typed = JobCreate.model_validate(prepared_body)
    prepared = Path(typed.params['caliby_request_dir'])
    before = {p.relative_to(prepared): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in prepared.rglob('*') if p.is_file()}
    target.unlink()
    preview = jobs._execution_plan_preview(typed)
    assert preview['admissible'], preview['blockers']
    approved = JobCreate.model_validate({**prepared_body, 'execution_plan_approval': preview['approval_digest']})
    result = await jobs._create_job(approved, BackgroundTasks(), admission)
    job = await admission.get(Job, result.id)
    assert job.params['caliby_request_dir'] == str(prepared)
    assert job.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
    assert_invocation(job, prepared)
    assert {p.relative_to(prepared): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in prepared.rglob('*') if p.is_file()} == before
