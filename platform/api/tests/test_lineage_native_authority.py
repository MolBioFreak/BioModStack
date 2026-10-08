"""Real TEST publication -> ingestion -> addressed and whole-job receipts."""
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select, event
from database import Design, Job, RFD3LocalRedesignRequest, RFD3LocalRedesignCandidate
from services.global_experiments.adapters import CoreProteinResultAdapter, TypedCoreJobResultAdapter, Rfd3LocalRedesignAdapter, AdapterError, _canonical_json_sha256
from services.result_ingester import ingest_job_results, validate_rfd3_local_redesign_manifest
from tests.test_core_protein_candidates import artifacts, setup, job
from tests.rfd3_native_fixture import write_native_result


@pytest.fixture(autouse=True)
def test_build_identity(monkeypatch, tmp_path):
    # Deployment source binding is parent-owned; no scientific proof is mocked.
    monkeypatch.setattr('services.global_experiments.adapters.source_build_revision', lambda: 'lineage-test-build')
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path / 'inputs'))


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [2, 1000, 1001])
async def test_native_job_receipt_bounded_complete_and_historical_digest(tmp_path, monkeypatch, count):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    root = artifacts(tmp_path, ids=tuple(f'c{i:04d}' for i in range(count)))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current); await session.commit()
            assert await ingest_job_results(current.id, str(tmp_path), session) == count
            assert await ingest_job_results(current.id, str(tmp_path), session) == 0
            current.status = 'completed'; await session.commit()
        async with factory() as session:
            current = await session.get(Job, 'job')
            rows = list((await session.scalars(select(Design).order_by(Design.id))).all())
            if count == 2:
                # Existing supported review-backed scope must keep old hashes.
                for row in rows:
                    row.review_profile_id = 'shape_blueprint'
                    row.review_contract_source = 'producer'
                    row.review_artifact_manifest = dict(row.confidence_metrics['core_protein_candidate_artifacts'])
                await session.commit()
            if count == 1001:
                from fastapi import FastAPI
                from httpx import ASGITransport, AsyncClient
                from routers.designs import router
                from database import get_session
                app = FastAPI(); app.include_router(router, prefix='/designs')
                async def scoped_session():
                    yield session
                app.dependency_overrides[get_session] = scoped_session
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                    response = await client.get('/designs', params={'job_id': 'job', 'limit': 500, 'offset': 1000})
                    assert response.status_code == 200, response.text
                    payload = response.json()
                    assert payload['total'] == count and len(payload['designs']) == 1
            selected = CoreProteinResultAdapter()
            prior = []
            for row in rows:
                receipt = await selected.verify(session, row.id)
                prior.append(dict(design_id=receipt['entity_id'], entity_revision_id=receipt['entity_revision_id'], content_digest=receipt['content_digest'], contract_digest=receipt['contract_digest']))
            historical = _canonical_json_sha256(dict(job_id=current.id, model_id=current.model_id, mode=current.mode, status=current.status, artifacts=prior))
            adapter = TypedCoreJobResultAdapter('esmfold2')
            queries = []
            def track(conn, cursor, statement, parameters, context, executemany):
                if 'FROM designs' in statement and 'ORDER BY designs.id' in statement:
                    queries.append((statement, parameters))
            event.listen(engine.sync_engine, 'before_cursor_execute', track)
            whole = await adapter.verify(session, current.id)
            event.remove(engine.sync_engine, 'before_cursor_execute', track)
            assert whole['content_digest'] == historical
            assert whole['metadata']['artifact_count'] == count
            if count in (2, 1001):
                attached = await attach_and_reverify(tmp_path, session, adapter, current.id, whole['content_digest'])
            assert queries and all('LIMIT' in sql and 128 in params for sql, params in queries)
            ancillary = Design(id='ancillary', job_id=current.id, name='ancillary', source_stage='review', pdb_path='/unavailable')
            if count == 2:
                ancillary.pdb_path = rows[0].pdb_path
                ancillary.review_profile_id = 'shape_blueprint'
                ancillary.review_contract_source = 'producer'
                ancillary.review_artifact_manifest = {'structure': dict(rows[0].confidence_metrics['core_protein_candidate_artifacts']['structure'])}
            session.add(ancillary)
            await session.commit()
            assert (await adapter.verify(session, current.id))['content_digest'] == historical
            if count == 2:
                legacy = await adapter.verify(session, current.id, legacy_all_designs=True)
                assert legacy['metadata']['artifact_count'] == 3
                assert legacy['content_digest'] != historical
                await verify_historical_acknowledgement(tmp_path, session, attached, legacy)
            # Required last candidate corruption cannot be hidden by pagination.
            victim = rows[-1]
            Path(victim.json_path).write_text('corrupted TEST metadata')
            with pytest.raises(AdapterError):
                await adapter.verify(session, current.id)
            if count in (2, 1001):
                await assert_reverify_denied(tmp_path, session, attached)
            if count == 2:
                assert (await selected.verify(session, rows[0].id))['availability'] == 'available'
                with pytest.raises(AdapterError):
                    await selected.verify(session, victim.id)
                (root / 'manifest.json').write_text('corrupted producer declaration')
                with pytest.raises(AdapterError):
                    await selected.verify(session, rows[0].id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('trajectories', [False, True])
async def test_rfd3_producer_ingest_readback_retry_and_shared_semantics(tmp_path, monkeypatch, trajectories):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))
    request, digest, manifest_digest, root = write_native_result(tmp_path, job_id='rfd', request_id='req', trajectories=trajectories)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = Job(id='rfd', name='TEST', model_id='protein_local_redesign', mode='local_redesign', status='completed', output_dir=str(root), params={})
            record = RFD3LocalRedesignRequest(request_id='req', job_id='rfd', request_sha256=digest,
                profile_id=request['profile_id'], profile_registry_sha256=request['profile_registry_sha256'],
                redesign_mode=request['redesign_mode'], sequence_policy=request['sequence_policy'], status='queued', request_json=request)
            session.add_all([current, record]); await session.commit()
            assert await ingest_job_results('rfd', str(root), session) == 1
            assert await ingest_job_results('rfd', str(root), session) == 1
            rows = list((await session.scalars(select(RFD3LocalRedesignCandidate))).all())
            assert len(rows) == 1
            assert rows[0].metrics_json == {'summary_confidences': {'test': 0.75}}
            from routers.jobs import get_rfd3_local_redesign_result
            projection = await get_rfd3_local_redesign_result('rfd', session)
            assert projection['request']['result_manifest_sha256'] == manifest_digest
            assert projection['candidates'][0]['metrics'] == rows[0].metrics_json
            assert projection['capabilities']['trajectories']['available'] is trajectories
            adapter = Rfd3LocalRedesignAdapter()
            from collections import Counter
            from services import result_ingester
            reads = Counter()
            real_read = result_ingester._local_redesign_read_bytes
            def counted(path):
                reads[str(path)] += 1
                return real_read(path)
            with monkeypatch.context() as patcher:
                patcher.setattr(result_ingester, '_local_redesign_read_bytes', counted)
                receipt = await adapter.verify(session, 'req')
            assert reads and max(reads.values()) == 1
            assert receipt['content_digest'] == manifest_digest
            attached = await attach_and_reverify(tmp_path, session, adapter, 'req', manifest_digest)
            before = copy.deepcopy(record.preparation_receipt_json)
            # One hash-bound native proof also validates exact metrics semantics.
            manifest_path = root / 'collected/protein_local_redesign/rfd3_result_manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['candidates'][0]['metrics'] = {'invented': 1}
            from services.result_ingester import _local_redesign_canonical_sha
            unsigned = dict(manifest); unsigned.pop('manifest_sha256')
            manifest['manifest_sha256'] = _local_redesign_canonical_sha(unsigned)
            manifest_path.write_text(json.dumps(manifest))
            with pytest.raises(RuntimeError, match='metrics'):
                validate_rfd3_local_redesign_manifest(root, record)
            with pytest.raises(AdapterError, match='metrics'):
                await adapter.verify(session, 'req')
            await assert_reverify_denied(tmp_path, session, attached)
            assert record.preparation_receipt_json == before and not session.dirty
    finally:
        await engine.dispose()


async def attach_and_reverify(tmp_path, session, adapter, entity_id, expected_digest):
    from experiment_database import create_experiment_engine, create_experiment_session_factory
    from experiment_migrations import run_all
    from experiment_services import create_project, create_global_experiment, create_domain_experiment
    from services.global_experiments.receipts import attach_verified_entity, reverify_source_receipt
    from services.global_experiments.result_surfaces import result_surface_for_receipt
    from tests.test_project_manager_adapters import _project_payload, _global_payload, _domain_payload
    path = tmp_path / 'projects.db'; run_all(path)
    engine = create_experiment_engine(f'sqlite+aiosqlite:///{path}')
    factory = create_experiment_session_factory(engine)
    try:
        async with factory() as experiments:
            project = await create_project(experiments, _project_payload())
            glob = await create_global_experiment(experiments, project.id, _global_payload())
            domain = await create_domain_experiment(experiments, project.id, glob.id, _domain_payload('protein_in_silico'))
            await experiments.commit()
            context = dict(project_id=project.id, global_experiment_id=glob.id, domain_experiment_id=domain.id)
            args = dict(**context, adapter_id=adapter.adapter_id, entity_id=entity_id,
                operation='link_output', role='produced', note=None, expected_head_generation=project.head_generation)
            receipt = await attach_verified_entity(experiments, session, **args)
            replay = await attach_verified_entity(experiments, session, **args)
            assert replay['attachment_receipt_id'] == receipt['attachment_receipt_id']
            assert receipt['source_receipt']['content_digest'] == expected_digest
            await experiments.commit()
            await reverify_source_receipt(experiments, session, **context, source_receipt_id=receipt['source_receipt_id'])
            surface = await result_surface_for_receipt(experiments, project_id=project.id, receipt_id=receipt['source_receipt_id'])
            assert surface['readiness'] == 'ready'
            return dict(**context, source_receipt_id=receipt['source_receipt_id'])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_boltz_native_producer_project_proof_rejects_selected_sidecar(tmp_path, monkeypatch):
    from tests.test_boltz_scientific_persistence import publication, job as boltz_job
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = boltz_job(tmp_path)
            session.add(current); await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session) == 1
            assert await ingest_job_results('job', str(tmp_path), session) == 0
            current.status = 'completed'; await session.commit()
        async with factory() as session:
            row = (await session.scalars(select(Design))).one()
            receipt = await CoreProteinResultAdapter().verify(session, row.id)
            assert receipt['content_digest'] == row.confidence_metrics['core_protein_candidate_artifacts']['structure']['sha256']
            await attach_and_reverify(tmp_path, session, CoreProteinResultAdapter(), row.id, receipt['content_digest'])
            Path(row.confidence_metrics['core_protein_candidate_artifacts']['ledger']['path']).write_text('corrupt TEST ledger')
            with pytest.raises(AdapterError):
                await CoreProteinResultAdapter().verify(session, row.id)
    finally:
        await engine.dispose()


async def assert_reverify_denied(tmp_path, session, attachment):
    from experiment_database import create_experiment_engine, create_experiment_session_factory
    from services.global_experiments.receipts import reverify_source_receipt
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{tmp_path / 'projects.db'}")
    factory = create_experiment_session_factory(engine)
    try:
        async with factory() as experiments:
            with pytest.raises(AdapterError):
                await reverify_source_receipt(experiments, session, **attachment)
    finally:
        await engine.dispose()


async def verify_historical_acknowledgement(tmp_path, session, attachment, legacy):
    from experiment_database import create_experiment_engine, create_experiment_session_factory
    from services.global_experiments.receipts import reverify_source_receipt
    from experiment_operations import register_external_entity_receipt
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{tmp_path / 'projects.db'}")
    try:
        async with create_experiment_session_factory(engine)() as experiments:
            legacy['metadata'].pop('result_scope')
            external = await register_external_entity_receipt(
                experiments, workspace_id=attachment['project_id'], store_id=legacy['store_id'],
                entity_kind=legacy['entity_kind'], entity_id=legacy['entity_id'],
                generation_or_revision=legacy['entity_revision_id'], content_digest=legacy['content_digest'],
                availability='available', acknowledgement=legacy, verification_authority=TypedCoreJobResultAdapter('esmfold2').adapter_id)
            from experiment_models import ExperimentLineageEdge
            experiments.add(ExperimentLineageEdge(
                id='historical-attachment', workspace_id=attachment['project_id'],
                source_resource_id=attachment['domain_experiment_id'], target_resource_id=external.id,
                edge_mode='produced', edge_key='historical-TEST'))
            await experiments.commit()
            result = await reverify_source_receipt(experiments, session, **{**attachment, 'source_receipt_id': external.id})
            assert result['source_digest'] == legacy['content_digest']
    finally:
        await engine.dispose()
