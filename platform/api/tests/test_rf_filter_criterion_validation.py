"""Resealed hostile evidence must fail at first SQLite result ingestion."""
import hashlib
import json
import pytest
from test_rf_filter_stage_accounting import publish, job, setup, result_ingester


def reseal(root, receipt, rows):
    outcomes = receipt / 'outcomes.jsonl'
    outcomes.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    invocation_path = receipt / 'invocation.json'
    invocation = json.loads(invocation_path.read_text())
    invocation['outcomes'].update(sha256=hashlib.sha256(outcomes.read_bytes()).hexdigest(), bytes=outcomes.stat().st_size)
    invocation_path.write_text(json.dumps(invocation))
    path = root / 'run/filter_stages/rf3_prediction_filter.json'
    stage = json.loads(path.read_text())
    stage['invocations'][0].update(sha256=hashlib.sha256(invocation_path.read_bytes()).hexdigest(), bytes=invocation_path.stat().st_size)
    path.write_text(json.dumps(stage))


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['removed', 'contradiction', 'boolean', 'unknown', 'unit', 'threshold', 'reason', 'revision'])
async def test_resealed_criteria_rejected_before_first_ingestion(tmp_path, monkeypatch, damage):
    receipt = publish(tmp_path, monkeypatch)
    rows = [json.loads(line) for line in (receipt / 'outcomes.jsonl').read_text().splitlines()]
    row = next(row for row in rows if row['passed'])
    criterion = row['criteria'][0]
    if damage == 'removed':
        for r in rows: r.update(criteria=[], reason=None)
    elif damage == 'contradiction': criterion['disposition'] = 'rejected_threshold'
    elif damage == 'boolean': criterion['evidence']['value'] = True
    elif damage == 'unknown': criterion['unknown'] = 1
    elif damage == 'unit': criterion['evidence']['units'] = 'angstrom'
    elif damage == 'threshold': criterion['minimum'] = 0
    elif damage == 'reason':
        next(r for r in rows if r['disposition'] == 'unevaluable_missing')['criteria'][0]['evidence']['reason_code'] = ''
    elif damage == 'revision': row['core_protein_scientific_contract'] = True
    reseal(tmp_path, receipt, rows)
    factory, engine = await setup(tmp_path)
    calls = []
    try:
        async with factory() as db:
            owner = job(tmp_path); db.add(owner); await db.commit()
            async def stronger(*a, **k):
                calls.append(True)
                return 17
            monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', stronger)
            with pytest.raises(ValueError, match='filter'):
                await result_ingester.ingest_job_results(owner.id, str(tmp_path), db)
            assert not calls
            assert 'rf_filter_stages' not in owner.provenance
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('compute_failure', [False, True])
async def test_no_enabled_threshold_and_computation_failure_controls(tmp_path, monkeypatch, compute_failure):
    publish(tmp_path, monkeypatch, minimum=None, compute_failure=compute_failure)
    owner = job(tmp_path)
    owner.params = {**owner.params, 'rf3_min_ptm': None}
    owner.provenance = {'core_protein_scientific_contract': 1}
    from services import rf_filter_task_roster as roster
    roster.begin(owner, owner.params)
    roster.observe(owner, '[ab/123456] Submitted process > FilterRF3 (1)')
    roster.finish(owner, 0)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            db.add(owner); await db.commit()
            async def stronger(*a, **k): return 17
            monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', stronger)
            assert await result_ingester.ingest_job_results(owner.id, str(tmp_path), db) == 17
            await db.commit()
            await db.refresh(owner)
            row = owner.provenance['rf_filter_stages']['rf3_prediction_filter']['dispositions'][0]
            assert row['criteria'] == []
            assert row['disposition'] == ('invalid_evidence' if compute_failure else 'passed')
            if compute_failure:
                assert row['candidate_failure']['code'] == 'candidate_evidence_failure'
    finally:
        await engine.dispose()
