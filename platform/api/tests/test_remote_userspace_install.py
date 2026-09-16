"""Guarded backend-selection and canonical-image handoff fault tests."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from tools import bms_managed_runtime as managed


@pytest.mark.parametrize('apptainer_ok', [True, False])
def test_selected_backend_executes_only_canonical_sif(tmp_path, monkeypatch, apptainer_ok):
    root = tmp_path / 'managed-assets/v1'
    release = root / 'releases/generation'
    image = tmp_path / 'cache/runtime-images/objects/sha256/digest/runtime.sif'
    calls = []
    installs = []
    monkeypatch.setattr(managed, 'canonical_probe_image', lambda *args: image)
    probes = []
    def qualify(*args):
        probes.append(args)
        return 'BMS_NEXTFLOW_INTERPRETERS_OK'
    monkeypatch.setattr(managed, 'qualify_nextflow', qualify)

    def run(argv, **kwargs):
        if argv[0] == 'bash':
            installs.append(argv)
            return SimpleNamespace(returncode=0)
        calls.append((argv, kwargs))
        ok = apptainer_ok or argv[0] == str(release / 'bin/bms-container')
        return SimpleNamespace(returncode=0 if ok else 1, stdout='BMS_CUDA_OK\n' if ok else '', stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    result = managed.qualify_container(root, release, {})
    assert result['backend'] == ('apptainer' if apptainer_ok else 'udocker')
    assert result['nextflow'] == 'BMS_NEXTFLOW_INTERPRETERS_OK'
    assert len(probes) == 1 and probes[0][2] == image
    assert len(calls) == (1 if apptainer_ok else 2)
    assert len(installs) == (0 if apptainer_ok else 1)
    if installs:
        assert installs[0] == ['bash', str(release / 'lib/bootstrap_worker.sh'), 'udocker', str(tmp_path)]
    for argv, kwargs in calls:
        assert argv[1:4] == ['exec', '--nv', str(image)]
        assert not any(value.startswith('docker://') for value in argv)
    if not apptainer_ok:
        env = calls[-1][1]['env']
        assert env['BMS_UDOCKER'] == str(tmp_path / 'tools/udocker-1.3.17/bin/udocker')
        assert env['UDOCKER_BIN'] == str(tmp_path / 'tools/udocker-1.3.17/engines/bin')
        assert env['UDOCKER_LIB'] == str(tmp_path / 'tools/udocker-1.3.17/engines/lib')
        assert env['BMS_CONTAINER_WORK_ROOT'] == str(tmp_path / 'container-workspaces')
        assert env['BMS_RUNTIME_IMAGE_STORE'] == str(tmp_path / 'cache/runtime-images')
        assert env['BMS_NEXTFLOW_EXECUTABLE'] == str(release / 'bin/bms-nextflow')
        assert 'PATH' not in env


def test_no_backend_success_is_not_qualification(tmp_path, monkeypatch):
    monkeypatch.setattr(managed, 'canonical_probe_image', lambda *args: tmp_path / 'canonical.sif')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(ValueError, match='CUDA container verification failed'):
        managed.qualify_container(tmp_path / 'managed-assets/v1', tmp_path / 'generation', {})


@pytest.mark.parametrize('backend', ['apptainer', 'udocker'])
@pytest.mark.parametrize('fault', [None, 'host', 'cuda', 'marker', 'missing_task', 'interpreter', 'exit'])
def test_nextflow_qualification_requires_interpreted_container_outputs(tmp_path, monkeypatch, backend, fault):
    """Boundary fixtures exercise proof rejection, not live container acceptance."""
    import shutil
    root = tmp_path / 'managed-assets/v1'
    release = root / 'releases/generation'
    helper = release / 'lib/scripts/lib/container_runtime.py'
    helper.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[3] / 'scripts/lib/container_runtime.py', helper)
    image = tmp_path / 'canonical.sif'
    env = dict(managed.container_environment(tmp_path, release, backend), NXF_OFFLINE='true',
               NXF_VER='25.10.1', NXF_HOME=str(release / 'nextflow/home'))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        temp = kwargs['cwd']
        assert temp.is_relative_to(root) and not temp.is_relative_to(release)
        assert argv[0] == str(release / 'bin/bms-nextflow')
        assert argv[argv.index('-C') + 1] == str(temp / 'nextflow.config')
        assert all(kwargs['env'][key] == value for key, value in env.items())
        assert kwargs['env']['BMS_COMPONENT_OUTPUT_DIR'] == str(temp)
        assert kwargs['env']['BMS_COMPONENT_CONTEXT'] == 'critical-nextflow-probe'
        assert kwargs['start_new_session'] and kwargs['stdout'] == subprocess.PIPE
        pipeline = (temp / 'main.nf').read_text()
        assert '#!/bin/bash' in pipeline and '#!/usr/bin/env python' in pipeline
        assert 'PROBE_HEADERLESS' in pipeline and 'libcuda.so.1' in pipeline
        assert ('BMS_EXECUTING_IMAGE' in pipeline) == (backend == 'udocker')
        config = (temp / 'nextflow.config').read_text()
        assert f'{"singularity" if backend == "udocker" else "apptainer"}.enabled = true' in config
        assert "process.containerOptions = '--nv'" in config
        assert 'task-shell' not in config and 'docker://' not in pipeline + config
        assert '-with-trace' not in argv  # fixed probe image has no procps
        for name in ('BASH', 'PYTHON', 'HEADERLESS'):
            if fault == 'missing_task' and name == 'PYTHON':
                continue
            task = temp / 'work/00' / name
            task.mkdir(parents=True)
            engine = 'singularity' if backend == 'udocker' else 'apptainer'
            command = ('/bin/bash .command.sh' if fault == 'host' else
                       f'{engine} exec --nv {image} /bin/bash .command.sh')
            (task / '.command.run').write_text(command)
            lines = [f'BMS_NEXTFLOW_{name if fault != "interpreter" else "BASH"}_OK',
                     'BMS_CUDA_OK' if fault != 'cuda' else 'NO_CUDA',
                     str(image) if fault != 'marker' else 'host']
            (task / 'result.txt').write_text('\n'.join(lines))
        return SimpleNamespace(pid=123, returncode=1 if fault == 'exit' else 0, stdout=None,
                               communicate=lambda timeout: ('probe diagnostic', None),
                               wait=lambda timeout: 0)

    from tools import bms_remote_worker as owner
    monkeypatch.setattr(owner, 'process_start_ticks', lambda pid: 456)
    monkeypatch.setattr(owner, 'attempt_writers', lambda identity: [])
    def quiesce(identity, timeout_seconds):
        assert identity['supervisor_pid'] == 123
        assert identity['supervisor_start_ticks'] == 456
        assert identity['boot_id'] == owner.boot_id()
        assert Path(identity['component_scope']['BMS_COMPONENT_OUTPUT_DIR']).is_dir()
        return True
    monkeypatch.setattr(owner, 'quiesce_writers', quiesce)
    monkeypatch.setattr(subprocess, 'Popen', run)
    if fault:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            managed.qualify_nextflow(root, release, image, env)
    else:
        assert managed.qualify_nextflow(root, release, image, env) == 'BMS_NEXTFLOW_INTERPRETERS_OK'
    assert len(calls) == 1
    assert list(root.glob('.nextflow-probe-*')) == []
    assert not list(release.rglob('.command.run'))
    assert not list(release.rglob('__pycache__'))


@pytest.fixture
def nextflow_probe_inputs(tmp_path):
    import shutil
    root = tmp_path / 'managed-assets/v1'
    release = root / 'releases/generation'
    helper = release / 'lib/scripts/lib/container_runtime.py'
    helper.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[3] / 'scripts/lib/container_runtime.py', helper)
    return root, release, tmp_path / 'canonical.sif', {'BMS_CONTAINER_BACKEND': 'apptainer'}


def test_standalone_probe_loads_published_writer_without_bytecode(nextflow_probe_inputs):
    import importlib.util
    import shutil
    root, release, image, env = nextflow_probe_inputs
    runner = release / 'runner/bms_remote_worker.py'
    runner.parent.mkdir()
    shutil.copyfile(Path(managed.__file__).with_name('bms_remote_worker.py'), runner)
    spec = importlib.util.spec_from_file_location('standalone_managed_probe', managed.__file__)
    assert spec is not None and spec.loader is not None
    standalone = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone)
    with pytest.raises(ValueError, match='critical_nextflow_execution_error'):
        from pathlib import PurePosixPath
        standalone.qualify_nextflow(PurePosixPath(root), PurePosixPath(release), image, env)
    assert not list(root.glob('.nextflow-probe-*'))
    assert not list(release.rglob('__pycache__'))


@pytest.mark.parametrize('fault', ['timeout', 'io', 'exit', 'spawn'])
@pytest.mark.parametrize('cleanup', ['ok', 'false', 'unknown', 'reap'])
def test_nextflow_probe_retains_unknown_writers(nextflow_probe_inputs, monkeypatch, fault, cleanup):
    from tools import bms_remote_worker as owner
    root, release, image, env = nextflow_probe_inputs
    events = []
    def communicate(timeout):
        assert timeout == 300
        events.append('communicate')
        if fault == 'timeout':
            raise subprocess.TimeoutExpired('probe', timeout, output=b'x' * 10000 + b'actionable tail')
        if fault == 'io':
            raise OSError('communicate failed')
        return 'x' * 10000 + 'actionable tail', None
    def wait(timeout):
        events.append('reap')
        if cleanup == 'reap':
            raise subprocess.TimeoutExpired('reap', timeout)
    def popen(*args, **kwargs):
        if fault == 'spawn':
            raise OSError('launcher missing')
        return SimpleNamespace(pid=123, returncode=1, stdout=None, communicate=communicate, wait=wait)
    def quiesce(identity, timeout_seconds):
        events.append('quiesce')
        assert Path(identity['component_scope']['BMS_COMPONENT_OUTPUT_DIR']).exists()
        if cleanup == 'unknown':
            raise RuntimeError('owner identity unknown')
        return cleanup != 'false'
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(owner, 'process_start_ticks', lambda pid: 456)
    monkeypatch.setattr(owner, 'attempt_writers', lambda identity: [])
    monkeypatch.setattr(owner, 'quiesce_writers', quiesce)
    with pytest.raises(ValueError) as caught:
        managed.qualify_nextflow(root, release, image, env)
    retained = list(root.glob('.nextflow-probe-*'))
    if fault != 'spawn' and cleanup != 'ok':
        assert len(retained) == 1 and (retained[0] / 'main.nf').exists()
        assert 'quiescence_unknown' in str(caught.value) and str(retained[0]) in str(caught.value)
    else:
        assert not retained
        if fault in {'timeout', 'exit'}:
            assert 'actionable tail' in str(caught.value) and len(str(caught.value)) < 4300
    if fault != 'spawn':
        assert events[:2] == ['communicate', 'quiesce']
        assert ('reap' in events) == (cleanup in {'ok', 'reap'})
    else:
        assert events == []


@pytest.mark.parametrize('fault', ['timeout', 'io', 'orphan'])
def test_nextflow_probe_real_scoped_children_stop_before_cleanup(nextflow_probe_inputs, monkeypatch, fault):
    """Real local process ownership; no Nextflow/image/scientific execution."""
    import shutil
    import sys
    from tools import bms_remote_worker as owner
    root, release, image, env = nextflow_probe_inputs
    real_popen, real_remove = subprocess.Popen, shutil.rmtree
    processes, identities = [], []
    real_quiesce = owner.quiesce_writers
    def quiesce(identity, timeout_seconds):
        identities.append(identity)
        assert len(owner.attempt_writers(identity)) >= (1 if fault == 'orphan' else 2)
        return real_quiesce(identity, timeout_seconds=timeout_seconds)
    def popen(command, **kwargs):
        # A setsid child survives a launcher-only termination and retains the
        # dedicated scope. Its parent exits before communicate's timeout.
        script = ('import subprocess,sys,time\n'
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"], '
                  'start_new_session=True)\n'
                  'print(p.pid, flush=True)\n'
                  f'time.sleep({0.1 if fault == "orphan" else 60})\n')
        proc = real_popen([sys.executable, '-c', script], **kwargs)
        processes.append(proc)
        real_communicate = proc.communicate
        def communicate(timeout):
            assert timeout == 300
            if fault == 'io':
                assert proc.stdout.readline().strip().isdigit()
                raise OSError('injected pipe failure')
            return real_communicate(timeout=0.5)
        proc.communicate = communicate
        return proc
    def remove(path, *args, **kwargs):
        if Path(path).name.startswith('.nextflow-probe-'):
            assert identities and owner.attempt_writers(identities[-1]) == []
            assert processes[0].returncode is not None
        return real_remove(path, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(owner, 'quiesce_writers', quiesce)
    monkeypatch.setattr(shutil, 'rmtree', remove)
    try:
        with pytest.raises(ValueError, match='critical_nextflow_(timeout|execution_error)'):
            managed.qualify_nextflow(root, release, image, env)
        assert len(identities) == 1 and not list(root.glob('.nextflow-probe-*'))
    finally:
        # Same existing owner also cleans up if an assertion fails.
        if identities:
            real_quiesce(identities[-1], timeout_seconds=0)
        for proc in processes:
            proc.wait(timeout=5)


def test_nextflow_failure_after_direct_cuda_is_not_backend_qualification(tmp_path, monkeypatch):
    monkeypatch.setattr(managed, 'canonical_probe_image', lambda *args: tmp_path / 'canonical.sif')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='BMS_CUDA_OK\n'))
    def fail(*args):
        raise ValueError('critical_nextflow_host_interpreter')
    monkeypatch.setattr(managed, 'qualify_nextflow', fail)
    with pytest.raises(ValueError, match='critical_nextflow_host_interpreter'):
        managed.qualify_container(tmp_path / 'managed-assets/v1', tmp_path / 'generation', {})


def test_bootstrap_does_not_gate_namespace_policy():
    source = (Path(__file__).parents[1] / 'services/remote_execution/bootstrap_worker.sh').read_text()
    assert 'unshare' not in source
    assert 'fd6589de0f3af7c1cd6a29554f2c00f2ef1fbd91da184ca30f8cea1eb42bcd2b' in source
    assert '2a4804ba82e087ca3e99305fce9887227925d2b56aaefcad6474c4cb86b7a157' in source
    embedded = source.split("<<'PY_INSTALL'\n", 1)[1].split('\nPY_INSTALL', 1)[0]
    compile(embedded, 'bootstrap-upstream-installer', 'exec')


def test_upstream_checksum_failure_publishes_nothing(tmp_path, monkeypatch):
    import io
    import sys
    import urllib.request
    source = (Path(__file__).parents[1] / 'services/remote_execution/bootstrap_worker.sh').read_text()
    embedded = source.split("<<'PY_INSTALL'\n", 1)[1].split('\nPY_INSTALL', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['installer', str(tmp_path)])
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'wrong upstream bytes'))
    with pytest.raises(ValueError, match='upstream checksum mismatch'):
        exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})
    assert not (tmp_path / 'tools/udocker-1.3.17').exists()
    assert not list((tmp_path / 'tools').iterdir())


def test_exact_upstream_install_repeat_and_corruption(tmp_path, monkeypatch):
    """Use caller-supplied independently acquired pinned upstream artifacts."""
    import os
    import sys
    import urllib.request
    import json
    wheel = os.environ.get('BMS_TEST_UDOCKER_WHEEL')
    engines = os.environ.get('BMS_TEST_UDOCKER_ENGINES')
    if not wheel or not engines:
        pytest.skip('independently downloaded pinned upstream artifacts not supplied')
    source = (Path(__file__).parents[1] / 'services/remote_execution/bootstrap_worker.sh').read_text()
    embedded = source.split("<<'PY_INSTALL'\n", 1)[1].split('\nPY_INSTALL', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['installer', str(tmp_path)])
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **kwargs:
                        Path(wheel if url.endswith('.whl') else engines).open('rb'))
    exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})
    def no_network(*args, **kwargs):
        raise AssertionError('verified repeat installation must stay offline')
    monkeypatch.setattr(urllib.request, 'urlopen', no_network)
    exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})
    installed = tmp_path / 'tools/udocker-1.3.17'
    record = json.loads((installed / 'manifest.json').read_text())
    assert 'bin/udocker' in record['files']
    assert 'engines/bin/proot-x86_64' in record['files']
    output = subprocess.run([str(installed / 'bin/udocker'), '--allow-root', '--version'],
                            capture_output=True, text=True, timeout=30)
    assert output.returncode == 0, output.stdout + output.stderr
    assert '1.3.17' in output.stdout
    (installed / 'engines/bin/proot-x86_64').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='udocker installed byte mismatch'):
        exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})
    import hashlib
    record['files']['engines/bin/proot-x86_64'] = hashlib.sha256(b'corrupt').hexdigest()
    (installed / 'manifest.json').write_text(json.dumps(record, sort_keys=True))
    with pytest.raises(ValueError, match='udocker installation identity mismatch'):
        exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})


def test_probe_publication_uses_canonical_lease_and_recovers_receipt(tmp_path, monkeypatch):
    import sys
    source_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(source_root))
    root = tmp_path / 'managed-assets/v1'
    root.mkdir(parents=True)
    calls = []

    def pull(argv, **kwargs):
        calls.append(argv)
        assert argv[:3] == ['apptainer', 'pull', '--disable-cache']
        assert argv[-1] == managed.PROBE_OCI
        # Acquisition-boundary byte fixture, never executed as a container.
        Path(argv[3]).write_bytes(b'opaque pinned image acquisition fixture')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, 'run', pull)
    release = root / 'releases/generation'
    image = managed.canonical_probe_image(root, release)
    assert image.is_relative_to(tmp_path / 'cache/runtime-images/objects/sha256')
    assert managed.canonical_probe_image(root, release) == image
    (root / 'probe-image.json').unlink()
    assert managed.canonical_probe_image(root, release) == image
    assert len(calls) == 1
    assert list(root.rglob('*.sif')) == []
    from scripts.lib.runtime_image_lifecycle import load_state
    assert len(load_state(tmp_path / 'cache/runtime-images')['leases']) == 1
