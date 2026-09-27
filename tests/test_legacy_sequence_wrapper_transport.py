"""Direct production shell transport with inert native executables; no science."""
import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def shell(module, substitutions):
    source = (ROOT / 'modules' / module).read_text()
    body = source.split('    """', 1)[1].rsplit('    """', 1)[0]
    for name, value in substitutions.items():
        body = body.replace('${' + name + '}', str(value))
    assert '${' not in body
    return body.replace('\\$', '$').replace('\\\\', '\\')


def run_shell(tmp_path, body, **env):
    script = tmp_path / 'command.sh'
    script.write_text(body)
    return subprocess.run(['bash', '-euo', 'pipefail', str(script)], cwd=tmp_path,
                          env={**os.environ, **env}, capture_output=True, text=True)


@pytest.mark.parametrize('chains', ['H', 'HL'])
@pytest.mark.parametrize('count,temperature', [(3, 0.7), (1, 0.0)])
def test_antifold_requested_settings_native_shell(tmp_path, chains, count, temperature):
    package = tmp_path / 'antifold'
    package.mkdir()
    (package / '__init__.py').touch()
    (package / 'main.py').write_text(textwrap.dedent('''
        import json, sys
        from pathlib import Path
        Path('argv.json').write_text(json.dumps(sys.argv[1:]))
        Path('probabilities.csv').write_text('inert probability fixture')
        Path('sampled.fasta').write_text('>inert\nX\n')
    '''.replace('>inert\nX\n', '>inert\\nX\\n')))
    (tmp_path / 'input.pdb').write_text(''.join(f'ATOM 1 CA ALA {c} 1\n' for c in chains))
    body = shell('antifold.nf', {'pdb_imgt': 'input.pdb', 'meta.id': 'candidate',
                 'antifoldModel': '/inert/model.pt', 'sequenceCount': count, 'temperature': temperature})
    result = run_shell(tmp_path, body, PYTHONPATH=str(tmp_path))
    assert result.returncode == 0, result.stderr
    argv = json.loads((tmp_path / 'argv.json').read_text())
    assert argv[argv.index('--num_seq_per_target') + 1] == str(count)
    assert argv[argv.index('--sampling_temp') + 1] == str(temperature)
    assert ('--nanobody_mode' in argv) == (len(chains) == 1)
    assert (tmp_path / 'candidate_sampled.fasta').exists()
    source = (ROOT / 'modules/antifold.nf').read_text()
    assert 'def sequenceCount = params.seqs_per_design' in source
    assert "params.containsKey('antifold_temperature')" in source
    assert 'params.antifold_temperature : 0.2' in source


@pytest.mark.parametrize('outcome', ['success', 'failure', 'failure_with_output', 'no_output', 'wrong_name', 'missing_script', 'missing_model'])
def test_thermompnn_attempt_owned_native_shell(tmp_path, outcome):
    native = tmp_path / 'native'
    analysis = native / 'analysis'
    analysis.mkdir(parents=True)
    (native / 'models').mkdir()
    if outcome != 'missing_model':
        (native / 'models/thermoMPNN_default.pt').touch()
    fixture = textwrap.dedent('''
        import argparse, json, os, sys
        from pathlib import Path
        parser = argparse.ArgumentParser()
        for key in ['pdb', 'model_path', 'out_dir']:
            parser.add_argument('--' + key, required=True)
        args = parser.parse_args()
        Path(os.environ['ARGV_RECORD']).write_text(json.dumps(vars(args)))
        assert Path(args.pdb).is_absolute()
        assert Path(args.out_dir).is_absolute()
        assert Path.cwd() == Path(__file__).parent
        outcome = os.environ['OUTCOME']
        if outcome in ['success', 'failure_with_output', 'wrong_name']:
            name = 'ThermoMPNN_inference_' + Path(args.pdb).name.rstrip('.pdb') + '.csv'
            if outcome == 'wrong_name':
                name = 'ThermoMPNN_inference_unrelated.csv'
            (Path(args.out_dir) / name).write_bytes(b'Model,Dataset,ddG_pred\\nThermoMPNN,inert,0.125\\n')
        print('inert native stdout')
        print('inert native stderr', file=sys.stderr)
        sys.exit(9 if outcome.startswith('failure') else 0)
    ''')
    if outcome != 'missing_script':
        (analysis / 'custom_inference.py').write_text(fixture)
    # Both shared-native and task-local stale candidates must be ignored.
    stale = b'Model,Dataset,ddG_pred\nSTALE,unrelated,-999\n'
    (analysis / 'ThermoMPNN_inference_input.csv').write_bytes(stale)
    (tmp_path / 'thermo_old.csv').write_bytes(stale)
    (tmp_path / 'candidate_stability.csv').write_bytes(stale)
    pdb = tmp_path / 'input.pdb'
    pdb.write_text('INERT PRIMARY CANDIDATE\n')
    body = shell('thermompnn.nf', {'pdb': pdb.name, 'meta.id': 'candidate',
                                 'params.weights_root': tmp_path / 'absent_weights'})
    # Relocate only the installed native boundary, not the production wrapper logic.
    body = body.replace('/opt/ThermoMPNN', str(native))
    result = run_shell(tmp_path, body, OUTCOME=outcome, ARGV_RECORD=str(tmp_path / 'argv.json'),
                       BMS_WEIGHTS='', BMS_THERMOMPNN_WEIGHTS='')
    assert result.returncode == 0, result.stderr
    output = tmp_path / 'candidate_stability.csv'
    log = (tmp_path / 'thermompnn.log').read_text()
    if outcome == 'success':
        assert output.read_bytes() == b'Model,Dataset,ddG_pred\nThermoMPNN,inert,0.125\n'
    else:
        assert not output.exists()
        assert 'scores unavailable' in log
    if outcome.startswith('failure'):
        assert 'exit status 9' in log
    assert pdb.read_text() == 'INERT PRIMARY CANDIDATE\n'
    assert (analysis / 'ThermoMPNN_inference_input.csv').read_bytes() == stale
    assert not list(tmp_path.glob('thermompnn-*'))
    assert 'optional: true, emit: stability' in (ROOT / 'modules/thermompnn.nf').read_text()


def test_dna_polymerase_label_preserves_engine():
    template = yaml.safe_load((ROOT / 'platform/api/config/templates/dna_polymerase.yaml').read_text())
    assert template['stages'][0]['tool'] == 'FA-MPNN'
    assert template['preset_params']['seq_method'] == 'fampnn'
    assert template['preset_params']['pred_method'] == 'boltz'
