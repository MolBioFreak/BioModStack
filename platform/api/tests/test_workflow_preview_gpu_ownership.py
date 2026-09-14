"""Offline compiler/approval regressions, not live model-inference evidence."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from component_runtime import NativeInvocation, NativeInvocationPreview, SourceIdentity
from schemas import JobCreate
from services import nextflow
from tests.test_remote_rectify_admission import admission


@pytest.fixture(autouse=True)
def compiler_infrastructure(monkeypatch):
    identity = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', classmethod(lambda cls, root: identity))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})


def scientific_request():
    return dict(sequence='ACDEFGHIKLMNPQRSTVWY', pred_method='protenix',
                protenix_model_weights='protenix-v2', protenix_seeds='42',
                protenix_n_sample=5, protenix_n_cycle=10, protenix_n_step=200,
                protenix_use_msa=True, msa_provider='neurosnap_api',
                run_frustrampnn=True, frustrampnn_requiredness='required')


def test_unsaved_full_structure_preview_is_nonexecutable_and_preserves_science(tmp_path):
    requested = scientific_request()
    payload = JobCreate.model_validate(dict(name='GFP transport fixture', model_id='protenix', mode='predict',
                        params=requested, execution_target_id='vast:fixture'))
    preview = nextflow.compile_workflow_provision_request(payload)
    assert isinstance(preview, NativeInvocationPreview)
    assert not isinstance(preview, NativeInvocation)
    assert not hasattr(preview, 'command')
    assert not hasattr(preview, 'materialize_inputs')
    assert json.loads(preview.requested_json) == requested
    assert preview.execution_plan is not None
    # Raw metadata retains the hosted-MSA admission edge; the ordinary API
    # adapter may defer that specific preparation, not scientific/GPU guards.
    assert {row.component_or_dependency_id for row in preview.execution_plan.blockers} == {'protenix:msa'}
    from routers.jobs import _execution_plan_preview
    approval = _execution_plan_preview(payload)
    assert approval['admissible'], approval['blockers']
    assert approval['deferred_preparation'] == ['protenix:msa']
    assert _execution_plan_preview(payload) == approval
    assert preview.native_parameters['run_frustrampnn'] is True
    assert preview.native_parameters['frustrampnn_requiredness'] == 'required'
    assert 'frustrampnn_physical_gpu_id' not in preview.native_parameters
    for key, value in requested.items():
        assert json.loads(preview.effective_json)[key] == value, key
    assert not Path(preview.native_parameters['out_dir']).exists()
    assert not list(tmp_path.iterdir())
    with pytest.raises(FrozenInstanceError):
        setattr(preview, 'mode', 'complex')


def test_same_compiler_preserves_plan_and_science_at_real_reservation(tmp_path):
    params = scientific_request()
    output = str(tmp_path / 'never-materialized')
    preview = nextflow.compile_nextflow_invocation('protenix', 'predict', params, output,
                                                  job_id='fixture', _preview_only=True)
    reservation = nextflow.NativeCompilerExecutionContext(7, (7,), 'cpu')
    execution = nextflow.compile_nextflow_invocation('protenix', 'predict', params, output,
                                                    job_id='fixture', execution_context=reservation)
    assert isinstance(execution, NativeInvocation)
    assert execution.command[execution.command.index('--frustrampnn_physical_gpu_id') + 1] == '7'
    assert execution.native_parameters['frustrampnn_physical_gpu_id'] == 7
    assert preview.execution_plan is not None and execution.execution_plan is not None
    assert preview.execution_plan.metadata == execution.execution_plan.metadata
    for key, value in preview.native_parameters.items():
        assert execution.native_parameters[key] == value, key
    assert preview.generated_inputs == execution.generated_inputs
    assert not Path(output).exists()


@pytest.mark.parametrize('injected', [{}, {'_preview_only': True}, {'preview_only': True}])
def test_real_compilation_cannot_skip_required_gpu_by_request_parameter(tmp_path, injected):
    with pytest.raises(ValueError, match='scheduler-assigned physical GPU ID'):
        nextflow.compile_nextflow_invocation('protenix', 'predict',
            {**scientific_request(), **injected}, str(tmp_path / 'not-created'), job_id='fixture')
    assert not list(tmp_path.iterdir())


def test_preview_cannot_accept_execution_context(tmp_path):
    with pytest.raises(ValueError, match='preview cannot carry an execution reservation'):
        nextflow.compile_nextflow_invocation('protenix', 'predict', scientific_request(),
            str(tmp_path), _preview_only=True,
            execution_context=nextflow.NativeCompilerExecutionContext(0, (0,), 'cpu'))


@pytest.mark.parametrize('key,value', [('gpu_id', 0), ('gpu_ids', [0]), ('frustrampnn_physical_gpu_id', 0)])
def test_preview_rejects_physical_bindings_in_request(tmp_path, key, value):
    with pytest.raises(ValueError, match='preview cannot carry physical GPU bindings'):
        nextflow.compile_workflow_provision_request(JobCreate.model_validate(dict(
            name='invalid binding', model_id='protenix', mode='predict',
            params={**scientific_request(), key: value})))


@pytest.mark.parametrize('value', [None, True, -1, 'not-a-gpu', '1.1'])
def test_runtime_invalid_gpu_rejection_is_unchanged(tmp_path, value):
    with pytest.raises(ValueError, match='scheduler-assigned physical GPU ID'):
        nextflow.compile_nextflow_invocation('protenix', 'predict',
            {**scientific_request(), 'gpu_id': value}, str(tmp_path / 'not-created'))


def test_preview_never_calls_executable_prepared_msa_binder(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A preview must not bind a prepared executable MSA package')
    monkeypatch.setattr(nextflow, '_bind_protenix_msa_transport', forbidden)
    preview = nextflow.compile_workflow_provision_request(JobCreate.model_validate(dict(
        name='no execution binding', model_id='protenix', mode='predict',
        params={**scientific_request(), 'protenix_prepared_msa_dir': '/fixture/prepared',
                'protenix_prepared_msa_sha256': 'c' * 64})))
    assert isinstance(preview, NativeInvocationPreview)
    assert 'protenix_prepared_msa_dir' not in preview.native_parameters


def test_runtime_selection_accepts_shared_plan_but_not_executable_preview():
    from services.remote_execution import bundle
    preview = nextflow.compile_workflow_provision_request(JobCreate.model_validate(dict(
        name='dependency boundary', model_id='protenix', mode='predict', params=scientific_request())))
    # Real selection/type boundary. No physical assets are requested by this
    # boundary test; byte-transfer acceptance belongs to the live cache tests.
    assert bundle._runtime_assets('protenix', 'predict', preview.native_parameters,
        selected_plan=preview.execution_plan, only_kinds=frozenset()) == []
    with pytest.raises(bundle.RemoteBundleError, match='matching selected native invocation'):
        bundle._runtime_assets('protenix', 'predict', preview.native_parameters,
            native_invocation=preview, only_kinds=frozenset())
    with pytest.raises(bundle.RemoteBundleError, match='requires a NativeInvocation'):
        bundle.compile_remote_dependencies('protenix', 'predict', [], native_invocation=preview)


@pytest.mark.asyncio
async def test_full_workflow_preview_post_persists_approval_without_gpu_assignment(admission, tmp_path, monkeypatch):
    from database import Job
    from routers import jobs
    from services.remote_execution.bundle import verify_approved_native_inputs
    # The existing admission check tests presence, not neural-weight validity.
    # Provide an explicitly fake local prerequisite; inference stays disabled.
    weights = tmp_path / 'fixture-weights'
    (weights / 'checkpoint').mkdir(parents=True)
    (weights / 'checkpoint/protenix-v2.pt').write_bytes(b'offline admission fixture; not model weights')
    monkeypatch.setattr(jobs, '_resolve_protenix_weights_dir', lambda params: weights)
    credential = tmp_path / 'neurosnap-offline-fixture'
    credential.write_text('not-a-real-credential-offline-fixture')
    credential.chmod(0o600)
    monkeypatch.setenv('BMS_NEUROSNAP_API_KEY_FILE', str(credential))
    client, sessions = admission
    body = dict(name='full structure admission', model_id='protenix', mode='predict',
                execution_target_id='vast:one', params=scientific_request())
    response = await client.post('/jobs/execution-plan/preview', json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['admissible'], preview
    response = await client.post('/jobs', json={**body, 'execution_plan_approval': preview['approval_digest']})
    assert response.status_code == 201, response.text
    async with sessions() as session:
        job = await session.get(Job, response.json()['id'])
        assert job is not None
        for key, value in scientific_request().items():
            assert job.params[key] == value, key
        assert job.provenance['execution_plan_approval']['approval_digest'] == preview['approval_digest']
        verify_approved_native_inputs(job, {})
        # Admission has not fabricated an allocation; actual dispatch still
        # requires the scheduler to supply one for the enabled analysis stage.
        with pytest.raises(ValueError, match='scheduler-assigned physical GPU ID'):
            nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
