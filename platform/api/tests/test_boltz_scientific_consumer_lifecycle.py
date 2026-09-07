"""Persisted PAE cache/worker and concurrent artifact mutation controls."""
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from database import Design, Job, AnalysisRun
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication, job, setup


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['ledger_race', 'source_race', 'missing', 'unsupported'])
async def test_pae_read_failure_is_reason_coded(tmp_path, monkeypatch, damage):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
        async with factory() as session:
            from services import analysis_subprocess as worker
            from services.boltz_scientific_consumer import compute_persisted_pae
            from services.analysis_registry import build_analysis_input_signature, get_analysis_definition
            row = (await session.execute(select(Design))).scalar_one()
            assert (await compute_persisted_pae(row, {}, session))[0]['status'] == 'ok'
            definition = get_analysis_definition('pae_matrix')
            good_signature = await build_analysis_input_signature(definition, row, {}, session)
            if damage.endswith('race'):
                original = worker._compute_pae_matrix
                path = Path(row.confidence_metrics['core_protein_scientific']['artifacts'][
                    'ledger' if damage == 'ledger_race' else 'structure']['path'])
                def raced(*args, **kwargs):
                    result = original(*args, **kwargs)
                    path.write_bytes(path.read_bytes() + b'changed')
                    return result
                monkeypatch.setattr(worker, '_compute_pae_matrix', raced)
            elif damage == 'missing':
                row.confidence_metrics = {}
            else:
                current = await session.get(Job, 'job')
                current.model_id = 'esmfold2'
            result, _, _ = await compute_persisted_pae(row, {}, session)
            assert result['status'] == 'unavailable'
            assert result['reason']
            assert result['pae_matrix'] is None
            invalid_signature = await build_analysis_input_signature(definition, row, {}, session)
            assert invalid_signature != good_signature
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('analysis_type', ['pae_matrix', 'chain_metrics'])
async def test_actual_worker_persists_pae_and_rejects_stale_cache(tmp_path, monkeypatch, analysis_type):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        from services import analysis_subprocess as worker
        from services.analysis_registry import build_analysis_input_signature, get_analysis_definition
        from services.analysis_runs import get_matching_design_analysis_run
        monkeypatch.setattr(worker, 'async_session', factory)
        monkeypatch.setattr('paths.get_analysis_cache_dir', lambda: tmp_path / 'cache')
        monkeypatch.setattr('services.analysis_runs.get_analysis_cache_dir', lambda: tmp_path / 'cache')
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
        async with factory() as session:
            row = (await session.execute(select(Design))).scalar_one()
            from routers.analyses import trigger_design_analysis, get_design_analysis, AnalysisRunRequest
            queued = await trigger_design_analysis(row.id, analysis_type, AnalysisRunRequest(params={}), session)
            run_id = queued.run_id
            assert queued.status == 'queued'
        assert await worker._run_analysis(run_id) == 0
        async with factory() as session:
            row = (await session.execute(select(Design))).scalar_one()
            run = await session.get(AnalysisRun, run_id)
            assert run.status == 'completed'
            response = await get_design_analysis(row.id, analysis_type, 200, session)
            wire = json.loads(response.model_dump_json())['result']
            assert wire['status'] == 'ok'
            assert wire['document']['candidateId'] == row.id
            if analysis_type == 'pae_matrix':
                assert wire['pae_matrix'][0][1] != wire['pae_matrix'][1][0]
            else:
                assert wire['metric'] == 'chain_metrics'
                assert [c['chain_id'] for c in wire['chain_index_map']] == ['T','H','L']
                assert row.chain_metrics is None
            assert (await get_matching_design_analysis_run(session, row, analysis_type, {}))[0].id == run.id
            block = copy.deepcopy(row.confidence_metrics)
            block['core_protein_scientific']['identity']['matrix_key'] = 'foreign'
            row.confidence_metrics = block
            assert (await get_matching_design_analysis_run(session, row, analysis_type, {}))[0] is None
    finally:
        await engine.dispose()
