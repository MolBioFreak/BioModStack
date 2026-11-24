"""Count real compiler/materializer passes up to (but not through) execution."""
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import database
from component_runtime import NativeInvocation
from services import model_msa_handoff, msa_preparation, nextflow
from services.remote_execution import executor
from test_core_protein_candidates import setup, job


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', [False, True], ids=['local', 'remote'])
async def test_prepared_handoff(tmp_path, monkeypatch, remote):
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            current.params = dict(sequence='ACDE', sequence_name='sample', boltz_use_msa=True,
                colabfold_pairing_mode='unpaired', sequence_batch_entries=[dict(name='one', sequence='ACDE')])
            if remote:
                current.execution_target_id = 'offline'
            session.add(current)
            await session.commit()

        compile_job = Mock(wraps=nextflow.compile_job_nextflow_invocation)
        materialize = NativeInvocation.materialize_inputs
        writes = []
        def counted(invocation, root):
            writes.append(invocation)
            return materialize(invocation, root)
        monkeypatch.setattr(nextflow, 'compile_job_nextflow_invocation', compile_job)
        monkeypatch.setattr(NativeInvocation, 'materialize_inputs', counted)

        def provider(*, sequences, params):
            assert sequences == ['ACDE']
            path = tmp_path / 'offline.a3m'
            path.write_text('>query\nACDE\n')
            return dict(provider='colabfold_api', request_digest='fixture', cache_hit=True,
                provenance={}, artifacts=[dict(chain_index=0, role='unpaired', path=str(path),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())])
        provider_call = Mock(side_effect=provider)
        monkeypatch.setattr(msa_preparation, 'prepare_model_msa', provider_call)
        # Exercise native preparation, not claim polling or network transport.
        async def prepare(session, job, model, params, dest, authority, *, native_invocation):
            return model_msa_handoff.prepare_launch_msa(model, params, dest, native_invocation=native_invocation)
        prep = AsyncMock(side_effect=prepare)
        monkeypatch.setattr(nextflow, '_prepare_launch_msa_on_controller', prep)
        monkeypatch.setattr(database, 'async_session', factory)
        monkeypatch.setattr(nextflow, 'configured_lane', lambda **kw: 'offline')
        monkeypatch.setattr(nextflow, 'adapter_identity_from_environment', lambda: None)
        monkeypatch.setattr(nextflow, 'workflow_adapter_enabled', lambda: False)
        monkeypatch.setattr(nextflow, 'transient_workflow_runner_mode', lambda: False)
        monkeypatch.setattr(nextflow, 'preflight_nextflow_java', lambda env: (True, 'offline'))
        monkeypatch.setattr(nextflow, '_resolve_dynamic_gpu_cpu_share', AsyncMock(return_value=None))
        monkeypatch.setattr(executor, '_publish_remote_transition', AsyncMock(return_value=True))

        class AtHandoff(BaseException):
            pass
        async def observe(session, job, invocation):
            original = writes[0]
            prepared = tmp_path / 'prepared-msa'
            authority = tmp_path / '.boltz-launch-authority.json'
            def sha(path):
                return hashlib.sha256(path.read_bytes()).hexdigest()
            assert invocation.command == (*original.command,
                '--boltz_prepared_msa_dir', str(prepared), '--boltz_prepared_msa_sha256', sha(prepared / 'msa-inputs.json'),
                '--protein_science_contract_revision', '1', '--boltz_launch_authority_path', str(authority),
                '--boltz_launch_authority_sha256', sha(authority))
            assert invocation.generated_inputs == original.generated_inputs
            assert original.generated_inputs
            for item in original.generated_inputs:
                assert (tmp_path / item.relative_path).read_bytes() == item.payload
            raise AtHandoff
        async def observe_remote(session, job, *, command, native_invocation, **kwargs):
            assert command == list(native_invocation.command)
            await observe(session, job, native_invocation)
        monkeypatch.setattr(nextflow, '_pin_local_invocation_images', observe)
        monkeypatch.setattr(executor, 'launch_remote_job', observe_remote)
        with pytest.raises(AtHandoff):
            await nextflow.launch_nextflow_job(current.id, current.model_id, current.mode, current.params, current.output_dir)
        counts = compile_job.call_count, len(writes), prep.await_count, provider_call.call_count
        assert counts == (2, 2, 1, 1)
    finally:
        await engine.dispose()
