"""Real offline Nextflow rendering and shell transport; never container/science acceptance.

Set BMS_TEST_NEXTFLOW_JAR to an already cached 25.10.1 one-JAR. Nothing is
acquired. Optional BMS_RFD4_NEXTFLOW_EVIDENCE retains commands and task artifacts.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_resources(root):
    """Tiny labelled files, including prefix companions; not usable model assets."""
    paths = {
        'af2': {'AF2_IMAGE': root / 'images/af2-open.sif',
                'AF2_MODEL_DIR': root / 'weights/alphafold',
                'AF2_JAX_CACHE_DIR': root / 'controller-cache/af2'},
        'af3': {'AF3_IMAGE': root / 'images/af3-open.sif',
                'AF3_MODEL_DIR': root / 'weights/af3',
                'AF3_JAX_CACHE_DIR': root / 'controller-cache/af3'},
        'foldseek': {'FOLDSEEK_EXEC': root / 'tools/foldseek',
                     'CATH_FOLDSEEK_DB': root / 'cath/cath_db',
                     'CATH_DOMAIN_LIST': root / 'cath/domains.txt'},
    }
    for values in paths.values():
        for key, path in values.items():
            if key.endswith('_JAX_CACHE_DIR'):
                continue  # must neither be mounted nor created by this boundary
            if key.endswith('_MODEL_DIR'):
                path.mkdir(parents=True)
                (path / 'FIXTURE_ONLY').write_text('NOT MODEL CONTENT\n')
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('FIXTURE ONLY: NOT AN IMAGE, EXECUTABLE OR DATABASE\n')
    (root / 'weights/alphafold/params').mkdir()
    for suffix in ('.dbtype', '.index', '_h', '_h.index'):
        (root / ('cath/cath_db' + suffix)).write_text('FIXTURE PREFIX COMPANION\n')
    return {family: {key: str(path) for key, path in values.items()}
            for family, values in paths.items()}


@pytest.mark.parametrize('case', ['inactive', 'retained-selected', 'worker-override', 'empty-override'])
def test_actual_rfd4_oracle_nextflow_shell(case, tmp_path):
    jar_setting = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar_setting:
        pytest.skip('requires explicit already-cached Nextflow 25.10.1 one-JAR')
    jar = Path(jar_setting).resolve()
    assert jar.is_file(), 'explicit cached JAR does not exist'
    java = shutil.which('java')
    assert java, 'cached Nextflow requires installed Java'
    run = tmp_path / case
    run.mkdir()
    controller = selected_resources(run / 'controller') if case != 'inactive' else {}
    worker = selected_resources(run / "worker path's resources") if case == 'worker-override' else None
    runtime = worker if worker is not None else ({} if case == 'empty-override' else controller)
    closure = run / 'closure'
    closure.mkdir()
    request = closure / 'request.json'
    request.write_text(json.dumps({'fixture_only': True, 'oracle_runtime': controller,
                                   'scientific_payload': {'retained': 'unchanged'}}))
    (closure / 'bindings.json').write_text('{}')
    original = request.read_bytes()
    shimdir = run / 'bin'
    shimdir.mkdir()
    shim = shimdir / 'apptainer'
    shim.write_text('#!' + sys.executable + '\n' + '''import hashlib, json, os, pathlib, sys
request = pathlib.Path('rfd4_closure/request.json')
record = {'fixture_only': True, 'container_executed': False,
          'argv': sys.argv[1:], 'cwd': os.getcwd(),
          'request_sha256': hashlib.sha256(request.read_bytes()).hexdigest()}
pathlib.Path('launch-argv.json').write_text(json.dumps(record, indent=2))
out = pathlib.Path('rfd4_proteina')
out.mkdir()
(out / 'FIXTURE_ONLY.json').write_text(json.dumps(record, indent=2))
print('FIXTURE ONLY: captured shell argv; no container, model or oracle executed')
''')
    shim.chmod(0o755)
    driver = run / 'driver.nf'
    driver.write_text("include { RunRFD4Proteina } from '" + str(ROOT / 'modules/rfd4_proteina.nf') + "'\n"
                      "workflow { RunRFD4Proteina(file(params.closure)) }\n")
    config = run / 'offline.config'
    config.write_text("process.executor='local'\nprocess.cpus=1\nprocess.memory='256 MB'\n"
                      "process.shell=['/bin/bash','-euo','pipefail']\n"
                      "executor.queueSize=1\nexecutor.pollInterval='100 ms'\n")
    params = {'closure': str(closure), 'out_dir': str(run / 'published'),
              'code_root': str(ROOT), 'weights_root': str(run / 'outer-weights'),
              'container_dir': str(run / 'outer-images'), 'gpu_id': 0,
              'rfd4_proteina_bundle': str(run / 'inert-bundle')}
    parameter_file = run / 'params.json'
    parameter_file.write_text(json.dumps(params))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('NXF_', 'BMS_SELECTED_IMAGE_', 'BMS_RFD4_ORACLE_'))
           and key not in ('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS')}
    env.update({'PATH': str(shimdir) + os.pathsep + os.environ['PATH'],
                'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true',
                'NXF_HOME': str(run / 'nxf-home'), 'NXF_TEMP': str(run / 'tmp'),
                'TMPDIR': str(run / 'tmp'), 'BMS_DATA': str(run / 'data'),
                'XDG_CACHE_HOME': str(run / 'xdg-cache'),
                'XDG_CONFIG_HOME': str(run / 'xdg-config'),
                'XDG_DATA_HOME': str(run / 'xdg-data')})
    for key in ('NXF_HOME', 'NXF_TEMP', 'TMPDIR', 'BMS_DATA', 'XDG_CACHE_HOME',
                'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        Path(env[key]).mkdir(exist_ok=True)
    if case in ('inactive', 'worker-override', 'empty-override'):
        env['BMS_RFD4_ORACLE_RUNTIME'] = json.dumps(runtime)
    command = [java, *['--add-opens=java.base/' + name + '=ALL-UNNAMED'
                       for name in ('java.lang', 'java.util', 'java.nio')],
               '-Djava.io.tmpdir=' + env['TMPDIR'], '-jar', str(jar),
               '-C', str(config), 'run', str(driver), '-params-file', str(parameter_file),
               '-work-dir', str(run / 'work'), '-ansi-log', 'false']
    identity = {'command': command, 'environment': {key: env[key] for key in env
                if key.startswith(('NXF_', 'BMS_RFD4_', 'XDG_')) or key == 'TMPDIR'},
                'module': str(ROOT / 'modules/rfd4_proteina.nf'),
                'module_sha256': digest(ROOT / 'modules/rfd4_proteina.nf'),
                'jar_sha256': digest(jar), 'shim_sha256': digest(shim),
                'network_namespace': os.readlink('/proc/self/ns/net')}
    (run / 'command-source-identity.json').write_text(json.dumps(identity, indent=2))
    try:
        result = subprocess.run(command, cwd=run, env=env, capture_output=True,
                                text=True, timeout=55)
        (run / 'nextflow.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        assert '25.10.1' in result.stdout + result.stderr
        tasks = list((run / 'work').rglob('.command.sh'))
        assert len(tasks) == 1
        task = tasks[0].parent
        assert (task / '.exitcode').read_text().strip() == '0'
        capture = json.loads((task / 'launch-argv.json').read_text())
        assert capture['fixture_only'] and not capture['container_executed']
        assert capture['cwd'] == str(task)
        assert capture['request_sha256'] == hashlib.sha256(original).hexdigest()
        assert request.read_bytes() == original
        assert (task / 'rfd4_closure/request.json').read_bytes() == original
        argv = capture['argv']
        assert argv[:4] == ['exec', '--no-home', '--nv', '--env']
        assert argv[-12:] == [str(run / 'outer-images/rfd4_proteina.sif'),
                             'python', '-m', 'bms_rfd4.run', '--request',
                             'rfd4_closure/request.json', '--bindings',
                             'rfd4_closure/bindings.json', '--bundle',
                             str(run / 'inert-bundle'), '--output', 'rfd4_proteina']
        binds = [argv[index + 1] for index, value in enumerate(argv) if value == '--bind']
        expected = [str(task) + ':' + str(task), str(ROOT / 'scripts') + ':/bms/scripts:ro',
                    str(run / 'outer-weights') + ':' + str(run / 'outer-weights') + ':ro']
        selected = []
        for family in runtime.values():
            for key, value in family.items():
                if key.endswith('_JAX_CACHE_DIR'):
                    assert value not in '\n'.join(binds)
                    assert not Path(value).exists()
                    continue
                resource = Path(value) if key.endswith('_MODEL_DIR') else Path(value).parent
                bind = f'{resource}:{resource}:ro'
                if bind not in selected:
                    selected.append(bind)
        assert binds == expected + selected
        if case == 'worker-override':
            assert "worker path's resources" in '\n'.join(binds)
            assert str(run / 'controller') not in '\n'.join(binds)
        assert json.loads((run / 'published/collected/rfd4_proteina/FIXTURE_ONLY.json').read_text()) == capture
    finally:
        evidence = os.environ.get('BMS_RFD4_NEXTFLOW_EVIDENCE')
        if evidence:
            shutil.copytree(run, Path(evidence) / case, dirs_exist_ok=True)
