"""Render exact NF/Groovy Boltz commands without executing the model.

Run with BMS_TEST_NEXTFLOW_JAR pointing at an existing cached jar. Parsed argv
are then qualified by native click make_context in the CPU evidence run.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def render_command(source, params, *, jar):
    start = source.index('python3 ${params.code_root}/scripts/run_boltz_wrapper.py predict')
    stop = source.index('2>&1', start)
    command = source[start:stop]
    script = 'import groovy.json.JsonSlurper\n'
    script += 'def params = new JsonSlurper().parseText(' + repr(json.dumps(params)) + ')\n'
    script += '''def recycling = params.boltz_recycling_steps != null ? params.boltz_recycling_steps : 3
def sampling = params.boltz_sampling_steps != null ? params.boltz_sampling_steps : 50
def numSamples = params.boltz_diffusion_samples != null ? params.boltz_diffusion_samples : (params.boltz_num_samples != null ? params.boltz_num_samples : 1)
'''
    script += 'println """' + command + '"""\n'
    # Native Groovy parses interpolation and Elvis/ternary semantics.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'render.groovy'; path.write_text(script)
        result = subprocess.run(['java', '-cp', str(jar), 'groovy.ui.GroovyMain', str(path)], capture_output=True, text=True, check=True)
    return shlex.split(result.stdout.replace('\\\n', ' '))


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    jar = os.environ['BMS_TEST_NEXTFLOW_JAR']
    params = {'code_root': '/source', 'boltz_seed': 0, 'boltz_recycling_steps': 0,
              'boltz_sampling_steps': 7, 'boltz_diffusion_samples': 3, 'boltz_max_parallel_samples': 2,
              'boltz_step_scale': 1.2, 'boltz_use_potentials': True,
              'boltz_predict_affinity': True, 'boltz_sampling_steps_affinity': 9,
              'boltz_diffusion_samples_affinity': 2, 'boltz_affinity_mw_correction': False}
    commands = {}
    source = (root/'modules/structure_prediction.nf').read_text()
    for owner in ('BoltzFromSequenceTask', 'BoltzFromSequenceWithMSATask', 'BoltzFromComplex'):
        body = source.split('process ' + owner + ' {', 1)[1].split('\n}', 1)[0]
        commands[owner] = render_command(body, params, jar=jar)
    commands['RunBoltz'] = render_command((root/'modules/boltz.nf').read_text(), params, jar=jar)
    for name, argv in commands.items():
        assert argv[argv.index('--seed')+1] == '0', (name, argv)
        assert argv[argv.index('--recycling_steps')+1] == '0', (name, argv)
        assert argv[argv.index('--diffusion_samples')+1] == '3', (name, argv)
        assert argv[argv.index('--sampling_steps')+1] == '7', (name, argv)
    print(json.dumps(commands, indent=2))
