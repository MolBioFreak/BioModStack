"""WP07 behavioral regressions. Parser/energy seams are instrumented CPU doubles,
not PyRosetta execution or claims about scientific model results.
"""
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import score_maturation as score
import filter_maturation as filtering


def mutate_after_capture(monkeypatch, paths):
    read = Path.read_bytes
    def capture(path):
        data = read(path)
        if path in paths:
            path.write_bytes(b'replaced after snapshot')
        return data
    monkeypatch.setattr(Path, 'read_bytes', capture)


def test_strict_parser_consumes_hashed_snapshot(monkeypatch, tmp_path):
    original, candidate = tmp_path/'ref.pdb', tmp_path/'candidate.pdb'
    original.write_bytes(b'reference snapshot')
    candidate.write_bytes(b'candidate snapshot')
    seen = []
    class ParsedBoth(Exception):
        pass
    def parse(path):
        seen.append(Path(path).read_bytes())
        if len(seen) == 2:
            raise ParsedBoth
        return object()
    monkeypatch.setitem(sys.modules, 'pyrosetta', SimpleNamespace(init=lambda _: None, pose_from_pdb=parse))
    mutate_after_capture(monkeypatch, {original, candidate})
    monkeypatch.setattr(sys, 'argv', ['score', '--core-protein-scientific-contract', '1', '--original_pdb', str(original), '--matured_pdb', str(candidate), '--antibody_chains', 'H', '--antigen_chains', 'A', '--output', str(tmp_path/'score.json')])
    with pytest.raises(ParsedBoth):
        score.main()
    assert seen == [b'reference snapshot', b'candidate snapshot']


def test_strict_filter_publishes_hashed_snapshot(monkeypatch, tmp_path):
    candidate = tmp_path/'candidate.pdb'
    candidate.write_bytes(b'candidate snapshot')
    scores = tmp_path/'score.json'
    scores.write_text(json.dumps({'core_protein_scientific_contract': 1, 'candidate_sha256': hashlib.sha256(b'candidate snapshot').hexdigest(), 'selected_delta_interface_score': 0.0}))
    mutate_after_capture(monkeypatch, {candidate})
    monkeypatch.setattr(sys, 'argv', ['filter', '--core-protein-scientific-contract', '1', '--score_json', str(scores), '--pdb_path', str(candidate), '--output_dir', str(tmp_path/'pass'), '--report_json', str(tmp_path/'report.json')])
    filtering.main()
    assert json.loads((tmp_path/'report.json').read_text())['passed'] is True
    assert (tmp_path/'pass'/'candidate.pdb').read_bytes() == b'candidate snapshot'


class Vec:
    def __init__(self, x):
        self.x, self.y, self.z = float(x), 0.0, 0.0

    def distance(self, other):
        return math.sqrt((self.x-other.x)**2 + (self.y-other.y)**2 + (self.z-other.z)**2)


class Pose:
    def __init__(self, keys, positions, energies):
        self.keys, self.positions, self.energies = keys, positions, energies

    def total_residue(self):
        return len(self.keys)

    def pdb_info(self):
        return SimpleNamespace(chain=lambda i: self.keys[i-1][0], number=lambda i: self.keys[i-1][1], icode=lambda i: self.keys[i-1][2])

    def residue(self, i):
        xyz = Vec(self.positions[i-1])
        return SimpleNamespace(has=lambda _: True, atom=lambda _: SimpleNamespace(xyz=lambda: xyz), nbr_atom_xyz=lambda: xyz, name1=lambda: 'A')


R = [('H', 100, ''), ('H', 100, 'A'), ('H', 101, '')]
C = [('B', 200, ''), ('B', 200, 'A'), ('B', 201, '')]


def run_scorer(monkeypatch, tmp_path, *, request=True, bad=None, mode='selected_interface', transform=None, strict=True):
    original, candidate = tmp_path/'ref.pdb', tmp_path/'candidate.pdb'
    original.write_bytes(b'reference snapshot')
    candidate.write_bytes(b'candidate snapshot')
    poses = {
        b'reference snapshot': Pose(R+[('A', 1, '')], [1, 2, 6, 0], [-100, -7, -5]),
        b'candidate snapshot': Pose(C+[('C', 1, '')], [1, 4, 6, 0], [-200, -3 if bad is None else bad, -5]),
    }
    runtime = SimpleNamespace(init=lambda _: None, pose_from_pdb=lambda path: poses[Path(path).read_bytes()], get_fa_scorefxn=lambda: lambda pose: None)
    monkeypatch.setitem(sys.modules, 'pyrosetta', runtime)
    monkeypatch.setattr(score, 'pair_energy_total', lambda fxn, pose, a, b: pose.energies[a-1])
    monkeypatch.setattr(score, 'calculate_rosetta_interface_analyzer_metrics', lambda *args: {})
    args = ['score', '--core-protein-scientific-contract', '1', '--original_pdb', str(original), '--matured_pdb', str(candidate), '--antibody_chains', 'H', '--antigen_chains', 'A', '--selected_positions', 'H100A', '--objective_mode', mode, '--output', str(tmp_path/'score.json')]
    def domain(indices):
        return {'reference': [R[i] for i in indices], 'candidate': [C[i] for i in indices], 'pairs': [(R[i], C[i]) for i in indices]}
    if request:
        payload = {'reference_sha256': hashlib.sha256(original.read_bytes()).hexdigest(), 'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(), 'roles': {'reference': {'binder': ['H'], 'target': ['A']}, 'candidate': {'binder': ['B'], 'target': ['C']}}, 'domains': {'whole_binder': domain([0, 1, 2]), 'selected': domain([1]), 'nonselected': domain([0, 2]), 'H3': domain([1])}}
        if transform:
            transform(payload)
        path = tmp_path/'request.json'
        path.write_text(json.dumps(payload))
        args += ['--comparison-request', str(path)]
    else:
        # Explicit same chain roles are NOT a residue-correspondence map.
        poses[b'candidate snapshot'].keys = R+[('A', 1, '')]
    if not strict:
        args = [args[0]] + args[3:]
    monkeypatch.setattr(sys, 'argv', args)
    score.main()
    return json.loads((tmp_path/'score.json').read_text())


def test_main_uses_exact_side_specific_request_domains(monkeypatch, tmp_path):
    data = run_scorer(monkeypatch, tmp_path)
    assert data['selected_interface_score_original'] == -7
    assert data['selected_interface_score_matured'] == -3
    assert data['selected_delta_interface_score'] == data['objective_score'] == 4
    assert data['selected_positions'] == ['H100A']
    assert data['selected_positions_matured'] == ['B200A']
    loop = data['loop_metrics']['H3']
    assert loop['positions'] == ['H100A']
    assert loop['positions_matured'] == ['B200A']
    assert loop['rmsd_backbone'] == 2
    assert loop['target_contact_count_original'] == loop['target_contact_count_matured'] == 1
    assert loop['target_min_distance_original'] == 2
    assert loop['target_min_distance_matured'] == 4
    assert loop['delta_interface_score'] == 4


def test_missing_map_does_not_infer_candidate_selected_domain(monkeypatch, tmp_path):
    data = run_scorer(monkeypatch, tmp_path, request=False)
    assert data['selected_interface_score_original'] == -7
    assert data['selected_interface_score_matured'] is None
    assert data['objective_score'] is None
    assert data['selection_scope_reason'] == 'missing_candidate_domain_authority'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), True, False])
def test_interface_observation_invalidates_only_affected_scopes(monkeypatch, bad):
    pose = Pose(R+[('A', 1, '')], [1, 2, 6, 0], [-2, bad, 0])
    monkeypatch.setattr(score, 'pyrosetta', SimpleNamespace(get_fa_scorefxn=lambda: lambda pose: None))
    monkeypatch.setattr(score, 'pair_energy_total', lambda fxn, pose, a, b: pose.energies[a-1])
    result = score.canonical_payload(score.interface_score(pose, ['H'], ['A'], 8, selected_positions={R[1]}, position_groups={'invalid': {R[1]}, 'valid': {R[0]}, 'zero': {R[2]}}, strict=True))
    assert result['global_score'] is None
    assert result['selected_score'] is None
    assert result['position_groups']['invalid']['score'] is None
    assert result['position_groups']['valid']['score'] == -2
    assert result['position_groups']['zero']['score'] == 0
    assert result['selected_unavailable_reason'] == 'invalid_pair_energy'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), True, False])
@pytest.mark.parametrize('filter_args', [[], ['--disable_filter'], ['--min_improvement', '0']])
def test_invalid_observation_cannot_pass_real_scorer_filter(monkeypatch, tmp_path, bad, filter_args):
    data = run_scorer(monkeypatch, tmp_path, bad=bad)
    assert data['selected_delta_interface_score'] is None
    assert data['objective_score'] is None
    assert data['loop_metrics']['H3']['objective_score'] is None
    monkeypatch.setattr(sys, 'argv', ['filter', '--core-protein-scientific-contract', '1', '--score_json', str(tmp_path/'score.json'), '--pdb_path', str(tmp_path/'candidate.pdb'), '--output_dir', str(tmp_path/'pass'), '--report_json', str(tmp_path/'report.json')] + filter_args)
    filtering.main()
    assert json.loads((tmp_path/'report.json').read_text())['passed'] is False
    assert not (tmp_path/'pass'/'candidate.pdb').exists()


def test_true_zero_pair_energy_is_valid(monkeypatch):
    pose = Pose([R[1], ('A', 1, '')], [2, 0], [0])
    monkeypatch.setattr(score, 'pyrosetta', SimpleNamespace(get_fa_scorefxn=lambda: lambda pose: None))
    monkeypatch.setattr(score, 'pair_energy_total', lambda *args: 0.0)
    result = score.interface_score(pose, ['H'], ['A'], 8, selected_positions={R[1]}, strict=True)
    assert result['global_score'] == result['selected_score'] == 0.0
    assert score.compute_overall_objective_score({}, 'selected_interface', result['selected_score'], result['global_score'], None, 0, strict=True) == 0.0


def test_declared_scopes_do_not_add_rmsd_gate_to_default(monkeypatch, tmp_path):
    def no_pairs(payload):
        for domain in payload['domains'].values():
            domain.pop('pairs')
    data = run_scorer(monkeypatch, tmp_path, transform=no_pairs)
    assert data['selected_rmsd_backbone'] is None
    assert data['objective_score'] == 4


def test_plain_number_domain_excludes_insertion_neighbor(monkeypatch, tmp_path):
    def plain(payload):
        for name in ('selected', 'H3'):
            payload['domains'][name] = {'reference': [R[0]], 'candidate': [C[0]], 'pairs': [(R[0], C[0])]}
    data = run_scorer(monkeypatch, tmp_path, transform=plain)
    assert data['selected_interface_score_original'] == -100
    assert data['selected_interface_score_matured'] == -200
    assert data['loop_metrics']['H3']['target_contact_count_matured'] == 1
    assert data['objective_score'] == -100


def test_loop_target_formula_uses_declared_geometry(monkeypatch, tmp_path):
    data = run_scorer(monkeypatch, tmp_path, mode='loop_target')
    # delta E=4, contact delta=0, distance/centroid improvement=-2,
    # loop RMSD=2, nonselected RMSD=0, one CA clash. No superposition.
    assert data['loop_metrics']['H3']['objective_score'] == pytest.approx(4.7)
    assert data['clash_count_ca'] == 1
    assert data['objective_score'] == pytest.approx(4.9)


def test_unmarked_numerics_remain_legacy(monkeypatch, tmp_path):
    data = run_scorer(monkeypatch, tmp_path, strict=False)
    assert 'core_protein_scientific_contract' not in data
    assert data['interface_score_original'] == -112
    assert data['interface_score_matured'] == -208
    assert data['delta_interface_score'] == -96
    assert data['selected_interface_score_original'] == -107
    assert data['selected_interface_score_matured'] == 0
    assert data['objective_score'] == 107
    assert data['rmsd_backbone'] == pytest.approx(math.sqrt(4/3))


@pytest.mark.parametrize('mode', ['loop_epitope', 'balanced'])
def test_explicit_target_domain_drives_epitope_contacts(monkeypatch, tmp_path, mode):
    def epitope(payload):
        payload['domains']['epitope'] = {'reference': [('A', 1, '')], 'candidate': [('C', 1, '')], 'pairs': [(('A', 1, ''), ('C', 1, ''))]}
    data = run_scorer(monkeypatch, tmp_path, mode=mode, transform=epitope)
    assert data['comparisons']['epitope']['value'] == 0
    loop = data['loop_metrics']['H3']
    assert loop['epitope_min_distance_original'] == 2
    assert loop['epitope_min_distance_matured'] == 4
    expected = 4 - .35*(-2) - .10*(-2) + .10*2
    if mode == 'balanced':
        expected += -.20*(-2) - .05*(-2)
    assert loop['objective_score'] == pytest.approx(expected)
    assert data['objective_score'] == pytest.approx(expected + .20)


@pytest.mark.parametrize('disabled', [False, True])
def test_missing_map_cannot_filter_via_global_fallback(monkeypatch, tmp_path, disabled):
    run_scorer(monkeypatch, tmp_path, request=False)
    args = ['filter', '--core-protein-scientific-contract', '1', '--score_json', str(tmp_path/'score.json'), '--pdb_path', str(tmp_path/'candidate.pdb'), '--output_dir', str(tmp_path/'pass'), '--report_json', str(tmp_path/'report.json')]
    monkeypatch.setattr(sys, 'argv', args + (['--disable_filter'] if disabled else []))
    filtering.main()
    assert json.loads((tmp_path/'report.json').read_text())['passed'] is False
