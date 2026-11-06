"""Continuous scratch Project to native MAFFT and retained HTTP reopen."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select

from database import Job, JobArtifact, current_launch_context_id, get_session
from experiment_database import get_experiment_session
from experiment_models import ExperimentRunAttempt
from experiment_services import reconcile_run_group
from routers import jobs, project_manager as pm, projects, family_msa_results
from services import gpu_orchestrator, nextflow
from test_project_workflow_setups import setup_store, _project_payload  # noqa: F401
from test_native_protein_launch_authority_regression import native_core_store, policy_cache  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('model,domain,molecule,sequence', [
    ('family_msa_protein', 'protein_in_silico', 'protein', 'ACDEFGHIKLMNPQRSTVWY'),
    ('family_msa_nucleic', 'ngs_molbio', 'dna', 'ACGTACGTACGTACGTACGT'),
])
async def test_continuous_project_native_retention(setup_store, native_core_store, tmp_path, monkeypatch, model, domain, molecule, sequence):
    image = os.environ.get('BMS_TEST_FAMILY_MSA_SIF')
    if not image:
        pytest.skip('set BMS_TEST_FAMILY_MSA_SIF to a task-owned native image')
    monkeypatch.setenv('BMS_FAMILY_MSA_SIF', image)
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'blobs'))
    monkeypatch.setenv('BMS_CORE_RUNTIME_MODE', '0')
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    import database
    monkeypatch.setattr(database, 'async_session', native_core_store)
    app = FastAPI()
    for router in (projects.router, pm.router, family_msa_results.router):
        app.include_router(router)
    app.include_router(jobs.router, prefix='/api/jobs')
    @app.middleware('http')
    async def authority(request: Request, call_next):
        actor = request.headers.get('x-test-actor')
        if actor:
            request.state.authenticated_principal = {'id': actor, 'roles': ['operator']}
        token = current_launch_context_id.set(request.headers.get('x-bms-launch-context-id'))
        try:
            return await call_next(request)
        finally:
            current_launch_context_id.reset(token)
    async def core_dep():
        async with native_core_store() as session:
            yield session
    async def pm_dep():
        async with setup_store() as session:
            yield session
    app.dependency_overrides[get_session] = core_dep
    app.dependency_overrides[get_experiment_session] = pm_dep
    app.dependency_overrides[pm.get_core_session] = core_dep
    # The NGS dependency is instantiated even for native domains; bind it to a
    # scratch store, never the application's default database.
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from molbio_ngs_models import MolBioNGSBase
    ngs_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ngs.db'}")
    async with ngs_engine.begin() as connection:
        await connection.run_sync(MolBioNGSBase.metadata.create_all)
    ngs_factory = async_sessionmaker(ngs_engine, expire_on_commit=False)
    async def ngs_dep():
        async with ngs_factory() as session:
            yield session
    app.dependency_overrides[pm.get_molbio_ngs_session] = ngs_dep
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'x-test-actor':'operator'}) as client:
        response = await client.post('/api/projects', json=_project_payload('Scratch alignment journey'))
        assert response.status_code == 201, response.text
        project = response.json()['id']
        response = await client.post(f'/api/projects/{project}/workflow-setups', json={
            'schema':'bms.project-workflow-setup.create.v1', 'relationship_kind':'primary',
            'experiment':{'name':'Scratch alignment', 'objective':'Synthetic sequences'},
            'domain_kind':domain, 'capability_id':model}, headers={'Idempotency-Key':'create'})
        assert response.status_code == 201, response.text
        setup = response.json()
        path = f"/api/projects/{project}/workflow-setups/{setup['setup_context_id']}"
        payload = {'roster':{'molecule':molecule, 'rows':[
            {'row_id':'r1', 'header':'one', 'sequence':sequence},
            {'row_id':'r2', 'header':'two', 'sequence':sequence[:-1] + 'A'}]},
            'settings':{'engine':'mafft', 'threads':1}}
        request = {'name':'Synthetic alignment', 'model_id':model, 'mode':'family_msa', 'params':payload}
        response = await client.put(path + '/draft', json={'expected_generation':0, 'draft':{'native_job_request':request}}, headers={'Idempotency-Key':'save'})
        assert response.status_code == 200, response.text
        response = await client.post(path + '/prepare-launch', json={'expected_generation':1}, headers={'Idempotency-Key':'prepare'})
        assert response.status_code == 200, response.text
        prepared = response.json()
        domain_path = f"/api/projects/{project}/experiments/{setup['global_experiment_id']}/domains/{setup['domain_experiment_id']}"
        response = await client.post(domain_path + '/run-groups', json={'preparation_launches':[{'preparation_id':prepared['preparation_id'], 'launch_context_id':prepared['launch_context_id']}]}, headers={'Idempotency-Key':'run'})
        assert response.status_code == 201, response.text
        group = response.json()
        context = prepared['launch_context_id']
        response = await client.get(f'/api/launch-contexts/{context}')
        assert response.status_code == 200, response.text
        pinned = response.json()['pinned_scheduler']
        response = await client.post('/api/jobs', json={**pinned, 'launch_context_id':context}, headers={'x-bms-launch-context-id':context})
        assert response.status_code == 201, response.text
        job_id = response.json()['id']
        assert job_id == group['runs'][0]['attempts'][0]['canonical_job_id']
        monkeypatch.setattr(gpu_orchestrator, 'read_scheduler_config', lambda: {'global':{'enabled':True}})
        monkeypatch.setattr(nextflow, 'assert_workflow_launch_allowed', lambda *a: None)
        monkeypatch.setattr(nextflow, 'configured_lane', lambda **k: None)
        monkeypatch.setattr(nextflow, 'transient_workflow_runner_mode', lambda: False)
        monkeypatch.setattr(nextflow, 'workflow_adapter_enabled', lambda: True)
        captured = []
        def dispatch(**kwargs):
            captured.append(kwargs)
            return {'accepted':True, 'run_id':'scratch-unit'}
        monkeypatch.setattr(nextflow, 'launch_via_workflow_adapter', dispatch)
        scheduler = gpu_orchestrator.GPUOrchestrator(native_core_store, lambda: [], nextflow.launch_nextflow_job)
        await scheduler._process_cycle()
        assert len(captured) == 1
        monkeypatch.setattr(nextflow, 'transient_workflow_runner_mode', lambda: True)
        monkeypatch.setattr(nextflow, 'configured_lane', lambda **k: 'scratch')
        monkeypatch.setattr(nextflow, 'assert_unit_lane', lambda *a: SimpleNamespace(job_id=job_id))
        monkeypatch.setenv(nextflow.TRANSIENT_WORKFLOW_UNIT_NAME_ENV, 'scratch-unit')
        monkeypatch.setenv(nextflow.TRANSIENT_WORKFLOW_OWNER_NONCE_ENV, 'scratch-nonce')
        await nextflow.launch_nextflow_job(**captured[0], allow_running_job=True)
        async with native_core_store() as core:
            job = await core.get(Job, job_id)
            assert job.status == 'completed', job.error_message
            output = Path(job.output_dir)
            artifacts = list(await core.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job_id)))
            alignment = next(a for a in artifacts if a.provenance['role'] == 'alignment')
            original = Path(alignment.storage_path).read_bytes()
            run_receipt = next(a for a in artifacts if a.provenance['role'] == 'receipt')
            evidence = json.loads(Path(run_receipt.storage_path).read_text())
            assert evidence['runtime']['sha256'] == hashlib.sha256(Path(image).read_bytes()).hexdigest()
            assert evidence['effective_settings']['engine'] == 'mafft'
            assert evidence['observations']['missing_ids'] == []
        async with setup_store() as pm_session, native_core_store() as core:
            await reconcile_run_group(pm_session, core, project, group['run_group_id'])
            attempt = await pm_session.get(ExperimentRunAttempt, group['runs'][0]['attempts'][0]['attempt_id'])
            terminal = json.loads(attempt.terminal_receipt_json)
            assert terminal.get('output_receipt_ids'), terminal
            receipt_id = terminal['output_receipt_ids'][0]
            await pm_session.commit()
        response = await client.delete(f'/api/jobs/{job_id}/permanent')
        assert response.status_code == 200, response.text
        assert not output.exists(), response.json()
        async with native_core_store() as core:
            assert await core.get(Job, job_id) is None
        await setup_store.kw['bind'].dispose()
        await native_core_store.kw['bind'].dispose()
        url = f'/api/family-msa/projects/{project}/receipts/{receipt_id}'
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.json()
        async with setup_store() as pm_session:
            from experiment_models import ExperimentExternalEntityReceipt
            receipt = await pm_session.get(ExperimentExternalEntityReceipt, receipt_id)
            ack = json.loads(receipt.acknowledgement_json)
            selected = next(a['resource_id'] for a in ack['metadata']['retained_artifacts'] if a['role'] == 'alignment')
        response = await client.get(f'{url}/artifacts/{selected}')
        assert response.status_code == 200 and response.content == original
        response = await client.get(f'{url}/documents/{selected}')
        assert response.status_code == 200, response.text
        assert len(response.json()['rows']) == 2
        assert (await client.get(url, headers={'x-test-actor':'other'})).status_code == 404
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
            assert (await anonymous.get(url)).status_code == 401
    await ngs_engine.dispose()
