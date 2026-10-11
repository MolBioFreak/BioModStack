"""Real launcher/cp/flock, tiny inert Python and image probe; no environment copy."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(tmp_path):
    source = tmp_path / 'source with spaces'
    api = source / 'platform/api'
    api.mkdir(parents=True)
    scripts = source / 'scripts'
    scripts.mkdir()
    for name in ('run_biomodstack_workflow_adapter.sh', 'python_runtime_guard.sh',
                 'cm_api_support_runtime.py', 'configuration_read_guard.sh'):
        shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
    (source / 'biomodstack_configuration.py').write_text(
        'def configuration_identity(): return None\ndef assert_configuration_readable(): pass\n')
    shutil.copyfile(ROOT / 'biomodstack_python_prerequisites.py', source / 'biomodstack_python_prerequisites.py')
    with (source / 'biomodstack_python_prerequisites.py').open('a') as stream:
        stream.write('''
_original_members = support_members
def support_members(*args, **kwargs):
    with open(os.environ['TEST_LOG'], 'a') as f: f.write('hash-tree\\n')
    return _original_members(*args, **kwargs)
def resolve_python_environment(project):
    if os.environ.get('TEST_MANAGED'):
        return {'python': os.environ['TEST_VENV'] + '/bin/python',
                'root': str(Path(os.environ['TEST_VENV']).parent)}
    return None
def publish_installed_support(root):
    return None  # This fixture isolates adapter publication; bootstrap has its own real fixture.
''')
    # Only image lookup is inert; receipt, relocation and publication are real.
    text = (scripts / 'cm_api_support_runtime.py').read_text()
    text = text.replace('from biomodstack_runtime_profile import resolve_runtime_paths', '')
    text = text.replace('from scripts.lib.runtime_image_lifecycle import object_path', '')
    text = text.replace('from scripts.select_workflow_adapter_image import select_probe_image', '')
    text = text.replace('from scripts.lib.shared_runtime_images import verify_image', '')
    text = text.replace('selected = support_image(Path(sys.argv[2]))',
                        "selected = Path(os.environ['TEST_IMAGE'])")
    (scripts / 'cm_api_support_runtime.py').write_text(text)
    (api / 'pyproject.toml').write_text('[project]\nname="tiny"\nversion="1"\n[dependency-groups]\ndev=[]\n')
    (api / 'uv.lock').write_text('tiny lock\n')
    (api / 'README.md').write_text('provenance, not dependencies\n')
    standalone = tmp_path / 'standalone'
    (standalone / 'bin').mkdir(parents=True)
    (standalone / 'lib/python3.12').mkdir(parents=True)
    (standalone / 'lib/python3.12/os.py').write_text('# tiny stdlib\n')
    (standalone / 'lib/libpython.so').write_bytes(b'tiny native runtime')
    executable = standalone / 'bin/python3.12'
    executable.write_text(f'#!{sys.executable}\n' + '''import json,os,sys
if 'import tomllib' in ' '.join(sys.argv):
    import subprocess
    subprocess.run([sys.executable, *sys.argv[1:]], check=True)
elif 'platform.python_implementation' in ' '.join(sys.argv):
    print(json.dumps(dict(implementation='CPython',version='3.12.fake',build=['tiny','build'],platform='linux',machine='x86_64',soabi='cpython-312')))
else:
    with open(os.environ['TEST_SERVER'], 'a') as f:
        f.write(json.dumps(dict(argv=sys.argv, python=os.environ.get('BMS_API_PYTHON'),
            receipt=os.environ.get('BMS_SUPPORT_PAYLOAD_RECEIPT'),
            fd9=os.path.exists('/proc/self/fd/9'))) + '\\n')
''')
    executable.chmod(0o755)
    venv = api / '.venv'
    (venv / 'bin').mkdir(parents=True)
    (venv / 'bin/python').symlink_to(executable)
    (venv / 'bin/python3').symlink_to('python')
    (venv / 'bin/activate').write_text(f'VIRTUAL_ENV="{venv}"\nexport VIRTUAL_ENV\n')
    (venv / 'pyvenv.cfg').write_text(f'home = {standalone}/bin\ninclude-system-site-packages = false\nversion = 3.12.fake\n')
    package = venv / 'lib/python3.12/site-packages/tiny'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('answer = 42\n')
    (package / '__pycache__').mkdir()
    (package / '__pycache__/__init__.cpython-312.pyc').write_bytes(b'regenerable')
    (package / 'source_less.pyc').write_bytes(b'retained bytecode')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    uv = bin_dir / 'uv'
    uv.write_text(f'#!{sys.executable}\n' + '''import json,os,sys
if sys.argv[1] == '--version': print('uv 0.8.22')
elif sys.argv[1] == 'sync':
    with open(os.environ['TEST_LOG'],'a') as f: f.write('sync\\n')
    print(os.environ.get('TEST_SYNC_REPORT', 'Audited 1 package in 1ms'), file=sys.stderr)
else:
    with open(os.environ['TEST_SERVER'], 'a') as f:
        f.write(json.dumps(dict(argv=sys.argv,python=os.environ.get('BMS_API_PYTHON'),
            receipt=os.environ.get('BMS_SUPPORT_PAYLOAD_RECEIPT'),
            fd9=os.path.exists('/proc/self/fd/9'))) + '\\n')
''')
    uv.chmod(0o755)
    home = tmp_path / 'home'
    home.mkdir()
    bash_env = tmp_path / 'shell-hooks'
    bash_env.write_text('''cp() {
    printf 'cp\\n' >> "$TEST_LOG"
    if [ "${TEST_FAIL_CP:-}" = 1 ]; then return 31; fi
    command cp "$@"
}
apptainer() {
    printf 'probe\\n' >> "$TEST_LOG"
    if [ "${TEST_FAIL_PROBE:-}" = 1 ]; then return 32; fi
    if [ "${TEST_HOLD_PROBE:-}" = 1 ]; then
        printf 'held\\n' > "$TEST_BARRIER"
        while [ ! -f "$TEST_RELEASE" ]; do sleep 0.05; done
    fi
    return 0
}
''')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('BMS_', 'UV_', 'PYTHON', 'XDG_'))}
    env.update(HOME=str(home), BMS_HOME=str(source), PATH=str(bin_dir) + os.pathsep + os.defpath,
        BASH_ENV=str(bash_env), BMS_WORKFLOW_ADAPTER_LANE='development',
        BMS_STATE_DIR=str(tmp_path / 'state'), BMS_DB_PATH=str(tmp_path / 'state/db'),
        BMS_WORK=str(tmp_path / 'work'), BMS_RESULTS_DIR=str(tmp_path / 'results'),
        BMS_DATA=str(tmp_path / 'data'), BMS_CM_API_RUNTIME_DIR=str(tmp_path / 'host-support'),
        TEST_LOG=str(tmp_path / 'operations'), TEST_SERVER=str(tmp_path / 'servers'),
        TEST_VENV=str(venv), TEST_IMAGE=str(tmp_path / 'image.sif'), PYTHONDONTWRITEBYTECODE='1')
    Path(env['TEST_IMAGE']).write_bytes(b'inert')
    return source, standalone, venv, env


def launch(runtime, **updates):
    source, _, _, env = runtime
    return subprocess.run(['bash', str(source / 'scripts/run_biomodstack_workflow_adapter.sh')],
                          env={**env, **updates}, text=True, capture_output=True, timeout=30)


def rows(runtime):
    return [json.loads(line) for line in Path(runtime[3]['TEST_SERVER']).read_text().splitlines()]


def operations(runtime):
    return Path(runtime[3]['TEST_LOG']).read_text().splitlines()


def assert_ok(result):
    assert result.returncode == 0, result.stdout + result.stderr


def test_unchanged_restart_and_source_image_changes_do_not_copy_or_hash(runtime):
    assert_ok(launch(runtime))
    first = rows(runtime)[0]
    counts = operations(runtime)
    assert counts.count('cp') == 2 and counts.count('hash-tree') == 2
    original = Path(first['python']).resolve()
    (runtime[0] / 'platform/api/README.md').write_text('changed README')
    (runtime[0] / 'code-only.py').write_text('new BMS code')
    Path(runtime[3]['TEST_IMAGE']).write_bytes(b'different image')
    assert_ok(launch(runtime))
    assert rows(runtime)[-1]['python'] == first['python']
    assert operations(runtime).count('cp') == 2
    assert operations(runtime).count('hash-tree') == 2
    assert operations(runtime).count('probe') == 2
    assert original.stat().st_mode & 0o222 == 0
    assert not any(row['fd9'] for row in rows(runtime))
    assert '/current/' not in first['python']
    # Actual external flock acquisition succeeds after both server exec paths.
    lock = Path(runtime[3]['BMS_CM_API_RUNTIME_DIR']) / '.provision.lock'
    subprocess.run(['flock', '-n', str(lock), 'true'], check=True)
    receipt = json.loads(Path(first['receipt']).read_text())
    assert receipt['selection']['groups'] == ['dev']
    assert any(member['path'].endswith('source_less.pyc') for member in receipt['members'])
    assert not any(member['path'].endswith('__init__.cpython-312.pyc') for member in receipt['members'])


@pytest.mark.parametrize('change', ['groups', 'interpreter', 'abi-build', 'installed-package'])
def test_real_selection_change_adds_one_version_and_freezes_current(runtime, change):
    assert_ok(launch(runtime))
    first = rows(runtime)[-1]
    root = Path(runtime[3]['BMS_CM_API_RUNTIME_DIR'])
    old_current = os.readlink(root / 'current')
    updates = {}
    if change == 'groups':
        (runtime[0] / 'platform/api/pyproject.toml').write_text('[project]\nname="tiny"\nversion="1"\n[tool.uv]\ndefault-groups=[]\n')
    elif change == 'interpreter':
        with (runtime[1] / 'bin/python3.12').open('a') as stream:
            stream.write('\n# different interpreter build\n')
    elif change == 'abi-build':
        executable = runtime[1] / 'bin/python3.12'
        executable.write_text(executable.read_text().replace("'cpython-312'", "'cpython-313'").replace("'tiny','build'", "'tiny','new-build'"))
    else:
        (runtime[2] / 'lib/python3.12/site-packages/tiny/__init__.py').write_text('answer = 43\n')
        updates['TEST_SYNC_REPORT'] = 'Installed 1 package in 1ms'
    assert_ok(launch(runtime, **updates))
    second = rows(runtime)[-1]
    assert second['python'] != first['python']
    if change == 'abi-build':
        receipt = json.loads(Path(second['receipt']).read_text())
        assert receipt['interpreter']['soabi'] == 'cpython-313'
        assert receipt['interpreter']['build'] == ['tiny', 'new-build']
    assert os.readlink(root / 'current') == old_current
    assert Path(first['python']).is_file()
    assert len(list((root / 'releases').iterdir())) == 2
    assert_ok(launch(runtime))
    assert rows(runtime)[-1]['python'] == second['python']
    assert operations(runtime).count('cp') == 4
    assert not any(row['fd9'] for row in rows(runtime))


@pytest.mark.parametrize('failure', ['TEST_FAIL_CP', 'TEST_FAIL_PROBE'])
def test_failed_preparation_preserves_old_and_cleans_owned_stage(runtime, failure):
    assert_ok(launch(runtime))
    root = Path(runtime[3]['BMS_CM_API_RUNTIME_DIR'])
    old = os.readlink(root / 'current')
    with (runtime[1] / 'bin/python3.12').open('a') as stream:
        stream.write('\n# next build\n')
    result = launch(runtime, **{failure: '1'})
    assert result.returncode in (31, 32), result.stderr
    assert os.readlink(root / 'current') == old
    assert (root / 'current/venv/bin/python').is_file()
    assert not list(root.glob('.stage.*'))
    assert len(list((root / 'releases').iterdir())) == 1
    assert_ok(launch(runtime))
    assert len(list((root / 'releases').iterdir())) == 2


def test_concurrent_start_and_other_lane_share_one_publication(runtime):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: launch(runtime), range(2)))
    for result in results:
        assert_ok(result)
    assert operations(runtime).count('cp') == 2
    assert operations(runtime).count('hash-tree') == 2
    assert len({row['python'] for row in rows(runtime)}) == 1
    assert_ok(launch(runtime, BMS_WORKFLOW_ADAPTER_LANE='production',
        BMS_DATA=str(runtime[0].parent / 'production-data'),
        BMS_STATE_DIR=str(runtime[0].parent / 'production-state')))
    assert operations(runtime).count('cp') == 2
    assert not any(row['fd9'] for row in rows(runtime))


def test_managed_missing_receipt_adopts_once_and_executes_payload(runtime):
    source, standalone, old_venv, env = runtime
    venv = source.parent / 'managed/environment'
    venv.parent.mkdir()
    shutil.move(old_venv, venv)
    (venv.parent / 'state.json').write_text(json.dumps({'status': 'complete'}))
    env.update(TEST_MANAGED='1', TEST_VENV=str(venv))
    assert_ok(launch(runtime))
    state = json.loads((venv.parent / 'state.json').read_text())
    receipt = state['support_payload']
    assert receipt['selection']['groups'] == []
    assert receipt['selection']['install_project'] is False
    assert receipt['python'] == rows(runtime)[-1]['python']
    assert rows(runtime)[-1]['argv'][0] == receipt['python']
    assert_ok(launch(runtime))
    assert operations(runtime).count('cp') == 2
    assert operations(runtime).count('hash-tree') == 2
    assert 'sync' not in operations(runtime)
    assert not any(row['fd9'] for row in rows(runtime))


def test_abandoned_unpublished_stage_recovery_does_not_prune_releases(runtime):
    root = Path(runtime[3]['BMS_CM_API_RUNTIME_DIR'])
    abandoned = root / '.stage.abandoned'
    abandoned.mkdir(parents=True)
    (abandoned / 'partial').write_bytes(b'partial')
    retained = root / 'releases/runtime-legacy'
    retained.mkdir(parents=True)
    (retained / 'retained').write_bytes(b'queued/resume reference')
    (root / 'current').symlink_to('releases/runtime-legacy')
    pending = root / 'releases/runtime-interrupted'
    pending.mkdir()
    (pending / '.pending-support-payload.json').write_text('{}')
    assert_ok(launch(runtime))
    assert not abandoned.exists()
    assert not pending.exists()
    assert (retained / 'retained').read_bytes() == b'queued/resume reference'
    assert os.readlink(root / 'current') == 'releases/runtime-legacy'


def test_installer_migrates_only_known_guard_dropin_idempotently(tmp_path, monkeypatch):
    import biomodstack_services as services
    target = tmp_path / 'systemd'
    dropin = target / (services.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE + '.d') / 'zzzz-runtime-retention-guard.conf'
    dropin.parent.mkdir(parents=True)
    preserved = '[Service]\nEnvironment=BMS_CM_API_RUNTIME_RETENTION=3\nEnvironment=BMS_UNRELATED=keep\nRestartPreventExitStatus=78\n'
    dropin.write_text(preserved + 'ExecStart=\nExecStart=/scratch/development_workflow_adapter_guard.sh\n')
    unrelated = dropin.with_name('operator.conf')
    unrelated.write_text('Environment=UNRELATED=yes\n')
    monkeypatch.setattr(services, 'ensure_development_proxy_identity', lambda: None)
    monkeypatch.setattr(services, 'render_user_units', lambda *args, **kwargs: {
        services.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE: '[Service]\nExecStart=/source/scripts/run_biomodstack_workflow_adapter.sh\n'})
    services.install_user_units(ROOT, target, runtime_mode='dev')
    assert dropin.read_text() == preserved
    stamp = dropin.stat().st_mtime_ns
    services.install_user_units(ROOT, target, runtime_mode='dev')
    assert dropin.stat().st_mtime_ns == stamp
    assert unrelated.read_text() == 'Environment=UNRELATED=yes\n'


def test_separate_checkouts_share_content_not_relocation_paths(runtime):
    assert_ok(launch(runtime))
    source, standalone, venv, env = runtime
    other = source.parent / 'other-checkout'
    shutil.copytree(source, other, symlinks=True)
    other_venv = other / 'platform/api/.venv'
    (other_venv / '.bms-support-payload.json').unlink()
    (other_venv / 'bin/activate').write_text(f'VIRTUAL_ENV="{other_venv}"\nexport VIRTUAL_ENV\n')
    (other / 'platform/api/README.md').write_text('other source checkout README')
    assert_ok(launch((other, standalone, other_venv, {**env, 'BMS_HOME': str(other)})))
    assert rows(runtime)[0]['python'] == rows(runtime)[1]['python']
    assert operations(runtime).count('cp') == 2
    assert operations(runtime).count('hash-tree') == 3  # one-time adoption of the second mutable input
    selected = Path(rows(runtime)[-1]['python']).parent.parent
    assert str(selected) in (selected / 'bin/activate').read_text()
    assert str(venv) not in (selected / 'bin/activate').read_text()


def test_existing_uv_no_dev_selection_is_not_recorded_as_default_dev(runtime):
    assert_ok(launch(runtime, UV_NO_DEV='1'))
    receipt = json.loads(Path(rows(runtime)[-1]['receipt']).read_text())
    assert receipt['selection']['groups'] == []
    assert_ok(launch(runtime))
    assert rows(runtime)[0]['python'] != rows(runtime)[1]['python']


def test_actual_offline_uv_mutation_reporting_and_noop_receipt(tmp_path, monkeypatch):
    # Install only our inert local wheel into a new tiny venv. Do not copy a
    # real environment or add anything to the locked development interpreter.
    import zipfile
    uv = shutil.which('uv')
    assert uv, 'the supported uv invocation must be available for this acceptance'
    project = tmp_path / 'uv-project'
    api = project / 'platform/api'
    api.mkdir(parents=True)
    for version in ('1', '2'):
        wheel = api / f'tiny_one_copy-{version}-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('tiny_one_copy.py', f'version = {version}\n')
            metadata = f'tiny_one_copy-{version}.dist-info'
            archive.writestr(metadata + '/METADATA', f'Metadata-Version: 2.1\nName: tiny-one-copy\nVersion: {version}\n')
            archive.writestr(metadata + '/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            archive.writestr(metadata + '/RECORD', '')
    def configure(version):
        (api / 'pyproject.toml').write_text(
            '[project]\nname="inert-project"\nversion="1"\nrequires-python=">=3.10"\n'
            f'dependencies=["tiny-one-copy @ {api.as_uri()}/tiny_one_copy-{version}-py3-none-any.whl"]\n'
            '[dependency-groups]\ndev=[]\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('UV_', 'VIRTUAL_ENV', 'PIP_'))}
    env.update(UV_OFFLINE='1', UV_PYTHON_DOWNLOADS='never', UV_PYTHON=sys.executable,
               UV_CACHE_DIR=str(tmp_path / 'uv-cache'))
    log = tmp_path / 'sync.log'
    def sync(version):
        configure(version)
        subprocess.run([uv, 'lock'], cwd=api, env=env, check=True, capture_output=True, text=True)
        result = subprocess.run([uv, 'sync', '--locked'], cwd=api, env=env, check=True, capture_output=True, text=True)
        log.write_text(result.stderr)
        return result.stderr
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('test_support_owner', ROOT / 'scripts/cm_api_support_runtime.py')
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    import biomodstack_python_prerequisites as prerequisites
    calls = []
    original = prerequisites.support_members
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(prerequisites, 'support_members', counted)
    native = tmp_path / 'inert-native'
    native.mkdir()
    (native / 'native.so').write_bytes(b'not a real runtime; sync boundary only')
    assert 'Installed 1 package' in sync('1')
    first = support.selected_payload(api / '.venv', native, project, log)
    assert 'Installed' not in sync('1')
    second = support.selected_payload(api / '.venv', native, project, log)
    assert second['payload_id'] == first['payload_id']
    assert len(calls) == 1
    assert 'Installed 1 package' in sync('2')
    third = support.selected_payload(api / '.venv', native, project, log)
    assert third['payload_id'] != first['payload_id']
    assert len(calls) == 2


def test_signal_cleans_inflight_publication_and_releases_lock(runtime):
    import signal
    import time
    assert_ok(launch(runtime))
    root = Path(runtime[3]['BMS_CM_API_RUNTIME_DIR'])
    old = os.readlink(root / 'current')
    with (runtime[1] / 'bin/python3.12').open('a') as stream:
        stream.write('\n# changed before interrupted publication\n')
    env = {**runtime[3], 'TEST_HOLD_PROBE': '1',
           'TEST_BARRIER': str(root / 'test-barrier'), 'TEST_RELEASE': str(root / 'test-release')}
    child = subprocess.Popen(['bash', str(runtime[0] / 'scripts/run_biomodstack_workflow_adapter.sh')],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    try:
        deadline = time.monotonic() + 15
        while not Path(env['TEST_BARRIER']).exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert Path(env['TEST_BARRIER']).exists()
        os.killpg(child.pid, signal.SIGTERM)
        child.communicate(timeout=15)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=15)
    assert child.returncode != 0
    assert os.readlink(root / 'current') == old
    assert not list(root.glob('.stage.*'))
    assert len(list((root / 'releases').iterdir())) == 1
    subprocess.run(['flock', '-n', str(root / '.provision.lock'), 'true'], check=True)
    assert_ok(launch(runtime))


def test_failed_warm_probe_keeps_completed_payload_without_copy(runtime):
    assert_ok(launch(runtime))
    selected = rows(runtime)[-1]['python']
    result = launch(runtime, TEST_FAIL_PROBE='1')
    assert result.returncode == 32
    assert Path(selected).is_file()
    assert operations(runtime).count('cp') == 2
    assert operations(runtime).count('hash-tree') == 2
    assert_ok(launch(runtime))
    assert rows(runtime)[-1]['python'] == selected


def test_generated_services_share_support_root_not_lane_data(tmp_path, monkeypatch):
    import biomodstack_services as services
    import biomodstack_python_prerequisites as prerequisites
    monkeypatch.setattr(prerequisites, 'resolve_python_environment', lambda root: None)
    monkeypatch.delenv('BMS_CM_API_RUNTIME_DIR', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'host-data'))
    monkeypatch.setenv('BMS_DATA', str(tmp_path / 'development-data'))
    development = services.render_user_units(ROOT, runtime_mode='dev')[services.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE]
    monkeypatch.setenv('BMS_DATA', str(tmp_path / 'production-data'))
    production = services.render_user_units(ROOT, runtime_mode='container')[services.PRODUCTION_WORKFLOW_ADAPTER_SERVICE]
    expected = f'Environment=BMS_CM_API_RUNTIME_DIR={tmp_path / "host-data/biomodstack/runtime/cm-api-python"}'
    assert expected in development and expected in production
    monkeypatch.setenv('BMS_CM_API_RUNTIME_DIR', str(tmp_path / 'explicit-host-support'))
    assert f'BMS_CM_API_RUNTIME_DIR={tmp_path / "explicit-host-support"}' in services.render_user_units(
        ROOT, runtime_mode='dev')[services.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE]


def test_completed_installer_resolver_exposes_optional_concrete_receipt(tmp_path, monkeypatch):
    import biomodstack_python_prerequisites as prerequisites
    root = tmp_path / 'managed'
    root.mkdir()
    source = tmp_path / 'source'
    (source / 'platform/api').mkdir(parents=True)
    for name in prerequisites.MANIFESTS:
        (source / 'platform/api' / name).write_text('inert manifest')
    python = tmp_path / 'immutable/venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('inert interpreter')
    receipt = {'payload_id': 'inert', 'python': str(python), 'payload_root': str(python.parents[2])}
    state = {'schema_version': prerequisites.SCHEMA, 'identity': prerequisites.identity(source),
             'status': 'complete', 'inventory_sha256': 'inert-inventory', 'support_payload': receipt}
    prerequisites.save(root, state)
    monkeypatch.setenv('BMS_PYTHON_ROOT', str(root))
    monkeypatch.setattr(prerequisites, 'check_environment', lambda root: 'inert-inventory')
    resolved = prerequisites.resolve_python_environment(source)
    assert resolved['python'] == str(python)
    assert resolved['source_python'] == str(root / 'environment/bin/python')
    assert resolved['support_payload'] == receipt
    assert resolved['env']['VIRTUAL_ENV'] == str(python.parent.parent)
    assert resolved['env']['PATH'].split(os.pathsep)[0] == str(python.parent)
    python.unlink()
    missing = prerequisites.resolve_python_environment(source)
    assert missing['python'] == str(root / 'environment/bin/python')
    assert missing['support_payload'] is None  # optional absence never adds admission


def test_explicit_installer_refresh_incorporates_same_version_edits(runtime, monkeypatch):
    import biomodstack_python_prerequisites as prerequisites
    for key, value in runtime[3].items():
        if key.startswith('TEST_'):
            monkeypatch.setenv(key, value)
    selection = {'groups': [], 'extras': [], 'install_project': False,
                 'policy': 'frozen-no-dev-no-build-no-install-project-v1'}
    first = prerequisites.support_payload_receipt(runtime[2], runtime[1], selection=selection,
        installer_version=prerequisites.UV_VERSION)
    leaf = runtime[2] / 'lib/python3.12/site-packages/tiny/__init__.py'
    leaf.write_text('same version, explicitly changed content\n')
    warm = prerequisites.support_payload_receipt(runtime[2], runtime[1], selection=selection,
        installer_version=prerequisites.UV_VERSION)
    assert warm['payload_id'] == first['payload_id']  # no recurring out-of-band policing
    refreshed = prerequisites.support_payload_receipt(runtime[2], runtime[1], selection=selection,
        installer_version=prerequisites.UV_VERSION, refresh=True)
    assert refreshed['payload_id'] != first['payload_id']


def test_missing_or_malformed_optional_receipt_converges_without_refusal(runtime):
    assert_ok(launch(runtime))
    selected = rows(runtime)[-1]['python']
    cache = runtime[2] / '.bms-support-payload.json'
    receipt = json.loads(cache.read_text())
    receipt['payload_id'] = '../not-a-content-identity'
    cache.write_text(json.dumps(receipt))
    assert_ok(launch(runtime))
    assert rows(runtime)[-1]['python'] == selected
    assert operations(runtime).count('cp') == 2
    assert operations(runtime).count('hash-tree') == 3
    assert_ok(launch(runtime))
    assert operations(runtime).count('hash-tree') == 3
    cache.unlink()
    assert_ok(launch(runtime))
    assert rows(runtime)[-1]['python'] == selected
    assert operations(runtime).count('cp') == 2
    assert operations(runtime).count('hash-tree') == 4
    assert_ok(launch(runtime))
    assert operations(runtime).count('hash-tree') == 4


def test_setup_dispatch_does_not_reexec_selected_payload_forever(tmp_path, monkeypatch):
    import biomodstack_python_prerequisites as prerequisites
    selected = tmp_path / 'support/releases/runtime-inert/venv/bin/python'
    original = tmp_path / 'installer/environment'
    resolved = {'root': str(original.parent), 'python': str(selected), 'env': {}}
    monkeypatch.setattr(prerequisites, 'resolve_python_environment', lambda source: resolved)
    calls = []
    monkeypatch.setattr(prerequisites.os, 'execve', lambda *args: calls.append(args))
    monkeypatch.setattr(prerequisites.sys, 'prefix', str(original))
    prerequisites.dispatch_setup(tmp_path / 'source')
    assert len(calls) == 1 and calls[0][0] == str(selected)
    monkeypatch.setattr(prerequisites.sys, 'prefix', str(selected.parent.parent))
    prerequisites.dispatch_setup(tmp_path / 'source')
    assert len(calls) == 1  # already selected: no re-exec loop
