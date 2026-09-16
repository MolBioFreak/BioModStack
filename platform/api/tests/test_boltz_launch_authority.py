"""Launch-owned input/task authority, persisted before scientific execution."""
import copy
import json

import pytest
from database import Job
from services import nextflow
from test_core_protein_candidates import setup, job

KEY = 'boltz_launch_authority'


@pytest.mark.asyncio
async def test_real_launcher_commits_authority_before_adapter_handoff(tmp_path, monkeypatch):
    import database
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id, current.mode = 'boltz2', 'predict'
            current.params = {'sequence': 'AAA:AA:A', 'sequence_name': 'sample', 'boltz_use_msa': False}
            current.status = 'queued'
            session.add(current)
            await session.commit()
        monkeypatch.setattr(database, 'async_session', factory)
        monkeypatch.setattr(nextflow, 'configured_lane', lambda **kwargs: None)
        monkeypatch.setattr(nextflow, 'transient_workflow_runner_mode', lambda: False)
        monkeypatch.setattr(nextflow, 'workflow_adapter_enabled', lambda: True)
        async def prepare(params):
            return params, []
        async def cpu(*args):
            return None
        monkeypatch.setattr(nextflow, 'prepare_boltzgen_params_for_launch', prepare)
        monkeypatch.setattr(nextflow, '_resolve_dynamic_gpu_cpu_share', cpu)
        observed = []
        def boundary(**kwargs):
            import sqlite3
            # Independent connection, not the launcher's in-memory ORM object.
            with sqlite3.connect(tmp_path / 'test.db') as connection:
                value = connection.execute('SELECT provenance FROM jobs WHERE id=?', ('job',)).fetchone()
            authority = json.loads(value[0])[KEY]
            observed.append(authority)
            return {'accepted': True, 'run_id': '123'}
        monkeypatch.setattr(nextflow, 'launch_via_workflow_adapter', boundary)
        await nextflow.launch_nextflow_job('job', 'boltz2', 'predict', current.params, str(tmp_path))
        assert len(observed) == 1
        assert observed[0]['tasks'][0]['namespace'] == 'sample'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['caller_sequence', 'caller_samples', 'typed_boolean', 'typed_count'])
async def test_launch_rejects_untrusted_or_untyped_settings(tmp_path, damage):
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id, current.mode = 'boltz2', 'predict'
            current.params = {'sequence': 'AAAA', 'sequence_name': 'sample', 'boltz_use_msa': False}
            if damage == 'typed_boolean':
                current.params['boltz_use_msa'] = 'true'
            if damage == 'typed_count':
                current.params['boltz_diffusion_samples'] = True
            session.add(current)
            await session.commit()
            supplied = dict(current.params)
            if damage == 'caller_sequence':
                supplied['sequence'] = 'CCCC'
            if damage == 'caller_samples':
                supplied['boltz_diffusion_samples'] = 2
            command = nextflow.build_nextflow_command('boltz2', 'predict', supplied, str(tmp_path), job_id='job')
            with pytest.raises(ValueError, match='persisted|typed'):
                await nextflow._persist_boltz_launch_authority(session, current, command)
            assert KEY not in current.provenance
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_launch_persists_exact_resolved_roster_and_resume_controls(tmp_path):
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id = 'boltz2'
            current.mode = 'predict'
            current.params = {'sequence': 'AAA:AA:A', 'sequence_name': 'sample', 'num_parallel_jobs': 2, 'use_msa': False}
            session.add(current)
            await session.commit()
            command = nextflow.build_nextflow_command(current.model_id, current.mode, current.params, str(tmp_path), job_id=current.id)
            owner = getattr(nextflow, '_persist_boltz_launch_authority', None)
            assert callable(owner), 'launch must persist authoritative tasks before producer execution'
            transport = await owner(session, current, command)
            expected = copy.deepcopy(current.provenance[KEY])
            assert [task['namespace'] for task in expected['tasks']] == ['sample_job0', 'sample_job1']
            assert all(task['metadata']['producer_sequence'] == 'AAA:AA:A' for task in expected['tasks'])
            assert '--boltz_launch_authority_path' in transport
            assert await owner(session, current, command) == transport
            assert current.provenance[KEY] == expected
            current.params = dict(current.params, sequence='AAAA')
            changed = nextflow.build_nextflow_command(current.model_id, current.mode, current.params, str(tmp_path), job_id=current.id)
            with pytest.raises(ValueError, match='attempt.*changed'):
                await owner(session, current, changed)
            assert current.provenance[KEY] == expected
            current.retry_count += 1
            await owner(session, current, changed)
            assert current.provenance[KEY]['attempt'] != expected['attempt']
        async with factory() as session:
            loaded = await session.get(Job, 'job')
            assert loaded.provenance[KEY]['tasks'][0]['metadata']['producer_sequence'] == 'AAAA'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_launch_complex_file_snapshot_rejects_same_attempt_mutation(tmp_path):
    factory, engine = await setup(tmp_path)
    source = tmp_path / 'input.json'
    source.write_text(json.dumps({'components': [{'type': 'protein', 'sequence': 'AAAA', 'id': 'A'}]}))
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.model_id, current.mode = 'boltz2', 'complex'
            current.params = {'complex_json_path': str(source), 'sequence_name': 'sample', 'use_msa': False}
            session.add(current)
            await session.commit()
            owner = getattr(nextflow, '_persist_boltz_launch_authority', None)
            assert callable(owner), 'launch must snapshot mutable file bytes before execution'
            command = nextflow.build_nextflow_command('boltz2', 'complex', current.params, str(tmp_path), job_id='job')
            await owner(session, current, command)
            before = copy.deepcopy(current.provenance[KEY])
            source.write_text(source.read_text().replace('AAAA', 'CCCC'))
            with pytest.raises(ValueError, match='attempt.*changed'):
                await owner(session, current, command)
            assert current.provenance[KEY] == before
    finally:
        await engine.dispose()
