"""Load the real config with Nextflow, without processes or scientific execution."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ('profile', 'owner'),
    [(None, None), ('protenix', None), ('protenix', 'amber'),
     ('protenix', 'gromacs'), ('protenix', 'prepared'), ('protenix', '')],
)
def test_md_preparation_config_load_and_owner_bindings(tmp_path, profile, owner):
    launcher = os.environ['BMS_TEST_NEXTFLOW_LAUNCHER']
    params = {
        'code_root': str(ROOT),
        'data_root': str(tmp_path / 'data'),
        'md_input_root': str(tmp_path / 'input'),
        'md_preparation_container': str(tmp_path / 'amber.sif'),
        'md_preparation_runtime_lock': str(tmp_path / 'amber.lock'),
        'md_gromacs_container': str(tmp_path / 'gromacs.sif'),
    }
    if owner is not None:
        params['md_preparation_owner'] = owner
    params_file = tmp_path / 'params.json'
    params_file.write_text(json.dumps(params))
    # Reading the actual label evaluates its container closure but starts no task.
    probe = tmp_path / 'probe.nf'
    probe.write_text('''workflow {
    def prep = workflow.session.config.process.'withLabel:MolecularDynamicsPreparation'
    def receipt = [container: prep.container.call(), options: prep.containerOptions.toString(),
                   owner: params.md_preparation_owner, cpus: prep.cpus,
                   memory: prep.memory, time: prep.time, errorStrategy: prep.errorStrategy]
    new File('receipt.json').text = groovy.json.JsonOutput.toJson(receipt)
}
''')
    command = [launcher, '-C', str(ROOT / 'nextflow.config'), 'run', str(probe),
               '-params-file', str(params_file), '-work-dir', str(tmp_path / 'work')]
    if profile:
        command.extend(['-profile', profile])
    completed = subprocess.run(
        command, cwd=tmp_path, env={**os.environ, 'NXF_OFFLINE': 'true', 'BMS_FEATURE_MOLECULAR_DYNAMICS': '0'},
        text=True, capture_output=True, timeout=180,
    )
    (tmp_path / 'nextflow-output.log').write_text(completed.stdout + completed.stderr)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    amber = not owner or owner == 'amber'
    assert receipt['owner'] == owner
    assert receipt['container'] == params['md_preparation_container' if amber else 'md_gromacs_container']
    expected = '--env BMS_FEATURE_MOLECULAR_DYNAMICS=0 '
    if amber:
        expected += (
            '--env BMS_MD_PREPARATION_SIF=/opt/bms-md-preparation-runtime.sif '
            '--env BMS_MD_PREPARATION_RUNTIME_LOCK=/opt/bms-md-preparation-runtime.lock '
            f"--bind {params['md_preparation_container']}:/opt/bms-md-preparation-runtime.sif:ro "
            f"--bind {params['md_preparation_runtime_lock']}:/opt/bms-md-preparation-runtime.lock:ro "
        )
    expected += ' '.join(f"--bind {params[key]}:{params[key]}" for key in
                         ('data_root', 'md_input_root', 'code_root'))
    assert ' '.join(receipt['options'].split()) == expected
    assert {key: receipt[key] for key in ('cpus', 'memory', 'time', 'errorStrategy')} == {
        'cpus': 1, 'memory': '4 GB', 'time': '1h', 'errorStrategy': 'terminate',
    }
