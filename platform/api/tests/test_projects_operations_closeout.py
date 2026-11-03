"""Connected Project draft and native custody regressions; inert science only."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from database import Design, Job, JobArtifact
from experiment_models import ExperimentExternalEntityReceipt
from services.global_experiments import receipts
from test_project_workflow_setups import setup_store, _project_payload  # noqa: F401
from test_native_protein_launch_authority_regression import native_core_store, native_http, policy_cache  # noqa: F401
from test_denovo_complete_terminal_project import _bound_terminal, _publish, _reconcile


@pytest.mark.asyncio
@pytest.mark.parametrize('capability', ['protein.de_novo.rfd3', 'protein.de_novo.disco', 'protein.de_novo.laproteina'])
async def test_incomplete_native_editor_null_persists_and_cold_reopens(native_http, setup_store, capability):
    response = await native_http.post('/api/projects', json=_project_payload('Incomplete editor'))
    assert response.status_code == 201, response.text
    project_id = response.json()['id']
    response = await native_http.post(f'/api/projects/{project_id}/workflow-setups', json={
        'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary',
        'experiment': {'name': 'Unfinished native editor', 'objective': 'NOT inference'},
        'domain_kind': 'protein_in_silico', 'capability_id': capability},
        headers={'Idempotency-Key': 'create'})
    assert response.status_code == 201, response.text
    path = f"/api/projects/{project_id}/workflow-setups/{response.json()['setup_context_id']}"
    draft = {'native_job_request': None, 'editor_state': {'params': {'length': None, 'seed': 0},
        'execution_target_id': None, 'enabled': False, 'inactive_draft': {'text': ''}}}
    for generation in range(2):
        response = await native_http.put(path + '/draft', json={'expected_generation': generation, 'draft': draft},
            headers={'Idempotency-Key': f'save-{generation}'})
        assert response.status_code == 200, response.text
        assert response.json()['validation_state'] == 'incomplete'
    await setup_store.kw['bind'].dispose()
    response = await native_http.get(path)
    assert response.status_code == 200, response.text
    loaded = response.json()['draft']
    assert loaded['native_job_request'] is None
    for key, value in draft['editor_state'].items():
        assert loaded[key] == value
    response = await native_http.post(path + '/prepare-launch', json={'expected_generation': 2},
        headers={'Idempotency-Key': 'not-executable'})
    assert response.status_code in {409, 422}


@pytest.mark.asyncio
@pytest.mark.parametrize('model,status,count', [('rfd4_proteina', 'failed', 2), ('complexa', 'completed', 0)])
async def test_project_native_custody_survives_producer_deletion(
        native_http, setup_store, native_core_store, tmp_path, monkeypatch, model, status, count):
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'project-artifacts'))
    from services import job_result_roots
    monkeypatch.setattr(job_result_roots, 'get_results_dir', lambda: tmp_path / 'results')
    ids = await _bound_terminal(native_http, setup_store, native_core_store, tmp_path, model, status)
    await _publish(native_core_store, ids[4], count)
    terminal = await _reconcile(setup_store, native_core_store, ids)
    assert terminal[4]['status'] == status
    assert terminal[4].get('output_receipt_ids'), terminal[4].get('output_evidence', terminal[4])
    async with native_core_store() as core:
        job = await core.get(Job, ids[4])
        root = Path(job.output_dir)
        expected = {a.logical_path: (a.sha256, Path(a.storage_path).read_bytes())
                    for a in await core.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))}
        assert expected
        await core.execute(delete(Design).where(Design.job_id == job.id))
        await core.execute(delete(JobArtifact).where(JobArtifact.owner_job_id == job.id))
        await core.delete(job)
        await core.commit()
    shutil.rmtree(root)
    await native_core_store.kw['bind'].dispose()
    await setup_store.kw['bind'].dispose()
    async with setup_store() as session:
        row = await session.scalar(select(ExperimentExternalEntityReceipt))
        ack = json.loads(row.acknowledgement_json)
        assert ack['metadata'].get('retained_artifacts'), ack
        acknowledgement, files = await receipts.retained_native_result(session, workspace_id=ids[0], receipt_id=row.id)
        assert acknowledgement['metadata']['record_count'] == count
        assert len(files) == len(expected)
        for entry, path in files:
            sha, raw = expected[entry['logical_path']]
            assert path.read_bytes() == raw
            assert hashlib.sha256(raw).hexdigest() == entry['sha256'] == sha
        from services.family_msa.publication import CustodyError
        with pytest.raises(CustodyError):
            await receipts.retained_native_result(session, workspace_id='another-project', receipt_id=row.id)
        response = await native_http.get(f'/api/projects/{ids[0]}/native-results/{row.id}')
        assert response.status_code == 200, response.text
        assert len(response.json()['artifacts']) == len(expected)
        for entry in response.json()['artifacts']:
            content = await native_http.get(entry['content_url'])
            assert content.status_code == 200, content.text
            assert content.content == expected[entry['logical_path']][1]
        files[0][1].write_bytes(b'changed retained bytes')
        with pytest.raises(CustodyError):
            await receipts.retained_native_result(session, workspace_id=ids[0], receipt_id=row.id)


@pytest.mark.asyncio
async def test_disabled_general_companion_does_not_shadow_enabled_round(native_core_store, setup_store):
    from schemas import JobCreate
    from services import binder_round as rounds
    request = JobCreate(name='Two accepted envelopes', model_id='protein_modification_experimental', mode='de_novo_design',
        sequence_design={'model_id': 'proteinmpnn', 'enabled': False, 'params': {}},
        binder_round={'enabled': True, 'sequence_design': {'model_id': 'proteinmpnn'},
                      'prediction': {'model_id': 'protenix'}})
    async with native_core_store() as core:
        job = Job(id='two-envelopes', name=request.name, model_id=request.model_id, mode=request.mode,
                  status='completed', params={}, provenance={
                      rounds.SEQUENCE_REQUEST: request.sequence_design.model_dump(mode='json'),
                      rounds.REQUEST: request.binder_round.model_dump(mode='json')})
        core.add(job)
        await core.commit()
    async with native_core_store() as core, setup_store() as experiments:
        result = await rounds.reconcile_round(core, experiments, 'two-envelopes')
        assert result['state'] == 'completed'
        assert result['kind'] == 'binder'
        assert result['sequence_design']['enabled'] is False
        assert result['binder_round']['enabled'] is True
        assert result['steps'] == {}  # Published empty input, not a phantom Job.


def test_current_workflow_adapter_registration_invariant():
    from services.workflow_adapter_registry import TYPED_CORE_JOB_ADAPTERS, is_workflow_adapter_registered
    from services.global_experiments.adapters import NativeBinderJobResultAdapter, registry
    for adapter in TYPED_CORE_JOB_ADAPTERS:
        assert is_workflow_adapter_registered('typed_core_job', adapter)
        assert not is_workflow_adapter_registered('typed_core_job', adapter + ' ')
        assert not is_workflow_adapter_registered('unregistered', adapter)
    for model in ('proteinmpnn', 'fampnn', 'caliby_experimental', 'ligandmpnn', 'protonpottsmpnn'):
        launch = f'bms.core-job.{model}.adapter.v1'
        assert launch in TYPED_CORE_JOB_ADAPTERS
        assert isinstance(registry.get(launch), NativeBinderJobResultAdapter)
        assert isinstance(registry.get(f'bms.native-binder.{model}.adapter.v1'), NativeBinderJobResultAdapter)
