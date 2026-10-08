"""Standalone shipped producer owner: no API path, Gemmi, or inference imports."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_relocated_producer_script_with_isolated_python(tmp_path):
    scripts = tmp_path/'scripts'
    scripts.mkdir()
    script = scripts/'analyse_fampnn.py'
    shutil.copy2(ROOT/'scripts/analyse_fampnn.py', script)
    inputs = tmp_path/'input'
    inputs.mkdir()
    (inputs/'test.pdb').write_text(
        'ATOM      1  CA  SER Z   1       0.000   0.000   0.000  1.00  0.00           C\n'
        'ATOM      2  CB  SER Z   1       1.000   0.000   0.000  1.00  2.00           C\n'
        'ATOM      3  OG  SER Z   1       2.000   0.000   0.000  1.00  8.00           O\nEND\n')
    # -I excludes repository/PYTHONPATH from import resolution, as /scripts does.
    subprocess.run([sys.executable, '-I', str(script), '--input_dir', str(inputs), '--out_dir', str(inputs), '--ignore_cbeta'], cwd=tmp_path, check=True)
    result = json.loads((inputs/'test.json').read_text())
    assert result['fampnn_avg_psce'] == 8
    assert result['psce_policy']['chain_id'] == 'all_chains'
    assert result['chain_avg_psce'] == {'Z': 8}
    # The dependency is present in both deployed owners; no new module to stage.
    assert 'biopython' in (ROOT/'apptainer/fampnn.def').read_text()
    assert 'biopython' in (ROOT/'platform/api/pyproject.toml').read_text()
    assert '${params.code_root}/scripts:/scripts' in (ROOT/'nextflow.config').read_text()


def test_no_score_candidates_do_not_abort_mixed_batch(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('test_psce_owner', ROOT/'scripts/analyse_fampnn.py')
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    inputs, outputs = tmp_path/'input', tmp_path/'output'
    inputs.mkdir()
    for filename, residue, atom in [('a_gly', 'GLY', 'CA'), ('b_ala', 'ALA', 'CB'), ('z_scored', 'SER', 'OG')]:
        (inputs/f'{filename}.pdb').write_text(f'ATOM      1 {atom:^4s} {residue} A   1       0.000   0.000   0.000  1.00  6.00           C\nEND\n')
    result = owner.average_per_residue_bfactor(inputs, 'all_chains', True, outputs)
    assert result == {'z_scored.pdb': 6}
    assert sorted(p.name for p in outputs.iterdir()) == ['z_scored.json']


def test_original_all_model_residue_and_alternate_atom_population(tmp_path):
    import importlib.util
    import pytest
    spec = importlib.util.spec_from_file_location('test_psce_population', ROOT/'scripts/analyse_fampnn.py')
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    path = tmp_path/'population.pdb'
    def atom(serial, name, residue, number, score, alt=' ', occupancy=1, record='ATOM'):
        return f'{record:6s}{serial:5d} {name:^4s}{alt}{residue} A{number:4d}    {0:8.3f}{0:8.3f}{0:8.3f}{occupancy:6.2f}{score:6.2f}           C\n'
    path.write_text('MODEL        1\n' + atom(1,'CA','MSE',1,0,record='HETATM')
        + atom(2,'SE','MSE',1,6,'A',.2,'HETATM') + atom(3,'SE','MSE',1,10,'B',.8,'HETATM')
        + atom(4,'C1','LIG',2,12,record='HETATM') + 'ENDMDL\nMODEL        2\n'
        + atom(5,'OG','SER',1,20) + 'ENDMDL\nEND\n')
    policy = owner.psce_policy('all_chains', True)
    profile = owner.compute_psce_profile(path, policy)
    assert profile['chains']['A']['psce'] == [8, 12, 20]
    assert profile['chains']['A']['model_indices'] == [0, 0, 1]
    assert profile['chains']['A']['residue_names'] == ['MSE', 'LIG', 'SER']
    assert profile['summary']['avg_psce'] == pytest.approx(40 / 3)
    assert policy['models'] == policy['residues'] == policy['altloc'] == 'all'
    owner.average_per_residue_bfactor(tmp_path, 'A', True, tmp_path/'output')
    assert json.loads((tmp_path/'output/population.json').read_text())['fampnn_avg_psce'] == 13.33
