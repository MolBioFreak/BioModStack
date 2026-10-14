"""Real Nextflow shells and helper imports/parsers; no API calls or MD science."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / 'workflows/experimental/molecular_dynamics/orchestrator.nf'
PROCESSES = (
    'MD_SPAWN_REPLICAS', 'MD_WAIT_FOR_REPLICAS', 'MD_COLLECT_REPLICAS',
    'MD_ASSERT_REPLICA_OUTCOME', 'MD_SPAWN_ANALYSIS', 'MD_WAIT_FOR_ANALYSIS',
    'MD_COLLECT_ANALYSIS', 'MD_ASSERT_ANALYSIS_OUTCOME', 'MD_COMPLETION_BARRIER',
)


@pytest.mark.parametrize('binding', ['worker', 'local'])
def test_managed_coordinator_commands(tmp_path, binding):
    launcher = os.environ.get('BMS_TEST_NEXTFLOW_LAUNCHER')
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert launcher and Path(launcher).is_file(), 'supply distribution Nextflow launcher'
    assert jar and Path(jar).is_file(), 'supply cached Nextflow 25.10.1 JAR'
    # Use the locked test venv as an isolated support-Python equivalent. Spaces
    # exercise the executable quoting, not a PATH-based interpreter replacement.
    runtime = tmp_path / 'managed runtime'
    runtime.mkdir()
    python = runtime / 'current/venv/bin/python'
    # CM's current/venv layout, with no writes to the shared interpreter.
    (runtime / 'current').mkdir()
    (runtime / 'current/venv').symlink_to(Path(sys.prefix), target_is_directory=True)
    subprocess.run([str(python), '-c', 'import requests; print(requests.__version__)'], check=True)

    aggregate = tmp_path / 'aggregate.json'
    aggregate.write_text(json.dumps({'job_id': 'parent', 'status': 'completed', 'replicas': [{}]}))
    analysis = tmp_path / 'analysis.json'
    analysis.write_text(json.dumps({'job_id': 'parent', 'status': 'completed',
        'aggregate_manifest_sha256': hashlib.sha256(aggregate.read_bytes()).hexdigest(),
        'required_analysis_children': 1, 'completed_analysis_children': 1}))
    fixture = tmp_path / 'input.json'
    fixture.write_text('{}')
    probe = tmp_path / 'probe'
    probe.mkdir()
    # Stop only after each real helper has imported its dependencies and parsed
    # the actual emitted argv. External orchestration/collection is inert; the
    # three inline outcome/barrier programs execute unchanged to completion.
    (probe / 'sitecustomize.py').write_text('''
import argparse, json, os, sys
from pathlib import Path
original = argparse.ArgumentParser.parse_args
def parse(self, *a, **kw):
    args = original(self, *a, **kw)
    import requests
    Path('parsed.json').write_text(json.dumps(dict(argv=sys.argv, args=vars(args),
        executable=sys.executable, requests=requests.__version__,
        context=os.environ.get('BMS_COMPONENT_CONTEXT'),
        portable=os.environ.get('BMS_PORTABLE_INPUT_BINDINGS')), default=str))
    if getattr(args, 'output', None):
        Path(args.output).write_text('{}')
    raise SystemExit(0)
argparse.ArgumentParser.parse_args = parse
''')
    harness = tmp_path / 'main.nf'
    harness.write_text("include { " + '; '.join(PROCESSES) + " } from '" + str(WORKFLOW) + "'\n" + f'''
workflow {{
    input = file('{fixture}')
    second = file('{aggregate}')
    third = file('{analysis}')
    MD_SPAWN_REPLICAS(input, second, third, 'parent', 'MD unchanged name', 'http://127.0.0.1:9')
    MD_WAIT_FOR_REPLICAS(input, 'parent', 'MD unchanged name', 'http://127.0.0.1:9', 7)
    MD_COLLECT_REPLICAS(input, second)
    MD_ASSERT_REPLICA_OUTCOME(input, '{aggregate}')
    MD_SPAWN_ANALYSIS(input, '{aggregate}', 'parent', 'MD unchanged name', 'http://127.0.0.1:9', 'runtime-digest')
    MD_WAIT_FOR_ANALYSIS(input, 'parent', 'MD unchanged name', 'http://127.0.0.1:9', 7)
    MD_COLLECT_ANALYSIS(input, '{aggregate}', second)
    MD_ASSERT_ANALYSIS_OUTCOME(input, '{analysis}')
    MD_COMPLETION_BARRIER(input, second, '{aggregate}', '{analysis}')
}}
''')
    config = tmp_path / 'test.config'
    config.write_text(f'''params.code_root = '{ROOT}'
params.cm_api_runtime_dir = '{runtime}'
params.out_dir = '{tmp_path / "out"}'
params.md_gromacs_container = '/inert/scientific-image.sif'
process.executor = 'local'
process.cpus = 1
process.memory = '256 MB'
process.container = null
apptainer.enabled = false
singularity.enabled = false
docker.enabled = false
''')
    home = tmp_path / 'home'
    cached = home / '.nextflow/framework/25.10.1'
    cached.mkdir(parents=True)
    (cached / 'nextflow-25.10.1-one.jar').symlink_to(Path(jar).resolve())
    env = dict(os.environ, HOME=str(home), NXF_HOME=str(home / '.nextflow'),
        NXF_OFFLINE='true', NXF_VER='25.10.1', NXF_DIST=str(home / '.nextflow/framework'),
        BMS_CACHE=str(tmp_path / 'cache'), BMS_DATA=str(tmp_path / 'data'),
        NXF_APPTAINER_CACHEDIR=str(tmp_path / 'apptainer'),
        PYTHONPATH=os.pathsep.join([str(probe), str(ROOT), str(ROOT / 'platform/api')]),
        BMS_COMPONENT_CONTEXT=str(tmp_path / 'component-context.json'),
        BMS_PORTABLE_INPUT_BINDINGS=str(tmp_path / 'portable-bindings.json'))
    env.pop('BMS_API_PYTHON', None)
    if binding == 'worker':
        env['BMS_API_PYTHON'] = str(python)
    else:
        env['BMS_CM_API_RUNTIME_DIR'] = str(runtime)
        env.pop('BMS_COMPONENT_CONTEXT')
        env.pop('BMS_PORTABLE_INPUT_BINDINGS')
    result = subprocess.run([launcher, '-C', f'{ROOT / "nextflow.config"},{config}', 'run', str(harness),
        '-w', str(tmp_path / 'work'), '-ansi-log', 'false'], cwd=tmp_path,
        env=env, text=True, capture_output=True, timeout=180)
    (tmp_path / 'nextflow-output.txt').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = list((tmp_path / 'work').glob('*/*/.command.sh'))
    assert len(commands) == len(PROCESSES)
    for command in commands:
        assert f"'{python}' " in command.read_text()
        assert (command.parent / '.exitcode').read_text().strip() == '0'
    parsed = [json.loads(p.read_text()) for p in (tmp_path / 'work').glob('*/*/parsed.json')]
    assert len(parsed) == 6
    for row in parsed:
        assert row['executable'] == str(python)
        assert row['requests']
        assert row['context'] == env.get('BMS_COMPONENT_CONTEXT')
        assert row['portable'] == env.get('BMS_PORTABLE_INPUT_BINDINGS')
    by_module = {row['argv'][0].split('/')[-1]: row['args'] for row in parsed
                 if not row['argv'][0].endswith('wait_for_children.py')}
    assert by_module['spawn_replicas.py']['parent_name'] == 'MD unchanged name'
    assert by_module['spawn_analysis.py']['runtime_sha256'] == 'runtime-digest'
    assert by_module['collect_analysis.py']['gromacs_container'] == '/inert/scientific-image.sif'
    waits = [row['args'] for row in parsed if row['argv'][0].endswith('wait_for_children.py')]
    assert {row['stage'] for row in waits} == {'md_replica', 'md_analysis'}
    assert all(row['poll_interval'] == 7 for row in waits)
    assert json.loads((tmp_path / 'out/md_completion_barrier.json').read_text())['status'] == 'completed'
