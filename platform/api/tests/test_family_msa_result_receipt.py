"""Scratch JobArtifact -> PM receipt and retained byte lifecycle."""
import json
import os
import shutil

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job, JobArtifact
from experiment_models import ExperimentArtifact, ExperimentExternalEntityReceipt, ExperimentLineageEdge, ExperimentRevision, ExperimentRunAttempt
from experiment_services import reconcile_run_group
from services.family_msa.execution import run_owned_job
from services.family_msa.publication import retained_path
from tests.test_project_manager_read_models import read_model_store, _hierarchy, _add_workflow_runs, _add_retry_attempts


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [False, True], ids=['fixture-engine', 'native-image'])
@pytest.mark.parametrize('model,molecule,sequence', [
    ('family_msa_nucleic', 'dna', 'ACGT'),
    ('family_msa_protein', 'protein', 'ACDE'),
])
async def test_receipt_custody_survives_source_delete(read_model_store, tmp_path, monkeypatch, model, molecule, sequence, native):
    results = tmp_path / 'results'
    output = results / 'job'
    output.mkdir(parents=True)
    monkeypatch.setenv('BMS_RESULTS_DIR', str(results))
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'blobs'))
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    if native:
        image = os.environ.get('BMS_TEST_FAMILY_MSA_SIF')
        if not image:
            pytest.skip('set BMS_TEST_FAMILY_MSA_SIF to the isolated native image')
        monkeypatch.setenv('BMS_FAMILY_MSA_SIF', image)
    else:
        monkeypatch.delenv('BMS_FAMILY_MSA_SIF', raising=False)
        # Fixture process exercises the real producer/DB path, not native MAFFT.
        executable = tmp_path / 'mafft'
        executable.write_text('#!/bin/sh\nfor a in "$@"; do last="$a"; done\ncat "$last"\n')
        executable.chmod(0o755)
        monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as con:
        await con.run_sync(Base.metadata.create_all)
    core_factory = async_sessionmaker(engine, expire_on_commit=False)
    payload = {'roster': {'molecule': molecule, 'rows': [{'row_id': 'r1', 'header': 'one', 'sequence': sequence}]}, 'settings': {'threads': 1}}
    async with core_factory() as core:
        core.add(Job(id='canonical-job-completed', name='fixture', model_id=model, mode='family_msa',
                     status='running', queue_status='running', nextflow_run_id='unit', params=payload, output_dir=str(output)))
        await core.commit()
    await run_owned_job('canonical-job-completed', payload, str(output), core_factory)
    async with core_factory() as core:
        job = await core.get(Job, 'canonical-job-completed')
        assert job.status == 'completed'
        artifacts = (await core.scalars(select(JobArtifact))).all()
        assert {a.provenance['role'] for a in artifacts} == {'result_manifest', 'input', 'engine_log', 'receipt', 'alignment'}
        receipt = next(a for a in artifacts if a.provenance['role'] == 'receipt')
        observation = json.loads((output / receipt.logical_path).read_text())['observations']
        assert observation['output_headers'] == ['r1']
        assert observation['missing_ids'] == [] and observation['changed_residue_ids'] == []
        from services.global_experiments.adapters import registry, AdapterError
        adapter = registry.get(f'bms.family-msa.{model}.adapter.v1')
        alignment = next(a for a in artifacts if a.provenance['role'] == 'alignment')
        alignment_path = output / alignment.logical_path
        original_bytes = alignment_path.read_bytes()
        # MAFFT may change nucleotide case; compare the HTTP document to the
        # actual retained engine output rather than the submitted spelling.
        expected_aligned = ''.join(original_bytes.decode().splitlines()[1:])
        assert expected_aligned.upper() == sequence.upper()
        alignment_path.write_bytes(b'corrupt')
        with pytest.raises(AdapterError):
            await adapter.verify(core, job.id)
        alignment_path.write_bytes(original_bytes)
    async with read_model_store() as pm:
        project, _, domain = await _hierarchy(pm)
        workflow, runs = await _add_workflow_runs(pm, project_id=project.id, domain_experiment_id=domain.id)
        await _add_retry_attempts(pm, project_id=project.id, run=runs[0])
        attempt = await pm.get(ExperimentRunAttempt, 'run-attempt-completed')
        attempt.external_binding_receipt_json = '{}'
        revision = await pm.get(ExperimentRevision, workflow.current_revision_id)
        revision.canonical_payload = json.dumps({'adapter_id': f'bms.family-msa.{model}.adapter.v1'})
        attempt.terminal_receipt_json = json.dumps({'output_evidence': {'state': 'pending'}})
        await pm.commit()
        from services.family_msa import publication
        original = publication.retain_job_artifact
        calls = 0
        async def fail_after_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('injected attachment failure')
            return await original(*args, **kwargs)
        monkeypatch.setattr(publication, 'retain_job_artifact', fail_after_first)
        async with core_factory() as core:
            await reconcile_run_group(pm, core, project.id, runs[0].run_group_id)
            await pm.commit()
            assert not (await pm.scalars(select(ExperimentArtifact))).all()
            monkeypatch.setattr(publication, 'retain_job_artifact', original)
            await reconcile_run_group(pm, core, project.id, runs[0].run_group_id)
            await pm.commit()
        edge = await pm.scalar(select(ExperimentLineageEdge).where(
            ExperimentLineageEdge.source_resource_id == attempt.resource_id,
            ExperimentLineageEdge.edge_key == 'verified-terminal-output'))
        assert edge is not None
        receipt = await pm.get(ExperimentExternalEntityReceipt, edge.target_resource_id)
        ack = json.loads(receipt.acknowledgement_json)
        retained = ack['metadata']['retained_artifacts']
        assert len(retained) == len(artifacts)
        before = {x['resource_id']: (await retained_path(pm, await pm.get(ExperimentArtifact, x['resource_id']))).read_bytes() for x in retained}
        from experiment_services import add_audit_event
        add_audit_event(pm, workspace_id=project.id, resource_id=project.id, event_type='workspace_owner_bound', generation=1, payload={'principal_id': 'owner'})
        await pm.commit()
        project_id, receipt_id = project.id, receipt.id
        other_project, _, _ = await _hierarchy(pm)
        add_audit_event(pm, workspace_id=other_project.id, resource_id=other_project.id, event_type='workspace_owner_bound', generation=1, payload={'principal_id': 'owner'})
        await pm.commit()
        other_project_id = other_project.id
        from services.global_experiments.result_surfaces import result_surface_for_receipt
        surface = await result_surface_for_receipt(pm, project_id=project_id, receipt_id=receipt_id)
        assert surface['route']['query'] == {'project_id': project_id, 'receipt_id': receipt_id}
        selected = next(x['resource_id'] for x in retained if x['role'] == 'alignment')
    import httpx
    from fastapi import FastAPI, Request
    from database import get_session
    from experiment_database import get_experiment_session
    from routers.family_msa_results import router
    app = FastAPI()
    app.include_router(router)
    @app.middleware('http')
    async def principal(request: Request, call_next):
        actor = request.headers.get('x-test-actor')
        if actor:
            request.state.authenticated_principal = {'id': actor, 'roles': ['operator']}
        return await call_next(request)
    async def core_dep():
        async with core_factory() as session:
            yield session
    async def pm_dep():
        async with read_model_store() as session:
            yield session
    app.dependency_overrides[get_session] = core_dep
    app.dependency_overrides[get_experiment_session] = pm_dep
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
    job_url = '/api/family-msa/jobs/canonical-job-completed'
    assert (await client.get(job_url)).status_code == 401
    assert (await client.get(job_url, headers={'x-test-actor':'owner'})).status_code == 200
    assert (await client.get(f'{job_url}/documents/{alignment.id}', headers={'x-test-actor':'owner'})).json()['rows'][0]['aligned'] == expected_aligned
    assert (await client.get(f'{job_url}/artifacts/{alignment.id}', headers={'x-test-actor':'owner'})).content == original_bytes
    async with core_factory() as core:
        await core.delete(await core.get(Job, 'canonical-job-completed'))
        await core.commit()
        assert not (await core.scalars(select(JobArtifact))).all()
    shutil.rmtree(output)
    async with read_model_store() as pm:
        for resource_id, expected in before.items():
            assert (await retained_path(pm, await pm.get(ExperimentArtifact, resource_id))).read_bytes() == expected
    url = f'/api/family-msa/projects/{project_id}/receipts/{receipt_id}'
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers={'x-test-actor':'other'})).status_code == 404
    assert (await client.get(f'/api/family-msa/projects/{other_project_id}/receipts/{receipt_id}', headers={'x-test-actor':'owner'})).status_code == 404
    assert (await client.get(url, headers={'x-test-actor':'owner'})).status_code == 200
    assert (await client.get(f'{url}/documents/{selected}', headers={'x-test-actor':'owner'})).json()['rows'][0]['aligned'] == expected_aligned
    assert (await client.get(f'{url}/artifacts/{selected}', headers={'x-test-actor':'owner'})).content == original_bytes
    assert (await client.get(f'{url}/artifacts/not-in-receipt', headers={'x-test-actor':'owner'})).status_code == 404
    assert (await client.get(job_url, headers={'x-test-actor':'owner'})).status_code == 404
    await client.aclose()
    await engine.dispose()
