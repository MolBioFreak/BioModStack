"""Offline transport/lifecycle regressions, not scientific/provider acceptance."""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from database import Job
from services.remote_execution import executor as ex, result_generation as gen
from services.remote_execution.contracts import RemoteAttemptStatus, RemoteResultManifest
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import success
from test_remote_result_generation import package


@pytest.mark.parametrize('generation', [0, 2])
def test_projection_context_preserves_original_and_current_identity(generation):
    original = 'a' * 64
    current = original if generation == 0 else 'b' * 64
    issued = {'root_job_id': 'job', 'attempt_id': 'attempt', 'target_id': 'target',
              'lease_id': 'original-lease', 'plan_sha256': original,
              'source_identity': {'revision': 'c' * 40, 'tree': 'd' * 40}}
    job = SimpleNamespace(provenance={'remote_execution_receipt': {
        'component_context_identity': issued, 'plan_sha256': current}})
    before = copy.deepcopy(job.provenance)
    status = RemoteAttemptStatus(job_id='job', attempt_id='attempt', state='succeeded',
                                generation=generation, plan_sha256=current)
    manifest = SimpleNamespace(artifacts=[SimpleNamespace(
        relative_path='native/.bms-components.json')])
    expected = ex._component_projection_context(job, status, PurePosixPath('native'), manifest)
    assert expected is not None
    assert expected['plan_sha256'] == original
    assert expected['current_plan_sha256'] == current
    assert expected['generation'] == generation
    assert expected['lease_id'] == issued['lease_id']
    assert expected['projection_relative_path'] == 'native/.bms-components.json'
    assert job.provenance == before


@pytest.mark.parametrize('invalid', ['missing-projection', 'missing-current', 'foreign-current', 'initial-mismatch'])
def test_projection_context_rejects_missing_or_conflicting_authority(invalid):
    job = SimpleNamespace(provenance={'remote_execution_receipt': {
        'component_context_identity': {'plan_sha256': 'a' * 64}, 'plan_sha256': 'b' * 64}})
    status = RemoteAttemptStatus(job_id='job', attempt_id='attempt', state='succeeded',
        generation=0 if invalid == 'initial-mismatch' else 2,
        plan_sha256=None if invalid == 'missing-current' else 'c' * 64 if invalid == 'foreign-current' else 'b' * 64)
    manifest = SimpleNamespace(artifacts=[] if invalid == 'missing-projection' else
        [SimpleNamespace(relative_path='.bms-components.json')])
    with pytest.raises(ex.RemoteExecutionError):
        ex._component_projection_context(job, status, PurePosixPath('.'), manifest)


def _resource_owner():
    issued = {'attempt_id': 'attempt', 'attempt': 1, 'generation': 0}
    job = SimpleNamespace(remote_attempt_id='attempt', params={}, provenance={
        'remote_execution_receipt': {'resource_execution': issued}})
    status = RemoteAttemptStatus(job_id='job', attempt_id='attempt', state='succeeded',
        quiescent=True, boot_id='00000000-0000-0000-0000-000000000001',
        supervisor_pid=123, supervisor_start_ticks=456, control_group='/fixture/control')
    return job, status


@pytest.mark.parametrize('missing', ['issued', 'quiescent', 'boot_id', 'supervisor_pid', 'supervisor_start_ticks', 'control_group'])
def test_missing_optional_resource_observation_owner_does_not_fail_science(missing):
    job, status = _resource_owner()
    if missing == 'issued':
        job.provenance = {}
    else:
        status = status.model_copy(update={missing: False if missing == 'quiescent' else None})
    assert ex._record_native_resource_execution(job, status) is False
    assert job.params == {}


def test_resource_owner_replay_deduplicates_and_rejects_conflicting_observation():
    job, status = _resource_owner()
    assert ex._record_native_resource_execution(job, status) is True
    accepted = copy.deepcopy(job.params)
    assert ex._record_native_resource_execution(job, status) is True
    assert job.params == accepted
    changed = status.model_copy(update={'supervisor_start_ticks': 789})
    assert ex._record_native_resource_execution(job, changed) is False
    assert job.params == accepted


@pytest.mark.parametrize('relative', ['.', '', '../escape', '/outside'])
def test_artifact_paths_do_not_inherit_native_directory_root_exception(tmp_path, relative):
    with pytest.raises(ex.RemoteExecutionError):
        ex._safe_result_path(tmp_path, relative)
    if relative != '.':
        with pytest.raises(ex.RemoteExecutionError):
            ex._safe_result_path(tmp_path, relative, allow_root=True)
    else:
        assert ex._safe_result_path(tmp_path, relative, allow_root=True) == tmp_path


@pytest.mark.parametrize('generation', [0, 2])
def test_native_result_directory_selects_root_or_contained_generation(tmp_path, generation):
    from test_remote_result_generation import job_at
    job = job_at(tmp_path)
    job.provenance = {'remote_execution_receipt': {'remote_attempt_dir': '/fixture/attempt'}}
    manifest, incoming, status = package(job)
    relative = PurePosixPath('generations/g2' if generation else '.')
    status = status.model_copy(update={'generation': generation,
        'native_output_directory': str(PurePosixPath('/fixture/attempt/results') / relative)})
    if generation:
        nested = incoming / str(relative)
        nested.mkdir(parents=True)
        (nested / 'first.txt').write_bytes(b'first')
        payload = manifest.model_dump()
        payload['generation'] = generation
        payload['artifacts'].append({**payload['artifacts'][0], 'relative_path': str(relative / 'first.txt')})
        manifest = RemoteResultManifest.model_validate(payload)
    original = manifest.model_dump()
    selected, directory, projected = ex._native_result_view(job, status, incoming, manifest)
    assert selected == relative
    assert directory == incoming / str(relative)
    assert (directory / 'first.txt').read_bytes() == b'first'
    assert [a.relative_path for a in projected.artifacts] == (['first.txt'] if generation else ['first.txt', 'second.txt'])
    assert manifest.model_dump() == original


@pytest.mark.parametrize('remote_output', [None, '/fixture/attempt/results_other', '/fixture/attempt/results/../escape'])
def test_native_result_directory_cannot_escape_or_guess_continuation_root(tmp_path, remote_output):
    from test_remote_result_generation import job_at
    job = job_at(tmp_path)
    job.provenance = {'remote_execution_receipt': {'remote_attempt_dir': '/fixture/attempt'}}
    manifest, incoming, status = package(job)
    status = status.model_copy(update={'generation': 1, 'native_output_directory': remote_output})
    with pytest.raises(ex.RemoteExecutionError):
        ex._native_result_view(job, status, incoming, manifest)


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['succeeded', 'failed', 'cancelled'])
async def test_terminal_status_without_writer_quiescence_keeps_ownership(store, monkeypatch, terminal):
    status = success().model_copy(update={'state': terminal, 'quiescent': False})
    async def observed(*args):
        return status
    async def forbidden(*args, **kwargs):
        pytest.fail('unquiescent science must not be collected or finalized')
    monkeypatch.setattr(ex, 'remote_status', observed)
    monkeypatch.setattr(ex, 'collect_remote_results', forbidden)
    monkeypatch.setattr(ex, '_finalize_pulled_results', forbidden)
    async with store() as session:
        job = await session.get(Job, 'job')
        assert await ex.reconcile_remote_job(session, job) is False
        await session.refresh(job)
        assert (job.status, job.queue_status, job.remote_state) == ('running', 'running', 'running')
        from database import ExecutionTarget
        assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'job'


@pytest.mark.asyncio
@pytest.mark.parametrize('telemetry', ['absent', 'invalid-json', 'oversized'])
async def test_ordinary_ngs_return_requires_shared_native_finalizer_even_without_metrics(
        store, tmp_path, monkeypatch, telemetry):
    from routers.ont_runs import _mode_for_ont_workflow
    from services import ont_ngs_completion, result_state_integrity

    class NativeRejection(ValueError):
        pass

    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id = 'nanopore'
        job.mode = _mode_for_ont_workflow('ont_construct_screening')
        job.params = {'ont_workflow_id': 'ont_construct_screening', 'ont_input_mode': 'fastq',
                      'input_mode': 'fastq', 'run_fastq_qc': False, 'run_assembly': True}
        job.output_dir = str(tmp_path / 'output')
        job.remote_state = 'returning'
        assert ont_ngs_completion.ont_native_completion_path(job) == 'shared_native_import'
        manifest, incoming, status = package(job)
        if telemetry != 'absent':
            data = b'not json' if telemetry == 'invalid-json' else b'x' * (256 * 1024 + 1)
            relative = '.bms-resource-usage.json'
            (incoming / relative).write_bytes(data)
            payload = manifest.model_dump()
            payload['artifacts'].append({'relative_path': relative, 'size_bytes': len(data),
                                        'sha256': hashlib.sha256(data).hexdigest(), 'role': 'result'})
            manifest = RemoteResultManifest.model_validate(payload)
            encoded = manifest.model_dump_json().encode()
            (incoming / 'result-manifest.json').write_bytes(encoded)
            digest = hashlib.sha256(encoded).hexdigest()
            relocated = gen.staging_path(job, digest)
            incoming.rename(relocated)
            incoming = relocated
            status = status.model_copy(update={'result_manifest_sha256': digest})
        _, owner = _resource_owner()
        status = status.model_copy(update={key: getattr(owner, key) for key in (
            'quiescent', 'boot_id', 'supervisor_pid', 'supervisor_start_ticks', 'control_group')})
        contract = ex.resolve_job_result_contract(job)
        job.provenance = {'remote_execution_receipt': {
            'remote_attempt_dir': '/fixture/attempt',
            'resource_execution': {'attempt_id': 'attempt', 'generation': 0, 'attempt': 1},
            'expected_result_contract_sha256': hashlib.sha256(json.dumps(
                contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}}
        await session.commit()
        calls = []

        async def native_stage_adapter(received_job, **kwargs):
            # Native scientific validation is owned/tested by ont_ngs_completion;
            # this fixture proves executor dispatch, optional observation handling,
            # and real DB/filesystem rollback, NOT valid NGS scientific output.
            calls.append('staged')
            assert kwargs['output_root'] == incoming
            assert kwargs['resource_usage_receipt'] is None
            return {'completion_path': ont_ngs_completion.ont_native_completion_path(received_job),
                    'state': 'pending_native_import'}

        async def native_finalizer(received_job, output, received_session):
            calls.append('native')
            assert received_session is session
            assert Path(output) == Path(received_job.output_dir)
            assert (Path(output) / 'first.txt').read_text() == 'first'
            raise NativeRejection('native scientific validator owns completion')

        monkeypatch.setattr(ont_ngs_completion, 'validate_and_prepare_remote_ont_completion', native_stage_adapter)
        monkeypatch.setattr(result_state_integrity, 'finalize_successful_job', native_finalizer)
        with pytest.raises(NativeRejection, match='owns completion'):
            await ex._finalize_pulled_results(session, job, status, manifest, incoming)
        assert calls == ['staged', 'native']
        await session.rollback()
        job = await session.get(Job, 'job')
        assert job.status != 'completed'
        await ex._recover_result_generation(session, job)
        assert (incoming / 'first.txt').read_text() == 'first'
        assert not Path(job.output_dir).exists()
