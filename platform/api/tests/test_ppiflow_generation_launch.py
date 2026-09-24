"""Controller/compiler integration only; no native sampling or model loading."""
from copy import deepcopy
import json
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException
import pytest

from database import Job
from routers import jobs, models
from schemas import JobCreate
from services import nextflow
from services.ppiflow_generation import (
    materialize_ppiflow_generation_request, normalize_ppiflow_generation_params,
    read_prepared_ppiflow_generation_request,
)
from test_core_protein_scientific_admission import admission


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    target = root / 'target.pdb'
    framework = root / 'framework.pdb'
    target.write_text('REMARK non-science transport target\nEND\n')
    framework.write_text('REMARK non-science transport framework\nEND\n')
    results = tmp_path / 'results'
    monkeypatch.setattr(jobs, 'get_allowed_roots', lambda: {'inputs': root, 'bms_results': results})
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: root)
    import paths
    monkeypatch.setattr(paths, 'get_results_dir', lambda: results)
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: root)
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(nextflow, 'get_data_root', lambda: tmp_path)
    monkeypatch.setenv('BMS_HOME', str(Path(__file__).resolve().parents[3]))
    return target, framework, results


def settings(mode, inputs):
    target, framework, _ = inputs
    result = {'target_pdb': str(target), 'samples_per_target': 2, 'self_condition': False}
    if mode == 'protein_binder':
        result.update(binder_chain='B', dataset_seed=0, translation_corrupt=False)
    else:
        result.update(framework_pdb=str(framework), antigen_chain='R', heavy_chain='H', specified_hotspots='R1')
        if mode == 'antibody_binder':
            result['light_chain'] = 'L'
    return result


@pytest.mark.parametrize('mode', ['protein_binder', 'antibody_binder', 'nanobody_binder'])
def test_normalization_preserves_native_initial_contract(mode, inputs):
    original = JobCreate(name='initial', model_id='ppiflow', mode=mode, params=settings(mode, inputs))
    before = deepcopy(original.params)
    normalized = jobs.normalize_job_request(original)
    assert original.params == before
    assert normalized.params == normalize_ppiflow_generation_params(mode, before)
    assert normalized.params['self_condition'] is False
    assert not any(key.startswith(('ppiflow_seed', 'run_', 'msa_')) for key in normalized.params)
    assert 'rfd_mode' not in normalized.params


@pytest.mark.parametrize('change', [{'unknown_science': 0}, {'samples_per_target': True}, {'rotation_corrupt': 'false'}])
def test_invalid_initial_settings_are_rejected_by_native_owner(inputs, change):
    with pytest.raises(HTTPException) as exc:
        jobs.normalize_job_request(JobCreate(name='invalid', model_id='ppiflow', mode='protein_binder',
            params={**settings('protein_binder', inputs), **change}))
    assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['protein_binder', 'antibody_binder', 'nanobody_binder'])
async def test_real_queue_materializes_native_request_and_compiler_preserves_it(admission, inputs, mode):
    requested = settings(mode, inputs)
    response = await jobs._create_job(JobCreate(name='initial-' + mode, model_id='ppiflow', mode=mode,
        params=requested), BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    root = Path(job.params['ppiflow_generation_request'])
    document = json.loads((root / 'request.json').read_text())
    assert document['requested_settings'] == requested
    assert document['effective_settings'] == normalize_ppiflow_generation_params(mode, requested)
    assert document['source_identity']['job_id'] == job.id
    assert (root / 'inputs/target_pdb.pdb').read_bytes() == inputs[0].read_bytes()
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert invocation.entrypoint == 'workflows/ppiflow_generation.nf'
    assert invocation.command[invocation.command.index('-profile') + 1] == 'workstation_ryzen7960x'
    assert invocation.native_parameters['ppiflow_generation_request'] == str(root)
    assert json.loads(invocation.effective_json)['self_condition'] is False
    assert '--samples_per_target' not in invocation.command
    assert not {'rfd_models', 'af2_models', 'boltz_models', 'msa_local_db', 'msa_cache_dir'} & invocation.native_parameters.keys()
    inputs[0].unlink()
    if mode != 'protein_binder':
        inputs[1].unlink()
    assert read_prepared_ppiflow_generation_request(mode, job.params, root)['effective_settings'] == document['effective_settings']
    assert nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir).native_parameters == invocation.native_parameters


@pytest.mark.asyncio
async def test_saved_initial_clone_reuses_snapshot_without_original_source(admission, inputs):
    first = await jobs._create_job(JobCreate(name='original', model_id='ppiflow', mode='protein_binder',
        params=settings('protein_binder', inputs)), BackgroundTasks(), admission)
    parent = await admission.get(Job, first.id)
    before = deepcopy(parent.params)
    root = Path(before['ppiflow_generation_request'])
    snapshot = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    inputs[0].unlink()
    replay = jobs._public_job_params(parent)
    replay.pop('remote_result_policy', None)  # Resubmit's existing typed-policy separation.
    second = await jobs._create_job(JobCreate(name='clone', model_id='ppiflow', mode='protein_binder',
        params=replay), BackgroundTasks(), admission)
    child = await admission.get(Job, second.id)
    new_root = Path(child.params['ppiflow_generation_request'])
    assert new_root != root
    assert {p.relative_to(new_root).as_posix(): p.read_bytes() for p in new_root.rglob('*') if p.is_file()} == snapshot
    assert parent.params == before
    assert child.params['dataset_seed'] == 0
    assert child.params['translation_corrupt'] is False


def test_pure_unsaved_preview_does_not_create_transport_directory(inputs):
    mode = 'protein_binder'
    requested = settings(mode, inputs)
    invocation = nextflow.compile_workflow_provision_request(JobCreate(name='preview', model_id='ppiflow',
        mode=mode, params=requested))
    assert json.loads(invocation.requested_json) == requested
    assert json.loads(invocation.effective_json)['dataset_seed'] == 0
    assert json.loads(invocation.effective_json)['translation_corrupt'] is False
    assert not Path(invocation.native_parameters['ppiflow_generation_request']).exists()
    assert not inputs[2].exists()


@pytest.mark.parametrize('tamper', ['settings', 'snapshot', 'mode', 'transport'])
def test_retained_request_integrity_is_input_binding_not_native_proof(inputs, tmp_path, tamper):
    mode = 'protein_binder'
    requested = settings(mode, inputs)
    root = tmp_path / 'retained'
    materialize_ppiflow_generation_request(mode, requested, root)
    path = root / 'request.json'
    document = json.loads(path.read_text())
    if tamper == 'snapshot':
        (root / 'inputs/target_pdb.pdb').write_text('changed source')
    elif tamper == 'mode':
        document['mode'] = 'antibody_binder'
    elif tamper == 'settings':
        document['effective_settings']['self_condition'] = True
    else:
        document['transport_settings']['self_condition'] = True
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_prepared_ppiflow_generation_request(mode, requested, root)


@pytest.mark.asyncio
async def test_generation_inventory_endpoint_uses_native_metadata(inputs):
    inventory = await models.get_generation_settings('ppiflow', 'protein_binder')
    assert inventory['mode'] == 'protein_binder'
    assert inventory['assets']['process'] == 'RunPPIFlowGeneration'
    fields = {field['name']: field for field in inventory['parameters']}
    assert fields['dataset_seed']['default'] == 123
    assert fields['dataset_seed']['native_path']
    with pytest.raises(HTTPException):
        await models.get_generation_settings('ppiflow', 'generator_backbone_refine')


@pytest.mark.asyncio
async def test_remote_preparation_preview_approval_reuses_owned_sources(admission, inputs, monkeypatch):
    from datetime import datetime
    from sqlalchemy import select
    from component_runtime import SourceIdentity
    from database import ExecutionTarget
    from services import ppiflow_generation as owner
    from services.remote_execution import bundle
    identity = SourceIdentity.from_checkout(Path(__file__).resolve().parents[3])
    # Dirty-checkout admission is not under test; retain its real commit identity.
    monkeypatch.setattr(bundle, 'current_source_identity', lambda: (identity.revision, identity.tree))
    admission.add(ExecutionTarget(id='vast:fixture', provider='vast', provider_instance_id='fixture',
        active=True, state='ready', capabilities={'gpu_count': 1},
        provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
            'status': 'complete', 'present': True, 'running': True}}))
    await admission.commit()
    calls = []
    original = owner.materialize_ppiflow_generation_request
    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, 'materialize_ppiflow_generation_request', counted)
    request = JobCreate(name='remote-initial', model_id='ppiflow', mode='protein_binder',
        params=settings('protein_binder', inputs), execution_target_id='vast:fixture')
    with pytest.raises(HTTPException) as response:
        await jobs._create_job(request, BackgroundTasks(), admission)
    assert response.value.status_code == 409
    detail = response.value.detail
    assert detail['code'] == 'remote_prepared_job_review_required'
    prepared = JobCreate.model_validate(detail['job_request'])
    retained = Path(prepared.params['ppiflow_generation_request'])
    snapshot = {p.relative_to(retained).as_posix(): p.read_bytes() for p in retained.rglob('*') if p.is_file()}
    assert len(calls) == 1
    assert not list(await admission.scalars(select(Job.id)))
    inputs[0].unlink()
    preview = jobs._execution_plan_preview(prepared)
    assert preview['admissible'], preview['blockers']
    assert preview['input_identities']
    altered = prepared.model_copy(deep=True)
    altered.params['self_condition'] = True
    altered.execution_plan_approval = preview['approval_digest']
    with pytest.raises(HTTPException) as changed:
        await jobs._create_job(altered, BackgroundTasks(), admission)
    assert changed.value.status_code in {409, 422}
    assert not list(await admission.scalars(select(Job.id)))
    prepared.execution_plan_approval = preview['approval_digest']
    result = await jobs._create_job(prepared, BackgroundTasks(), admission)
    job = await admission.get(Job, result.id)
    assert job.status == 'queued'
    assert job.execution_target_id == 'vast:fixture'
    assert len(calls) == 1
    destination = Path(job.params['ppiflow_generation_request'])
    assert destination == retained
    assert {p.relative_to(destination).as_posix(): p.read_bytes() for p in destination.rglob('*') if p.is_file()} == snapshot
    assert {p.relative_to(retained).as_posix(): p.read_bytes() for p in retained.rglob('*') if p.is_file()} == snapshot
    assert job.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert invocation.native_parameters['ppiflow_generation_request'] == str(destination)
