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


def test_dna_polymerase_label_preserves_engine():
    template = yaml.safe_load((ROOT / 'platform/api/config/templates/dna_polymerase.yaml').read_text())
    assert template['stages'][0]['tool'] == 'FA-MPNN'
    assert template['preset_params']['seq_method'] == 'fampnn'
    assert template['preset_params']['pred_method'] == 'boltz'
