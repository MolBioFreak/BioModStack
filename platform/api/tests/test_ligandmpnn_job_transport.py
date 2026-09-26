"""Ordinary LigandMPNN production owners with scratch stores and inert inputs."""
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import BackgroundTasks, HTTPException
import pytest
from sqlalchemy import select

from component_runtime import SourceIdentity
from database import ExecutionTarget, Job
from routers import jobs
from schemas import JobCreate
from services.ligandmpnn_design import MODES, prepare_for_job, read_prepared_request
from services.nextflow import compile_nextflow_invocation, compile_job_nextflow_invocation
from services.remote_execution import bundle
from test_boltzgen_generation_launch import admission, target  # noqa: F401


def request(mode, source, target_id=None):
    return JobCreate(name='ordinary LigandMPNN transport', model_id='ligandmpnn', mode=mode,
        execution_target_id=target_id, params={'target_pdb': str(source), 'design_seed': 0,
            'temperature': None, 'remove_ccds': [], 'remove_waters': False,
            'occupancy_threshold_sidechain': None, 'structure_noise': 0.,
            'bias_per_residue': {'A1': {'ALA': 0.}}, 'write_structures': False})


def assert_native(invocation, mode):
    assert invocation.entrypoint == 'workflows/ligandmpnn_design.nf'
    native = invocation.native_parameters
    assert native['temperature'] is None
    assert native['remove_ccds'] == []
    assert native['remove_waters'] is False
    assert native['design_seed'] == 0
    assert native['bias_per_residue'] == {'A1': {'ALA': 0.}}
    assert not {'af2_models', 'rfd_models', 'boltz_models', 'msa_local_db', 'msa_cache_dir'} & native.keys()
    plan = invocation.execution_plan
    assert plan.complete, plan.blockers
    assert {node.component_key for node in plan.metadata.static_components} == {'RunLigandMPNNDesign'}
    assert {item.relative_path for item in plan.metadata.dependencies if item.kind == 'image'} == {'foundry.sif'}
    assert not any(item.kind == 'weights' for item in plan.metadata.dependencies)
    contract = json.loads(plan.metadata.result_contract_json)
    assert contract == bundle.resolve_job_result_contract(SimpleNamespace(model_id='ligandmpnn', mode=mode))
    assert contract['contract'] == 'ligandmpnn_design.v1'


@pytest.mark.parametrize('mode', sorted(MODES))
def test_real_normalizer_preview_clone_preserves_all_native_values(mode, tmp_path):
    source = tmp_path / 'input with spaces.cif'
    source.write_text('data_inert_transport\n')
    normalized = jobs.normalize_job_request(request(mode, source))
    replay = jobs.normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert replay == normalized
    output = tmp_path / 'not-created'
    invocation = compile_nextflow_invocation('ligandmpnn', mode, replay.params, str(output), _preview_only=True)
    assert_native(invocation, mode)
    payload = json.loads(invocation.generated_inputs[0].payload)
    assert payload['options']['temperature'] is None
    assert 'ligandmpnn_design_request' not in invocation.native_parameters
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', sorted(MODES))
async def test_local_job_owns_native_files_and_replays_without_original(admission, target, mode):
    response = await jobs._create_job(request(mode, target), BackgroundTasks(), admission)
    admission.expire_all()
    job = await admission.get(Job, response.id)
    before = read_prepared_request(mode, job.params)
    source = Path(job.params['ligandmpnn_design_input'])
    assert source.read_bytes() == target.read_bytes()
    target.unlink()
    invocation = compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert_native(invocation, mode)
    assert read_prepared_request(mode, job.params) == before
    assert invocation.command[invocation.command.index('--ligandmpnn_design_input') + 1] == str(source)
    # BackgroundTasks are not executed; no model, queue worker or GPU is started.


def test_retained_request_relocation_and_tamper_checks(tmp_path):
    source = tmp_path / 'original.pdb'
    source.write_bytes(b'REMARK inert source bytes\n')
    mode = 'ligand_aware'
    normalized = jobs.normalize_job_request(request(mode, source))
    params = {**normalized.params, **prepare_for_job(mode, normalized.params, tmp_path/'prepared', allowed_roots=[tmp_path])}
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (tmp_path/'prepared').iterdir()}
    source.unlink()
    assert prepare_for_job(mode, params, tmp_path/'unused', allowed_roots=[tmp_path], retain_prepared=True) == {
        key: params[key] for key in ('ligandmpnn_design_request', 'ligandmpnn_design_input')}
    assert not (tmp_path/'unused').exists()
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before
    moved = {**params, **prepare_for_job(mode, params, tmp_path/'moved', allowed_roots=[tmp_path])}
    assert read_prepared_request(mode, moved) == read_prepared_request(mode, params)
    Path(moved['ligandmpnn_design_input']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='differs'):
        read_prepared_request(mode, moved)


@pytest.mark.asyncio
async def test_remote_review_approval_retains_exact_native_files_offline(admission, target, monkeypatch):
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: target.parent)
    selected = ExecutionTarget(id='vast:ligandmpnn-fixture', provider='vast', provider_instance_id='ligandmpnn-fixture',
        active=True, state='ready', capabilities={'gpu_count': 1,
            'critical_runtime_binding': {'paths': {'python': '/fixture/runtime/python/bin/python',
                'nextflow': '/fixture/runtime/bin/nextflow'}, 'environment': {}}},
        provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
            'status': 'complete', 'present': True, 'running': True}})
    admission.add(selected)
    await admission.commit()
    identity = SourceIdentity.from_checkout(Path(__file__).resolve().parents[3])
    # Isolate input custody from dirty-tree release admission. Not release evidence.
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: (identity.revision, identity.tree))
    mode = 'ligand_aware'
    with pytest.raises(HTTPException) as review:
        await jobs._create_job(request(mode, target, selected.id), BackgroundTasks(), admission)
    assert review.value.status_code == 409
    assert review.value.detail['code'] == 'remote_prepared_job_review_required'
    assert list(await admission.scalars(select(Job.id))) == []
    body = review.value.detail['job_request']
    typed = JobCreate.model_validate(body)
    owned = [Path(typed.params[k]) for k in ('ligandmpnn_design_request', 'ligandmpnn_design_input')]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in owned}
    target.unlink()
    preview = jobs._execution_plan_preview(typed)
    assert preview['admissible'], preview['blockers']
    approved = JobCreate.model_validate({**body, 'execution_plan_approval': preview['approval_digest']})
    response = await jobs._create_job(approved, BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    assert job.params['ligandmpnn_design_request'] == str(owned[0])
    assert_native(compile_job_nextflow_invocation(job, job.params, job.output_dir), mode)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in owned} == before
