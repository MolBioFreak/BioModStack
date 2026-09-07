"""Literal FilterBoltzGen staging/publication with data only; no image is run."""
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.filtering.evidence import npz_metadata


def test_native_bytes_survive_literal_filter_staging(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    source = tmp_path / 'source'; source.mkdir()
    designs = tmp_path / 'designs'; designs.mkdir()
    np.savez(source / 'a.npz', design_ptm=np.array([0.]), affinity_probability_binary1=np.array([0.]))
    npz_metadata(source, designs, {'a'})
    (designs / 'a.pdb').write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\nEND\n')
    harness = tmp_path / 'native.nf'
    harness.write_text(f"include {{ FilterBoltzGen }} from '{ROOT}/modules/boltzgen.nf'\nworkflow {{ FilterBoltzGen(Channel.fromPath(params.pdbs).collect(), Channel.fromPath(params.jsons).collect()) }}\n")
    config = tmp_path / 'local.config'; config.write_text("process.executor = 'local'\nprocess.shell = ['/bin/bash', '-euo', 'pipefail']\n")
    output = tmp_path / 'published'
    params = {'out_dir': str(output), 'code_root': str(ROOT), 'pdbs': str(designs / '*.pdb'),
              'jsons': str(designs / '*.{json,npz,csv}'), 'core_protein_scientific_contract': 1,
              'boltzgen_filter_biased': False, 'boltzgen_budget': 1, 'boltzgen_alpha': 0,
              'boltzgen_metrics_override': 'design_ptm=none affinity_probability=none filter_rmsd=none'}
    params_file = tmp_path / 'params.json'; params_file.write_text(json.dumps(params))
    jar = os.environ['BMS_TEST_NEXTFLOW_JAR']
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    result = subprocess.run(['java', '-jar', jar, 'run', str(harness), '-c', str(config), '-params-file', str(params_file),
                             '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false'], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    published = output / 'collected/boltzgen_filtered'
    assert (published / 'native_a.npz').read_bytes() == (source / 'a.npz').read_bytes()
    report = json.loads((published / 'filter_summary.json').read_text())
    assert set(report['publication']['a']) == {'structure', 'metrics', 'native'}
