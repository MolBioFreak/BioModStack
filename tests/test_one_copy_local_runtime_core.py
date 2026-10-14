"""Image-less bootstrap/controller receipt, real private publication and locks."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import biomodstack_python_prerequisites as prerequisites
from test_one_copy_local_runtime import runtime, ROOT


def managed(runtime, monkeypatch):
    project, standalone, old, env = runtime
    root = project.parent / 'managed'
    root.mkdir()
    shutil.move(old, root / 'environment')
    shutil.move(standalone, root / 'base')
    (root / 'base/lib/python3.12/encodings').mkdir()
    (root / 'base/lib/python3.12/encodings/__init__.py').write_text('# inert stdlib fixture\n')
    venv = root / 'environment'
    (venv / 'bin/python').unlink()
    (venv / 'bin/python').symlink_to(root / 'base/bin/python3.12')
    (venv / 'pyvenv.cfg').write_text(f'home = {root}/base/bin\n')
    (root / 'toolchain/bin').mkdir(parents=True)
    shutil.copy2(Path(env['PATH'].split(os.pathsep)[0]) / 'uv', root / 'toolchain/bin/uv')
    shutil.copyfile(ROOT / 'biomodstack_python_prerequisites.py', project / 'biomodstack_python_prerequisites.py')
    import sys
    (Path(env['PATH'].split(os.pathsep)[0]) / 'python3').symlink_to(sys.executable)
    env.update(BMS_PYTHON_ROOT=str(root), TEST_VENV=str(venv))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    state = dict(schema_version=prerequisites.SCHEMA, identity=prerequisites.identity(project),
                 status='complete', inventory_sha256=hashlib.sha256(b'').hexdigest(), steps=[])
    prerequisites.save(root, state)
    return root, venv


def test_bootstrap_without_image_publishes_and_guard_exports_same_selection(runtime, monkeypatch):
    root, venv = managed(runtime, monkeypatch)
    project, _, _, env = runtime
    Path(env['TEST_IMAGE']).unlink()
    report = prerequisites.prerequisite_report('python-bootstrap', project_root=project)
    assert report['status'] == 'already-installed' and not report['errors'], report
    receipt = json.loads((root / 'state.json').read_text())['support_payload']
    payload = Path(receipt['payload_root'])
    donor = venv / 'lib/python3.12/site-packages/tiny/__init__.py'
    published = payload / 'venv/lib/python3.12/site-packages/tiny/__init__.py'
    assert published.read_bytes() == donor.read_bytes()
    assert published.stat().st_ino != donor.stat().st_ino
    assert donor.stat().st_mode & 0o200 and not published.stat().st_mode & 0o222
    resolved = prerequisites.resolve_python_environment(project)
    assert resolved['python'] == receipt['python']
    assert resolved['env']['BMS_SUPPORT_PAYLOAD_RECEIPT'] == str(payload / 'support-payload.json')
    # Warm installer and concurrent guard/controller starts may not re-inventory.
    monkeypatch.setattr(prerequisites, 'support_members', lambda *a, **kw: pytest.fail('warm full inventory'))
    original_copytree = shutil.copytree
    original_copy2 = shutil.copy2
    monkeypatch.setattr(shutil, 'copytree', lambda *a, **kw: pytest.fail('warm body copy'))
    assert prerequisites.prerequisite_report('python-bootstrap', project_root=project)['status'] == 'already-installed'
    with (project / 'biomodstack_python_prerequisites.py').open('a') as stream:
        stream.write('\ndef support_members(*a, **kw): raise AssertionError("warm full inventory")\n')
    command = ['bash', '-c', 'source "$BMS_HOME/scripts/python_runtime_guard.sh"; bms_python_runtime_resolve; printf "%s\\n%s\\n" "$BMS_API_PYTHON" "$BMS_SUPPORT_PAYLOAD_RECEIPT"']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: subprocess.run(command, env=env, text=True, capture_output=True), range(2)))
    for result in results:
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [receipt['python'], str(payload / 'support-payload.json')]
    # Exercise the actual API launcher, not just its guard, with no local image.
    import sys
    shutil.copyfile(ROOT / 'scripts/run_biomodstack_api.sh', project / 'scripts/run_biomodstack_api.sh')
    with Path(env['BASH_ENV']).open('a') as hooks:
        hooks.write('\npython3() { command \"$TEST_HOST_PYTHON\" \"$@\"; }\n')
    api = subprocess.run(['bash', str(project / 'scripts/run_biomodstack_api.sh')],
        env={**env, 'TEST_HOST_PYTHON': sys.executable, 'BMS_CPU_POWER_STRICT': '0', 'BMS_RUNTIME_MODE': 'dev'},
        capture_output=True, text=True, timeout=30)
    assert api.returncode == 0, api.stderr
    launched = [json.loads(line) for line in Path(env['TEST_SERVER']).read_text().splitlines()]
    server = next(row for row in reversed(launched) if 'main:app' in row['argv'])
    assert server['python'] == receipt['python'] and server['receipt'] == str(payload / 'support-payload.json')
    assert not server['fd9']
    monkeypatch.setattr(shutil, 'copytree', original_copytree)
    monkeypatch.setattr(shutil, 'copy2', lambda *a, **kw: pytest.fail('immutable payload copy'))
    from services.remote_execution import critical_runtime, bundle
    monkeypatch.setenv('BMS_SUPPORT_PAYLOAD_RECEIPT', resolved['env']['BMS_SUPPORT_PAYLOAD_RECEIPT'])
    assert critical_runtime.selected_support_root() == payload
    # Unbound preparation uses this same receipt and private relocated metadata.
    for attempt in ('a', 'b'):
        view = bundle._relocate_python_runtime(payload, project.parent / attempt, '/worker/' + attempt)
        assert (view / 'venv/lib/python3.12/site-packages/tiny/__init__.py').stat().st_ino == published.stat().st_ino
        assert (view / 'venv/pyvenv.cfg').stat().st_ino != (payload / 'venv/pyvenv.cfg').stat().st_ino
    monkeypatch.setattr(shutil, 'copy2', original_copy2)
    _controller_to_worker(project.parent, monkeypatch, payload)


def _controller_to_worker(base, monkeypatch, payload):
    """Actual controller/helper/install storage path; inert scientific probes."""
    import asyncio
    import uuid
    from types import SimpleNamespace
    from services.remote_execution import bundle, critical_runtime, cache as controller
    from services import nextflow
    from tools import bms_managed_runtime as installer, bms_artifact_cache as cache
    launcher = base / 'nextflow'
    launcher.write_text('#!/bin/sh\nprintf "nextflow version 25.10.1\\n"\n')
    launcher.chmod(0o755)
    jar = base / 'nxf/framework/25.10.1/nextflow-25.10.1-one.jar'
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b'inert tiny jar, never executed')
    monkeypatch.setenv('NXF_HOME', str(base / 'nxf'))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: str(launcher))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_version', lambda: '25.10.1')
    monkeypatch.setattr(bundle, 'get_code_root', lambda: ROOT)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda _: ('a'*40, 'b'*40))
    worker = base / 'worker'
    connection = SimpleNamespace(remote_root=str(worker))
    uploads = []
    async def run(connection, argv, input_bytes=None, **kwargs):
        return subprocess.run(argv, input=input_bytes, capture_output=True, check=True)
    async def upload(connection, source, destination, **kwargs):
        source = Path(source)
        uploads.append(str(source))
        if source.is_dir():
            assert source.name.startswith('bms-cache-batch-'), 'no support-tree rsync bypass'
            assert all(p.is_file() and len(p.name) == 64 for p in source.iterdir())
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    monkeypatch.setattr(controller, 'run_remote', run)
    monkeypatch.setattr(controller, 'rsync_to_remote', upload)
    monkeypatch.setattr(installer, 'observed_compatibility', lambda: dict(system='Linux', machine='x86_64',
        java='fixture Java', apptainer='fixture Apptainer', driver='fixture driver'))
    monkeypatch.setattr(installer, 'qualify_container', lambda *a: dict(backend='apptainer',
        cuda='BMS_CUDA_OK', nextflow='BMS_NEXTFLOW_INTERPRETERS_OK'))
    async def stage(entries):
        await controller._cache_artifacts(connection=connection, artifacts=entries,
            operation_id=str(uuid.uuid4()), progress=controller._noop,
            check_fence=controller._noop, materialize=False)
    first, entries = critical_runtime.project_runtime(str(worker), base / 'bound-a')
    asyncio.run(stage(entries))
    install_root = worker / 'managed-assets/v1'
    installer.install(install_root, first, installer.boot_id(), cache)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda _: ('c'*40, 'd'*40))
    second, entries = critical_runtime.project_runtime(str(worker), base / 'bound-b')
    asyncio.run(stage(entries))
    installer.install(install_root, second, installer.boot_id(), cache)
    paths = [installer.release_path(install_root, manifest) for manifest in (first, second)]
    for name in ('venv/lib/python3.12/site-packages/tiny/__init__.py', 'python-runtime/lib/libpython.so'):
        old, new = [p / 'support-python' / name for p in paths]
        assert old.stat().st_ino == new.stat().st_ino
        assert old.read_bytes() == (payload / name).read_bytes()
    assert installer.observe(install_root, second, cache)['state'] == 'verified'
    assert uploads and not any('support-python' in path for path in uploads)



def test_guard_adopts_completed_pre_receipt_install_without_image(runtime, monkeypatch):
    root, _ = managed(runtime, monkeypatch)
    project, _, _, env = runtime
    Path(env['TEST_IMAGE']).unlink()
    command = ['bash', '-c', 'source "$BMS_HOME/scripts/python_runtime_guard.sh"; bms_python_runtime_resolve; printf "%s\\n" "$BMS_SUPPORT_PAYLOAD_RECEIPT"']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: subprocess.run(command, env=env, text=True, capture_output=True), range(2)))
    for result in results:
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()).is_file()
    assert results[0].stdout == results[1].stdout
    receipt = json.loads((root / 'state.json').read_text())['support_payload']
    assert receipt['payload_root'] in results[0].stdout
    assert len(list(Path(env['BMS_CM_API_RUNTIME_DIR'], 'releases').iterdir())) == 1


def test_optional_adoption_storage_failure_does_not_add_startup_gate(runtime, monkeypatch):
    root, venv = managed(runtime, monkeypatch)
    project, _, _, env = runtime
    with (project / 'biomodstack_python_prerequisites.py').open('a') as stream:
        stream.write('\ndef publish_installed_support(root): raise OSError("fixture storage unavailable")\n')
    result = subprocess.run(['bash', '-c', 'source "$BMS_HOME/scripts/python_runtime_guard.sh"; bms_python_runtime_resolve || exit $?; printf "%s\\n" "$BMS_MANAGED_PYTHON"'],
        env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(venv / 'bin/python')
    assert 'optional support publication unavailable' in result.stderr
    assert json.loads((root / 'state.json').read_text())['status'] == 'complete'


def test_image_less_publication_failure_recovers_without_retargeting_current(runtime, monkeypatch, capsys):
    root, _ = managed(runtime, monkeypatch)
    project, _, _, env = runtime
    shared = Path(env['BMS_CM_API_RUNTIME_DIR'])
    old = shared / 'retained'
    old.mkdir(parents=True)
    (old / 'consumer').write_bytes(b'queued old path')
    (shared / 'current').symlink_to('retained')
    original = shutil.copytree
    def fail(*a, **kw):
        raise OSError('fixture copy failure')
    monkeypatch.setattr(shutil, 'copytree', fail)
    report = prerequisites.prerequisite_report('python-bootstrap', project_root=project)
    assert not report['errors'] and report['status'] == 'already-installed', report
    assert report['dependencies_installed'] is True
    assert 'optional support publication unavailable' in capsys.readouterr().err
    assert json.loads((root / 'state.json').read_text())['status'] == 'complete'
    assert not list(shared.glob('.stage.*'))
    assert os.readlink(shared / 'current') == 'retained'
    monkeypatch.setattr(shutil, 'copytree', original)
    report = prerequisites.prerequisite_report('python-bootstrap', project_root=project)
    assert not report['errors'] and report['status'] == 'already-installed'
    assert os.readlink(shared / 'current') == 'retained'
    assert (old / 'consumer').read_bytes() == b'queued old path'
