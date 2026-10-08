"""Offline orchestration fixtures; NOT native model inference acceptance."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('interactive_checkpoint', ROOT / 'scripts/interactive_checkpoint.py')
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


@pytest.fixture
def checkpoint(tmp_path):
    context = dict(job_id='job', attempt_id='attempt', worker_id='worker-a',
                   source_digest='source', plan_digest='plan')
    cp.atomic_json(tmp_path / 'context.json', context)
    # Deliberately nonalphabetical: checkpoint must preserve candidate order.
    candidates = []
    for name in ('z.pdb', 'a.pdb'):
        p = tmp_path / name
        p.write_text('REMARK synthetic orchestration fixture, not inference\nEND\n')
        candidates.append(p)
    manifest = tmp_path / 'region_manifest.json'
    manifest.write_text('{}')
    root = tmp_path / 'checkpoint'
    receipt = cp.seal(root, context, 'post_rfantibody', candidates, [manifest])
    return root, context, receipt, candidates


def decision(context, receipt):
    return dict(schema_version=cp.VERSION, **context, checkpoint_id=receipt['checkpoint_id'],
                decision_id='decision-1', action='continue', selected_artifacts=[
                    {k: a[k] for k in ('artifact_id', 'sha256')}
                    for a in receipt['artifacts'] if a['role'] == 'candidate'])


def approve(root, context, receipt):
    return cp.authorize_decision(root, decision(context, receipt), principal='operator',
                                 authorize=lambda *args: True)


def prepare(root, context, execution='execution-1'):
    return cp.prepare_continuation(root, context, execution_id=execution,
                                   quiescence=dict(predecessor_execution_id='initial-execution',
                                       worker_id='worker-a', attempt_id='attempt', exit_observed=True, descendants_quiescent=True),
                                   reservation=dict(worker_id='worker-a', attempt_id='attempt', plan_digest='plan',
                                       lease_id='lease-2', generation=2))


def test_reopen_exit_and_identity(checkpoint, tmp_path):
    root, context, receipt, candidates = checkpoint
    request = dict(root=str(root), context=str(tmp_path / 'context.json'), stage='post_rfantibody',
                   candidates=list(map(str, candidates)), review=[str(tmp_path / 'region_manifest.json')])
    cp.atomic_json(tmp_path / 'request.json', request)
    subprocess.run([sys.executable, str(ROOT / 'scripts/interactive_checkpoint.py'), 'seal',
                    '--request', str(tmp_path / 'request.json'), '--output', str(tmp_path / 'out.json')], check=True)
    assert cp.read(tmp_path / 'out.json') == receipt
    assert cp.read(root / 'state.json')['state'] == 'awaiting_review'
    with pytest.raises(ValueError, match='approved'):
        prepare(root, context)
    approve(root, context, receipt)
    first = prepare(root, context)
    assert [Path(p).name for p in first['selected_paths']] == ['z.pdb', 'a.pdb']
    assert prepare(root, context) == first
    with pytest.raises(ValueError, match='another execution'):
        prepare(root, context, 'duplicate-science')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/interactive_checkpoint.py'),
                             'verify-continuation', '--root', str(root), '--context', str(tmp_path / 'context.json'),
                             '--execution-id', 'execution-1'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == first


@pytest.mark.parametrize('binding', ['checkpoint_id', *cp.BINDINGS])
def test_stale_decision(checkpoint, binding):
    root, context, receipt, _ = checkpoint
    d = decision(context, receipt)
    d[binding] = 'stale'
    with pytest.raises(ValueError, match='stale'):
        cp.authorize_decision(root, d, principal='operator', authorize=lambda *a: True)
    assert cp.read(root / 'state.json')['state'] == 'awaiting_review'


def test_unauthorized_replay_and_corrupt_selection(checkpoint):
    root, context, receipt, _ = checkpoint
    d = decision(context, receipt)
    with pytest.raises(PermissionError):
        cp.authorize_decision(root, d, principal='intruder', authorize=lambda *a: False)
    for mutate in (lambda d: d['selected_artifacts'].reverse(),
                   lambda d: d['selected_artifacts'][0].update(sha256='corrupt'),
                   lambda d: d.update(automatic_approval=True)):
        invalid = copy.deepcopy(d)
        mutate(invalid)
        with pytest.raises(ValueError):
            cp.authorize_decision(root, invalid, principal='operator', authorize=lambda *a: True)
    approve(root, context, receipt)
    with pytest.raises(ValueError, match='replay'):
        approve(root, context, receipt)
    with pytest.raises(ValueError, match='worker'):
        prepare(root, dict(context, worker_id='migrated'))
    prepare(root, context)
    (root / 'selected' / 'rogue.pdb').write_text('unselected')
    with pytest.raises(ValueError, match='undeclared'):
        cp.verify_continuation(root, context, 'execution-1')


def test_seal_requires_files_and_detects_mutation(checkpoint, tmp_path):
    root, context, receipt, candidates = checkpoint
    with pytest.raises(FileNotFoundError):
        cp.seal(tmp_path / 'missing', context, 'post_fampnn', [tmp_path / 'absent.pdb'])
    artifact = root / receipt['artifacts'][0]['path']
    artifact.chmod(0o644)
    artifact.write_text('corrupt')
    with pytest.raises(ValueError, match='corrupt'):
        cp.verify(root, receipt)
    with pytest.raises(ValueError, match='relative'):
        cp.contained(root, '../escape')


def test_schema_reject_and_safe_quiescence(checkpoint):
    import jsonschema
    root, context, receipt, _ = checkpoint
    schema = cp.read(ROOT / 'schemas/interactive_checkpoint.schema.json')
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(receipt, schema)
    d = decision(context, receipt)
    jsonschema.validate(d, schema)
    with pytest.raises(ValueError, match='quiescence'):
        cp.prepare_continuation(root, context, execution_id='new', quiescence={}, reservation={})
    d.update(action='reject', selected_artifacts=[])
    cp.authorize_decision(root, d, principal='operator', authorize=lambda *args: True)
    assert cp.read(root / 'state.json')['state'] == 'rejected'
    with pytest.raises(ValueError, match='approved'):
        prepare(root, context)


def test_crash_between_receipt_and_state_reconciles(checkpoint, tmp_path, monkeypatch):
    _, context, _, candidates = checkpoint
    root = tmp_path / 'crashed'
    original = cp.atomic_json
    def interrupt(path, value):
        if Path(path).name == 'state.json':
            raise RuntimeError('injected process crash')
        original(path, value)
    monkeypatch.setattr(cp, 'atomic_json', interrupt)
    with pytest.raises(RuntimeError, match='injected'):
        cp.seal(root, context, 'post_fampnn', candidates)
    assert (root / 'receipt.json').exists()
    assert not (root / 'state.json').exists()
    monkeypatch.setattr(cp, 'atomic_json', original)
    receipt = cp.seal(root, context, 'post_fampnn', candidates)
    assert cp.read(root / 'state.json')['state'] == 'awaiting_review'
    approve(root, context, receipt)
    selected = root / 'selected'
    selected.mkdir()
    (selected / '.checkpoint-interrupted').write_text('partial copy')
    assert prepare(root, context)['stage'] == 'post_fampnn'
    assert not (selected / '.checkpoint-interrupted').exists()


def test_original_worker_and_local_placement_share_artifacts(checkpoint, tmp_path):
    _, context, receipt, candidates = checkpoint
    local = cp.seal(tmp_path / 'local', dict(context, worker_id='local'), 'post_rfantibody',
                    candidates, [tmp_path / 'region_manifest.json'])
    assert local['artifacts'] == receipt['artifacts']
    assert local['checkpoint_id'] != receipt['checkpoint_id']


def run_nf(tmp_path, script, params, resume=False):
    executable = shutil.which('nextflow')
    if not executable:
        pytest.skip('Nextflow unavailable; Python contract still tested')
    config = tmp_path / 'offline.config'
    config.write_text("process.executor = 'local'\nprocess.cpus = 1\nprocess.memory = '128 MB'\nprocess.container = null\napptainer.enabled = false\nsingularity.enabled = false\n")
    param_file = tmp_path / 'params.json'
    cp.atomic_json(param_file, dict(code_root=str(ROOT), out_dir=str(tmp_path / 'results'), **params))
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    launch = ['java', '--add-opens=java.base/java.util=ALL-UNNAMED',
              '--add-opens=java.base/java.lang=ALL-UNNAMED', '-jar', jar] if jar else [executable]
    cmd = launch + ['-C', str(config), 'run', str(script), '-ansi-log', 'false',
           '-lib', str(ROOT / 'lib'), '-params-file', str(param_file)]
    if resume:
        cmd.append('-resume')
    return subprocess.run(cmd, cwd=tmp_path, env=dict(os.environ, NXF_OFFLINE='true'),
                          capture_output=True, text=True, timeout=120)


def test_actual_gate_process_exits_and_nextflow_resume(checkpoint, tmp_path):
    root, context, receipt, candidates = checkpoint
    harness = tmp_path / 'gate.nf'
    harness.write_text(f"""nextflow.enable.dsl=2
include {{ OpenInteractiveGate }} from '{ROOT}/modules/interactive_checkpoint.nf'
workflow {{
  OpenInteractiveGate('post_rfantibody', [file('{candidates[0]}'), file('{candidates[1]}')],
    [file('{tmp_path}/region_manifest.json')], [:])
}}
""")
    params = dict(checkpoint_context=str(tmp_path / 'context.json'))
    result = run_nf(tmp_path, harness, params)
    assert result.returncode == 0, result.stdout + result.stderr
    sealed = tmp_path / 'results/checkpoints/post_rfantibody'
    assert cp.read(sealed / 'state.json')['state'] == 'awaiting_review'
    assert cp.read(sealed / 'receipt.json')['checkpoint_id'] == receipt['checkpoint_id']
    result = run_nf(tmp_path, harness, params, resume=True)
    assert result.returncode == 0, result.stdout + result.stderr
    # Sealing may be re-executed by Nextflow; reopening must remain the same
    # logical checkpoint, never an implicit review decision or science restart.
    assert cp.read(sealed / 'receipt.json') == receipt
    assert cp.read(sealed / 'state.json') == {'state': 'awaiting_review', 'sequence': 1}


@pytest.mark.parametrize('workflow,stage', [('ppiflow_generator_design', 'post_ppiflow_generator'),
                                           ('protein_local_redesign', 'post_structure_validation')])
def test_actual_workflow_continuation_submits_no_completed_science(tmp_path, checkpoint, workflow, stage):
    _, context, _, candidates = checkpoint
    root = tmp_path / 'final-review'
    receipt = cp.seal(root, context, stage, candidates)
    approve(root, context, receipt)
    prepare(root, context)
    params = dict(checkpoint_context=str(tmp_path / 'context.json'), checkpoint_continuation=str(root),
                  checkpoint_execution_id='execution-1', plr_input_pdb=str(candidates[0]),
                  plr_design_chains='A', plr_redesign_ranges='A1-2')
    result = run_nf(tmp_path, ROOT / f'workflows/{workflow}.nf', params)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Submitted process' not in result.stdout


@pytest.mark.parametrize('workflow', ['protein_local_redesign', 'ppiflow_generator_design'])
def test_actual_workflow_rejects_unbound_approval(tmp_path, checkpoint, workflow):
    _, _, _, candidates = checkpoint
    result = run_nf(tmp_path, ROOT / f'workflows/{workflow}.nf', dict(
        interactive_gate_continue=True, plr_input_pdb=str(candidates[0]),
        plr_design_chains='A', plr_redesign_ranges='A1-2', ppiflow_seed_complex_path=str(candidates[0])))
    assert result.returncode != 0
    assert 'Unbound gate continuation/resume inputs prohibited' in result.stdout + result.stderr
    assert 'Submitted process' not in result.stdout


@pytest.mark.parametrize('state', ['partial', 'failed'])
def test_actual_required_validator_gate_failure(tmp_path, state):
    receipt = tmp_path / 'suite.json'
    cp.atomic_json(receipt, {'state': state})
    harness = tmp_path / 'failed.nf'
    harness.write_text(f"""nextflow.enable.dsl=2
include {{ EnforceProteinLocalValidatorSuite }} from '{ROOT}/workflows/protein_local_redesign.nf'
workflow {{ EnforceProteinLocalValidatorSuite(file('{receipt}')) }}
""")
    result = run_nf(tmp_path, harness, {})
    assert result.returncode != 0
    assert 'Protein Local Redesign validator suite is ' + state in result.stdout + result.stderr
    assert not (tmp_path / 'results/checkpoints').exists()


@pytest.mark.parametrize('failed', ['', 'IdentifyAnchorResidues', 'RunPartialFlow',
                                     'ScorePartialFlowImprovement', 'FilterByMaturation'])
def test_actual_ppiflow_dag_with_explicit_synthetic_science(tmp_path, checkpoint, failed):
    _, context, _, candidates = checkpoint
    # Execute the actual workflow, replacing ONLY the external scientific producers
    # with explicitly labeled synthetic process contracts. Collectors, joins,
    # stage ordering, gate sealing, and continuation checks are production code.
    source = (ROOT / 'workflows/ppiflow_generator_design.nf').read_text()
    source = source.replace("'../modules/ppiflow.nf'", f"'{ROOT}/tests/fixtures/checkpoint_ppiflow_science.nf'")
    source = source.replace("'../modules/interactive_checkpoint.nf'", f"'{ROOT}/modules/interactive_checkpoint.nf'")
    workflow = tmp_path / 'ppiflow.nf'
    workflow.write_text(source)
    result = run_nf(tmp_path, workflow, dict(ppiflow_seed_complex_path=str(candidates[0]),
        interactive_gating=True, fixture_fail=failed, checkpoint_context=str(tmp_path / 'context.json')))
    receipt_path = tmp_path / 'results/checkpoints/post_ppiflow_generator/receipt.json'
    if failed:
        assert result.returncode != 0, result.stdout + result.stderr
        assert failed in result.stdout + result.stderr
        assert not receipt_path.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        receipt = cp.read(receipt_path)
        assert [a['artifact_id'] for a in receipt['artifacts'] if a['role'] == 'candidate'] == ['candidate:seed_enriched.pdb', 'candidate:seed_sample0.pdb']
        assert any(a['path'].endswith('seed_sample0_score.json') for a in receipt['artifacts'])
        assert any(a['path'].endswith('filter.json') for a in receipt['artifacts'])
        assert receipt['science_complete'] is False


def redesign_fixture_workflow(tmp_path):
    import re
    source = (ROOT / 'workflows/protein_local_redesign.nf').read_text()
    fixture = ROOT / 'tests/fixtures/checkpoint_redesign_science.nf'
    for module in ['rfd3', 'fampnn', 'proteinmpnn', 'esmfold2_experimental']:
        source = source.replace(f"'../modules/{module}.nf'", f"'{fixture}'")
    source = source.replace("'../modules/", f"'{ROOT}/modules/")
    for process in ['ResolveProteinLocalRegion', 'PrepProteinLocalRFD3Input',
                    'MergeProteinLocalComplexes', 'PrepProteinLocalFAMPNN',
                    'PrepProteinLocalMPNN', 'PrepareProteinLocalValidatorInput']:
        source, count = re.subn(r'^process ' + process + r' \{.*?^\}\n',
             f"include {{ {process} }} from '{fixture}'\n", source, flags=re.M | re.S)
        assert count == 1
    path = tmp_path / 'redesign.nf'
    path.write_text(source)
    return path


@pytest.mark.parametrize('stage', ['post_rfantibody', 'post_fampnn', 'post_structure_validation'])
@pytest.mark.parametrize('sequence_method', ['fampnn', 'mpnn'])
def test_actual_redesign_dag_gate_and_continuation(tmp_path, checkpoint, stage, sequence_method):
    _, context, _, candidates = checkpoint
    workflow = redesign_fixture_workflow(tmp_path)
    params = dict(plr_input_pdb=str(candidates[0]), plr_design_chains='A', plr_redesign_ranges='A1-2',
                  plr_structure_validators='esmfold2', fixture_fail='', interactive_gating=True,
                  interactive_gate_stage=stage, plr_seq_method=sequence_method, checkpoint_context=str(tmp_path / 'context.json'))
    result = run_nf(tmp_path, workflow, params)
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / f'results/checkpoints/{stage}'
    receipt = cp.read(root / 'receipt.json')
    assert receipt['stage'] == stage
    if stage == 'post_structure_validation':
        assert any(a['path'].endswith('prediction.cif') for a in receipt['artifacts'])
        assert any(a['path'].endswith('validator_suite_receipt.json') for a in receipt['artifacts'])
    approve(root, context, receipt)
    prepare(root, context)
    result = run_nf(tmp_path, workflow, dict(params, checkpoint_continuation=str(root), checkpoint_execution_id='execution-1'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Submitted process > PROTEIN_LOCAL_REDESIGN:RunRFD3' not in result.stdout
    if stage != 'post_rfantibody':
        assert 'Submitted process > PROTEIN_LOCAL_REDESIGN:RunFAMPNN' not in result.stdout
        assert 'Submitted process > PROTEIN_LOCAL_REDESIGN:RunMPNN' not in result.stdout
    if stage != 'post_structure_validation':
        assert cp.read(tmp_path / 'results/validation/validator_suite_receipt.json')['state'] == 'complete'
        resumed = run_nf(tmp_path, workflow, dict(params, checkpoint_continuation=str(root),
                         checkpoint_execution_id='execution-1'), resume=True)
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        for model in ('RunRFD3', 'RunFAMPNN', 'RunMPNN', 'ESMFold2FromPdb'):
            assert f'Submitted process > PROTEIN_LOCAL_REDESIGN:{model}' not in resumed.stdout, resumed.stdout


@pytest.mark.parametrize('failed', ['RunRFD3', 'FilterRFD3', 'RunFAMPNN', 'FilterFAMPNN', 'RunMPNN', 'FilterMPNN', 'ESMFold2FromPdb'])
def test_actual_redesign_required_stage_failure(tmp_path, checkpoint, failed):
    _, _, _, candidates = checkpoint
    workflow = redesign_fixture_workflow(tmp_path)
    result = run_nf(tmp_path, workflow, dict(plr_input_pdb=str(candidates[0]), plr_design_chains='A',
         plr_redesign_ranges='A1-2', plr_structure_validators='esmfold2', fixture_fail=failed,
         plr_seq_method='mpnn' if failed in ('RunMPNN', 'FilterMPNN') else 'fampnn',
         interactive_gating=True, checkpoint_context=str(tmp_path / 'context.json')))
    assert result.returncode != 0, result.stdout + result.stderr
    assert failed in result.stdout + result.stderr
    assert not (tmp_path / 'results/checkpoints/post_structure_validation/receipt.json').exists()


