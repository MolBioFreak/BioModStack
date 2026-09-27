"""Shape-owned preparation/approval/atomic insertion; no worker or science runs."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from component_runtime import SourceIdentity
from database import ExecutionTarget, Job, ShapeDesignRequest
from routers import jobs, shape_blueprint
from schemas import JobCreate
from services import shape_requests, shape_resources
from services.remote_execution import bundle, preloading
from services.remote_execution.contracts import WorkflowProvisionSelection, ProvisionSelection
from test_core_protein_scientific_admission import admission  # noqa: F401
from test_shape_submission import CUBE_OBJ


async def submitted_request(session, tmp_path):
    geometry = await shape_resources.admit_obj_geometry(session, data_root=tmp_path,
        payload=CUBE_OBJ, filename='cube.obj', angstrom_per_unit=10.0)
    return shape_requests.SubmittedShapeRequest(
        client_request_id='aa56b5a3-7c52-4254-ae25-246274533c47', name='shape-remote-review',
        geometry_id=geometry.geometry_id, expected_geometry_sha256=geometry.geometry_sha256,
        expected_geometry_manifest_sha256=geometry.manifest['manifest_sha256'],
        expected_point_pool_sha256=geometry.point_pool_sha256, target_length=120,
        num_backbones=1, sequences_per_backbone=0, sequence_policy='skip', seed=0,
        execution_target_id='vast:shape-fixture')


@pytest.fixture
def isolated_roots(tmp_path, monkeypatch):
    import paths
    from services import nextflow
    monkeypatch.setenv('BMS_SHAPE_BLUEPRINT_ENABLED', 'true')
    monkeypatch.setenv('BMS_HOME', str(Path(__file__).resolve().parents[3]))
    monkeypatch.setattr(shape_blueprint, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(nextflow, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(paths, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    identity = SourceIdentity.from_checkout(Path(__file__).resolve().parents[3])
    # Working-tree source cleanliness is independently checked by the integrator.
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: (identity.revision, identity.tree))
    monkeypatch.setattr(preloading, 'current_source_identity', lambda *_: (identity.revision, identity.tree))


@pytest.mark.asyncio
async def test_shape_approval_is_outside_retained_scientific_identity(admission, tmp_path):
    submitted = await submitted_request(admission, tmp_path)
    first = await shape_requests.materialize_shape_request(admission, data_root=tmp_path, submitted=submitted)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in Path(first.stage_dir).iterdir()}
    approved = submitted.model_copy(update={'execution_plan_approval': 'a' * 64})
    replay = await shape_requests.materialize_shape_request(admission, data_root=tmp_path, submitted=approved)
    assert replay == first
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in Path(replay.stage_dir).iterdir()}
    assert not list(await admission.scalars(select(Job)))
    with pytest.raises(shape_requests.ShapeRequestError, match='different scientific intent'):
        await shape_requests.materialize_shape_request(admission, data_root=tmp_path,
            submitted=submitted.model_copy(update={'seed': 1}))


@pytest.mark.asyncio
async def test_shape_review_approval_real_insertion_replay(admission, isolated_roots, tmp_path):
    session = admission
    session.add(ExecutionTarget(id='vast:shape-fixture', provider='vast', provider_instance_id='shape-fixture',
        active=True, state='ready', capabilities={'gpu_count': 1},
        provider_metadata={'inventory': {'checked_at': datetime.utcnow().isoformat(),
            'status': 'complete', 'present': True, 'running': True}}))
    await session.commit()
    submitted = await submitted_request(session, tmp_path)
    with pytest.raises(HTTPException) as review:
        await shape_blueprint.submit_shape_request(submitted, BackgroundTasks(), session)
    assert review.value.status_code == 409
    detail = review.value.detail
    assert detail['code'] == 'remote_prepared_job_review_required'
    assert not list(await session.scalars(select(Job)))
    row = await session.get(ShapeDesignRequest, detail['response_context']['request_id'])
    assert row.job_id is None
    prepared = JobCreate.model_validate(detail['job_request'])
    assert 'msa_provider' not in prepared.params
    stage = Path(prepared.params['shape_request_path']).parent
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in stage.iterdir()}
    # Review/replay consumes retained bytes, not the original canonical artifacts.
    for p in (tmp_path / 'shape_blueprint').glob('geometries/**/*'):
        if p.is_file():
            p.unlink()
    preview = await jobs.preview_job_execution_plan(prepared, session=session)
    assert preview['admissible'], preview['blockers']
    approved = submitted.model_copy(update={'execution_plan_approval': preview['approval_digest']})
    created = await shape_blueprint.submit_shape_request(approved, BackgroundTasks(), session)
    assert created['job_id'] == detail['response_context']['job_id']
    session.expire_all()
    row = await session.get(ShapeDesignRequest, created['request_id'])
    job = await session.get(Job, created['job_id'])
    assert row.job_id == job.id
    assert job.execution_target_id == submitted.execution_target_id
    assert job.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
    assert row.request_sha256 == created['request_sha256'] == detail['response_context']['request_sha256']
    assert 'execution_plan_approval' not in row.request_spec
    replay = await shape_blueprint.submit_shape_request(approved, BackgroundTasks(), session)
    assert replay['reused'] is True
    assert replay['job_id'] == job.id
    assert len(list(await session.scalars(select(Job)))) == 1
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in stage.iterdir()}


@pytest.mark.asyncio
async def test_shape_local_real_insertion_without_remote_review(admission, isolated_roots, tmp_path):
    submitted = (await submitted_request(admission, tmp_path)).model_copy(update={'execution_target_id': None})
    created = await shape_blueprint.submit_shape_request(submitted, BackgroundTasks(), admission)
    admission.expire_all()
    job = await admission.get(Job, created['job_id'])
    row = await admission.get(ShapeDesignRequest, created['request_id'])
    assert row.job_id == job.id
    assert job.execution_target_id is None and job.parent_job_id is None
    assert job.params['shape_sequence_policy'] == 'skip'
    assert job.params['shape_sequences_per_backbone'] == 0
    assert 'execution_plan_approval' not in job.provenance
    assert 'msa_provider' not in job.params


@pytest.mark.asyncio
@pytest.mark.parametrize('designer', [None, 'proteinmpnn', 'fampnn'])
async def test_shape_current_preparation_uses_same_projection_family_is_source_free(admission, isolated_roots, tmp_path, designer):
    submitted = await submitted_request(admission, tmp_path)
    if designer:
        submitted = shape_requests.SubmittedShapeRequest.model_validate({
            **submitted.model_dump(), 'sequence_policy': 'external',
            'sequence_engine': designer, 'sequences_per_backbone': 1})
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request={
        'workflow_type': 'shape_blueprint', 'request': submitted.model_dump(mode='json')})
    controller = preloading.PreloadController.__new__(preloading.PreloadController)
    plan = await controller._compile_native(selection, SimpleNamespace(), admission)
    staged = await shape_requests.materialize_shape_request(admission, data_root=tmp_path, submitted=submitted)
    from services.nextflow import compile_workflow_provision_request, compile_nextflow_invocation
    expected = compile_workflow_provision_request(shape_requests.shape_job_request(staged, submitted)).execution_plan
    assert plan is not None and expected is not None
    assert plan.to_dict() == expected.to_dict()
    assert plan.metadata.complete, plan.metadata.blockers
    images = {d.relative_path for d in plan.dependencies if d.kind == "image"}
    if designer:
        assert images == {'shape_rfd3.sif', 'esmfold2.sif', 'boltz2.sif', 'protenix.sif',
                          'dl_binder_design.sif' if designer == 'proteinmpnn' else 'fampnn.sif'}
    else:
        assert images == {'shape_rfd3.sif'}
    assert not plan.metadata.external_services
    invocation = compile_nextflow_invocation(staged.model_id, staged.mode,
        staged.launch_params, str(tmp_path / 'results' / 'shape-job'), job_id='shape-job')
    assert invocation.execution_plan.complete
    assert invocation.command[2] == 'workflows/shape_blueprint_design.nf'
    assert '--shape_request_path' in invocation.command
    assert invocation.command[invocation.command.index('--shape_request_path') + 1] == staged.launch_params['shape_request_path']
    assert '--shape_request' not in invocation.command
    import json
    assert 'shape_request' not in json.loads(invocation.effective_json)
    assert 'msa_provider' not in invocation.native_parameters
    assert not list(await admission.scalars(select(Job)))
    assert (await admission.get(ShapeDesignRequest, staged.request_id)).job_id is None
    family = ProvisionSelection(kind='model', model_id='protein_modification_experimental')
    assert await controller._compile_native(family, None, None) is None
