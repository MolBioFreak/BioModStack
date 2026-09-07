"""Pure CPU correspondence and actual filter CLI acceptance; no model outcomes."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import score_maturation as score


def compare(a, b, pairs, reference_domain=None, candidate_domain=None, subset=None):
    fn = getattr(score, 'compare_declared_domain', None)
    assert callable(fn), 'production scorer needs exact declared-domain correspondence'
    return fn(a, b, reference_domain or list(a), candidate_domain or list(b), pairs, domain='whole_binder', subset=subset)


def test_request_domain_projection_drives_real_comparison_and_cannot_shrink_full_binder():
    fn = getattr(score, 'compare_request_domains', None)
    assert callable(fn), 'production domain projection is missing'
    request = {'domains': {'whole_binder': {'reference': [A, B], 'candidate': [X, Y], 'pairs': [(A, X), (B, Y)]}, 'selected': {'reference': [B], 'candidate': [Y], 'pairs': [(B, Y)]}}}
    result = fn(request, {A: xyz(0), B: xyz(2)}, {X: xyz(0), Y: xyz(2)}, [A, B], [X, Y])
    assert result['whole_binder']['value'] == 0
    assert result['selected']['matched_count'] == 1
    request['domains']['whole_binder'] = {'reference': [A], 'candidate': [X], 'pairs': [(A, X)]}
    assert fn(request, {A: xyz(0), B: xyz(2)}, {X: xyz(0), Y: xyz(2)}, [A, B], [X, Y])['whole_binder']['reason'] == 'declared_domain_mismatch'


def test_request_binding_rejects_foreign_bytes_and_roles():
    fn = getattr(score, 'validate_comparison_request', None)
    assert callable(fn), 'request binding must be validated by production scorer'
    request = {'reference_sha256': hashlib.sha256(b'reference').hexdigest(), 'candidate_sha256': hashlib.sha256(b'candidate').hexdigest(), 'roles': {'reference': {'binder': ['H'], 'target': ['A']}, 'candidate': {'binder': ['B'], 'target': ['C']}}, 'domains': {}}
    assert fn(request, b'reference', b'candidate') is None
    assert fn(request, b'foreign', b'candidate') == 'reference_identity_mismatch'
    assert fn(request, b'reference', b'foreign') == 'candidate_identity_mismatch'
    request['roles']['candidate']['binder'] = ['C']
    assert fn(request, b'reference', b'candidate') == 'invalid_role_authority'


@pytest.mark.parametrize('antigen_arg', [[], ['--antigen_chains', 'A']])
def test_real_scorer_missing_roles_emits_unavailable_without_runtime(tmp_path, antigen_arg):
    original, candidate = tmp_path/'source.pdb', tmp_path/'candidate.pdb'
    original.write_bytes(b'not a structure: no model must load for missing authority')
    candidate.write_bytes(b'candidate bytes')
    output = tmp_path/'score.json'
    run = subprocess.run([sys.executable, str(Path(__file__).with_name('score_maturation.py')), '--core-protein-scientific-contract', '1', '--original_pdb', str(original), '--matured_pdb', str(candidate), '--output', str(output)] + antigen_arg, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    data = json.loads(output.read_text())
    assert data['objective_score'] is None
    assert data['unavailable_reason'] == 'missing_role_authority'
    assert data['candidate_sha256'] == hashlib.sha256(candidate.read_bytes()).hexdigest()


def xyz(x):
    return SimpleNamespace(x=x, y=0.0, z=0.0)


A, B, C = ('H', 1, ''), ('H', 2, 'A'), ('H', 3, '')
X, Y = ('B', 101, ''), ('B', 102, 'A')


def test_complete_explicit_renumbering_preserves_frame_and_formula():
    result = compare({A: xyz(0), B: xyz(2)}, {X: xyz(3), Y: xyz(5)}, [(A, X), (B, Y)])
    assert result['value'] == 3.0  # no superposition
    assert result['reference_coverage'] == result['candidate_coverage'] == 1
    assert result['matched_count'] == 2


@pytest.mark.parametrize('pairs', [[(A, X)], []])
def test_partial_or_zero_never_reports_full_domain(pairs):
    result = compare({A: xyz(0), B: xyz(2)}, {X: xyz(0)}, pairs)
    assert result['value'] is None
    assert result['reason'] in {'incomplete_correspondence', 'zero_correspondence'}
    assert result['expected_reference_count'] == 2
    assert result['expected_candidate_count'] == 1
    assert result['unmatched_reference']


def test_declared_deleted_terminus_remains_in_denominator():
    result = compare({A: xyz(0)}, {X: xyz(0)}, [(A, X), (B, Y)], [A, B], [X, Y])
    assert result['value'] is None
    assert result['reference_coverage'] == result['candidate_coverage'] == .5
    assert result['unmatched_reference'][0]['reason'] == 'missing_coordinates'


def test_explicit_subset_is_named_and_cannot_fill_full_value():
    result = compare({A: xyz(0), B: xyz(2), C: xyz(4)}, {X: xyz(0), Y: xyz(2)}, [(A, X)], subset='selected_loop_H1')
    assert result['value'] is None
    assert result['subset']['name'] == 'selected_loop_H1'
    assert result['subset']['value'] == 0.0
    assert result['reference_coverage'] == pytest.approx(1/3)
    assert result['candidate_coverage'] == .5


@pytest.mark.parametrize('pairs', [[(A, X), (A, Y)], [(A, X), (B, X)], [(C, X)]])
def test_duplicate_and_foreign_mapping_rejected(pairs):
    result = compare({A: xyz(0), B: xyz(2)}, {X: xyz(0), Y: xyz(2)}, pairs)
    assert result['value'] is None
    assert result['reason'] == 'invalid_correspondence'


def test_integer_overlap_is_not_authority():
    result = compare({A: xyz(0)}, {A: xyz(0)}, None)
    assert result['value'] is None
    assert result['reason'] == 'missing_correspondence_authority'


@pytest.mark.parametrize('bad', [True, float('nan'), float('inf')])
def test_invalid_coordinates_are_unavailable(bad):
    assert compare({A: xyz(bad)}, {X: xyz(0)}, [(A, X)])['value'] is None


@pytest.mark.parametrize('mode', ['loop_epitope', 'balanced'])
def test_strict_objective_cannot_fall_back_to_target_when_epitope_missing(mode):
    assert score.compute_loop_objective_score({'delta_interface_score': -2, 'rmsd_backbone': 0.0}, mode, strict=True) is None


@pytest.mark.parametrize('bad', [True, float('nan'), float('inf')])
def test_strict_objective_rejects_invalid_rmsd(bad):
    assert score.compute_loop_objective_score({'delta_interface_score': -2, 'rmsd_backbone': bad}, 'loop_target', strict=True) is None


@pytest.mark.parametrize('mode', ['loop_target', 'loop_epitope', 'balanced'])
def test_rmsd_dependent_objectives_block_missing_and_accept_zero(mode):
    fn = score.compute_loop_objective_score
    assert fn({'delta_interface_score': -2}, mode, strict=True) is None
    assert fn({'delta_interface_score': -2, 'rmsd_backbone': 0.0, 'epitope_contact_delta': 0.0}, mode, strict=True) == -2
    assert score.compute_overall_objective_score({}, mode, -2, -3, None, 0, strict=True) is None


@pytest.mark.parametrize('bad', [True, float('nan'), float('inf')])
def test_strict_selected_objective_rejects_invalid_numeric_evidence(bad):
    assert score.compute_loop_objective_score({'delta_interface_score': bad}, 'selected_interface', strict=True) is None
    assert score.compute_overall_objective_score({}, 'selected_interface', bad, -3, None, 0, strict=True) is None


def test_selected_interface_does_not_acquire_rmsd_gate():
    assert score.compute_loop_objective_score({'delta_interface_score': -2}, 'selected_interface', strict=True) == -2
    assert score.compute_overall_objective_score({}, 'selected_interface', -2, -3, None, 0, strict=True) == -2


def test_native_child_ranking_never_replaces_missing_strict_score(tmp_path):
    import os
    source = (Path(__file__).resolve().parents[1]/'workflows/maturation_child_core.nf').read_text()
    marker = 'def resolveMaturationRankingScore('
    assert marker in source, 'child ranking needs strict unavailable-aware authority'
    function = marker + source.split(marker, 1)[1].split('\n}', 1)[0] + '\n}'
    harness = tmp_path/'ranking.nf'
    harness.write_text(function + '''
workflow {
    assert resolveMaturationRankingScore([objective_score:null, delta_interface_score:-9], true) == null
    assert resolveMaturationRankingScore([objective_score:0], true) == 0
    assert resolveMaturationRankingScore([objective_score:true], true) == null
    assert resolveMaturationRankingScore([objective_score:Double.NaN], true) == null
    assert resolveMaturationRankingScore([objective_score:null, delta_interface_score:-9], false) == -9
}
''')
    run = subprocess.run(['java', '-jar', str(Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'), 'run', str(harness)], cwd=tmp_path, env=dict(os.environ, NXF_OFFLINE='true', NXF_ANSI_LOG='false', NXF_HOME=str(tmp_path/'nxf')), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize('process_name', ['ScorePartialFlowImprovement', 'ScoreMaturationImprovement'])
def test_native_nextflow_scorer_filter_transport(tmp_path, process_name):
    import os
    root = Path(__file__).resolve().parents[1]
    (tmp_path/'source.pdb').write_text('CPU authority rejection fixture')
    (tmp_path/'candidate.pdb').write_text('CPU candidate fixture')
    (tmp_path/'positions.txt').write_text('H1')
    (tmp_path/'loops.json').write_text('{}')
    harness = tmp_path/'transport.nf'
    harness.write_text(f'''nextflow.enable.dsl=2
params.code_root = '{root}'
params.out_dir = '{tmp_path}/published'
params.core_protein_scientific_contract = 1
params.ppiflow_objective_mode = 'balanced'
include {{ {process_name}; FilterByMaturation }} from '{root}/modules/ppiflow.nf'
workflow {{
  {process_name}(Channel.of(tuple([id:'candidate'], file('{tmp_path}/source.pdb'), file('{tmp_path}/candidate.pdb'), file('{tmp_path}/positions.txt'), file('{tmp_path}/loops.json'))))
  FilterByMaturation({process_name}.out.scores.map {{ meta, score -> tuple(meta, file('{tmp_path}/candidate.pdb'), score) }})
}}
''')
    env = dict(os.environ, NXF_OFFLINE='true', NXF_ANSI_LOG='false', NXF_HOME=str(tmp_path/'nxf'))
    run = subprocess.run(['java', '-jar', str(Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'), '-log', str(tmp_path/'nextflow.log'), 'run', str(harness), '-work-dir', str(tmp_path/'work')], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    reports = list((tmp_path/'published').rglob('*_maturation_filter.json'))
    assert len(reports) == 1
    result = json.loads(reports[0].read_text())
    assert result['core_protein_scientific_contract'] == 1
    assert result['passed'] is False
    assert result['score_data']['unavailable_reason'] == 'missing_role_authority'
    assert not list((tmp_path/'published').rglob('*.pdb'))


@pytest.mark.parametrize('foreign', [False, True])
def test_real_filter_selected_interface_zero_needs_no_rmsd(tmp_path, foreign):
    pdb = tmp_path/'candidate.pdb'
    pdb.write_bytes(b'CPU filter fixture')
    scores = tmp_path/'score.json'
    scores.write_text(json.dumps({'core_protein_scientific_contract': 1, 'candidate_sha256': hashlib.sha256(b'foreign' if foreign else pdb.read_bytes()).hexdigest(), 'objective_mode': 'selected_interface', 'selected_delta_interface_score': 0.0, 'rmsd_backbone': None}))
    report = tmp_path/'report.json'
    run = subprocess.run([sys.executable, str(Path(__file__).with_name('filter_maturation.py')), '--core-protein-scientific-contract', '1', '--score_json', str(scores), '--pdb_path', str(pdb), '--output_dir', str(tmp_path/'pass'), '--report_json', str(report)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(report.read_text())['passed'] is (not foreign)


@pytest.mark.parametrize('disabled', [False, True])
@pytest.mark.parametrize('objective', [None, -2.0, True, float('nan')])
def test_real_filter_cli_unavailable_required_cannot_pass_without_threshold(tmp_path, disabled, objective):
    pdb = tmp_path / 'candidate.pdb'
    pdb.write_text('explicit CPU fixture, not a model output')
    scores = tmp_path / 'score.json'
    scores.write_text(json.dumps({'core_protein_scientific_contract': 1, 'candidate_sha256': hashlib.sha256(pdb.read_bytes()).hexdigest(), 'objective_mode': 'balanced', 'objective_score': objective}))
    report = tmp_path / 'report.json'
    command = [sys.executable, str(Path(__file__).with_name('filter_maturation.py')), '--core-protein-scientific-contract', '1', '--score_json', str(scores), '--pdb_path', str(pdb), '--output_dir', str(tmp_path/'pass'), '--report_json', str(report)]
    if disabled:
        command.append('--disable_filter')
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(report.read_text())['passed'] is False
    assert not list((tmp_path/'pass').glob('*.pdb'))
