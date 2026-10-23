"""Automatic native Job -> Project attachment, isolated real owners; NOT inference.

Only native producer bytes and the already-terminal canonical Job are inert.
Project prepare/reserve/claim/consume, publication registration, pinned result
adapter verification and lineage/receipt persistence run without substitutes.
"""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from database import Design, Job
from experiment_models import (
    ExperimentExternalEntityReceipt, ExperimentLaunchContext, ExperimentLineageEdge,
    ExperimentRunAttempt, ExperimentWorkflowPreparation,
)
from experiment_services import load_workflow_plan_authority, reconcile_run_group
from routers import project_manager as pm
from services.global_experiments.launch_contexts import claim_launch_context, consume_launch_context
from services.rfd4_proteina_results import publish_native_generation, read_published_generation_page
from test_denovo_project_http import request_for
from test_native_protein_launch_authority_regression import (  # noqa: F401
    native_core_store, native_http, policy_cache,
)
from test_project_workflow_setups import setup_store, _project_payload  # noqa: F401


async def _bound_terminal(client, experiments, core_store, tmp_path, model, status):
    """Real scratch HTTP preparation/reservation and durable inert Job binding."""
    capability, request = request_for(model + ':generate', tmp_path)
    response = await client.post('/api/projects', json=_project_payload('NOT inference: terminal publication'))
    assert response.status_code == 201, response.text
    project_id = response.json()['id']
    response = await client.post(f'/api/projects/{project_id}/workflow-setups', json={
        'schema': 'bms.project-workflow-setup.create.v1', 'relationship_kind': 'primary',
        'experiment': {'name': 'Terminal fixture', 'objective': 'NOT inference'},
        'domain_kind': 'protein_in_silico', 'capability_id': capability,
    }, headers={'Idempotency-Key': 'create'})
    assert response.status_code == 201, response.text
    setup = response.json()
    path = f"/api/projects/{project_id}/workflow-setups/{setup['setup_context_id']}"
    response = await client.put(path + '/draft', json={
        'expected_generation': 0, 'draft': {'native_job_request': request}},
        headers={'Idempotency-Key': 'save'})
    assert response.status_code == 200, response.text
    response = await client.post(path + '/prepare-launch', json={'expected_generation': 1},
                                 headers={'Idempotency-Key': 'prepare'})
    assert response.status_code == 200, response.text
    prepared = response.json()
    domain_path = (f"/api/projects/{project_id}/experiments/{setup['global_experiment_id']}"
                   f"/domains/{setup['domain_experiment_id']}")
    response = await client.post(domain_path + '/run-groups', json={'preparation_launches': [{
        'preparation_id': prepared['preparation_id'], 'launch_context_id': prepared['launch_context_id']}]},
        headers={'Idempotency-Key': 'run'})
    assert response.status_code == 201, response.text
    group = response.json()
    attempt = group['runs'][0]['attempts'][0]
    async with experiments() as session, core_store() as core:
        preparation = await session.get(ExperimentWorkflowPreparation, prepared['preparation_id'])
        scheduler = json.loads(preparation.scheduler_payload_json)
        authority, payload = await load_workflow_plan_authority(session, prepared['diagnostics']['workflow_id'])
        result_adapter = f'bms.native-binder.{model}.adapter.v1'
        # At 00e2fc1 normalized_job_plan_contract loses result_adapter_ids.
        # Do not patch that owner or hide the attachment failure with a double.
        launch_adapter = scheduler['params']['workflow_adapter']
        assert payload['capability']['workflow_adapter_id'] == launch_adapter
        assert launch_adapter != result_adapter
        job = Job(id=attempt['canonical_job_id'], name=scheduler['name'], model_id=model,
            mode=scheduler['mode'], params=scheduler['params'], status=status,
            provenance={'fixture': 'NOT inference'}, completed_at=datetime.now(timezone.utc),
            output_dir=str(tmp_path / 'results' / model),
            error_message=None if status == 'completed' else 'Inert partial publication fixture')
        core.add(job)
        await core.flush()
        context, token = await claim_launch_context(session, prepared['launch_context_id'])
        context, binding = await consume_launch_context(session, launch_context_id=context.launch_context_id,
            claim_token=token, canonical_job_id=job.id, canonical_batch_id=None)
        await pm._project_bound_job(session, core, context, job, binding)
        assert context.canonical_job_id == job.id
        durable = await session.get(ExperimentRunAttempt, attempt['attempt_id'])
        assert durable.external_binding_receipt_json
        await core.commit()
        await session.commit()
    return project_id, setup['domain_experiment_id'], group['run_group_id'], attempt['attempt_id'], job.id, result_adapter


async def _publish(core_store, job_id, count):
    """Inert producer manifest, using the real native publisher; NOT inference.

    Mirrors test_denovo_results_receiving's document + observation population.
    Two records deliberately distinguish total from the adapter's limit=1 page.
    """
    from pathlib import Path
    async with core_store() as core:
        job = await core.get(Job, job_id)
        model = job.model_id
        root = Path(job.output_dir) / 'collected' / model
        root.mkdir(parents=True)
        manifest = {'model_id': model, 'contract': model + '_v1', 'artifacts': [],
            'execution_status': job.status, 'population_completeness': 'partial',
            'observation_errors': ['NOT inference: inert native publisher fixture']}
        if count:
            assert count == 2
            (root / 'one.cif').write_text('data_NOT_INFERENCE\n')
            manifest['artifacts'] = [{'path': 'one.cif'}]
        if model == 'rfd4_proteina':
            name = 'manifest.json'
            manifest.update(records=[{'example_id': 'one', 'document': 'one.cif', 'metrics': {}}] if count else [],
                events=[{'event': 'diagnostic', 'message': 'NOT inference'}] if count else [])
        else:
            name = 'complexa-results.json'
            manifest.update(documents=[{'path': 'one.cif', 'role': 'lookahead'}] if count else [],
                observations=[{'values': {'loss': None}}] if count else [])
        (root / name).write_text(json.dumps(manifest))
        await publish_native_generation(core, job, Path(job.output_dir))
        page = await read_published_generation_page(job, core, limit=1)
        assert page['total'] == count and len(page['records']) == min(count, 1)
        assert page['execution_status'] == job.status
        assert page['population_completeness'] == 'partial'
        assert list(await core.scalars(select(Design))) == []
        # Real registered reader, independent of the launch/result dispatch bug.
        from services.global_experiments.adapters import NativeBinderJobResultAdapter
        verified = await NativeBinderJobResultAdapter(model).verify(core, job_id)
        assert verified['metadata']['record_count'] == count
        assert verified['metadata']['job_status'] == job.status
        await core.commit()


async def _reconcile(experiments, core_store, ids):
    project_id, _, group_id, attempt_id, _, _ = ids
    async with experiments() as session, core_store() as core:
        group = await reconcile_run_group(session, core, project_id, group_id)
        attempt = await session.get(ExperimentRunAttempt, attempt_id)
        receipt = json.loads(attempt.terminal_receipt_json)
        snapshot = (group.state, group.generation, attempt.state, attempt.terminal_receipt_sha256, receipt)
        await session.commit()
        return snapshot


async def _assert_links(experiments, ids, receipt, count):
    _, domain_id, _, attempt_id, job_id, adapter = ids
    assert receipt.get('adapter_id') == adapter, receipt.get('output_evidence', receipt)
    assert receipt['output_count'] == count
    assert len(receipt['output_receipt_ids']) == len(receipt['output_content_digests']) == 1
    assert 'output_evidence' not in receipt
    async with experiments() as session:
        rows = list(await session.scalars(select(ExperimentExternalEntityReceipt)))
        assert len(rows) == 1
        row = rows[0]
        assert row.id == receipt['output_receipt_ids'][0]
        assert row.entity_id == job_id and row.verification_authority == adapter
        assert row.content_digest == receipt['output_content_digests'][0]
        edges = list(await session.scalars(select(ExperimentLineageEdge).where(
            ExperimentLineageEdge.target_resource_id == row.id)))
        assert {(e.source_resource_id, e.edge_key, e.edge_mode) for e in edges} == {
            (domain_id, f'terminal-output:{attempt_id}', 'produced'),
            (attempt_id, 'verified-terminal-output', 'produced')}
        assert all(json.loads(e.metadata_json)['adapter_id'] == adapter for e in edges)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,status,count', [
    ('rfd4_proteina', 'completed', 2),
    ('complexa', 'failed', 2),
    ('rfd4_proteina', 'cancelled', 2),
    ('complexa', 'completed', 0),
])
async def test_automatic_terminal_publication_cold_idempotent(
        native_http, setup_store, native_core_store, tmp_path, model, status, count):
    ids = await _bound_terminal(native_http, setup_store, native_core_store, tmp_path, model, status)
    await _publish(native_core_store, ids[4], count)
    first = await _reconcile(setup_store, native_core_store, ids)
    assert first[0] == first[2] == status
    assert first[4]['status'] == first[4]['terminal_state'] == status
    # Discard connection pools as well as ORM sessions: committed cold readback.
    await setup_store.kw['bind'].dispose()
    await native_core_store.kw['bind'].dispose()
    again = await _reconcile(setup_store, native_core_store, ids)
    assert again == first
    await _assert_links(setup_store, ids, again[4], count)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
async def test_missing_publication_is_evidence_not_a_terminal_gate(
        native_http, setup_store, native_core_store, tmp_path, status):
    ids = await _bound_terminal(native_http, setup_store, native_core_store, tmp_path, 'complexa', status)
    first = await _reconcile(setup_store, native_core_store, ids)
    assert first[0] == first[2] == status
    receipt = first[4]
    assert receipt['status'] == receipt['terminal_state'] == status
    assert receipt['output_evidence']['core_status'] == status
    assert receipt['output_evidence']['state'] == 'pending'
    assert receipt['output_evidence']['message']  # uncertainty, not admission
    assert not receipt.get('output_receipt_ids')
    assert receipt.get('output_count') is None  # unavailable is not registered zero yield
    assert await _reconcile(setup_store, native_core_store, ids) == first
    async with setup_store() as session:
        assert list(await session.scalars(select(ExperimentExternalEntityReceipt))) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model,count', [('rfd4_proteina', 2), ('complexa', 0)])
async def test_native_publisher_adapter_total_cold_readback(
        native_http, setup_store, native_core_store, tmp_path, model, count):
    ids = await _bound_terminal(native_http, setup_store, native_core_store, tmp_path, model, 'failed')
    await _publish(native_core_store, ids[4], count)
    await native_core_store.kw['bind'].dispose()
    from services.global_experiments.adapters import NativeBinderJobResultAdapter
    async with native_core_store() as core:
        verified = await NativeBinderJobResultAdapter(model).verify(core, ids[4])
        assert verified['metadata']['record_count'] == count
        assert verified['metadata']['job_status'] == 'failed'
        assert verified['verifier_id'] == ids[5]
        assert list(await core.scalars(select(Design))) == []


@pytest.mark.asyncio
async def test_bounded_late_publication_automatically_attaches(
        native_http, setup_store, native_core_store, tmp_path):
    ids = await _bound_terminal(native_http, setup_store, native_core_store, tmp_path, 'rfd4_proteina', 'failed')
    before = await _reconcile(setup_store, native_core_store, ids)
    assert before[0] == before[2] == 'failed' and before[4]['output_evidence']
    await _publish(native_core_store, ids[4], 2)
    # Two deterministic reconciliations, no sleeps, services or inference.
    for _ in range(2):
        after = await _reconcile(setup_store, native_core_store, ids)
        assert after[0] == after[2] == 'failed'
        if after[4].get('output_receipt_ids'):
            break
    assert after[4].get('output_receipt_ids'), 'Late publication remains unattached after two real reconciliations'
    await _assert_links(setup_store, ids, after[4], 2)
