"""Synthetic-origin coordinates, real filter -> SQLite -> finalization."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from database import Job, Design
from services.result_ingester import ingest_job_results
from services.result_state_integrity import finalize_successful_job
from test_core_protein_candidates import setup


@pytest.fixture(autouse=True)
def isolated_scientific_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'scientific_artifacts'))

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from filter_boltzgen import run_strict_filter


def published(tmp_path):
    source = tmp_path / 'native'
    source.mkdir()
    out = tmp_path / 'collected/boltzgen_filtered'
    out.mkdir(parents=True)
    pdbs, jsons = [], []
    for name, ptm in [('a', .9), ('b', .5), ('c', None)]:
        pdb = source / f'{name}.pdb'
        pdb.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
        meta = source / f'confidence_{name}.json'
        meta.write_text(json.dumps({'design_id': name, 'design_ptm': ptm, 'affinity_probability': .8,
                                    'filter_rmsd': 0., 'source': 'boltzgen', 'metrics_source': 'npz'}))
        pdbs.append(str(pdb)); jsons.append(str(meta))
    run_strict_filter(SimpleNamespace(pdbs=pdbs, jsons=jsons, out_dir=str(out), filter_biased='false',
        metrics_override=None, additional_filters=None, size_buckets=None,
        boltzgen_min_plddt=None, boltzgen_min_conf_score=None, boltzgen_max_rmsd=None, budget=1, alpha=0))
    return out


def make_job(tmp_path):
    return Job(id='job', name='synthetic-filter', model_id='boltzgen', mode='peptide_binder',
               params={}, status='running', queue_status='running', awaiting_input=False,
               output_dir=str(tmp_path), provenance={'core_protein_scientific_contract': 1})


@pytest.mark.asyncio
async def test_existing_native_terminal_owner_is_not_replaced(tmp_path, monkeypatch):
    from services import result_ingester
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = make_job(tmp_path); db.add(job); await db.commit()
            async def native_owner(*args, **kwargs):
                return 7
            monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', native_owner)
            assert await result_ingester.ingest_job_results(job.id, str(tmp_path), db) == 7
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_filter_publication_ingestion_finalization_and_replay(tmp_path):
    root = published(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = make_job(tmp_path); db.add(job); await db.commit()
            result = await finalize_successful_job(job, str(tmp_path), db)
            assert result.completed
            rows = list((await db.execute(select(Design))).scalars())
            assert [r.name for r in rows] == ['a']
            summary = job.provenance['core_protein_candidate_publication']['summary']
            assert summary['generated_count'] == 3
            assert summary['rejected_count'] == 1
            assert summary['unevaluable_count'] == 1
            assert summary['expected_publication_count'] == summary['published_count'] == 1
            assert summary['requested_count'] is None
            from routers.jobs import get_job
            response = (await get_job(job.id, db)).model_dump(mode='json')
            assert response['result_summary']['generated_count'] == 3
            assert response['result_summary']['rejected_count'] == 1
            assert response['result_summary']['expected_publication_count'] == 1
            assert response['result_summary']['persisted_count'] == 1
            assert response['result_summary']['state'] == 'validated'
            assert response['result_summary']['dispositions'][1]['reason_code'] == 'not_selected_by_diversity_budget'
            assert rows[0].plddt_overall is None  # Coordinates' B factors are NOT native pLDDT.
            assert await ingest_job_results(job.id, str(tmp_path), db) == 0
            (root / 'confidence_a.json').write_text('{}')
            with pytest.raises(RuntimeError):
                await ingest_job_results(job.id, str(tmp_path), db)
            job.status = job.queue_status = 'running'
            await db.commit()
            failed = await finalize_successful_job(job, str(tmp_path), db)
            assert not failed.completed
            failure_response = (await get_job(job.id, db)).model_dump(mode='json')
            assert failure_response['status'] == 'failed'
            assert failure_response['result_summary']['state'] == 'ingestion_failed'
            assert failure_response['result_summary']['reason']['code'] == 'candidate_replay_changed'
            import os
            if destination := os.environ.get('BMS_TEST_ACCOUNTING_WIRE'):
                Path(destination).write_text(json.dumps({'origin': 'synthetic-data-only-filter-sqlite-api',
                                                        'success': response, 'failure': failure_response}, indent=2))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing', 'extra', 'duplicate', 'foreign'])
async def test_whole_publication_validated_before_any_write(tmp_path, damage):
    root = published(tmp_path)
    report_path = root / 'filter_summary.json'
    report = json.loads(report_path.read_text())
    if damage == 'missing': (root / 'a.pdb').unlink()
    elif damage == 'extra': (root / 'extra.pdb').write_bytes((root / 'a.pdb').read_bytes())
    elif damage == 'duplicate': report['dispositions'].append(report['dispositions'][0])
    else: report['publication']['foreign'] = report['publication'].pop('a')
    report_path.write_text(json.dumps(report))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            job = make_job(tmp_path); db.add(job); await db.commit()
            with pytest.raises(RuntimeError):
                await ingest_job_results(job.id, str(tmp_path), db, commit=False)
            assert not db.new and not db.dirty and not db.deleted
            await db.commit()
            assert list((await db.execute(select(Design))).scalars()) == []
    finally:
        await engine.dispose()
