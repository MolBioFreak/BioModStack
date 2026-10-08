"""CPU-only model-output fixtures exercising the real antibody Nextflow DAG.

NOT inference, remote acceptance, provider acceptance, or model qualification.
Only scientific process bodies are replaced; orchestration, staging, guards,
annotation joins, review publication, and terminal closeout execute for real.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from antibody_checkpoint import seal_review, verify_decision


@pytest.fixture
def nextflow_command():
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Set BMS_TEST_NEXTFLOW_JAR to a pinned local Nextflow jar for offline DAG tests')
    return ['java', '--add-opens=java.base/java.util=ALL-UNNAMED',
            '--add-opens=java.base/java.lang=ALL-UNNAMED', '-jar', jar]


ANARCII = '''process ANARCII {
 input:
 tuple val(meta), path(pdb)
 output:
 tuple val(meta), path("${meta.id}_imgt.pdb"), emit: pdb_imgt
 tuple val(meta), path("${meta.id}_cdrs.json"), emit: cdrs
 tuple val(meta), path("${meta.id}_cdr_positions.json"), emit: cdr_positions
 script:
 """
 cp ${pdb} ${meta.id}_imgt.pdb
 printf '{"fixture":true}' > ${meta.id}_cdrs.json
 printf '{"H1":[1]}' > ${meta.id}_cdr_positions.json
 """
}
'''
RFANTIBODY = '''process RFANTIBODY {
 input:
 tuple val(meta), path(target), val(hotspots), val(gpu), val(count)
 path framework
 output:
 tuple val(meta), path('output/*.pdb'), emit: designs
 path 'output/*.trb', emit: metadata
 path '*.log', emit: log
 script:
 def commands = (0..<count).collect { i -> "cp ${target} output/${meta.id}_${i}.pdb; printf fixture > output/${meta.id}_${i}.trb" }.join('\\n')
 """
 mkdir output
 ${commands}
 printf offline > fixture.log
 """
}
'''
FAMPNN = '''process PrepFAMPNN {
 input:
 tuple path(pdbs), path(jsons)
 output:
 path 'prepared/*.pdb', emit: pdbs
 path 'positions.csv', emit: csv
 script:
 """
 mkdir prepared
 cp ${pdbs} prepared/
 printf fixture > positions.csv
 """
}
process RunFAMPNN {
 input:
 tuple val(batch_id), path(pdbs), path(csv), val(gpu)
 val chain
 val contract
 output:
 tuple path('results/*.pdb'), path('results/*.json'), emit: pdbs_jsons
 tuple val(batch_id), path('results/*.pdb'), path('results/*.json'), emit: antibody_batches
 script:
 def paths = pdbs instanceof Collection ? pdbs : [pdbs]
 def commands = paths.collect { pdb -> "cp ${pdb} results/${pdb.baseName}_seq0.pdb; printf '{}' > results/${pdb.baseName}_seq0.json" }.join('\\n')
 """
 mkdir results
 ${params.offline_fail_fampnn == true ? "exit 7" : "true"}
 ${commands}
 """
}
process FilterFAMPNN {
 input:
 tuple path(pdbs), path(jsons)
 output:
 path 'filtered/*.pdb', emit: pdbs
 path 'filtered/*.json', emit: jsons
 script:
 """
 mkdir filtered
 cp ${pdbs} ${jsons} filtered/
 """
}
'''


def fixture_tree(tmp_path):
    root = tmp_path / 'code'
    root.mkdir()
    for folder in ('workflows', 'modules', 'lib'):
        shutil.copytree(ROOT / folder, root / folder)
    for folder in ('scripts', 'platform'):
        (root / folder).symlink_to(ROOT / folder, target_is_directory=True)
    for name in ('NO_FRAMEWORK', 'NO_MSA', 'NO_JSON', 'empty-meta.jsonl'):
        (root / 'lib' / name).touch()
    (root / 'modules/utils/anarci.nf').write_text(ANARCII)
    (root / 'modules/rfantibody.nf').write_text(RFANTIBODY)
    (root / 'modules/fampnn.nf').write_text(FAMPNN)
    shutil.copyfile(ROOT / 'platform/api/tests/fixtures/antibody_native/ppiflow.nf', root / 'modules/ppiflow.nf')
    shutil.copyfile(ROOT / 'platform/api/tests/fixtures/antibody_native/antibody_batch.nf', root / 'modules/antibody_batch.nf')
    target = tmp_path / 'target.pdb'
    target.write_text('ATOM      1  CA  ALA H   1       1.000   2.000   3.000  1.00 90.00           C\nTER\nEND\n')
    config = tmp_path / 'offline.config'
    config.write_text("process.executor='local'\nprocess.cpus=1\nprocess.memory='128 MB'\nprocess.errorStrategy='terminate'\nparams.container_dir='UNUSED_OFFLINE_FIXTURE'\n")
    return root, target, config


def run_dag(nextflow_command, tmp_path, changes=None, *, expect_success=True):
    root, target, config = fixture_tree(tmp_path)
    out = tmp_path / 'output'
    params = dict(code_root=str(root), out_dir=str(out), target_pdb=str(target), framework_pdb=str(target),
                  job_id='offline-antibody', job_name='fixture', run_structure_validation=False,
                  run_frustrampnn=False, run_immunogenicity_scoring=False, run_thermompnn=False,
                  seq_design_fampnn=True, seq_design_antifold=False, seq_design_proteinmpnn=False,
                  enable_fampnn_filter=False, rfantibody_num_designs=3, designs_per_job=2,
                  parallel_mode='full_orchestrator', pdbs_per_job=2,
                  antibody_checkpoint_settings={'schema_name':'offline_fixture_effective_settings','seed':0})
    params.update(changes or {})
    request = tmp_path / 'params.json'
    request.write_text(json.dumps(params))
    result = subprocess.run(nextflow_command + ['-C', str(config), 'run', str(root / 'workflows/antibody_denovo.nf'),
        '-lib', str(root / 'lib'), '-params-file', str(request),
        '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false'], cwd=tmp_path,
        text=True, capture_output=True, timeout=150,
        env={**os.environ, 'NXF_HOME': str(tmp_path / 'nxf-home'), 'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true'})
    if expect_success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return out, result.stdout


def test_native_noninteractive_dag_without_host(nextflow_command, tmp_path):
    out, log = run_dag(nextflow_command, tmp_path)
    receipts = sorted((out / 'components/fampnn').glob('*receipt.json'))
    assert len(receipts) == 2
    assert len(list((out / 'collected/rfantibody_raw').glob('*.trb'))) == 3
    groups = [json.loads(p.read_text()) for p in receipts]
    assert [g['group_ordinal'] for g in groups] == [0, 1]
    assert [len([a for a in g['artifacts'] if a['role'] == 'pdbs']) for g in groups] == [2, 1]
    assert len(list((out / 'annotations/anarcii').rglob('*_cdrs.json'))) == 3
    report = json.loads((out / 'terminal_closeout_report.json').read_text())
    assert report['total_terminal_designs'] == 3
    assert 'Spawn' not in log and 'WaitFor' not in log


def test_native_review_dag_pauses_without_approval(nextflow_command, tmp_path):
    out, _ = run_dag(nextflow_command, tmp_path, dict(interactive_gating=True, interactive_gate_stage='post_fampnn'))
    checkpoint = out / 'gates/post_fampnn/checkpoint.json'
    receipt = json.loads(checkpoint.read_text())
    assert receipt['status'] == 'awaiting_review' and receipt['science_complete'] is False
    assert len([a for a in receipt['artifacts'] if a['role'] == 'structure']) == 3
    assert len([a for a in receipt['artifacts'] if a['role'] == 'annotation']) == 6
    assert len([a for a in receipt['artifacts'] if a['role'] == 'native_analysis']) == 3
    assert not (out / 'annotations/anarcii').exists()
    assert not (out / 'terminal_closeout_report.json').exists()


def test_checkpoint_rejects_unbound_or_changed_selection(tmp_path):
    source = tmp_path / 'source.pdb'; source.write_text('OFFLINE fixture structure')
    annotation = tmp_path / 'source_cdrs.json'; annotation.write_text('{}')
    review = tmp_path / 'checkpoint'
    receipt = seal_review(job_id='job', stage='post_fampnn', sources=[source], annotations=[annotation],
                          settings={'seed': 0}, output=review)
    selected = tmp_path / 'selected'; selected.mkdir(); shutil.copy(source, selected)
    choice = {'schema_name': 'bms.antibody-checkpoint-decision.v1', 'checkpoint_id': receipt['checkpoint_id'],
              'authorized_by': 'offline-test-operator', 'decision': 'continue',
              'job_id': 'job', 'stage': 'post_fampnn',
              'selected_artifacts': ['review/structure/source.pdb']}
    decision = tmp_path / 'decision.json'; decision.write_text(json.dumps(choice))
    assert verify_decision(review / 'checkpoint.json', decision, selected)['stage'] == 'post_fampnn'
    (selected / source.name).write_text('changed')
    with pytest.raises(ValueError, match='inputs differ'):
        verify_decision(review / 'checkpoint.json', decision, selected)
    shutil.copy(source, selected)
    choice['checkpoint_id'] = 'foreign'; decision.write_text(json.dumps(choice))
    with pytest.raises(ValueError, match='checkpoint-bound'):
        verify_decision(review / 'checkpoint.json', decision, selected)


def test_native_maturation_preserves_per_group_top_n(nextflow_command, tmp_path):
    out, log = run_dag(nextflow_command, tmp_path, dict(
        run_ppiflow_maturation=True, maturation_designs_per_job=2,
        maturation_redesign_enabled=True, maturation_redesign_top_n=1))
    # Three original structures -> two original child groups; top-1 is per
    # group, not silently converted to a global top-1 by DAG consolidation.
    report = json.loads((out / 'terminal_closeout_report.json').read_text())
    assert report['total_terminal_designs'] == 2
    assert 'AntibodySequenceMaturation:RunPartialFlow' in log
    assert 'AntibodySequenceMaturation:RunMaturationFAMPNN' in log
    assert len(list((out / 'annotations/anarcii').rglob('*_cdrs.json'))) == 2


@pytest.mark.parametrize('validator', ['boltz2', 'protenix', 'esmfold2'])
def test_native_validation_retains_complete_artifacts(nextflow_command, tmp_path, validator):
    out, log = run_dag(nextflow_command, tmp_path, dict(
        run_structure_validation=True, exploration_mode=True, structure_validator=validator,
        protenix_use_msa=False, seqs_per_validation_job=2))
    report = json.loads((out / 'aggregation_report.json').read_text())
    assert report['total_validated_designs'] == 3
    assert len(list((out / 'pdb_files/validated_designs').glob('*.pdb'))) == 3
    assert len(list((out / 'pdb_files/validated_designs').glob('*.json'))) == 3
    assert len(list((out / 'annotations/anarcii').rglob('*_cdrs.json'))) == 3
    assert 'SpawnChildJobs' not in log
    plan = json.loads((out / 'components/structure_validation/validation_component_plan.json').read_text())
    assert [len(g['candidates']) for g in plan['groups']] == [2, 1]
    assert plan['requiredness'] == 'required' and plan['status'] == 'planned'


def test_native_backbone_validation_and_post_maturation_dag(nextflow_command, tmp_path):
    out, log = run_dag(nextflow_command, tmp_path, dict(
        run_ppiflow_backbone_refine=True, run_ppiflow_maturation=True,
        run_structure_validation=True, run_post_validation_maturation=True,
        maturation_designs_per_job=2, maturation_redesign_enabled=True,
        maturation_redesign_top_n=1, structure_validator='boltz2'))
    assert 'AntibodyBackboneMaturation:RunPartialFlow' in log
    assert 'AntibodySequenceMaturation:RunMaturationFAMPNN' in log
    assert 'AntibodyValidatedMaturation:RunMaturationFAMPNN' in log
    assert len(list((out / 'annotations/anarcii').rglob('*_cdrs.json'))) == 2


def test_checkpoint_is_immutable_and_allows_no_implicit_decision(tmp_path):
    source = tmp_path / 'a.pdb'; source.write_text('fixture')
    annotation = tmp_path / 'a_cdrs.json'; annotation.write_text('{}')
    kwargs = dict(job_id='job', stage='post_fampnn', sources=[source], annotations=[annotation],
                  settings={'seed': 0}, output=tmp_path / 'sealed')
    first = seal_review(**kwargs)
    assert seal_review(**kwargs) == first
    with pytest.raises(ValueError, match='different authority'):
        seal_review(**{**kwargs, 'settings': {'seed': 1}})
    assert json.loads((tmp_path / 'sealed/checkpoint.json').read_text()) == first


@pytest.mark.parametrize('changes,reason', [
    ({'offline_fail_fampnn': True}, 'CollectNativeAntibodyFAMPNN'),
    ({'interactive_gate_continue': True}, 'checkpoint_bound_decision_required'),
    ({'run_ppiflow_maturation': True, 'offline_zero_anchors': True}, 'ZERO-YIELD'),
])
def test_required_failure_or_unapproved_gate_cannot_complete(nextflow_command, tmp_path, changes, reason):
    out, log = run_dag(nextflow_command, tmp_path, changes, expect_success=False)
    assert not (out / 'terminal_closeout_report.json').exists()
    if changes.get('offline_fail_fampnn'):
        assert not list((out / 'components/fampnn').glob('*receipt.json'))
    else:
        assert reason in log


def test_native_checkpoint_selected_continuation(nextflow_command, tmp_path):
    first = tmp_path / 'first'; first.mkdir()
    out, _ = run_dag(nextflow_command, first, dict(interactive_gating=True, interactive_gate_stage='post_fampnn'))
    checkpoint = out / 'gates/post_fampnn/checkpoint.json'
    receipt = json.loads(checkpoint.read_text())
    artifact = next(a for a in receipt['artifacts'] if a['role'] == 'structure')
    selected = tmp_path / 'selected'; selected.mkdir()
    shutil.copyfile(checkpoint.parent / artifact['relative_path'], selected / Path(artifact['relative_path']).name)
    decision = tmp_path / 'decision.json'
    decision.write_text(json.dumps(dict(schema_name='bms.antibody-checkpoint-decision.v1',
        checkpoint_id=receipt['checkpoint_id'], authorized_by='offline-fixture-operator', decision='continue',
        job_id=receipt['job_id'], stage=receipt['stage'], selected_artifacts=[artifact['relative_path']])))
    second = tmp_path / 'second'; second.mkdir()
    continued, _ = run_dag(nextflow_command, second, dict(interactive_gate_continue=True,
        antibody_checkpoint=str(checkpoint), antibody_checkpoint_decision=str(decision),
        selected_input_dir=str(selected), selected_input_artifact_class='sequence_designed_complex',
        selected_input_stage_family='fampnn', seq_design_fampnn=False, skip_rfantibody=True))
    assert json.loads((continued / 'terminal_closeout_report.json').read_text())['total_terminal_designs'] == 1


def test_offline_local_worker_roots_have_same_checkpoint_identity(nextflow_command, tmp_path):
    receipts = []
    for placement in ('local-root', 'worker-root'):
        path = tmp_path / placement; path.mkdir()
        out, _ = run_dag(nextflow_command, path, dict(interactive_gating=True, interactive_gate_stage='post_fampnn'))
        receipts.append(json.loads((out / 'gates/post_fampnn/checkpoint.json').read_text()))
    assert receipts[0] == receipts[1]


def test_exploration_keeps_balanced_not_fixed_size_groups(nextflow_command, tmp_path):
    out, _ = run_dag(nextflow_command, tmp_path, dict(
        rfantibody_num_designs=5, run_structure_validation=True, exploration_mode=True,
        structure_validator='boltz2', seqs_per_validation_job=4))
    plan = json.loads((out / 'components/structure_validation/validation_component_plan.json').read_text())
    assert [len(g['candidates']) for g in plan['groups']] == [3, 2]
