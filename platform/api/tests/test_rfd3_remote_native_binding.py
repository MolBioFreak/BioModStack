"""Native generation transport parity; no scientific execution or provisioning."""
import json
from types import SimpleNamespace

import pytest

from routers.jobs import _execution_plan_preview
from schemas import JobCreate
from services.nextflow import compile_nextflow_invocation, compile_workflow_provision_request
from services.rfd3_generation import materialize_generation_request, normalize_generation_params
from services.remote_execution.bundle import resolve_job_result_contract
from tests.test_remote_rectify_admission import admission  # noqa: F401

MODEL = 'protein_modification_experimental'
MODE = 'de_novo_design'
SCIENCE = dict(generator='rfd3', generation_mode='unconditional_monomer',
               min_length=40, max_length=40, num_designs=1, seed=8312027,
               dump_trajectories=False)


def request():
    return JobCreate(name='native-parity', model_id=MODEL, mode=MODE, params=dict(SCIENCE))


def test_nonexecuting_preview_uses_complete_native_generation(tmp_path, monkeypatch):
    import paths
    from services import rfd3_generation
    def forbidden(*args, **kwargs):
        raise AssertionError('Preview must never materialize the native request')
    monkeypatch.setattr(rfd3_generation, 'materialize_generation_request', forbidden)
    monkeypatch.setattr(paths, 'get_results_dir', lambda: tmp_path / 'not-created')
    invocation = compile_workflow_provision_request(request())
    preview = _execution_plan_preview(request())
    assert preview['admissible'], preview['blockers']
    assert not (tmp_path / 'not-created').exists()
    assert json.loads(invocation.requested_json) == SCIENCE
    assert len(invocation.generated_inputs) == 1
    item = invocation.generated_inputs[0]
    assert item.relative_path == 'requests/rfd3_generation_request.json'
    effective = json.loads(invocation.effective_json)
    assert json.loads(item.payload) == effective['rfd3_generation_request']
    assert not hasattr(invocation, 'command')
    assert effective['rfd3_generation_request_path'] == invocation.native_parameters[
        'rfd3_generation_request_path']
    stages = {row.component_key for row in invocation.execution_plan.metadata.static_components}
    assert {'PrepareGeneralRFD3Input', 'RunRFD3', 'BuildGeneralRFD3ResultManifest',
            'FilterRFD3', 'PublishResults'} <= stages
    assert 'PrepRFD3Input' not in stages
    assert preview == _execution_plan_preview(request())
    changed = request()
    changed.params['seed'] += 1
    assert _execution_plan_preview(changed)['approval_digest'] != preview['approval_digest']


def materialized(tmp_path):
    params, _, _ = normalize_generation_params(SCIENCE, job_name='native-parity')
    bound, path = materialize_generation_request(params, output_dir=tmp_path, job_id='real-job')
    invocation = compile_nextflow_invocation(MODEL, MODE, bound, str(tmp_path), job_id='real-job')
    return bound, path, invocation


def test_materialized_local_request_is_exact_generated_bundle_authority(tmp_path):
    bound, path, invocation = materialized(tmp_path)
    assert len(invocation.generated_inputs) == 1
    assert invocation.generated_inputs[0].payload == path.read_bytes()
    assert json.loads(invocation.effective_json)['rfd3_generation_request'] == bound['rfd3_generation_request']


def test_remote_result_contract_is_native_import_not_generic_review(tmp_path):
    bound, _, invocation = materialized(tmp_path)
    job = SimpleNamespace(model_id=MODEL, mode=MODE, params=bound, stage_family=None,
                          stage_mode=None, selected_input_artifact_class=None, provenance={})
    remote = resolve_job_result_contract(job)
    assert remote['contract_id'] == 'rfd3_generation_v1'
    assert remote == json.loads(invocation.execution_plan.metadata.result_contract_json)
    assert remote['completion_authority'].endswith(':validate_result_manifest')
    assert remote['native_contract_authority'].endswith(':_ingest_rfd3_generation_manifest')


@pytest.mark.parametrize('field,value', [
    ('rfd3_generation_request_sha256', '0' * 64),
    ('rfd3_generation_request_id', 'foreign-request'),
    ('rfd3_generation_request_path', '/foreign/request.json'),
    ('rfd3_generation_request_path', None),
    ('rfd3_generation_result_contract_id', 'de_novo_generation_v1'),
])
def test_compiler_rejects_changed_persisted_native_binding(tmp_path, field, value):
    bound, _, _ = materialized(tmp_path)
    bound[field] = value
    with pytest.raises(ValueError, match='digest mismatch|binding changed'):
        compile_nextflow_invocation(MODEL, MODE, bound, str(tmp_path), job_id='real-job')


def test_compiler_rejects_foreign_job_identity(tmp_path):
    bound, _, _ = materialized(tmp_path)
    with pytest.raises(ValueError, match='binding changed'):
        compile_nextflow_invocation(MODEL, MODE, bound, str(tmp_path), job_id='other-job')


@pytest.mark.asyncio
async def test_approved_create_preserves_native_binding_through_bundle(admission, tmp_path, monkeypatch):
    from pathlib import Path
    import hashlib
    import shutil
    from sqlalchemy import select
    from database import Job
    from routers import jobs
    import paths
    from services import nextflow
    from services.remote_execution import bundle
    from tools import bms_remote_worker as worker

    client, factory = admission
    roots = {key: tmp_path / key for key in ('data', 'inputs', 'results', 'weights', 'containers')}
    for root in roots.values():
        root.mkdir()
    for name, key in [('get_data_root', 'data'), ('get_inputs_dir', 'inputs'),
                      ('get_results_dir', 'results'), ('get_weights_root', 'weights'),
                      ('get_container_dir', 'containers')]:
        monkeypatch.setattr(paths, name, lambda key=key: roots[key])
        monkeypatch.setattr(bundle, name, lambda key=key: roots[key])
        if hasattr(jobs, name):
            monkeypatch.setattr(jobs, name, lambda key=key: roots[key])
    payload = request().model_dump(mode='json')
    payload['execution_target_id'] = 'vast:one'
    rejected = await client.post('/jobs', json=payload)
    assert rejected.status_code == 409, rejected.text
    response = await client.post('/jobs/execution-plan/preview', json=payload)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['admissible'], preview['blockers']
    assert preview['input_identities'] == []
    async with factory() as session:
        assert list((await session.scalars(select(Job))).all()) == []
    assert not list(roots['results'].iterdir())
    payload['execution_plan_approval'] = preview['approval_digest']
    edited = {**payload, 'params': {**payload['params'], 'seed': 42}}
    rejected = await client.post('/jobs', json=edited)
    assert rejected.status_code == 409, rejected.text
    accepted = await client.post('/jobs', json=payload)
    assert accepted.status_code == 201, accepted.text
    async with factory() as session:
        job = (await session.scalars(select(Job))).one()
        assert job.status == 'queued'
        assert job.provenance['core_protein_requested_params'] == SCIENCE
        assert job.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
        bound = job.params['rfd3_generation_request']
        assert bound['job_id'] == job.id and not job.id.startswith('provision-')
        expected = preview['plan']['effective_json']['rfd3_generation_request']
        assert bound['generation'] == expected['generation']
        assert bound['execution'] == expected['execution']
        assert bound['request_id'] != expected['request_id']
        invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
        request_file = Path(job.params['rfd3_generation_request_path'])
        assert invocation.generated_inputs[0].payload == request_file.read_bytes()
        invocation.materialize_inputs(Path(job.output_dir))
        # Source/runtime packaging seams only: no inference or dependency setup.
        # All native compiler, approval, input custody and worker verification
        # functions below are the production consumers, with no argv repairs.
        monkeypatch.setattr(bundle, '_git', lambda *_: 'b' * 40)
        monkeypatch.setattr(bundle, '_runtime_assets', lambda *_, **__: [])
        actual_run = bundle.subprocess.run
        def archive(command, **kwargs):
            if command[:2] == ['git', 'archive']:
                command = ['git', 'archive', '--format=tar', 'HEAD']
            return actual_run(command, **kwargs)
        monkeypatch.setattr(bundle.subprocess, 'run', archive)
        job.assigned_gpu = 0
        job.provenance = {**job.provenance, 'remote_execution_assignment':
                          {'lease_id': 'fixture-lease', 'gpu_indices': [0]}}
        target = SimpleNamespace(id=job.execution_target_id, remote_root=str(tmp_path / 'remote'))
        prepared = bundle.prepare_remote_bundle(job=job, target=target,
            command=list(invocation.command), native_invocation=invocation)
        assert prepared.envelope.expected_result_contract == resolve_job_result_contract(job)
        assert prepared.envelope.expected_result_contract['contract_id'] == 'rfd3_generation_v1'
        records = [row for row in prepared.envelope.files
                   if row.role == 'input' and row.relative_path.endswith('/rfd3_generation_request.json')]
        assert len(records) == 1
        assert records[0].sha256 == hashlib.sha256(request_file.read_bytes()).hexdigest()
        for transfer in (*prepared.input_transfers, *prepared.runtime_transfers, prepared.source_transfer):
            destination = Path(transfer.remote_destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if transfer.source.is_dir():
                shutil.copytree(transfer.source, destination)
            else:
                shutil.copy2(transfer.source, destination)
        attempt = Path(prepared.remote_attempt_dir)
        shutil.copytree(prepared.local_attempt_dir, attempt, dirs_exist_ok=True)
        (attempt / 'bundle/source').symlink_to(prepared.remote_source_dir, target_is_directory=True)
        (attempt / 'bundle/runtime').symlink_to(prepared.remote_runtime_dir, target_is_directory=True)
        worker.verify_bundle(attempt)
        command = prepared.envelope.command
        if 'BMS_COMPONENT_CONTEXT' in prepared.envelope.environment:
            context = json.loads(Path(prepared.envelope.environment['BMS_COMPONENT_CONTEXT']).read_bytes())
            command = context['root_command']
        remote_request = Path(command[command.index('--rfd3_generation_request_path') + 1])
        assert remote_request != request_file
        assert remote_request.read_bytes() == request_file.read_bytes()
        # A changed generated file may not be packaged under a fresh digest.
        request_file.write_bytes(request_file.read_bytes() + b' ')
        with pytest.raises(bundle.RemoteBundleError, match='Generated native input differs from compiler bytes'):
            bundle.prepare_remote_bundle(job=job, target=target,
                command=list(invocation.command), native_invocation=invocation)
        remote_request.write_bytes(b'changed')
        with pytest.raises(Exception):
            worker.verify_bundle(attempt)


def test_workflow_provision_uses_native_plan_without_staging_biology(tmp_path, monkeypatch):
    import paths
    from component_runtime import SourceIdentity
    from services.remote_execution import cache
    from services.remote_execution.contracts import WorkflowProvisionSelection
    source = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: source)
    monkeypatch.setattr(cache, 'current_source_identity', lambda: (source.revision, source.tree))
    monkeypatch.setattr(paths, 'get_results_dir', lambda: tmp_path / 'not-created')
    seen = []
    def unavailable_runtime(model, mode, params, *, include_support, selected_plan):
        assert (model, mode) == (MODEL, MODE)
        assert include_support is False
        assert selected_plan.complete
        assert not hasattr(selected_plan, 'command')
        native = json.loads(selected_plan.native_parameters_json)
        assert params['rfd3_generation_request_path'] == native['rfd3_generation_request_path']
        seen.append(selected_plan)
        # Missing dependencies remain setup blockers, not implicit materialization.
        raise FileNotFoundError('controlled missing scientific runtime')
    monkeypatch.setattr(cache, '_runtime_assets', unavailable_runtime)
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request=request())
    with pytest.raises(FileNotFoundError, match='controlled missing scientific runtime'):
        cache.workflow_plan(selection)
    assert len(seen) == 1
    assert not list(tmp_path.iterdir())
