"""Host-side AF2/AF3/OF3 oracle launcher daemons in modules/rfd4_proteina.nf.

Real offline Nextflow rendering with an apptainer shim and a FIXTURE daemon (no container, GPU, model or
refold). Set BMS_TEST_NEXTFLOW_JAR to an already cached 25.10.1 one-JAR; Java 17 must be on PATH.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest

from test_rfd4_oracle_nextflow import ROOT, selected_resources

FAKE_DAEMON = '''import json, os, socket, sys
args = sys.argv[1:]
path = args[args.index('--socket-path') + 1]
record = {'argv': args, 'cwd': os.getcwd(), 'pid': os.getpid(),
          'env': {k: v for k, v in os.environ.items() if k.startswith(('AF3_', 'AF2_', 'APPTAINER', 'CUDA'))},
          'path': os.environ['PATH'].split(':')[0]}
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
server.listen(1)
open('daemon-record.%s.json' % os.path.basename(__file__), 'w').write(json.dumps(record))
while True:
    conn, _ = server.accept()
    data = conn.recv(4096)
    if b'shutdown' in data:
        conn.sendall(b'{"status": "ok"}\\n')
        open('daemon-shutdown.%s' % os.path.basename(__file__), 'w').write('clean')
        break
    conn.sendall(b'{"status": "ok"}\\n')
'''

SHIM = '''#!%s
import hashlib, json, os, pathlib, sys
args = sys.argv[1:]
if 'extract' in args and any(a.endswith('oracle_daemons.py') for a in args):
    dest = pathlib.Path(args[-1])
    for name in ('af3', 'af2'):
        (dest / name).mkdir(parents=True, exist_ok=True)
        (dest / name / (name + '_daemon.py')).write_text(%r)
    sys.exit(0)
pathlib.Path('launch-argv.json').write_text(json.dumps({'argv': args, 'env_socket': os.environ.get('AF3_LAUNCHER_SOCKET_TEMPLATE')}))
out = pathlib.Path('rfd4_proteina'); out.mkdir()
(out / 'FIXTURE_ONLY.json').write_text('{}')
if os.environ.get('FIXTURE_EXIT'):
    sys.exit(int(os.environ['FIXTURE_EXIT']))
'''


def render(tmp_path, runtime, *, gpu_id='0', exit_code=None):
    jar_setting = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar_setting:
        pytest.skip('requires explicit already-cached Nextflow 25.10.1 one-JAR')
    jar, java = Path(jar_setting).resolve(), shutil.which('java')
    assert jar.is_file() and java
    run = tmp_path
    closure = run / 'closure'
    closure.mkdir()
    (closure / 'request.json').write_text(json.dumps({'fixture_only': True, 'oracle_runtime': runtime}))
    (closure / 'bindings.json').write_text('{}')
    shimdir = run / 'bin'
    shimdir.mkdir()
    shim = shimdir / 'apptainer'
    shim.write_text(SHIM % (sys.executable, FAKE_DAEMON))
    shim.chmod(0o755)
    driver = run / 'driver.nf'
    driver.write_text("include { RunRFD4Proteina } from '" + str(ROOT / 'modules/rfd4_proteina.nf') + "'\n"
                      "workflow { RunRFD4Proteina(file(params.closure)) }\n")
    config = run / 'offline.config'
    config.write_text("process.executor='local'\nprocess.cpus=1\nprocess.memory='256 MB'\n"
                      "process.shell=['/bin/bash','-euo','pipefail']\nexecutor.queueSize=1\n")
    cache = run / 'cache'
    cache.mkdir()
    params = {'closure': str(closure), 'out_dir': str(run / 'published'), 'cache_root': str(cache),
              'code_root': str(ROOT), 'weights_root': str(run / 'outer-weights'),
              'container_dir': str(run / 'outer-images'), 'gpu_id': gpu_id,
              'rfd4_proteina_bundle': str(run / 'inert-bundle')}
    (run / 'params.json').write_text(json.dumps(params))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('NXF_', 'BMS_SELECTED_IMAGE_', 'BMS_RFD4_ORACLE_'))}
    env.update({'PATH': str(shimdir) + os.pathsep + os.environ['PATH'], 'NXF_OFFLINE': 'true',
                'NXF_HOME': str(run / 'nxf-home'), 'NXF_TEMP': str(run / 'tmp'), 'TMPDIR': str(run / 'tmp'),
                'BMS_RFD4_ORACLE_RUNTIME': json.dumps(runtime)})
    if exit_code:
        env['FIXTURE_EXIT'] = str(exit_code)
    (run / 'tmp').mkdir()
    (run / 'nxf-home').mkdir()
    command = [java, *['--add-opens=java.base/' + n + '=ALL-UNNAMED' for n in ('java.lang', 'java.util', 'java.nio')],
               '-jar', str(jar), '-C', str(config), 'run', str(driver), '-params-file', str(run / 'params.json'),
               '-work-dir', str(run / 'work'), '-ansi-log', 'false']
    result = subprocess.run(command, cwd=run, env=env, capture_output=True, text=True, timeout=120)
    tasks = [p.parent for p in (run / 'work').rglob('.command.sh')]
    assert len(tasks) == 1
    return result, tasks[0]


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_no_oracle_script_has_no_daemon_text(tmp_path):
    result, task = render(tmp_path, {})
    assert result.returncode == 0, result.stdout + result.stderr
    script = (task / '.command.sh').read_text()
    assert 'oracle_daemons' not in script and 'LAUNCHER_SOCKET' not in script and 'bms_oracle' not in script
    assert 'trap' not in script
    capture = json.loads((task / 'launch-argv.json').read_text())
    assert '--env' in capture['argv'] and not any('LAUNCHER' in a for a in capture['argv'])


def test_module_source_adds_nothing_unconditionally():
    text = (ROOT / 'modules/rfd4_proteina.nf').read_text()
    # The only unconditional additions are empty-string interpolations.
    assert text.count('${daemonSetup}') == 1 and text.count('${daemonArgs}') == 1
    assert "def daemonSetup = hostDaemons ? " in text and ": ''" in text


@pytest.mark.parametrize('exit_code', [None, 7])
def test_of3_runtime_starts_host_daemon_and_stops_it(tmp_path, exit_code):
    runtime = selected_resources(tmp_path / 'res')
    of3 = {'of3': {'AF3_IMAGE': runtime['af3']['AF3_IMAGE'], 'AF3_MODEL_DIR': runtime['af3']['AF3_MODEL_DIR'],
                   'AF3_JAX_CACHE_DIR': runtime['af3']['AF3_JAX_CACHE_DIR']}}
    (tmp_path / 'run').mkdir()
    result, task = render(tmp_path / 'run', of3, gpu_id='1', exit_code=exit_code)
    assert (result.returncode != 0) == bool(exit_code), result.stdout + result.stderr
    assert (task / '.exitcode').read_text().strip() == str(exit_code or 0)
    script = (task / '.command.sh').read_text()
    assert 'oracle_daemons.sh' in script and 'bms_oracle_start AF3' in script and '--lazy-warmup' in script
    assert 'bms_oracle_start AF2' not in script
    capture = json.loads((task / 'launch-argv.json').read_text())
    argv = capture['argv']
    template = next(a for a in argv if a.startswith('AF3_LAUNCHER_SOCKET_TEMPLATE='))
    assert argv[argv.index(template) - 1] == '--env'
    path = template.split('=', 1)[1]
    assert re.fullmatch(r'/tmp/bms-oracle\.[A-Za-z0-9]+/af3_daemon_rank\{rank\}\.sock', path)
    assert len(path.format(rank=0)) < 107 and len(path.format(rank=0)) < len(str(task))
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == '--bind']
    assert '/tmp:/tmp' in binds
    assert argv.index('/tmp:/tmp') < argv.index('python')
    # Daemon was started on the host with the family's env, GPU and binds, and is gone with the task.
    record = json.loads((task / 'daemon-record.af3_daemon.py.json').read_text())
    assert record['argv'][:2] == ['serve', '--skip-if-no-env'] and '--lazy-warmup' in record['argv']
    assert record['argv'][record['argv'].index('--gpu-device') + 1] == '1'
    assert record['env']['AF3_IMAGE'] == of3['of3']['AF3_IMAGE']
    assert record['env']['AF3_MODEL_DIR'] == of3['of3']['AF3_MODEL_DIR']
    assert record['env']['AF3_JAX_CACHE_DIR'] == str(task / 'oracle_daemon/cache/af3/jax')
    assert str(task) in record['env']['APPTAINER_BIND_PATHS'].split(';')[0]
    assert record['env']['CUDA_DEVICE_ORDER'] == 'PCI_BUS_ID'
    assert (task / 'daemon-shutdown.af3_daemon.py').read_text() == 'clean'
    time.sleep(0.5)
    assert not alive(record['pid'])
    assert not Path(path.format(rank=0)).exists() and not Path(path).parent.exists()


def test_af2_runtime_gets_its_own_daemon_and_template(tmp_path):
    runtime = selected_resources(tmp_path / 'res')
    both = {'af2': runtime['af2'], 'af3': runtime['af3']}
    (tmp_path / 'run').mkdir()
    result, task = render(tmp_path / 'run', both)
    assert result.returncode == 0, result.stdout + result.stderr
    argv = json.loads((task / 'launch-argv.json').read_text())['argv']
    assert [a for a in argv if 'LAUNCHER_SOCKET_TEMPLATE' in a] == [
        a for a in argv if a.startswith(('AF3_LAUNCHER_SOCKET_TEMPLATE=', 'AF2_LAUNCHER_SOCKET_TEMPLATE='))]
    assert sum(a.startswith('AF2_LAUNCHER_SOCKET_TEMPLATE=') for a in argv) == 1
    af2 = json.loads((task / 'daemon-record.af2_daemon.py.json').read_text())
    assert '--lazy-warmup' not in af2['argv'] and af2['env']['AF2_MODEL_DIR'] == runtime['af2']['AF2_MODEL_DIR']
    assert (task / 'daemon-shutdown.af2_daemon.py').read_text() == 'clean'
    assert (task / 'daemon-shutdown.af3_daemon.py').read_text() == 'clean'
    # Weight directories stay read-only in the task container binds.
    binds = [argv[i + 1] for i, a in enumerate(argv) if a == '--bind']
    assert runtime['af3']['AF3_MODEL_DIR'] + ':' + runtime['af3']['AF3_MODEL_DIR'] + ':ro' in binds


def test_foldseek_only_runtime_adds_no_daemon(tmp_path):
    runtime = selected_resources(tmp_path / 'res')
    (tmp_path / 'run').mkdir()
    result, task = render(tmp_path / 'run', {'foldseek': runtime['foldseek']})
    assert result.returncode == 0, result.stdout + result.stderr
    script = (task / '.command.sh').read_text()
    assert 'bms_oracle' not in script and 'oracle_daemons' not in script
