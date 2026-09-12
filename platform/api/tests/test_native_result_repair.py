"""Generated TEST artifacts exercise native publication, not model inference."""
import time
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from database import Design, Job, FrustraMPNNResult
from test_boltz_scientific_persistence import publication, job, setup
from services.result_ingester import ingest_job_results


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 4])
async def test_selected_snapshot_does_not_read_siblings(tmp_path, monkeypatch, count):
    publication(tmp_path, count=count)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
            rows = list((await session.execute(select(Design).order_by(Design.name))).scalars())
            from services import boltz_scientific_persistence as owner
            from services.boltz_scientific_consumer import verified_boltz_design, compute_persisted_native_metric
            original = owner._snapshot
            reads = []
            def counted(root, key):
                artifact, content = original(root, key)
                reads.append((key, len(content)))
                return artifact, content
            monkeypatch.setattr(owner, '_snapshot', counted)
            started = time.perf_counter()
            selected = await verified_boltz_design(rows[0], session)
            seconds = time.perf_counter() - started
            assert len(reads) == 8  # inventory, binding, manifest and five native payloads
            assert all('_model_1' not in key for key, _ in reads)
            before = len(reads)
            metric = await compute_persisted_native_metric(rows[0], 'residue_plddt', session, selected=selected)
            assert metric.status == 'ok'
            assert len(reads) == before
            if count > 1:
                Path(rows[-1].pdb_path).unlink()
                assert (await verified_boltz_design(rows[0], session))['artifacts'] == selected['artifacts']
                with pytest.raises(RuntimeError):
                    await ingest_job_results('job', str(tmp_path), session)
            # Retained response bytes remain bound; the next request denies mutation.
            Path(rows[0].pdb_path).write_bytes(b'changed')
            assert hashlib.sha256(selected['snapshots']['structure']).hexdigest() == selected['artifacts']['structure']['sha256']
            with pytest.raises(ValueError):
                await verified_boltz_design(rows[0], session)
            print('NATIVE_READ_MEASUREMENT', json.dumps({'cohort': count, 'initial_opens': before,
                'initial_bytes': sum(size for _, size in reads[:before]), 'seconds': seconds}))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['symlink', 'inode', 'attempt'])
async def test_addressed_read_preserves_trust_boundary(tmp_path, damage):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
            row = (await session.execute(select(Design))).scalar_one()
            from services.boltz_scientific_consumer import verified_boltz_design
            if damage == 'attempt':
                current.retry_count += 1
            else:
                source = Path(row.pdb_path)
                replacement = source.with_suffix('.replacement')
                replacement.write_bytes(source.read_bytes() if damage == 'symlink' else b'foreign')
                source.unlink()
                if damage == 'symlink':
                    source.symlink_to(replacement)
                else:
                    replacement.rename(source)
            with pytest.raises((ValueError, OSError)):
                await verified_boltz_design(row, session)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['esmfold2', 'esmfold2_experimental', 'protenix', 'fampnn', 'boltzgen_child', 'antibody_child'])
async def test_marked_spatial_applicability_agrees_with_routes(tmp_path, monkeypatch, model):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
            row = (await session.execute(select(Design))).scalar_one()
            # Even a copied valid Boltz block never grants another model Boltz axes.
            current.model_id = model
            from services import boltz_scientific_consumer as boltz
            async def forbidden(*args, **kwargs):
                raise AssertionError('non-Boltz dispatched to Boltz')
            monkeypatch.setattr(boltz, 'verified_boltz_design', forbidden)
            from fastapi import FastAPI
            from httpx import ASGITransport, AsyncClient
            from routers.designs import router
            from database import get_session
            app = FastAPI()
            app.include_router(router, prefix='/designs')
            async def dependency():
                yield session
            app.dependency_overrides[get_session] = dependency
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                for url in ['/designs?job_id=job', '/designs/by-job/job']:
                    response = await client.get(url)
                    assert response.status_code == 200, response.text
                    assert all(d['scientific_structure_document'] is None for d in response.json()['designs'])
                detail = await client.get(f'/designs/{row.id}')
                assert detail.status_code == 200, detail.text
                assert detail.json()['scientific_structure_document'] is None
                structure = await client.get(f'/designs/{row.id}/pdb')
                assert structure.status_code == 200 and b'ATOM' in structure.content
                for metric_path in ['residue-metrics', 'chain-metrics', 'pae']:
                    response = await client.get(f'/designs/{row.id}/{metric_path}')
                    assert response.status_code == 200, response.text
                    assert response.json()['reason'] == 'unsupported_model_native_spatial_metric'
            from routers.designs import get_residue_metrics, get_chain_metrics, get_pae_data
            from services.core_protein_scientific_contract import compute_persisted_native_metric, compute_persisted_pae
            for name, endpoint in [('residue_plddt', get_residue_metrics), ('chain_metrics', get_chain_metrics)]:
                direct = await endpoint(row.id, session)
                expected = await compute_persisted_native_metric(row, name, session)
                assert direct.model_dump(mode='json') == expected.model_dump(mode='json')
                assert direct.reason == 'unsupported_model_native_spatial_metric'
            expected, _, _ = await compute_persisted_pae(row, {}, session)
            direct = await get_pae_data(row.id, 300, session)
            assert direct.reason == expected['reason']
            from services.analysis_subprocess import _dispatch_ipsae_interface
            assert (await _dispatch_ipsae_interface(row, {}, session))[0]['reason'] == expected['reason']
            if model == 'esmfold2':
                # Actual persisted-analysis worker must publish the same explicit
                # unsupported state as the direct endpoint, not a Boltz error.
                from services import analysis_subprocess as worker
                from routers.analyses import trigger_design_analysis, get_design_analysis, AnalysisRunRequest
                monkeypatch.setattr(worker, 'async_session', factory)
                monkeypatch.setattr('paths.get_analysis_cache_dir', lambda: tmp_path / 'cache')
                monkeypatch.setattr('services.analysis_runs.get_analysis_cache_dir', lambda: tmp_path / 'cache')
                await session.commit()
                queued = await trigger_design_analysis(row.id, 'chain_metrics', AnalysisRunRequest(params={}), session)
                assert await worker._run_analysis(queued.run_id) == 0
                response = await get_design_analysis(row.id, 'chain_metrics', 200, session)
                assert response.result['reason'] == expected['reason']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_frustra_failure_rolls_back_native_publication(tmp_path):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.stage_outputs = {'frustrampnn': [str(tmp_path / 'missing/workflow_component_result_v3.json')]}
            session.add(current)
            await session.commit()
            with pytest.raises(RuntimeError):
                await ingest_job_results('job', str(tmp_path), session)
        async with factory() as session:
            assert list((await session.execute(select(Design))).scalars()) == []
            current = await session.get(Job, 'job')
            assert 'core_protein_candidate_publication' not in current.provenance
    finally:
        await engine.dispose()


def frustra_publication(tmp_path, root, manifest, monkeypatch):
    """Actual prepare + component publisher, fencing only GPU runtime output."""
    from scripts.prepare_frustrampnn_candidate import prepare_candidate, producer_identity_sha256
    from services.frustrampnn.settings import FrustraMPNNRequestedSettings, requested_settings_sha256
    from services.frustrampnn.contracts import canonical_json_bytes
    import scripts.run_frustrampnn_component as component
    from test_frustrampnn_component_phase3 import _mock_v2_runtime
    producer = manifest['candidates'][0]
    identity = {key: producer[key] for key in ('producer_method', 'producer_sample', 'producer_rank', 'producer_output_key')}
    key = 'frustrampnn/sources/boltz/test.normalized.pdb'
    normalized = tmp_path / key
    normalized.parent.mkdir(parents=True)
    source = root / 'predictions' / Path(producer['producer_output_key']).name
    metadata = dict(identity, parent_job_id='job', parent_workflow_id='structure_prediction',
        producer_stage='structure_prediction:boltz', producer_candidate_key=key,
        requiredness='required', producer_identity_sha256=producer_identity_sha256(identity),
        producer_artifact_sha256=producer['producer_artifact_sha256'], source_format='pdb')
    from scripts.prepare_frustrampnn_candidate import _decode_metadata
    import base64
    metadata = _decode_metadata(base64.b64encode(json.dumps(metadata).encode()).decode(), source=source, request_version=3)
    settings = FrustraMPNNRequestedSettings()
    request_path = tmp_path / 'workflow_component_request_v3.json'
    structure_map = tmp_path / 'frustrampnn_structure_map_v1.json'
    request = prepare_candidate(source=source, output_pdb=normalized, request_path=request_path,
        metadata=metadata, request_version=3, structure_map_path=structure_map,
        settings_payload=canonical_json_bytes(settings.model_dump(mode='json', exclude_none=False, exclude={'settings_value_origin'})),
        settings_sha256=requested_settings_sha256(settings), settings_value_origin=settings.settings_value_origin)
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    from services.frustrampnn import runtime
    def native_test_scores(invocation, pinned, **kwargs):
        import csv
        import subprocess
        from services.frustrampnn.contracts import AA_ORDER
        argv = list(invocation.argv)
        binds = [argv[i + 1] for i, arg in enumerate(argv) if arg == '--bind']
        output_root = Path(next(v.split(':', 1)[0] for v in binds if v.endswith(':/bms/output:rw')))
        output = output_root / Path(argv[argv.index('--output') + 1]).name
        with output.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=['frustration_pred', 'position', 'wildtype', 'mutation', 'chain', 'pdb'])
            writer.writeheader()
            for row in json.loads(structure_map.read_text())['rows']:
                if row['status'] == 'mapped':
                    for mutation in AA_ORDER:
                        writer.writerow(dict(frustration_pred=0., position=row['model_position'], wildtype=row['wt'],
                            mutation=mutation, chain=row['pdb_chain_id'], pdb='normalized'))
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(runtime, 'execute_frustrampnn', native_test_scores)
    bundle = tmp_path / 'frustrampnn/results/test'
    component.run_component(request=request, request_payload=canonical_json_bytes(request),
        source_structure=normalized, structure_map=structure_map, output_dir=bundle,
        container=tmp_path / 'mock.sif', physical_gpu_id=3)
    return request, bundle


@pytest.mark.asyncio
@pytest.mark.parametrize('commit', [True, False])
async def test_actual_boltz_frustra_terminal_chain_and_retry(tmp_path, monkeypatch, commit):
    root, manifest = publication(tmp_path, full_backbone=True)
    request, bundle = frustra_publication(tmp_path, root, manifest, monkeypatch)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            current.stage_outputs = {'frustrampnn': [str(bundle / 'workflow_component_result_v3.json'),
                str(bundle / 'frustrampnn_result_manifest_v3.json')]}
            session.add(current)
            await session.commit()
            assert await ingest_job_results('job', str(tmp_path), session, commit=commit) == 1
            await session.commit()
        async with factory() as session:
            rows = list((await session.execute(select(Design))).scalars())
            native = next(row for row in rows if row.source_stage is None)
            derived = next(row for row in rows if row.source_stage == 'frustrampnn_candidate')
            assert len(rows) == 2
            assert derived.parent_design_id == native.id
            assert derived.id == request['candidate_id']
            result = (await session.execute(select(FrustraMPNNResult))).scalar_one()
            assert result.design_id == derived.id
            from services.boltz_scientific_consumer import verified_boltz_design, compute_persisted_pae
            selected = await verified_boltz_design(native, session)
            assert selected['artifacts']['structure']['sha256'] == manifest['candidates'][0]['producer_artifact_sha256']
            assert (await compute_persisted_pae(native, {}, session))[0]['status'] == 'ok'
            before = sorted(row.id for row in rows)
            assert await ingest_job_results('job', str(tmp_path), session) == 0
            assert sorted((await session.execute(select(Design.id))).scalars()) == before
            from services.result_state_integrity import finalize_successful_job
            current = await session.get(Job, 'job')
            if not commit:
                # Explicit remote terminal pull: no lease/hardware operation.
                current.execution_target_id = 'test-remote-target'
                current.remote_state = 'returning'
                current.remote_attempt_id = 'test-attempt'
                await session.commit()
            final = await finalize_successful_job(current, str(tmp_path), session)
            assert final.completed
            assert current.status == 'completed'
            if not commit:
                assert current.remote_state == 'ingested'
            assert sorted((await session.execute(select(Design.id))).scalars()) == before
    finally:
        await engine.dispose()
