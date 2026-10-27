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
@pytest.mark.parametrize('model,molecule,sequence', [
    ('family_msa_nucleic', 'dna', 'ACGT'),
    ('family_msa_protein', 'protein', 'ACDE'),
])
async def test_receipt_custody_survives_source_delete(read_model_store, tmp_path, monkeypatch, model, molecule, sequence):
    results = tmp_path / 'results'
    output = results / 'job'
    output.mkdir(parents=True)
    monkeypatch.setenv('BMS_RESULTS_DIR', str(results))
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'blobs'))
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
    async with core_factory() as core:
        await core.delete(await core.get(Job, 'canonical-job-completed'))
        await core.commit()
        assert not (await core.scalars(select(JobArtifact))).all()
    shutil.rmtree(output)
    async with read_model_store() as pm:
        for resource_id, expected in before.items():
            assert (await retained_path(pm, await pm.get(ExperimentArtifact, resource_id))).read_bytes() == expected
    await engine.dispose()
