"""R1/R4: real queue -> source signature -> worker -> cached API response."""
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from database import AnalysisRun, Design, Job
from services import analysis_subprocess as worker
from services.analysis_registry import build_analysis_input_signature, get_analysis_definition
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication, job, setup


def cache(monkeypatch, tmp_path, factory):
    monkeypatch.setattr(worker, 'async_session', factory)
    monkeypatch.setattr('paths.get_analysis_cache_dir', lambda: tmp_path / 'cache')
    monkeypatch.setattr('services.analysis_runs.get_analysis_cache_dir', lambda: tmp_path / 'cache')


def snapshot(row):
    return {column.key: copy.deepcopy(getattr(row, column.key)) for column in Design.__table__.columns}


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['none', 'missing', 'unsupported', 'roles'])
async def test_ipsae_real_dispatch(tmp_path, monkeypatch, damage):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    cache(monkeypatch, tmp_path, factory)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
            row = (await session.execute(select(Design))).scalar_one()
            row.review_profile_id = 'binder_design_v1'
            row.review_role_map = {'binder_chains': ['H', 'L'], 'target_chains': ['T']}
            await session.commit()
            definition = get_analysis_definition('ipsae_interface')
            params = definition.normalize_params({})
            signature = await build_analysis_input_signature(definition, row, params, session)
            if damage == 'missing':
                row.confidence_metrics = {}
            elif damage == 'unsupported':
                (await session.get(Job, 'job')).model_id = 'esmfold2'
            elif damage == 'roles':
                row.review_role_map = {}
            if damage != 'none':
                assert await build_analysis_input_signature(definition, row, params, session) != signature
            await session.commit()
            await session.refresh(row)  # Resolve the governed artifact after the fixture write.
            before = snapshot(row)
            design_id = row.id
            from routers.analyses import trigger_design_analysis, AnalysisRunRequest
            queued = await trigger_design_analysis(row.id, 'ipsae_interface', AnalysisRunRequest(), session)
        assert await worker._run_analysis(queued.run_id) == 0
        async with factory() as session:
            from routers.analyses import get_design_analysis
            response = await get_design_analysis(design_id, 'ipsae_interface', None, session)
            wire = json.loads(response.model_dump_json())['result']
            assert wire['contract_revision'] == 1
            assert wire['status'] == ('ok' if damage == 'none' else 'unavailable')
            if damage == 'none':
                assert wire['ipsae'] is not None
                assert wire['producer_binding']['candidate_id'] == 'input_model_0.pdb'
                assert wire['identity_evidence']['row_axis']['residues']
                row = await session.get(Design, design_id)
                block = copy.deepcopy(row.confidence_metrics)
                block['core_protein_scientific']['identity']['matrix_key'] = 'foreign'
                row.confidence_metrics = block
                from services.analysis_runs import get_matching_design_analysis_run
                assert (await get_matching_design_analysis_run(session, row, 'ipsae_interface', {}))[0] is None
                row.confidence_metrics = before['confidence_metrics']
            else:
                assert wire['ipsae'] is None and wire['reason']
            assert snapshot(await session.get(Design, design_id)) == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed,missing', [(False, False), (True, False), (True, True)])
async def test_correlation_real_dispatch_isolates_contracts(tmp_path, monkeypatch, mixed, missing):
    publication(tmp_path, count=2, native_scalars=[
        {'ptm': 0.0, 'complex_plddt': 0.0}, {'ptm': 0.8, 'complex_plddt': 0.4}])
    factory, engine = await setup(tmp_path)
    cache(monkeypatch, tmp_path, factory)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
            rows = list((await session.execute(select(Design).order_by(Design.id))).scalars())
            marked_ids = {row.id for row in rows}
            for row in rows:
                row.review_profile_id = 'binder_design_v1'
                # Deliberately contradictory legacy values must never contribute.
                row.ptm, row.plddt_overall = 99.0, 99.0
                if missing:
                    row.confidence_metrics = {}
            if mixed:
                session.add(Job(id='old', name='old', model_id='boltz2', mode='complex',
                    parent_job_id='job', status='completed', params={}, provenance={}))
                for index in range(5):
                    session.add(Design(id=f'old-{index}', name=f'old-{index}', job_id='old',
                        review_profile_id=rows[0].review_profile_id, pdb_path=rows[0].pdb_path,
                        ptm=float(index), plddt_overall=float(index * 2)))
            await session.commit()
            for row in rows:
                await session.refresh(row)
            old_before = {row.id: snapshot(row) for row in
                (await session.execute(select(Design).where(Design.job_id == 'old'))).scalars()}
            from routers.analyses import trigger_job_analysis, AnalysisRunRequest
            queued = await trigger_job_analysis('job', 'job_correlation_matrix', AnalysisRunRequest(), session)
        assert await worker._run_analysis(queued.run_id) == 0
        async with factory() as session:
            from routers.analytics import get_correlation_matrix
            response = await get_correlation_matrix('job', True, None, session)
            wire = json.loads(response.model_dump_json())
            assert len(wire['scientific_cohorts']) == 1
            cohort = wire['scientific_cohorts'][0]
            assert set(cohort['design_ids']) == marked_ids
            pair = cohort['pairs']['complex_plddt_vs_ptm']
            assert pair['correlation'] == {'state': 'unavailable', 'value': None, 'reason_code': 'insufficient_pairs'}
            assert pair['pair_count'] == (0 if missing else 2)
            if not missing:
                assert any(p['x'] == 0.0 and p['y'] == 0.0 for p in pair['points'])
            if mixed:
                assert set(wire['metrics']) == {'ptm', 'plddt_overall'}
                assert wire['sample_sizes'] == [[5, 5], [5, 5]]
                assert wire['matrix'] == [[1.0, 1.0], [1.0, 1.0]]
            else:
                assert wire['metrics'] == [] and wire['matrix'] == []
            assert {row.id: snapshot(row) for row in
                (await session.execute(select(Design).where(Design.job_id == 'old'))).scalars()} == old_before
            from services.analysis_runs import get_matching_job_analysis_run
            current = await session.get(Job, 'job')
            assert (await get_matching_job_analysis_run(session, current, 'job_correlation_matrix', {}))[0].id == queued.run_id
            row = await session.get(Design, next(iter(marked_ids)))
            # Source-descriptor substitution must invalidate even an unavailable cache.
            block = copy.deepcopy(row.confidence_metrics)
            block['core_protein_scientific'] = {'foreign_source': 'changed'}
            row.confidence_metrics = block
            assert (await get_matching_job_analysis_run(session, current, 'job_correlation_matrix', {}))[0] is None
            row.confidence_metrics = {}
            await session.flush()
            signature = await build_analysis_input_signature(get_analysis_definition('job_correlation_matrix'), current, {}, session)
            current.provenance = {}
            assert await build_analysis_input_signature(get_analysis_definition('job_correlation_matrix'), current, {}, session) != signature
    finally:
        await engine.dispose()
