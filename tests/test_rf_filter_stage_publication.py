"""Data-only FL07–09 producer/transport accounting; no model execution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PDB = 'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n'


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path))


def produce(tmp_path, stage='prediction', empty=False, reject=False):
    source = tmp_path / 'source'; source.mkdir()
    if not empty:
        for name, value in [('a', .9), ('b', .1), ('c', None), ('d', float('nan'))]:
            (source / f'{name}.pdb').write_text(PDB)
            (source / f'{name}.json').write_text(json.dumps({'ptm': value}))
    receipt = tmp_path / 'rf_filter_1'
    cmd = [sys.executable, str(ROOT / 'scripts/filter_structures.py'), stage,
           '--input-dir', str(source), '--output-dir', str(tmp_path / 'passing'),
           '--output-jsonl', str(tmp_path / 'outcomes.jsonl'),
           '--core-protein-scientific-contract', '1', '--stage-receipt-dir', str(receipt),
           '--stage-id', 'rf3_prediction_filter' if stage == 'prediction' else 'rfd3_backbone_filter',
           '--job-id', 'filter-test', '--task-id', '1']
    if stage == 'prediction':
        cmd += ['--min-ptm', '1' if reject else '.5']
    result = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return receipt


@pytest.mark.parametrize('empty,reject', [(False, False), (False, True), (True, False)])
def test_actual_prediction_filter_retains_hash_bound_dispositions(tmp_path, empty, reject):
    receipt = produce(tmp_path, empty=empty, reject=reject)
    manifest = json.loads((receipt / 'invocation.json').read_text())
    assert manifest['counts']['input_count'] == (0 if empty else 4)
    assert manifest['counts']['passed'] == (0 if empty or reject else 1)
    records = [json.loads(line) for line in (receipt / 'outcomes.jsonl').read_text().splitlines()]
    if not empty:
        assert {r['disposition'] for r in records} == ({'rejected_threshold', 'unevaluable_missing', 'invalid_evidence'} | (set() if reject else {'passed'}))
        for row in records:
            assert row['criteria'][0]['candidate_id'] == row['candidate_id']
            for artifact in row['artifacts'].values():
                if artifact is not None:
                    assert hashlib.sha256((receipt / artifact['path']).read_bytes()).hexdigest() == artifact['sha256']
    assert manifest['outcomes']['sha256'] == hashlib.sha256((receipt / 'outcomes.jsonl').read_bytes()).hexdigest()


@pytest.mark.parametrize('role,empty', [('upstream', False), ('selected_publication', False), ('upstream', True), ('skipped', True)])
def test_owning_stage_inventory(tmp_path, role, empty):
    receipt = produce(tmp_path, empty=empty)
    output = tmp_path / 'stage.json'
    receipts = [] if role == 'skipped' else ['--receipt', str(receipt)]
    terminal = tmp_path / 'terminal.json'
    terminal.write_text(json.dumps({'candidate_id': 'terminal-a', 'parent_job_id': 'filter-test',
        'parent_workflow_id': 'protein_design', 'producer_output_key': 'rf3_terminal/a.pdb',
        'producer_method': 'rf3', 'producer_artifact_sha256': hashlib.sha256(PDB.encode()).hexdigest()}))
    command = [sys.executable, str(ROOT / 'scripts/collect_rf_filter_stage.py'),
               '--job-id', 'filter-test', '--owner', 'protein_design', '--stage-id', 'rf3_prediction_filter',
               '--role', role, '--expected-tasks', '0' if role == 'skipped' else '1',
               '--output', str(output), *receipts,
               *(['--terminal-manifest', str(terminal)] if role == 'selected_publication' else [])]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    stage = json.loads(output.read_text())
    assert stage['role'] == role
    assert len(stage['invocations']) == (0 if role == 'skipped' else 1)
    assert stage['counts']['input_count'] == (0 if empty else 4)
    assert stage['state'] == ('skipped' if role == 'skipped' else 'observed')
    if role == 'selected_publication':
        assert stage['selection'][0]['candidate_id'] == 'terminal-a'
        forged = json.loads(terminal.read_text())
        forged['producer_output_key'] = 'rf3_terminal/b.pdb'  # Same bytes, rejected identity.
        terminal.write_text(json.dumps(forged))
        failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
        assert failed.returncode != 0
    if role != 'skipped':
        (receipt / 'invocation.json').unlink()
        failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
        assert failed.returncode != 0


@pytest.mark.parametrize('model', ['rf3', 'rfd3'])
@pytest.mark.parametrize('scenario', ['populated', 'zero_survivors', 'no_tasks'])
def test_literal_nextflow_filter_receipt_publication(tmp_path, model, scenario):
    produce(tmp_path)
    process = 'FilterRF3' if model == 'rf3' else 'FilterRFD3'
    harness = tmp_path / 'filter.nf'
    caller = 'protein_design' if model == 'rf3' else 'protein_local_redesign'
    source = (ROOT / f'workflows/{caller}.nf').read_text()
    # Run the actual caller's stage assignment and publisher statements, replacing
    # only the model-producing channel with fixture data. No model process exists
    # in this executable harness.
    import_line = next(line for line in source.splitlines() if line.startswith('include { PublishRFFilterStage'))
    import_line = import_line.replace("'../modules/rf_filter_stage'", f"'{ROOT}/modules/rf_filter_stage'")
    terminal = tmp_path / 'terminal.json'
    terminal.write_text(json.dumps({'candidate_id': 'terminal-a', 'parent_job_id': 'filter-test',
        'parent_workflow_id': 'protein_design', 'producer_method': 'rf3',
        'producer_output_key': 'rf3_terminal/a.pdb', 'producer_artifact_sha256': hashlib.sha256(PDB.encode()).hexdigest()}))
    active_start = source.index('            FilterRF3(rf3_tuple)') if model == 'rf3' else source.index('        FilterRFD3(RunRFD3.out.structures_metadata)\n        rfd3StageTasks')
    active = '\n'.join(source[active_start:].splitlines()[:4])
    publisher_lines = '\n'.join(line for line in source.splitlines() if line.strip().startswith('PublishProtein') and 'Stage(' in line)
    initial = "def rf3StageTasks = Channel.empty(); def rf3StageExpected = Channel.value(0); def rf3StageRole = 'skipped'\n" + \
              "def rfd3StageTasks = Channel.empty(); def rfd3StageExpected = Channel.value(0); def rfd3StageRole = 'skipped'\n"
    stub = "workflow RunRFD3 { take: fixture; main: data = fixture; emit: structures_metadata = data }\n" if model == 'rfd3' else ''
    initial += f"def terminal_candidate_manifests = Channel.value([file('{terminal}')])\n" if scenario == 'populated' else "def terminal_candidate_manifests = Channel.value([])\n"
    data = "def rf3_tuple = Channel.of(tuple(file(params.pdbs), file(params.jsons)))\n" if model == 'rf3' else "RunRFD3(Channel.of(tuple(file(params.pdbs), file(params.jsons))))\n"
    if scenario == 'no_tasks':
        data = data.replace('Channel.of(tuple(file(params.pdbs), file(params.jsons)))', 'Channel.empty()')
    harness.write_text(f"include {{ {process} }} from '{ROOT}/modules/{model}.nf'\n" + import_line + '\n' + stub +
                       'workflow {\n' + initial + data + active + '\n' + publisher_lines + '\n}\n')
    config = tmp_path / 'local.config'
    config.write_text("process.executor = 'local'\nprocess.shell = ['/bin/bash', '-euo', 'pipefail']\n")
    output = tmp_path / 'published'
    params_file = tmp_path / 'params.json'
    params_file.write_text(json.dumps(dict(out_dir=str(output), code_root=str(ROOT), job_id='filter-test',
        pdbs=str(tmp_path / 'source/*.pdb'), jsons=str(tmp_path / 'source/*.json'),
        core_protein_scientific_contract=1, rf3_min_ptm=1 if scenario == 'zero_survivors' else .5,
        rfd_min_rog=1 if scenario == 'zero_survivors' else None,
        pred_method='rf3', skip_rfd=True, run_rfd_only=False)))
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'cached Nextflow JAR required'
    # Translate the container script mount for this data-only local executor.
    # All other Python commands fail; this cannot execute a model wrapper.
    shim_dir = tmp_path / 'bin'; shim_dir.mkdir()
    shim = shim_dir / 'python3'
    shim.write_text(f'#!/bin/bash\nif [[ "$1" == "{ROOT}/scripts/collect_rf_filter_stage.py" ]]; then exec "{sys.executable}" "$@"; fi\n[[ "$1" == /scripts/filter_structures.py ]] || exit 97\nshift\nexec "{sys.executable}" "{ROOT}/scripts/filter_structures.py" "$@"\n')
    shim.chmod(0o755)
    # The actual orchestrator lifecycle writer consumes scheduling stdout while
    # the data-only Nextflow engine is running, before reading any filter files.
    import asyncio
    from types import SimpleNamespace
    sys.path[:0] = [str(ROOT / 'platform/api'), str(ROOT / 'platform/api/tests')]
    async def scheduled_run(resume=False):
        from test_core_protein_candidates import setup
        from database import Job
        from services import rf_filter_task_roster as roster
        factory, engine = await setup(tmp_path)
        try:
            async with factory() as db:
                job = Job(id='filter-test', name='filter', model_id='protein_modification_experimental' if model == 'rf3' else 'protein_local_redesign',
                    mode='de_novo_design' if model == 'rf3' else 'local_redesign', params=json.loads(params_file.read_text()), status='running',
                    output_dir=str(output), provenance={'core_protein_scientific_contract': 1})
                if resume:
                    job = await db.get(Job, 'filter-test')
                else:
                    db.add(job); await db.commit()
                roster.begin(job, job.params); await db.commit()
                process = await asyncio.create_subprocess_exec('java', '-jar', jar, 'run', str(harness), '-c', str(config),
                    '-params-file', str(params_file), '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false',
                    *(['-resume'] if resume else []),
                    cwd=tmp_path, env=dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
                    PATH=str(shim_dir) + os.pathsep + os.environ['PATH']),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                lines = []
                while line := await asyncio.wait_for(process.stdout.readline(), 120):
                    text = line.decode(); lines.append(text)
                    if roster.observe(job, text): await db.commit()
                code = await process.wait()
                roster.finish(job, code); await db.commit()
                return SimpleNamespace(returncode=code, stdout=''.join(lines), stderr='')
        finally:
            await engine.dispose()
    run = asyncio.run(scheduled_run())
    assert run.returncode == 0, run.stdout + run.stderr
    if scenario == 'populated':
        cached = asyncio.run(scheduled_run(resume=True))
        assert cached.returncode == 0, cached.stdout
        assert any(f'{event} process > {process} (1)' in cached.stdout for event in ('Cached', 'Submitted'))
    receipt = output / 'run' / f'filter_{model}' / f'{model}_filter_1' / 'invocation.json'
    expected_inputs = 0 if scenario == 'no_tasks' else 4
    assert receipt.is_file() == (scenario != 'no_tasks'), run.stdout + run.stderr
    if receipt.is_file():
        assert json.loads(receipt.read_text())['counts']['input_count'] == expected_inputs
    stage_id = 'rf3_prediction_filter' if model == 'rf3' else 'rfd3_backbone_filter'
    stage = json.loads((output / 'run/filter_stages' / f'{stage_id}.json').read_text())
    assert stage['counts']['input_count'] == expected_inputs
    assert stage['expected_tasks'] == (0 if scenario == 'no_tasks' else 1)
    if scenario != 'populated':
        assert stage['counts']['passed'] == 0
    assert stage['owner'] == caller
    # Transported native bytes then pass the real persisted Job owner and API.
    import asyncio
    import shutil
    sys.path[:0] = [str(ROOT / 'platform/api'), str(ROOT / 'platform/api/tests')]
    if model == 'rf3':
        final = output / 'results/best_designs'; final.mkdir(parents=True)
        for pdb in receipt.parent.joinpath('passed').glob('*.pdb'):
            shutil.copy2(pdb, final / 'candidate_terminal-a.pdb')
    async def persist():
        from test_core_protein_candidates import setup
        from database import Job
        from services.rf_filter_stage_accounting import prepare_filter_stages, retain_filter_stages
        from routers.jobs import get_job
        factory, engine = await setup(tmp_path)
        try:
            async with factory() as db:
                from sqlalchemy import select
                job = await db.scalar(select(Job).where(Job.id == 'filter-test'))
                assert job.provenance['rf_filter_task_roster']['complete'] is True
                if scenario == 'populated':
                    # Whole-receipt deletion/retyping is tested before the first
                    # accepted ingress, against independently persisted events.
                    stage_path = output / 'run/filter_stages' / f'{stage_id}.json'
                    saved = stage_path.read_bytes()
                    forged = json.loads(saved)
                    forged.update(role='skipped', state='skipped', expected_tasks=0,
                        invocations=[], selection=[], counts={key: 0 for key in forged['counts']})
                    stage_path.write_text(json.dumps(forged))
                    hidden = tmp_path / 'removed-invocation'
                    shutil.move(str(receipt.parent), str(hidden))
                    with pytest.raises(ValueError, match='filter'):
                        prepare_filter_stages(job, output)
                    # An unchanged active role cannot hide the same whole task.
                    forged.update(role=json.loads(saved)['role'], state='observed')
                    stage_path.write_text(json.dumps(forged))
                    with pytest.raises(ValueError, match='filter'):
                        prepare_filter_stages(job, output)
                    shutil.move(str(hidden), str(receipt.parent))
                    if model == 'rf3':
                        forged = json.loads(saved); forged.update(role='upstream', selection=[])
                        stage_path.write_text(json.dumps(forged))
                        with pytest.raises(ValueError, match='filter'):
                            prepare_filter_stages(job, output)
                    stage_path.write_bytes(saved)
                    if model == 'rf3':
                        from test_rf_filter_criterion_validation import reseal
                        outcomes = receipt.parent / 'outcomes.jsonl'
                        original_outcomes, original_invocation = outcomes.read_bytes(), receipt.read_bytes()
                        for damage in ('removed', 'contradiction', 'boolean', 'unknown'):
                            rows = [json.loads(line) for line in original_outcomes.splitlines()]
                            passed = next(row for row in rows if row['passed'])
                            if damage == 'removed':
                                for row in rows: row.update(criteria=[], reason=None)
                            elif damage == 'contradiction': passed['criteria'][0]['disposition'] = 'rejected_threshold'
                            elif damage == 'boolean': passed['criteria'][0]['evidence']['value'] = True
                            else: passed['criteria'][0]['extra'] = 'untrusted'
                            reseal(output, receipt.parent, rows)
                            with pytest.raises(ValueError, match='filter'):
                                prepare_filter_stages(job, output)
                            outcomes.write_bytes(original_outcomes)
                            receipt.write_bytes(original_invocation)
                            stage_path.write_bytes(saved)
                retain_filter_stages(job, prepare_filter_stages(job, output))
                await db.commit(); await db.refresh(job)
                wire = (await get_job(job.id, db)).model_dump(mode='json')
                assert wire['provenance']['rf_filter_stages'][stage_id]['counts'] == stage['counts']
                assert len(wire['provenance']['rf_filter_stages'][stage_id]['dispositions']) == expected_inputs
                assert wire['result_summary']['generated_count'] is None
                (output / 'serialized-job-response.json').write_text(json.dumps(wire))
        finally:
            await engine.dispose()
    asyncio.run(persist())


def test_actual_backbone_filter_binds_descriptor_source(tmp_path):
    receipt = produce(tmp_path, 'backbone')
    rows = [json.loads(line) for line in (receipt / 'outcomes.jsonl').read_text().splitlines()]
    assert len(rows) == 4
    for row in rows:
        assert all(p['source_sha256'] == row['artifacts']['structure']['sha256'] for p in row.get('descriptor_provenance', {}).values())
        assert row['artifacts']['structure']['sha256'] == hashlib.sha256(PDB.encode()).hexdigest()
