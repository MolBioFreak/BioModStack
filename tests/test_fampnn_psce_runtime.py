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
