"""Native CLI and module-command qualification with explicit scientific doubles."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / 'scripts/run_protenix_inference.py'


@pytest.fixture
def native(tmp_path):
    fake = tmp_path / 'fake'
    fake.mkdir()
    (fake / 'torch.py').write_text('''import os
print('TORCH_IMPORT')
mode = os.environ['TORCH_CASE']
if mode == 'import': raise RuntimeError('fixture import failure')
__version__ = 'fixture'
class version: cuda = 'fixture'
class cuda:
    @staticmethod
    def is_available():
        print('CUDA_CHECK')
        return mode != 'cpu'
    @staticmethod
    def get_device_capability(index):
        assert index == 0
        if mode == 'capability': raise RuntimeError('fixture capability failure')
        return (8, 0)
    @staticmethod
    def get_arch_list():
        return {'supported': ['sm_80', 'sm_75', 'sm_80'],
                'unsupported': ['sm_90', 'sm_75'], 'empty': [],
                'malformed': ['compute_80', 'sm_bad', 'garbage'],
                'mixed': ['sm_bad', 'sm_80suffix'], 'nonstring': [None]
                }.get(mode, ['sm_80'])
''')
    for package in ['runner', 'configs']:
        (fake / package).mkdir()
        (fake / package / '__init__.py').write_text('')
    (fake / 'configs/configs_inference.py').write_text("print('CONFIG_IMPORT')\ninference_configs = {}\n")
    (fake / 'runner/batch_inference.py').write_text('''import json
import torch
print('RUNNER_IMPORT')
class Config(dict): sorted_by_ranking_score = False
class Runner:
    configs = Config()
    def init_dumper(self, **kw): print('DUMPER', json.dumps(kw, sort_keys=True))
def get_default_runner(**kw):
    print('CONSTRUCT', json.dumps(kw, sort_keys=True))
    return Runner()
def preprocess_input(filename, **kw):
    print('PREPROCESS', json.dumps(kw, sort_keys=True))
    return filename
''')
    (fake / 'runner/inference.py').write_text("def infer_predict(*args): print('PREDICT')\n")
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    for name in ['a.json', 'b.json']:
        (inputs / name).write_text('[]')
    env = dict(os.environ, PYTHONPATH=str(fake), TORCH_CASE='supported',
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    return tmp_path, inputs, env


@pytest.mark.parametrize('case,code,message', [
    ('supported', 0, "supported=['sm_75', 'sm_80']"),
    ('unsupported', 88, "ERROR: GPU architecture sm_80 is unsupported by this torch build: ['sm_75', 'sm_90']"),
    ('empty', 0, 'supported=[]'),
    ('malformed', 0, 'supported=[]'),
    ('mixed', 0, "supported=['sm_80']"),
    ('cpu', 0, 'WARNING: torch.cuda.is_available() is false; continuing'),
    ('import', 87, 'ERROR: Could not import torch: fixture import failure'),
    ('capability', 1, 'fixture capability failure'),
    ('nonstring', 1, 'TypeError'),
])
def test_native_cli_gpu_branches_and_control_order(native, case, code, message):
    root, inputs, env = native
    result = subprocess.run([sys.executable, str(WRAPPER), '--input', str(inputs),
        '--out_dir', str(root / 'out')], env=dict(env, TORCH_CASE=case),
        cwd=root, capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    assert result.returncode == code, output
    assert message in output
    assert output.count('TORCH_IMPORT') == 1
    if code:
        assert 'CONFIG_IMPORT' not in output and 'CONSTRUCT' not in output
    else:
        assert output.count('CUDA_CHECK') == 1
        assert output.index(message) < output.index('CONFIG_IMPORT') < output.index('RUNNER_IMPORT') < output.index('CONSTRUCT') < output.index('DUMPER') < output.index('PREPROCESS') < output.index('PREDICT')
        assert output.count('PREDICT') == 2  # one check per invocation, not per input
        options = json.loads(next(line.removeprefix('CONSTRUCT ') for line in output.splitlines() if line.startswith('CONSTRUCT ')))
        assert options['model_name'] == 'protenix-v2'
        assert options['n_cycle'] == 10 and options['n_step'] == 200


@pytest.mark.parametrize('args,code', [(['--help'], 0), ([], 2)])
def test_cli_parser_does_not_load_scientific_runtime(native, args, code):
    root, _, env = native
    result = subprocess.run([sys.executable, str(WRAPPER), *args], cwd=root,
        env=dict(env, TORCH_CASE='import'), capture_output=True, text=True, timeout=20)
    assert result.returncode == code
    assert 'TORCH_IMPORT' not in result.stdout


@pytest.mark.parametrize('process', ['ProtenixPredict', 'ProtenixFromComplex'])
@pytest.mark.parametrize('case,code', [('supported', 0), ('unsupported', 88), ('cpu', 0)])
def test_actual_module_prediction_command_consumes_wrapper(native, process, case, code):
    root, inputs, env = native
    source = (ROOT / 'modules/protenix.nf').read_text()
    assert 'import torch' not in source
    body = source.split('process ' + process + ' {', 1)[1]
    start = body.index('    python3 ${params.code_root}/scripts/run_protenix_inference.py')
    command = body[start:body.index('\n\n', start)]
    # Evaluate the actual Groovy GString, preserving its shell quoting and flags.
    bindings = dict(params={'code_root': str(ROOT)}, effective_model='protenix-v2',
        seeds='7,9', n_sample=2, n_step=31, n_cycle=4, use_msa=False,
        use_template=False, enable_cache=True, enable_fusion=True, anchor_target=False)
    groovy = root / 'command.groovy'
    groovy.write_text('\n'.join(f'def {k} = new groovy.json.JsonSlurper().parseText({json.dumps(json.dumps(v))})'
        for k, v in bindings.items()) + '\nprint """' + command + '"""\n')
    rendered = subprocess.run(['java', '-cp', os.environ['BMS_TEST_NEXTFLOW_JAR'],
        'groovy.ui.GroovyMain', str(groovy)], cwd=root, capture_output=True, text=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', rendered.stdout],
        cwd=root, env=dict(env, TORCH_CASE=case, PROTENIX_INPUT_JSON=str(inputs)),
        capture_output=True, text=True, timeout=20)
    assert result.returncode == code, result.stdout + result.stderr
    assert result.stdout.count('TORCH_IMPORT') == 1
    assert result.stdout.count('CUDA_CHECK') == 1
    if code == 0:
        options = json.loads(next(line.removeprefix('CONSTRUCT ') for line in result.stdout.splitlines() if line.startswith('CONSTRUCT ')))
        assert options['seeds'] == [7, 9] and options['n_sample'] == 2
        assert options['use_msa'] is False and options['use_template'] is False
        assert options['n_cycle'] == 4 and options['n_step'] == 31
    else:
        assert 'CONSTRUCT' not in result.stdout
