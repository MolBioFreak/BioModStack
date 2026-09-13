"""Pinned Nextflow boundary acceptance; recorder only, no container/science/GPU."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.component_adapter import (native_resource_config, resource_bound_command,
                                           task_container_config, wf_clone_container_config)


def test_runtime_selection_and_child_forwarding(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_CONTAINER_BACKEND', 'udocker')
    monkeypatch.setenv('BMS_CONTAINER_EXECUTABLE', '/trusted/bms-container')
    assert 'task-shell' in task_container_config()
    assert task_container_config({'backend': 'apptainer'}) == ''
    with pytest.raises(ValueError, match='absolute'):
        task_container_config({'backend': 'udocker', 'executable': 'bms-container'})
    plan = {'metadata': {'static_components': []}}
    context = dict(resources={'required': {'cpus': 1, 'memory_bytes': 1024}},
                   resource_lock_path=str(tmp_path/'compute.lock'), execution_plan=plan,
                   working_directory=str(ROOT))
    argv = resource_bound_command(['nextflow', 'run', 'main.nf'], None, context, tmp_path)
    assert argv[:3] == ['nextflow', 'run', 'main.nf']
    assert 'task-shell' in Path(argv[-1]).read_text()
    assert 'task.workDir' not in Path(argv[-1]).read_text()
    # The same function is invoked by both root and compiled-child launch paths.
    source = (ROOT/'scripts/lib/component_adapter.py').read_text()
    assert source.count('start_native(resource_bound_command(') == 2


def test_nested_inventory_and_truthful_identity(monkeypatch):
    monkeypatch.setenv('BMS_CONTAINER_BACKEND', 'udocker')
    monkeypatch.setenv('BMS_CONTAINER_EXECUTABLE', '/trusted/bms-container')
    lock = ROOT/'config/ngs/wf_clone_validation_v1.8.4.lock.json'
    config = wf_clone_container_config(lock)
    for image in json.loads(lock.read_text())['containers']['images']:
        assert image['cache_file'] in config and image['uri'] in config
    module = (ROOT/'modules/ngs/clone_validation.nf').read_text()
    assert 'wf_clone_container_config(sys.argv[2])' in module
    assert '"\\${runtime_args[@]}"' in module
    assert 'BMS_EXECUTING_IMAGE:-' in (ROOT/'modules/conformational_mapping_protenix.nf').read_text()
    from scripts.local_msa_runtime import is_isolated_task_runtime
    assert is_isolated_task_runtime({'BMS_EXECUTING_IMAGE': '/verified/image.sif'})
    assert not is_isolated_task_runtime({'BMS_CONTAINER_BACKEND': 'udocker'})
    config = (ROOT/'nextflow.config').read_text()
    assert '--network none' not in config and '--net ' not in config


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('trace', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_pinned_task_shell_composition_lock_host_and_resume(tmp_path, monkeypatch, fail, trace, nested):
    launcher = os.environ.get('BMS_TEST_NEXTFLOW_LAUNCHER')
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert launcher and Path(launcher).is_file(), 'supply the normal pinned Nextflow launcher'
    assert jar and Path(jar).is_file(), 'supply cached Nextflow 25.10.1 JAR (no downloads)'
    from native_components import PROCESS_CONTRACTS
    monkeypatch.setitem(PROCESS_CONTRACTS, 'test.nf:SCIENCE',
                        ((), (), (), (), ("beforeScript 'export NATIVE_SETUP=retained'",)))
    plan = {'metadata': {'static_components': [dict(authority='test.nf:SCIENCE',
            selection_json={}, resources_json={'execution_role': 'compute'})]}}
    recorder = tmp_path/'runtime with spaces'/'bms-container'
    recorder.parent.mkdir()
    recorder.write_text('''#!/usr/bin/env python3
import base64, fcntl, json, os, pathlib, subprocess, sys, time
assert sys.argv[1] == 'task-shell'
payload = json.loads(base64.b64decode(sys.argv[2]))
pathlib.Path('shell-argv.json').write_text(json.dumps(sys.argv))
shell_args = sys.argv[3:]
assert shell_args[:1] == ['-ue'] or shell_args[:2] == ['-euo', 'pipefail']
if pathlib.Path(shell_args[-2]).name == '.command.run' and shell_args[-1] == 'nxf_trace':
    os.execv('/bin/bash', ['/bin/bash', *shell_args])
assert pathlib.Path(shell_args[-1]).name == '.command.sh', sys.argv
assert os.environ['NATIVE_SETUP'] == 'retained'
os.fstat(198)
with open(os.environ['TEST_LOCK'], 'a') as contender:
    try:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise AssertionError('inherited FD198 did not hold the compute slot')
start = time.time_ns()
result = subprocess.run(['/bin/bash', *sys.argv[3:]], pass_fds=(198,))
with open(os.environ['TEST_RECORD'], 'a') as output:
    output.write(json.dumps(dict(payload=payload, cwd=os.getcwd(), argv=sys.argv[3:], start=start, end=time.time_ns(), rc=result.returncode)) + '\\n')
sys.exit(result.returncode)
''')
    recorder.chmod(0o700)
    lock = tmp_path/'compute.lock'
    runtime = {'backend': 'udocker', 'executable': str(recorder)}
    config = native_resource_config(plan, {'required': {'cpus': 2, 'memory_bytes': 2**30}},
                                    str(lock), ROOT, runtime)
    (tmp_path/'runtime.config').write_text(config)
    (tmp_path/'nextflow.config').write_text('''process.executor = 'local'
process { withLabel: science {
    container = { "/verified/image-${member}.sif" }
    ext.containerOptions = { "--bind '/input ${member}:/data:ro' --writable-tmpfs" }
    containerOptions = { "--nv --env CUDA_VISIBLE_DEVICES=2 " + task.ext.containerOptions }
} }
profiles { standard {
    apptainer.enabled = true
    singularity.enabled = true
    docker.enabled = true
} }
''')
    (tmp_path/'main.nf').write_text('''nextflow.enable.dsl=2
process SCIENCE {
    label 'science'
    input: val member
    output: path 'result.txt'
    script:
    """
    sleep 0.3
    echo ${member} > result.txt
    ''' + ('exit 23' if fail else 'true') + '''
    """
}
process HOST {
    container null
    output: path 'host.txt'
    script:
    """
    echo ordinary-bash > host.txt
    """
}
workflow {
    SCIENCE(Channel.of('one', 'two'))
    HOST()
}
''')
    if nested:
        monkeypatch.setenv('BMS_CONTAINER_BACKEND', 'udocker')
        monkeypatch.setenv('BMS_CONTAINER_EXECUTABLE', str(recorder))
        inventory = tmp_path/'nested-images.json'
        inventory.write_text(json.dumps({'containers': {'cache_dir': '/verified', 'images': [
            {'uri': f'docker://ontresearch/image-{member}:sha', 'cache_file': f'image-{member}.sif'}
            for member in ('one', 'two')]}}))
        (tmp_path/'runtime.config').write_text(config + wf_clone_container_config(inventory))
        selected = tmp_path/'nextflow.config'
        selected.write_text(selected.read_text().replace('/verified/image-${member}.sif',
                                                        'ontresearch/image-${member}:sha'))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('NXF_', 'BMS_CONTAINER_', 'APPTAINER', 'SINGULARITY'))}
    home = tmp_path/'home'
    framework = home/'.nextflow/framework/25.10.1'
    framework.mkdir(parents=True)
    (framework/'nextflow-25.10.1-one.jar').symlink_to(Path(jar).resolve())
    env.update(HOME=str(home), NXF_HOME=str(home/'.nextflow'), NXF_VER='25.10.1',
               NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_PLUGINS_DEFAULT='',
               NXF_OPTS=f'-Duser.home={home}', TEST_LOCK=str(lock), TEST_RECORD=str(tmp_path/'record.jsonl'))
    # The preserved dist launcher prefix is not a standalone executable JAR.
    # Preserve its exact JVM opens, including the Kryo/cache-required modules.
    import re
    opens = re.findall(r'launcher\+=\((--add-opens=[^\s)]+)\)', Path(launcher).read_text())
    assert len(opens) == 17
    cmd = ['java', *opens, f'-Duser.home={home}', '-jar', jar, 'run', 'main.nf',
           '-c', 'runtime.config', '-ansi-log', 'false']
    if trace:
        cmd += ['-with-trace', 'trace.txt']
    first = subprocess.run(cmd, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    (tmp_path/'first.log').write_text(first.stdout + first.stderr)
    if fail:
        assert first.returncode != 0, first.stdout + first.stderr
        assert '23' in [p.read_text().strip() for p in (tmp_path/'work').glob('*/*/.exitcode')]
        return
    assert first.returncode == 0, first.stdout + first.stderr
    records = [json.loads(line) for line in (tmp_path/'record.jsonl').read_text().splitlines()]
    assert len(records) == 2  # HOST is never sent through the runtime.
    for row in records:
        member = Path(row['payload']['image']).stem.removeprefix('image-')
        assert row['payload'] == dict(image=f'/verified/image-{member}.sif',
            options=f"--nv --env CUDA_VISIBLE_DEVICES=2 --bind '/input {member}:/data:ro' --writable-tmpfs")
        assert Path(row['cwd'], 'result.txt').read_text().strip() == member
        assert row['rc'] == 0
    ordered = sorted(records, key=lambda row: row['start'])
    assert ordered[0]['end'] <= ordered[1]['start']
    witness = (tmp_path/'record.jsonl').read_bytes()
    outputs = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in (tmp_path/'work').glob('*/*/*.txt')}
    resume_cmd = ['resume-trace.txt' if arg == 'trace.txt' else arg for arg in cmd]
    second = subprocess.run(resume_cmd + ['-resume'], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    (tmp_path/'resume.log').write_text(second.stdout + second.stderr)
    assert second.returncode == 0, second.stdout + second.stderr
    assert 'Cached process' in second.stdout + second.stderr
    assert (tmp_path/'record.jsonl').read_bytes() == witness
    assert {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in (tmp_path/'work').glob('*/*/*.txt')} == outputs
    if trace:
        assert (tmp_path/'resume-trace.txt').read_text().count('CACHED') == 3
