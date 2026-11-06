"""Synthetic PM inputs; real admission, adapter, runner and child process.

Only the systemd transport/property boundary is simulated. No live service or
scientific dataset is touched, and no test directly invokes the native runner.
"""
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job, get_session
from experiment_database import get_experiment_session, create_experiment_engine
from experiment_migrations import run_all
from experiment_services import create_project, create_global_experiment, create_domain_experiment, add_audit_event
from routers.family_msa_results import router
from test_project_manager_adapters import _project_payload, _global_payload, _domain_payload
from tests.test_core_protein_scientific_admission import admission


def test_managed_operation_openapi_has_typed_inputs():
    app = FastAPI(); app.include_router(router)
    schema = app.openapi()
    request = schema['components']['schemas']['OperationRequest']
    assert len(request['properties']['inputs']['anyOf']) == 3
    path = '/api/family-msa/projects/{project_id}/operations'
    assert '202' in schema['paths'][path]['post']['responses']


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['compare', 'ensemble-support', 'export', 'failed', 'cancelled'])
async def test_managed_operation_admission_dispatch_retention(admission, tmp_path, monkeypatch, scenario):
    operation = scenario if scenario in {'compare', 'ensemble-support', 'export'} else 'compare'
    import database
    from services import nextflow, gpu_orchestrator, execution_ownership as ownership
    from routers import workflow_adapter as adapter
    monkeypatch.setenv('BMS_EXPERIMENT_DB_PATH', str(tmp_path / 'pm.db'))
    monkeypatch.delenv('BMS_EXPERIMENT_DATABASE_URL', raising=False)
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'blobs'))
    monkeypatch.setenv('BMS_CM_TRUSTED_PROXY_SECRET', 'test-secret')
    monkeypatch.setenv('BMS_WORKFLOW_ADAPTER_LANE', 'development')
    monkeypatch.setenv('BMS_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('BMS_DB_PATH', str(tmp_path / 'jobs.sqlite'))
    monkeypatch.setenv('DATABASE_URL', f'sqlite+aiosqlite:///{tmp_path / "jobs.sqlite"}')
    monkeypatch.setenv('BMS_WORK', str(tmp_path / 'work'))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))
    monkeypatch.setenv('BMS_WORKFLOW_ADAPTER_URL', 'http://127.0.0.1:18001')
    monkeypatch.delenv('BMS_TRANSIENT_WORKFLOW_UNIT', raising=False)
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    run_all(tmp_path / 'pm.db')
    engine = create_experiment_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    import experiment_database
    monkeypatch.setattr(experiment_database, 'experiment_session_factory', sessions)
    factory = async_sessionmaker(admission.bind, expire_on_commit=False)
    monkeypatch.setattr(database, 'async_session', factory)
    async with sessions() as session:
        project = await create_project(session, _project_payload())
        experiment = await create_global_experiment(session, project.id, _global_payload())
        domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload('ngs_molbio'))
        add_audit_event(session, workspace_id=project.id, resource_id=project.id,
            event_type='workspace_owner_bound', generation=project.head_generation,
            payload={'principal_id': 'local-application-operator'})
        await session.commit()
        project_id, domain_id = project.id, domain.id
    app = FastAPI(); app.include_router(router)
    async def pm_dependency():
        async with sessions() as session:
            yield session
    async def core_dependency():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_experiment_session] = pm_dependency
    app.dependency_overrides[get_session] = core_dependency
    processes = []
    loop = asyncio.get_running_loop()
    # The genuine adapter receives the launch. It must create the deterministic
    # unit and receipt before this test systemd transport starts its real runner.
    def handoff(**kwargs):
        future = asyncio.run_coroutine_threadsafe(adapter.workflow_adapter_launch(
            adapter.WorkflowAdapterLaunchRequest(**kwargs),
            SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'))), loop)
        return future.result(timeout=30).model_dump()
    monkeypatch.setattr(nextflow, 'launch_via_workflow_adapter', handoff)
    def create_unit(command):
        environment = os.environ.copy()
        for arg in command:
            if arg.startswith('--setenv='):
                key, value = arg[len('--setenv='):].split('=', 1)
                environment[key] = value
        environment['INVOCATION_ID'] = 'synthetic-invocation'
        unit = environment['BMS_TRANSIENT_WORKFLOW_UNIT_NAME']
        argv = command[command.index('--') + 1:]
        assert Path(argv[1]).name == 'workflow_job_runner.py'
        wrapper = '''import asyncio,sys
import workflow_job_runner as runner
from services.execution_ownership import UnitProperties, workflow_slice_for_lane
runner.show_unit_properties = lambda *args: UnitProperties('active','running','','42','0','success',workflow_slice_for_lane('development'),'synthetic-invocation')
sys.exit(asyncio.run(runner.run_workflow_job(sys.argv[1],sys.argv[2])))
'''
        log = (tmp_path / f'runner-{len(processes)}.log').open('wb')
        process = subprocess.Popen([argv[0], '-c', wrapper, argv[argv.index('--job-id')+1], 'development'],
            env=environment, stdout=log, stderr=subprocess.STDOUT)
        processes.append((process, log))
        return unit
    monkeypatch.setattr(adapter, 'create_systemd_workflow_unit', create_unit)
    monkeypatch.setattr(adapter, 'wait_for_unit_invocation', lambda *args, **kwargs: ownership.UnitProperties(
        'active', 'running', '', '42', '0', 'success', ownership.workflow_slice_for_lane('development'), 'synthetic-invocation'))
    monkeypatch.setattr(gpu_orchestrator, 'read_scheduler_config', lambda: {'global': {'enabled': True}})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                headers={'X-BMS-CM-Proxy-Secret':'test-secret'}) as client:
            base = f'/api/family-msa/projects/{project_id}'
            imported = await client.post(base + '/imports/alignment', params={'format':'fasta', 'molecule':'dna', 'replay_key':'synthetic'}, content=b'>one\nAC-G\n>two\nACGG\n')
            assert imported.status_code == 200, imported.text
            receipt_id = imported.json()['receipt_id']
            artifacts = (await client.get(base + '/receipts/' + receipt_id)).json()['artifacts']
            aligned = next(a['id'] for a in artifacts if a['role'] == 'alignment')
            inputs = {'compare': {'left_receipt_id':receipt_id, 'left_artifact_id':aligned, 'right_receipt_id':receipt_id, 'right_artifact_id':aligned},
                'ensemble-support': {'receipt_id':receipt_id, 'reference_artifact_id':aligned, 'member_artifact_ids':[aligned]},
                'export': {'receipt_id':receipt_id, 'shared_state_ids':[]}}[operation]
            if operation == 'export':
                saved = await client.post(base + '/shared-state', json={'name':'view', 'domain_experiment_id':domain_id,
                    'state': {'views':[{'id':'view', 'name':'view', 'artifact_id':aligned, 'selected_marks':['mark'],
                                       'renderer_state': {'scrollX':0, 'hideGaps':False, 'zoomLevel':1.5}}]}})
                assert saved.status_code == 201, saved.text
                inputs['shared_state_ids'] = [saved.json()['id']]
            if scenario == 'failed':
                inputs['left_artifact_id'] = 'missing-artifact'
            response = await client.post(base + '/operations', json={'operation':operation, 'molecule':'dna', 'domain_experiment_id':domain_id, 'inputs':inputs})
            assert response.status_code == 202, response.text
            accepted = response.json()
            assert accepted['status'] == 'queued' and accepted['artifact_url'] is None
            scheduler = gpu_orchestrator.GPUOrchestrator(factory, lambda: [], nextflow.launch_nextflow_job)
            if scenario == 'cancelled':
                from routers.jobs import cancel_job
                async with factory() as core:
                    await cancel_job(accepted['job_id'], core)
                await scheduler._process_cycle()
                assert not processes
                poll = (await client.get(accepted['result_url'])).json()
                assert poll['status'] == 'cancelled' and poll['artifact_url'] is None
                assert (await client.get(accepted['result_url'] + '/artifact')).status_code == 409
                from routers.jobs import resubmit_job
                from fastapi import Request, Response
                async with factory() as core:
                    resubmitted = await resubmit_job(accepted['job_id'], Request({'type':'http','headers':[]}), Response(), core)
                new_url = base + '/operations/' + resubmitted['new_job_id']
                rebound = (await client.get(new_url)).json()
                assert rebound['request_dataset_id'] != accepted['request_dataset_id']
                accepted = rebound
            await scheduler._process_cycle()
            assert len(processes) == 1
            process, log = processes[0]
            code = await asyncio.to_thread(process.wait, 45)
            log.close()
            if scenario == 'failed':
                assert code == 1, (tmp_path / 'runner-0.log').read_text()
                poll = (await client.get(accepted['result_url'])).json()
                assert poll['status'] == 'failed' and poll['artifact_url'] is None
                assert poll['error_message']
                assert (await client.get(accepted['result_url'] + '/artifact')).status_code == 409
                return
            assert code == 0, (tmp_path / 'runner-0.log').read_text()
            poll = await client.get(accepted['result_url'])
            assert poll.status_code == 200, poll.text
            assert poll.json()['status'] == 'completed', (poll.text, (tmp_path / 'runner-0.log').read_text())
            async with factory() as core:
                job = await core.get(Job, accepted['job_id'])
                assert job.assigned_gpu is None and job.vram_estimate_mb == 0
                assert ownership.latest_execution_attempt(job.params)['state'] == 'completed'
                shutil.rmtree(job.output_dir)
            result = await client.get(poll.json()['artifact_url'])
            assert result.status_code == 200, result.text
            if operation == 'export':
                import io, zipfile
                from bms_alignment.bundle import inspect_project_bundle
                with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
                    archive.extractall(tmp_path / 'export')
                assert inspect_project_bundle(tmp_path / 'export')['inventory']['artifacts']
                snapshot = json.loads((tmp_path / 'export/shared-state/000000.json').read_text())
                assert snapshot['state']['views'][0]['renderer_state'] == {'scrollX':0, 'hideGaps':False, 'zoomLevel':1.5}
                assert snapshot['state']['views'][0]['selected_marks'] == ['mark']
            else:
                assert 'report' in result.json()
            client.headers.clear()
            assert (await client.get(accepted['result_url'])).status_code == 401
    finally:
        for process, log in processes:
            if process.poll() is None:
                process.terminate(); await asyncio.to_thread(process.wait, 10)
            log.close()
        await engine.dispose()
