"""Real Nextflow module shell with an explicit non-science inference executable."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
JAR = Path(os.environ.get('BMS_TEST_NEXTFLOW_JAR', '/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'))


@pytest.mark.parametrize('case', ['inference', 'framework', 'checkpoint', 'config', 'native_operation'])
def test_selected_rf_module_reaches_native_owner_without_synthetic_gate(tmp_path, case):
    assert JAR.is_file(), 'Pinned offline Nextflow JAR is required for this compiler fixture'
    native = tmp_path / 'native'
    native.mkdir()
    scripts = tmp_path / 'code/scripts'; scripts.mkdir(parents=True)
    # Only native installation paths/executable are fixture-relocated. The selected module shell remains real.
    module = (ROOT / 'modules/rfantibody.nf').read_text().replace('/opt/RFantibody', str(native)).replace('/opt/rfantibody_weights', str(tmp_path / 'weights'))
    assert 'check_rfantibody_runtime.py' not in module
    (tmp_path / 'rfantibody.nf').write_text(module)
    config = native / 'scripts/config/inference'
    if case != 'config':
        config.mkdir(parents=True)
    checkpoint = tmp_path / 'weights/RFdiffusion_Ab.pt'
    if case != 'checkpoint':
        checkpoint.parent.mkdir(); checkpoint.write_bytes(b'inert fixture checkpoint')
    (scripts / 'check_rfantibody_runtime.py').write_text("raise RuntimeError('synthetic probe must not run')\n")
    (scripts / 'rfantibody_inference_wrapper.py').write_text("""import json, pathlib, sys
args = sys.argv[1:]
print('FIXTURE_NATIVE_INFERENCE_REACHED')
if %s:
    raise RuntimeError('fixture native operation failed')
prefix = next(a.split('=', 1)[1] for a in args if a.startswith('inference.output_prefix='))
pathlib.Path(prefix + '_0.pdb').write_text('NON_SCIENCE_TRANSPORT_FIXTURE\\n')
""" % (case == 'native_operation'))
    target = tmp_path / 'target.pdb'; target.write_text('ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 80.00           C\n')
    framework = tmp_path / 'framework.pdb'
    framework.write_text(target.read_text().replace('ALA A', 'ALA X' if case == 'framework' else 'ALA H'))
    (tmp_path / 'main.nf').write_text("include { RFANTIBODY } from './rfantibody.nf'\nworkflow { RFANTIBODY(Channel.of(tuple([id:'fixture'], file(params.target), 'A1', 0, 1)), Channel.value(file(params.framework))) }\n")
    (tmp_path / 'nextflow.config').write_text('process.executor = "local"\nprocess.cpus = 1\nprocess.errorStrategy = "terminate"\n')
    env = {**os.environ, 'NXF_HOME': str(tmp_path / 'nxf'), 'NXF_OFFLINE': 'true',
           'JAVA_TOOL_OPTIONS': '--add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED --add-opens=java.base/java.nio=ALL-UNNAMED'}
    command = ['java', '-jar', str(JAR), 'run', str(tmp_path / 'main.nf'), '-c', str(tmp_path / 'nextflow.config'),
               '-w', str(tmp_path / 'work'), '--code_root', str(tmp_path / 'code'), '--container_dir', str(tmp_path),
               '--weights_root', str(tmp_path), '--out_dir', str(tmp_path / 'out'), '--target', str(target),
               '--framework', str(framework), '--rfd_models', str(checkpoint.parent)]
    result = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    logs = '\n'.join(path.read_text() for path in (tmp_path / 'work').rglob('.command.log'))
    evidence = result.stdout + result.stderr + logs
    (tmp_path / 'nextflow-fixture.log').write_text(evidence)
    assert list((tmp_path / 'work').rglob('.command.sh')), evidence
    assert 'synthetic probe must not run' not in evidence
    if case == 'inference':
        assert result.returncode == 0, evidence
        assert 'FIXTURE_NATIVE_INFERENCE_REACHED' in logs
    else:
        assert result.returncode != 0, evidence
        expected = {'framework': 'does not contain antibody chains labeled H or L',
                    'checkpoint': 'Could not locate RFantibody checkpoint',
                    'config': 'config directory not found',
                    'native_operation': 'fixture native operation failed'}[case]
        assert expected in evidence
        assert ('FIXTURE_NATIVE_INFERENCE_REACHED' in logs) == (case == 'native_operation')
    print('RF_POLICY_COUNTER', json.dumps({'case': case, 'synthetic_probe_calls': 0,
          'inference_reached': 'FIXTURE_NATIVE_INFERENCE_REACHED' in logs, 'returncode': result.returncode}))


def test_preload_document_preserves_supported_alternate_backend_and_manual_diagnostic():
    doc = (ROOT / 'docs/Remote_Worker_Execution_and_Cache.md').read_text()
    assert 'not a VM-only runtime requirement' in doc and 'On-start script' in doc
    assert 'images=not_requested' in doc and 'explicit full audits remain available' in doc
    assert 'requires real VM capabilities' not in doc
    assert (ROOT / 'scripts/check_rfantibody_runtime.py').is_file()
    assert 'Explicit CLI invocation only' in (ROOT / 'scripts/check_rfantibody_runtime.py').read_text()
