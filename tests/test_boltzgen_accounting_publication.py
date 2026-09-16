"""Data-only synthetic-origin filter publication; never runs a model."""
import json
import subprocess
import sys
from pathlib import Path

import os
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_scientific_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'scientific_artifacts'))


def publish(root):
    source = root / 'native'
    source.mkdir()
    out = root / 'collected' / 'boltzgen_filtered'
    pdbs, jsons = [], []
    for name, ptm in [('a', .9), ('b', .5), ('c', None)]:
        pdb = source / f'{name}.pdb'
        pdb.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
        meta = source / f'confidence_{name}.json'
        meta.write_text(json.dumps({'design_id': name, 'design_ptm': ptm, 'affinity_probability': .8,
                                    'filter_rmsd': 0., 'metrics_source': 'npz', 'source': 'boltzgen'}))
        pdbs.append(str(pdb))
        jsons.append(str(meta))
    command = [sys.executable, str(ROOT / 'scripts/filter_boltzgen.py'), '--pdbs', *pdbs,
               '--jsons', *jsons, '--out_dir', str(out), '--filter-biased', 'false',
               '--budget', '1', '--alpha', '0', '--core-protein-scientific-contract', '1']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return out


def test_selection_rejections_and_exact_published_artifacts(tmp_path):
    import hashlib
    out = publish(tmp_path)
    summary = json.loads((out / 'filter_summary.json').read_text())
    by_id = {d['candidate_id']: d for d in summary['dispositions']}
    assert by_id['b']['selection_rejection'] == {'criterion': 'diversity_budget_selection', 'reason_code': 'not_selected_by_diversity_budget'}
    assert by_id['c']['disposition'] == 'unevaluable_missing'
    assert set(summary['publication']) == {'a'}
    for artifact in summary['publication']['a'].values():
        assert hashlib.sha256((out / artifact['path']).read_bytes()).hexdigest() == artifact['sha256']


@pytest.mark.parametrize('budget', [0, 1])
def test_literal_nextflow_filter_publication(tmp_path, budget):
    # Only input data are synthetic. Literal process body and publishDir execute.
    publish(tmp_path)
    harness = tmp_path / 'filter.nf'
    harness.write_text(f"include {{ FilterBoltzGen }} from '{ROOT}/modules/boltzgen.nf'\n"
                       "workflow { FilterBoltzGen(Channel.fromPath(params.pdbs).collect(), Channel.fromPath(params.jsons).collect()) }\n")
    config = tmp_path / 'local.config'
    config.write_text("process.executor = 'local'\nprocess.shell = ['/bin/bash', '-euo', 'pipefail']\n")
    output = tmp_path / 'published'
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'cached Nextflow jar required'
    params = dict(out_dir=str(output), code_root=str(ROOT), pdbs=str(tmp_path / 'native/*.pdb'),
                  jsons=str(tmp_path / 'native/*.json'), core_protein_scientific_contract=1,
                  boltzgen_filter_biased=False, boltzgen_budget=budget, boltzgen_alpha=0)
    params_file = tmp_path / 'params.json'; params_file.write_text(json.dumps(params))
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    run = subprocess.run(['java', '-jar', jar, 'run', str(harness), '-c', str(config), '-params-file', str(params_file),
                          '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false'],
                         cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads((output / 'collected/boltzgen_filtered/filter_summary.json').read_text())
    assert report['input_count'] == 3
    assert report['final_count'] == budget
    if budget == 1:
        import asyncio
        sys.path[:0] = [str(ROOT / 'platform/api'), str(ROOT / 'platform/api/tests')]
        async def persist_and_project():
            from test_boltzgen_candidate_accounting import setup, make_job
            from services.result_state_integrity import finalize_successful_job
            from routers.jobs import get_job
            factory, engine = await setup(tmp_path)
            try:
                async with factory() as db:
                    job = make_job(output); db.add(job); await db.commit()
                    result = await finalize_successful_job(job, str(output), db)
                    assert result.completed
                    success = (await get_job(job.id, db)).model_dump(mode='json')
                    (output / 'collected/boltzgen_filtered/confidence_a.json').write_text('{}')
                    job.status = job.queue_status = 'running'; await db.commit()
                    failed = await finalize_successful_job(job, str(output), db)
                    assert not failed.completed
                    failure = (await get_job(job.id, db)).model_dump(mode='json')
                    return dict(origin='synthetic-data-only-filter-sqlite-api', success=success, failure=failure)
            finally:
                await engine.dispose()
        wire = tmp_path / 'nextflow-sqlite-api.json'
        wire.write_text(json.dumps(asyncio.run(persist_and_project())))
        frontend = ROOT / 'platform/frontend'
        mounted = subprocess.run([str(frontend / 'node_modules/.bin/vitest'), 'run', '--config', 'vitest.md.config.ts',
                                  'tests/vitest/candidateAccountingMounted.test.tsx'], cwd=frontend,
                                 env=dict(os.environ, BMS_TEST_ACCOUNTING_WIRE=str(wire)), capture_output=True, text=True, timeout=120)
        assert mounted.returncode == 0, mounted.stdout + mounted.stderr
        print('Literal Nextflow -> publication -> SQLite -> API -> mounted status: ' + mounted.stdout)
