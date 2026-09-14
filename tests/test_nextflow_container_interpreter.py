"""Pinned Nextflow's actual container command; parser/recorder, not GPU science."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.component_adapter import (native_resource_config, resource_bound_command,
                                           task_container_config, wf_clone_container_config)


def test_runtime_selection_and_child_forwarding(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_CONTAINER_BACKEND', 'udocker')
    monkeypatch.setenv('BMS_CONTAINER_EXECUTABLE', '/trusted/bin/bms-container')
    assert 'singularity.enabled = true' in task_container_config()
    assert 'task-shell' not in task_container_config()
    assert task_container_config({'backend': 'apptainer'}) == ''
    with pytest.raises(ValueError, match='absolute'):
        task_container_config({'backend': 'udocker', 'executable': 'bms-container'})
    context = dict(resources={'required': {'cpus': 1, 'memory_bytes': 1024}},
                   resource_lock_path=str(tmp_path/'compute.lock'),
                   execution_plan={'metadata': {'static_components': []}}, working_directory=str(ROOT))
    argv = resource_bound_command(['nextflow', 'run', 'main.nf'], None, context, tmp_path)
    assert argv[:3] == ['nextflow', 'run', 'main.nf']
    assert 'singularity.enabled = true' in Path(argv[-1]).read_text()
    assert 'task.workDir' not in Path(argv[-1]).read_text()
    assert (ROOT/'scripts/lib/component_adapter.py').read_text().count('start_native(resource_bound_command(') == 2


def test_nested_inventory_and_truthful_identity(monkeypatch):
    monkeypatch.setenv('BMS_CONTAINER_BACKEND', 'udocker')
    monkeypatch.setenv('BMS_CONTAINER_EXECUTABLE', '/trusted/bin/bms-container')
    lock = ROOT/'config/ngs/wf_clone_validation_v1.8.4.lock.json'
    config = wf_clone_container_config(lock)
    assert json.loads(lock.read_text())['containers']['cache_dir'] in config
    assert 'singularity.libraryDir' in config
    with pytest.raises(ValueError, match='cache naming'):
        task_container_config(image_paths={'docker://example:pin': '/cache/wrong.img'})
    module = (ROOT/'modules/ngs/clone_validation.nf').read_text()
    assert 'wf_clone_container_config(sys.argv[2])' in module
    assert 'BMS_NEXTFLOW_EXECUTABLE:-/usr/local/bin/nextflow' in module
    assert 'export NXF_SINGULARITY_ENABLED=false' not in module
    assert 'BMS_EXECUTING_IMAGE:-' in (ROOT/'modules/conformational_mapping_protenix.nf').read_text()
    from scripts.local_msa_runtime import is_isolated_task_runtime
    assert is_isolated_task_runtime({'BMS_EXECUTING_IMAGE': '/verified/image.sif'})
    assert not is_isolated_task_runtime({'BMS_CONTAINER_BACKEND': 'udocker'})


def prepare(tmp_path, monkeypatch, *, shebang='bash', trace=False, nested=False, fail=False, wait=False):
    launcher = os.environ.get('BMS_TEST_NEXTFLOW_LAUNCHER')
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert launcher and Path(launcher).is_file(), 'supply normal pinned Nextflow launcher'
    assert jar and Path(jar).is_file(), 'supply cached Nextflow25.10.1 JAR (no downloads)'
    from native_components import PROCESS_CONTRACTS
    monkeypatch.setitem(PROCESS_CONTRACTS, 'test.nf:SCIENCE',
                        ((), (), (), (), ("beforeScript 'export NATIVE_SETUP=retained'",)))
    plan = {'metadata': {'static_components': [dict(authority='test.nf:SCIENCE',
            selection_json={}, resources_json={'execution_role': 'compute'})]}}
    release = tmp_path/'runtime with spaces'
    for directory in ('bin', 'nextflow/container-bin', 'nextflow/home/framework/25.10.1'):
        (release/directory).mkdir(parents=True, exist_ok=True)
    for source, dest in [('bms_nextflow.sh', 'bin/bms-nextflow'),
                         ('bms_nextflow_singularity.py', 'nextflow/container-bin/singularity')]:
        shutil.copyfile(ROOT/'platform/api/tools'/source, release/dest)
        (release/dest).chmod(0o700)
    # Preserve the actual distribution launcher, including all JVM module opens.
    shutil.copyfile(launcher, release/'nextflow/nextflow')
    (release/'nextflow/nextflow').chmod(0o700)
    (release/'nextflow/home/framework/25.10.1/nextflow-25.10.1-one.jar').symlink_to(Path(jar).resolve())
    recorder = release/'bin/bms-container'
    recorder.write_text('''#!/usr/bin/env python3
import fcntl, importlib.util, json, os, pathlib, subprocess, sys, time
assert sys.argv[1] == 'exec', sys.argv
spec = importlib.util.spec_from_file_location('real_bms_driver', os.environ['TEST_DRIVER'])
driver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = driver
spec.loader.exec_module(driver)
def execute(invocation, identity):
    assert '--pid' not in sys.argv
    assert os.environ['NATIVE_SETUP'] == 'retained'
    assert 'container-bin' not in os.environ['PATH']
    assert 'SINGULARITYENV_PATH' not in os.environ
    os.fstat(198)
    with open(os.environ['TEST_LOCK'], 'a') as contender:
        try: fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise AssertionError('FD198 did not hold the compute slot')
    start = time.time_ns()
    os.environ['TEST_RECORDER_ENTERED'] = '1'
    pathlib.Path('entered.json').write_text(json.dumps(dict(pid=os.getpid(), argv=sys.argv)))
    result = driver.run_owned(invocation.command, dict(os.environ), (198,), identity)
    with open(os.environ['TEST_RECORD'], 'a') as output:
        output.write(json.dumps(dict(image=invocation.image, command=invocation.command,
            binds=invocation.binds, env=invocation.environment, gpu=invocation.gpu,
            cwd=os.getcwd(), start=start, end=time.time_ns(), rc=result)) + '\\n')
    return result
driver.execute = execute
sys.exit(driver.main(sys.argv[1:]))
''')
    recorder.chmod(0o700)
    lock = tmp_path/'compute.lock'
    runtime = {'backend': 'udocker', 'executable': str(recorder)}
    config = native_resource_config(plan, {'required': {'cpus': 2, 'memory_bytes': 2**30}}, str(lock), ROOT, runtime)
    library = tmp_path/'images'
    library.mkdir()
    for member in ('one', 'two'):
        (library/f'image-{member}.sif').write_text('Non-executed image identity fixture')
    image_expr = f'{library}/image-${{member}}.sif'
    if nested:
        monkeypatch.setenv('BMS_CONTAINER_BACKEND', 'udocker')
        monkeypatch.setenv('BMS_CONTAINER_EXECUTABLE', str(recorder))
        inventory = tmp_path/'nested-images.json'
        inventory.write_text(json.dumps({'containers': {'cache_dir': str(library), 'images': [
            {'uri': f'docker://ontresearch/image-{member}:sha',
             'cache_file': f'ontresearch-image-{member}-sha.img'} for member in ('one', 'two')]}}))
        for member in ('one', 'two'):
            (library/f'ontresearch-image-{member}-sha.img').symlink_to(library/f'image-{member}.sif')
        config += wf_clone_container_config(inventory)
        image_expr = 'ontresearch/image-${member}:sha'
    (tmp_path/'runtime.config').write_text(config)
    (tmp_path/'nextflow.config').write_text('''process.executor = 'local'
process { withLabel: science {
    container = { "''' + image_expr + '''" }
    ext.containerOptions = { "--bind '/input ${member}:/data:ro' --writable-tmpfs" }
    containerOptions = { "--nv --env CUDA_VISIBLE_DEVICES=2 " + task.ext.containerOptions }
} }
profiles { standard {
    apptainer.enabled = true
    singularity.enabled = true
    docker.enabled = true
} }
''')
    if shebang == 'python':
        script = '''#!/usr/bin/env python3
    import os, time
    assert os.environ['TEST_RECORDER_ENTERED'] == '1'
    import json
    open('science-started.json', 'w').write(json.dumps({'pid': os.getpid()}))
    time.sleep(''' + ('300' if wait else '0.2') + ''')
    open('result.txt', 'w').write('${member}')
    raise SystemExit(''' + ('23' if fail else '0') + ''')'''
    else:
        script = ('#!/bin/bash\n    ' if shebang == 'bash' else '') + '''test "\\$TEST_RECORDER_ENTERED" = 1
    sleep ''' + ('300' if wait else '0.2') + '''
    echo ${member} > result.txt
    exit ''' + ('23' if fail else '0')
    (tmp_path/'main.nf').write_text('''nextflow.enable.dsl=2
process SCIENCE {
    label 'science'
    input: val member
    output: path 'result.txt'
    script:
    """
    ''' + script + '''
    """
}
process HOST {
    container null
    output: path 'host.txt'
    script:
    """
    test -z "\\${TEST_RECORDER_ENTERED:-}"
    echo ordinary-bash > host.txt
    """
}
workflow { SCIENCE(Channel.of('one', 'two')); HOST() }
''')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('NXF_', 'BMS_CONTAINER_', 'APPTAINER', 'SINGULARITY'))}
    home = tmp_path/'home'; home.mkdir()
    # The pinned distribution launcher itself leaves its JAR expansion unquoted;
    # keep NXF_HOME whitespace-free while testing our adapter's spaced location.
    nxf_home = tmp_path/'nxf-home'
    nxf_home.symlink_to(release/'nextflow/home', target_is_directory=True)
    env.update(HOME=str(home), NXF_HOME=str(nxf_home), NXF_VER='25.10.1',
               NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_PLUGINS_DEFAULT='',
               NXF_OPTS=f'-Duser.home={home}', TEST_LOCK=str(lock), TEST_RECORD=str(tmp_path/'record.jsonl'),
               NATIVE_SETUP='before-script-must-replace', TEST_DRIVER=str(ROOT/'platform/api/tools/bms_container.py'),
               BMS_CONTAINER_BACKEND='udocker', BMS_CONTAINER_EXECUTABLE=str(recorder))
    cmd = [str(release/'bin/bms-nextflow'), 'run', 'main.nf', '-profile', 'standard', '-c', 'runtime.config', '-ansi-log', 'false']
    if trace: cmd += ['-with-trace', 'trace.txt']
    return cmd, env


@pytest.mark.parametrize('shebang', ['bash', 'python', 'default'])
@pytest.mark.parametrize('trace,nested', [(False, False), (True, False), (True, True)])
def test_pinned_interpreters_lock_host_and_resume(tmp_path, monkeypatch, shebang, trace, nested):
    cmd, env = prepare(tmp_path, monkeypatch, shebang=shebang, trace=trace, nested=nested)
    first = subprocess.run(cmd, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    (tmp_path/'first.log').write_text(first.stdout + first.stderr)
    assert first.returncode == 0, first.stdout + first.stderr
    records = [json.loads(line) for line in (tmp_path/'record.jsonl').read_text().splitlines()]
    assert len(records) == 2  # containerless HOST never enters the adapter
    for row in records:
        assert row['gpu'] and row['env'] == {'CUDA_VISIBLE_DEVICES': '2'}
        assert any(source.startswith('/input ') and target == '/data' and mode == 'ro'
                   for source, target, mode in row['binds'])
        assert Path(row['image']).resolve().parent == tmp_path/'images'
        assert row['rc'] == 0
        command = Path(row['cwd'], '.command.run').read_text()
        assert 'singularity exec' in command and 'task-shell' not in command
        assert Path(row['cwd'], 'result.txt').read_text().strip() in {'one', 'two'}
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
    if trace: assert (tmp_path/'resume-trace.txt').read_text().count('CACHED') == 3


@pytest.mark.parametrize('shebang', ['bash', 'python', 'default'])
def test_actual_interpreter_failure_is_not_success(tmp_path, monkeypatch, shebang):
    cmd, env = prepare(tmp_path, monkeypatch, shebang=shebang, trace=True, fail=True)
    first = subprocess.run(cmd, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    (tmp_path/'first.log').write_text(first.stdout + first.stderr)
    assert first.returncode != 0
    records = [json.loads(line) for line in (tmp_path/'record.jsonl').read_text().splitlines()]
    assert records and all(row['rc'] == 23 for row in records)
    assert '23' in [p.read_text().strip() for p in (tmp_path/'work').glob('*/*/.exitcode')]


def test_shared_cancellation_reaps_interpreter_and_releases_lock(tmp_path, monkeypatch):
    import fcntl
    from scripts.lib.component_adapter import _stop_processes
    from tools.bms_remote_worker import boot_id, process_start_ticks
    cmd, env = prepare(tmp_path, monkeypatch, shebang='python', trace=True, wait=True)
    # The existing BMS owner (not raw Nextflow SIGINT alone) owns all writers,
    # including a task that was waiting for FD198 when its sibling was stopped.
    scope = dict(BMS_COMPONENT_CONTEXT=str(tmp_path/'context.json'),
                 BMS_COMPONENT_JOB_ID=tmp_path.name, BMS_COMPONENT_OUTPUT_DIR=str(tmp_path/'results'))
    env.update(scope)
    with (tmp_path/'cancel.log').open('w') as log:
        process = subprocess.Popen(cmd, cwd=tmp_path, env=env, stdout=log, stderr=log, start_new_session=True)
        process._bms_writer_identity = dict(boot_id=boot_id(), supervisor_pid=process.pid,
            supervisor_start_ticks=process_start_ticks(process.pid), component_scope=scope)
        entered = []
        try:
            end = time.monotonic() + 90
            while time.monotonic() < end and process.poll() is None:
                entered = list((tmp_path/'work').glob('*/*/science-started.json'))
                if entered: break
                time.sleep(.1)
            assert entered, (tmp_path/'cancel.log').read_text()
            assert _stop_processes([process], timeout=5)
            process.wait(timeout=35)
        finally:
            assert _stop_processes([process], timeout=5)
            if process.poll() is None:
                process.wait(timeout=10)
        assert process.returncode != 0
    for path in entered:
        pid = json.loads(path.read_text())['pid']
        assert process_start_ticks(pid) is None
    with (tmp_path/'compute.lock').open('a') as lock:
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline: raise
                time.sleep(.1)


def test_nested_missing_image_never_acquires_or_executes(tmp_path, monkeypatch):
    cmd, env = prepare(tmp_path, monkeypatch, nested=True)
    config = tmp_path/'nextflow.config'
    config.write_text(config.read_text().replace('ontresearch/image-${member}:sha',
                                                'unlisted/image-${member}:sha'))
    result = subprocess.run(cmd, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    (tmp_path/'missing-image.log').write_text(result.stdout + result.stderr)
    assert result.returncode != 0
    assert 'no image acquisition' in result.stdout + result.stderr
    assert not (tmp_path/'record.jsonl').exists()


@pytest.mark.parametrize('remote,backend,prepared,use_msa,needs_db', [
    (True, 'neurosnap_api', True, True, False),
    (True, 'neurosnap_api', False, True, False),
    (True, 'neurosnap', False, True, False),
    (True, 'colabfold_api', False, True, False),
    (True, 'local', True, True, False),
    (True, 'local', False, True, True),
    (True, 'local', False, False, False),
    (False, 'neurosnap_api', True, True, True),
])
def test_real_protenix_profile_requests_only_selected_msa_bind(
        tmp_path, monkeypatch, remote, backend, prepared, use_msa, needs_db):
    """Evaluate actual production label/profile, not a copied option expression."""
    import shlex
    cmd, env = prepare(tmp_path, monkeypatch)
    env.update(BMS_REMOTE_EXECUTION='1' if remote else '0',
               BMS_DATA=str(tmp_path/'data'), XDG_CACHE_HOME=str(tmp_path/'cache'))
    cmd[cmd.index('-profile')+1] = 'workstation_ryzen7960x'
    # Use the production file as a top-level config, as the actual launcher
    # does; includeConfig changes Groovy helper-method/profile resolution.
    shutil.copyfile(ROOT/'nextflow.config', tmp_path/'nextflow.config')
    (tmp_path/'observe.config').write_text(
        "process { withName: ObservedOptions { container = null; cpus = 1; memory = '256MB' } }\n")
    cmd += ['-c', str(tmp_path/'observe.config')]
    (tmp_path/'main.nf').write_text('''
    nextflow.enable.dsl=2
    process ObservedOptions {
      label 'Protenix'
      label 'gpu'
      output: path 'options.json'
      script:
      """
      printf '%s' '${groovy.json.JsonOutput.toJson([options: task.containerOptions.toString()])}' > options.json
      """
    }
    workflow { ObservedOptions() }
    ''')
    db = tmp_path/'unused-local-database'
    prepared_path = tmp_path/'prepared-msa'; prepared_path.mkdir()
    weights = tmp_path/'weights'; weights.mkdir()
    params = dict(gpu_id=0, protenix_msa_backend=backend, protenix_use_msa=use_msa,
                  protenix_prepared_msa_dir=str(prepared_path) if prepared else None,
                  msa_local_db=str(db), protenix_weights=str(weights),
                  msa_cache_dir=str(tmp_path/'msa-cache'),
                  cm_api_runtime_dir=str(tmp_path/'support'), code_root=str(ROOT))
    (tmp_path/'params.json').write_text(json.dumps(params))
    cmd += ['-params-file', str(tmp_path/'params.json')]
    result = subprocess.run(cmd, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    (tmp_path/'profile.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = list((tmp_path/'work').glob('*/*/options.json'))
    assert len(outputs) == 1
    options = shlex.split(json.loads(outputs[0].read_text())['options'])
    assert (f'{db}:{db}' in options) is needs_db
    assert f'{weights}:/protenix_weights' in options
    assert f"{tmp_path/'msa-cache'}:{tmp_path/'msa-cache'}" in options
    assert f'{ROOT}/scripts:/scripts' in options
    assert '--nv' in options and 'CUDA_VISIBLE_DEVICES=0' in options
    assert not db.exists(), 'Selection must not create an empty pretend database'
