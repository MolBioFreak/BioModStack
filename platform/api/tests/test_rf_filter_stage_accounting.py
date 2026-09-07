"""Existing Job metadata owner retains RF filter accounting independently of final results."""
import json
import runpy
import sys
from pathlib import Path

import pytest
from database import Job
from services import result_ingester
from test_core_protein_candidates import setup

ROOT = Path(__file__).resolve().parents[3]
KEY = 'rf_filter_stages'
PDB = 'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n'


def publish(tmp_path, monkeypatch, *, role='selected_publication', empty=False, minimum=.5, compute_failure=False):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    source = tmp_path / 'source'; source.mkdir()
    if not empty:
        values = [('a', .9)] if minimum is None else [('a', .9), ('b', .1), ('c', None), ('d', float('nan'))]
        for name, value in values:
            (source / f'{name}.pdb').write_text(PDB)
            (source / f'{name}.json').write_text(json.dumps({'design_id': 'foreign'} if compute_failure else {'ptm': value}))
    receipt = tmp_path / 'run/filter_rf3/rf3_filter_1'
    monkeypatch.setattr(sys, 'argv', ['filter', 'prediction', '--input-dir', str(source),
        '--output-dir', str(tmp_path / 'results/best_designs'),
        *(['--min-ptm', str(minimum)] if minimum is not None else []),
        '--core-protein-scientific-contract', '1', '--stage-id', 'rf3_prediction_filter',
        '--stage-receipt-dir', str(receipt), '--job-id', 'filter-test', '--task-id', '1'])
    runpy.run_path(str(ROOT / 'scripts/filter_structures.py'), run_name='__main__')
    stages = tmp_path / 'run/filter_stages'; stages.mkdir(parents=True)
    terminal = tmp_path / 'terminal.json'
    import hashlib
    terminal.write_text(json.dumps({'candidate_id': 'terminal-a', 'parent_job_id': 'filter-test',
        'parent_workflow_id': 'protein_design', 'producer_method': 'rf3',
        'producer_output_key': 'rf3_terminal/a.pdb', 'producer_artifact_sha256': hashlib.sha256(PDB.encode()).hexdigest()}))
    if role == 'selected_publication' and not empty and not compute_failure:
        (tmp_path / 'results/best_designs/a.pdb').rename(tmp_path / 'results/best_designs/candidate_terminal-a.pdb')
    for stage in ('rf3_prediction_filter', 'rfd3_backbone_filter'):
        selected = stage == 'rf3_prediction_filter'
        monkeypatch.setattr(sys, 'argv', ['collect', '--job-id', 'filter-test', '--owner', 'protein_design',
            '--stage-id', stage, '--role', role if selected else 'skipped',
            '--expected-tasks', '1' if selected else '0', '--output', str(stages / f'{stage}.json'),
            *(['--receipt', str(receipt)] if selected else []),
            *(['--terminal-manifest', str(terminal)] if selected and role == 'selected_publication' and not empty and not compute_failure else [])])
        runpy.run_path(str(ROOT / 'scripts/collect_rf_filter_stage.py'), run_name='__main__')
    return receipt


def job(root):
    owner = Job(id='filter-test', name='filter', model_id='protein_modification_experimental',
               mode='de_novo_design', params={'generator': 'rfd3', 'rf3_min_ptm': .5, 'pred_method': 'rf3', 'skip_rfd': True, 'run_rfd_only': False}, status='running', queue_status='running',
               output_dir=str(root), provenance={'core_protein_scientific_contract': 1})
    from services.rf_filter_task_roster import begin, observe, finish
    begin(owner, owner.params)
    observe(owner, '[ab/123456] Submitted process > PROTEIN_DESIGN:FilterRF3 (1)')
    finish(owner, 0)
    return owner


def test_legacy_fallback_is_not_filter_owner_authority():
    from types import SimpleNamespace
    from services.rf_filter_stage_accounting import prepare_filter_stages
    for model in ('fampnn', 'ppiflow', 'unrostered_model'):
        owner = SimpleNamespace(model_id=model, mode='unrostered_mode', params={},
            provenance={'core_protein_scientific_contract': 1})
        assert prepare_filter_stages(owner, Path('/must-not-read-unrostered-fallback')) is None


@pytest.mark.asyncio
async def test_stage_metadata_persists_and_serializes_without_replacing_owner(tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            owner = job(tmp_path); db.add(owner); await db.commit()
            async def stronger_owner(*args, **kwargs):
                return 17  # Later owner has its own, deliberately different domain.
            monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', stronger_owner)
            assert await result_ingester.ingest_job_results(owner.id, str(tmp_path), db, commit=False) == 17
            await db.commit(); await db.refresh(owner)
            assert owner.provenance[KEY]['rf3_prediction_filter']['counts']['input_count'] == 4
            from routers.jobs import get_job
            wire = (await get_job(owner.id, db)).model_dump(mode='json')
            rows = wire['provenance'][KEY]['rf3_prediction_filter']['dispositions']
            assert len(rows) == 4
            assert {row['disposition'] for row in rows} == {'passed', 'rejected_threshold', 'unevaluable_missing', 'invalid_evidence'}
            assert wire['result_summary']['generated_count'] is None
            assert wire['provenance'][KEY]['rfd3_backbone_filter']['state'] == 'skipped'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_zero_survivor_stage_retained_on_terminal_failure(tmp_path, monkeypatch):
    publish(tmp_path, monkeypatch, empty=True)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as db:
            owner = job(tmp_path); db.add(owner); await db.commit()
            async def stronger_owner(*args, **kwargs):
                return 0
            monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', stronger_owner)
            from services.result_state_integrity import finalize_successful_job
            result = await finalize_successful_job(owner, str(tmp_path), db)
            assert not result.completed
            await db.refresh(owner)
            stage = owner.provenance[KEY]['rf3_prediction_filter']
            assert stage['counts']['input_count'] == 0
            assert stage['state'] == 'observed'
            assert stage['expected_tasks'] == 1
            assert stage['dispositions'] == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['missing_stage', 'outcome', 'source', 'missing_invocation', 'selected_mismatch', 'resealed_drop', 'whole_task_erased', 'selected_retyped'])
async def test_required_stage_damage_fails_before_stronger_owner(tmp_path, monkeypatch, damage):
    receipt = publish(tmp_path, monkeypatch, role='selected_publication')
    if damage == 'missing_stage': (tmp_path / 'run/filter_stages/rf3_prediction_filter.json').unlink()
    if damage == 'outcome': (receipt / 'outcomes.jsonl').write_text('')
    if damage == 'source': (receipt / 'inputs/a.json').write_text('{}')
    if damage == 'missing_invocation': (receipt / 'invocation.json').unlink()
    if damage == 'selected_mismatch': (tmp_path / 'results/best_designs/candidate_terminal-a.pdb').write_text('wrong')
    if damage in ('whole_task_erased', 'selected_retyped'):
        path = tmp_path / 'run/filter_stages/rf3_prediction_filter.json'
        stage = json.loads(path.read_text())
        stage.update(role='upstream', selection=[])
        if damage == 'whole_task_erased':
            import shutil
            shutil.rmtree(receipt)
            stage.update(role='skipped', state='skipped', expected_tasks=0, invocations=[], counts={key: 0 for key in stage['counts']})
        path.write_text(json.dumps(stage))
    if damage == 'resealed_drop':
        import hashlib
        outcomes = receipt / 'outcomes.jsonl'
        rows = [json.loads(line) for line in outcomes.read_text().splitlines()]
        outcomes.write_text(''.join(json.dumps(row) + '\n' for row in rows if row['candidate_id'] != 'c.pdb'))
        invocation_path = receipt / 'invocation.json'
        invocation = json.loads(invocation_path.read_text())
        invocation['outcomes'].update(sha256=hashlib.sha256(outcomes.read_bytes()).hexdigest(), bytes=outcomes.stat().st_size)
        invocation['counts']['input_count'] -= 1
        invocation['counts']['unevaluable_missing'] -= 1
        invocation_path.write_text(json.dumps(invocation))
        stage_path = tmp_path / 'run/filter_stages/rf3_prediction_filter.json'
        stage = json.loads(stage_path.read_text())
        stage['counts'] = invocation['counts']
        stage['invocations'][0].update(sha256=hashlib.sha256(invocation_path.read_bytes()).hexdigest(), bytes=invocation_path.stat().st_size)
        stage_path.write_text(json.dumps(stage))
    factory, engine = await setup(tmp_path)
    calls = []
    try:
        async with factory() as db:
            owner = job(tmp_path); db.add(owner); await db.commit()
            async def stronger_owner(*args, **kwargs):
                calls.append(True)
                return 17
            monkeypatch.setattr(result_ingester, '_ingest_explicit_frustrampnn_results', stronger_owner)
            with pytest.raises((ValueError, RuntimeError), match='filter'):
                await result_ingester.ingest_job_results(owner.id, str(tmp_path), db)
            assert not calls
    finally:
        await engine.dispose()
