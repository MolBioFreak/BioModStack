"""Disposable managed acceptance; no real services, jobs or network calls."""
import json
import os
from pathlib import Path
import subprocess

import pytest
from test_transactional_configuration import fixture, ROOT
import biomodstack_configuration as tx
import biomodstack_runtime_profile as profiles
from scripts import biomodstack_release as release

IDENTITY = release.BuildIdentity('a' * 40, 'managed-first', '2026-09-07T00:00:00Z')
IMAGES = {key: 'sha256:' + 'b' * 64 for key in release.BUILD_SERVICES}
RECEIPT = {**IDENTITY.as_environment(), 'BMS_MANAGED_API_IMAGE_ID': IMAGES['bms-api'],
           'BMS_MANAGED_WEB_IMAGE_ID': IMAGES['bms-web']}
POINTS = ['release_intent', 'release_stage:profile', 'release_stage:core_runtime_env',
          'release_stage:compat_env', 'release_stage:manifest.json', 'release_directory',
          'release_before_activation', 'release_activation_rename', 'release_activation_sync',
          'release_before_known_good', 'release_known_good', 'release_before_ack', 'release_ack']


def setup(fixture):
    completed = subprocess.run(['bash', str(ROOT / 'start_ui.sh'), 'configure', '--document',
                                str(fixture), '--json'], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    return release.ProductionReleaseBackend(repo_root=ROOT, allow_first_install=True)


def snapshot():
    paths = list((tx.transaction_dir() / 'generation').iterdir()) + [tx.transaction_dir() / 'journal.json']
    return {str(p): (p.read_bytes(), p.stat().st_ino) for p in paths}, {
        str(p): (os.readlink(p), p.lstat().st_ino) for p in map(Path, tx._destinations().values())}


def unchanged(original):
    assert snapshot() == original


def adapters(monkeypatch, backend):
    calls = []
    # Service/build boundary adapters: real orchestrator and real acceptance writer.
    monkeypatch.setattr(backend, 'snapshot_known_good', lambda: {'images': {k: None for k in IMAGES}})
    for name in ['build_images', 'verify_generated_ownership', 'verify_image_provenance',
                 'stop_installed_owner', 'install_units', 'start_candidate', 'validate_candidate_release',
                 'stop_candidate', 'restore_known_good', 'restart_known_good', 'revalidate_known_good']:
        monkeypatch.setattr(backend, name, lambda *args, name=name: calls.append(name))
    monkeypatch.setattr(backend, '_candidate_running_image_ids', lambda: IMAGES)
    return calls


def accepted(backend):
    record = json.loads((tx.transaction_dir() / 'release.json').read_text())
    good = json.loads((backend.state_dir / 'known-good.json').read_text())
    assert record['state'] == 'committed'
    assert good['release_id'] == IDENTITY.build_id
    assert good['configuration_generation_id'] == tx.configuration_identity()
    assert good['build'] == IDENTITY.as_environment()
    assert good['images'] == IMAGES
    assert profiles.load_install_profile() == json.loads(tx._load()['files']['profile'])
    env = release._read_runtime_env(profiles.get_core_runtime_env_path())
    assert all(env[k] == v for k, v in RECEIPT.items())
    profiles.resolve_installed_core_runtime_paths(ROOT)


def test_real_configuration_release_acceptance(fixture, monkeypatch):
    backend = setup(fixture)
    original = snapshot()
    calls = adapters(monkeypatch, backend)
    release.execute_release(backend, IDENTITY)
    assert calls[-1] == 'validate_candidate_release'
    accepted(backend)
    backend.recover_managed_known_good(IDENTITY.build_id)
    backend.recover_managed_known_good(IDENTITY.build_id)
    accepted(backend)
    unchanged(original)
    with pytest.raises(tx.ConfigurationBlocked):
        profiles.save_install_profile({'web_host_port': 19000}, ROOT)


@pytest.mark.parametrize('point', POINTS)
def test_recover_every_boundary(fixture, monkeypatch, point):
    backend = setup(fixture)
    original = snapshot()
    calls = adapters(monkeypatch, backend)
    def fail(name):
        if name == point:
            raise OSError('fault ' + point)
    monkeypatch.setattr(tx, '_checkpoint', fail)
    with pytest.raises(tx.ManagedReleaseRecoveryRequired):
        release.execute_release(backend, IDENTITY)
    assert 'restore_known_good' not in calls and 'stop_candidate' not in calls
    unchanged(original)
    monkeypatch.setattr(tx, '_checkpoint', lambda _: None)
    # Runtime state appearing after initial configure must not invalidate recovery.
    data = Path(profiles.load_install_profile()['data_root'])
    data.mkdir(parents=True, exist_ok=True)
    (data / 'keep.db').write_text('existing state')
    backend.recover_managed_known_good(IDENTITY.build_id)
    backend.recover_managed_known_good(IDENTITY.build_id)
    assert (data / 'keep.db').read_text() == 'existing state'
    accepted(backend)
    unchanged(original)


def test_preintent_failure_rolls_back_stopped_first_install(fixture, monkeypatch):
    backend = setup(fixture)
    calls = adapters(monkeypatch, backend)
    def fail(name):
        if name == 'release_before_intent':
            raise OSError('preintent')
    monkeypatch.setattr(tx, '_checkpoint', fail)
    with pytest.raises(OSError):
        release.execute_release(backend, IDENTITY)
    assert calls[-2:] == ['stop_candidate', 'restore_known_good']
    assert tx.configuration_identity() == 'generation'
    assert not (backend.state_dir / 'known-good.json').exists()


@pytest.mark.parametrize('change', ['extra', 'image', 'shell', 'stale', 'source', 'override'])
def test_incompatible_input_fails_closed(fixture, monkeypatch, change):
    backend = setup(fixture)
    original = snapshot()
    receipt = dict(RECEIPT)
    base = dict(backend.managed_base)
    source = ROOT
    path = backend.state_dir / 'known-good.json'
    if change == 'extra': receipt['BMS_DATA'] = '/tmp/foreign'
    if change == 'image': receipt['BMS_MANAGED_API_IMAGE_ID'] = 'mutable:latest'
    if change == 'shell': receipt['BMS_BUILD_ID'] = '$(bad)'
    if change == 'stale': base['operation_id'] = 'stale'
    if change == 'source': source = fixture.parent
    if change == 'override': path = fixture.parent / 'known-good.json'
    with pytest.raises((tx.ConfigurationBlocked, ValueError)):
        tx.commit_managed_release(source, base, IDENTITY.build_id, receipt,
                                  known_good_path=path, known_good={})
    unchanged(original)
    assert tx.configuration_identity() == 'generation'


def test_reader_switch_rejected(fixture, monkeypatch):
    backend = setup(fixture)
    adapters(monkeypatch, backend)
    original_read = Path.read_text
    switched = False
    def interleaved(path, *args, **kwargs):
        nonlocal switched
        text = original_read(path, *args, **kwargs)
        if path == profiles.get_install_profile_path() and not switched:
            switched = True
            release.execute_release(backend, IDENTITY)
        return text
    monkeypatch.setattr(Path, 'read_text', interleaved)
    with pytest.raises(RuntimeError, match='changed'):
        profiles.load_install_profile()


def test_recovery_requires_validated_candidate(fixture, monkeypatch):
    backend = setup(fixture)
    adapters(monkeypatch, backend)
    monkeypatch.setattr(tx, '_checkpoint', lambda name: (_ for _ in ()).throw(OSError('fault'))
                        if name == 'release_before_known_good' else None)
    with pytest.raises(tx.ManagedReleaseRecoveryRequired): release.execute_release(backend, IDENTITY)
    monkeypatch.setattr(tx, '_checkpoint', lambda _: None)
    monkeypatch.setattr(backend, 'validate_candidate_release', lambda _: (_ for _ in ()).throw(release.ReleaseValidationError('not ready')))
    with pytest.raises(release.ReleaseValidationError): backend.recover_managed_known_good(IDENTITY.build_id)
    assert not (backend.state_dir / 'known-good.json').exists()




@pytest.mark.parametrize('when', ['before', 'after'])
def test_every_fsync_failure_is_recoverable(fixture, monkeypatch, when):
    real_fsync = os.fsync
    total = 0
    def count(fd):
        nonlocal total
        total += 1
        real_fsync(fd)
    backend = setup(fixture)
    adapters(monkeypatch, backend)
    with monkeypatch.context() as m:
        m.setattr(os, 'fsync', count)
        release.execute_release(backend, IDENTITY)
    assert total > 10
    for index in range(1, total + 1):
        sandbox = fixture.parent / f'{when}-{index}'
        home = sandbox / 'home'
        home.mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        monkeypatch.setenv('XDG_CONFIG_HOME', str(sandbox / 'config'))
        monkeypatch.setenv('XDG_STATE_HOME', str(sandbox / 'state'))
        backend = setup(fixture)
        original = snapshot()
        calls = adapters(monkeypatch, backend)
        seen = 0
        def fail(fd):
            nonlocal seen
            seen += 1
            if seen == index and when == 'before': raise OSError('fsync fault')
            real_fsync(fd)
            if seen == index and when == 'after': raise OSError('fsync fault')
        with monkeypatch.context() as m:
            m.setattr(os, 'fsync', fail)
            with pytest.raises((OSError, tx.ManagedReleaseRecoveryRequired)):
                release.execute_release(backend, IDENTITY)
        if (tx.transaction_dir() / 'release.json').exists():
            assert 'stop_candidate' not in calls
            backend.recover_managed_known_good(IDENTITY.build_id)
            backend.recover_managed_known_good(IDENTITY.build_id)
        else:
            assert calls[-2:] == ['stop_candidate', 'restore_known_good']
            release.execute_release(backend, IDENTITY)
        accepted(backend)
        unchanged(original)
    print(f' {when}: exercised all {total} acceptance fsync positions')


@pytest.mark.parametrize('corrupt', ['profile', 'core_runtime_env', 'compat_env', 'manifest.json'])
def test_corrupt_staging_is_not_repaired(fixture, monkeypatch, corrupt):
    backend = setup(fixture)
    original = snapshot()
    adapters(monkeypatch, backend)
    def fail(name):
        if name == 'release_directory': raise OSError('fault')
    monkeypatch.setattr(tx, '_checkpoint', fail)
    with pytest.raises(tx.ManagedReleaseRecoveryRequired): release.execute_release(backend, IDENTITY)
    record = json.loads((tx.transaction_dir() / 'release.json').read_text())
    staged = tx.transaction_dir() / record['manifest']['generation_id'] / corrupt
    staged.write_text('corrupted')
    monkeypatch.setattr(tx, '_checkpoint', lambda _: None)
    with pytest.raises(tx.ConfigurationBlocked): backend.recover_managed_known_good(IDENTITY.build_id)
    assert staged.read_text() == 'corrupted'
    assert tx.configuration_identity() == 'generation'
    unchanged(original)


def test_second_writer_and_foreign_target_rejected(fixture, monkeypatch):
    backend = setup(fixture)
    adapters(monkeypatch, backend)
    with tx.configuration_lock():
        with pytest.raises(tx.ConfigurationBlocked, match='busy'):
            backend.commit_known_good({}, IDENTITY)
    release.execute_release(backend, IDENTITY)
    other = release.ProductionReleaseBackend(repo_root=ROOT, allow_first_install=True)
    with pytest.raises(tx.ConfigurationBlocked, match='already_recorded'):
        other.snapshot_known_good()
    with pytest.raises(tx.ConfigurationBlocked):
        tx.commit_managed_release(ROOT, backend.managed_base, 'other', RECEIPT,
            known_good_path=backend.state_dir / 'known-good.json',
            known_good={'build': IDENTITY.as_environment(), 'images': IMAGES})
    active = tx.transaction_dir() / 'active'
    active.unlink()
    active.symlink_to(fixture.parent)
    with pytest.raises(tx.ConfigurationBlocked): profiles.load_install_profile()




def test_real_unit_install_and_start_orchestration_with_external_adapter(fixture, monkeypatch):
    backend = setup(fixture)
    original = snapshot()
    calls = adapters(monkeypatch, backend)
    # Restore real unit rendering/publication and activation orchestration.
    monkeypatch.setattr(backend, 'install_units', release.ProductionReleaseBackend.install_units.__get__(backend))
    monkeypatch.setattr(backend, 'start_candidate', release.ProductionReleaseBackend.start_candidate.__get__(backend))
    external = []
    monkeypatch.setattr(backend, '_run', lambda command, **kwargs: external.append(command))
    monkeypatch.setattr(release.services, 'assert_production_core_listener_preflight', lambda **kwargs: None)
    units = fixture.parent / 'systemd'
    monkeypatch.setattr(release.services, 'get_user_systemd_dir', lambda: units)
    release.execute_release(backend, IDENTITY)
    assert external == [['systemctl', '--user', 'daemon-reload'],
                        ['systemctl', '--user', 'enable', *release.MANAGED_UNIT_NAMES],
                        ['systemctl', '--user', 'start', *release.MANAGED_UNIT_NAMES]]
    for name in release.MANAGED_UNIT_NAMES:
        assert str(ROOT) in (units / name).read_text()
    assert calls[-1] == 'validate_candidate_release'
    accepted(backend)
    unchanged(original)


@pytest.mark.parametrize('context', ['HOME', 'XDG_STATE_HOME', 'BMS_CORE_RUNTIME_ENV_FILE', 'BMS_WEB_HOST_PORT'])
def test_constructor_override_fails_closed(fixture, monkeypatch, context):
    backend = setup(fixture)
    original = snapshot()
    monkeypatch.setenv(context, str(fixture.parent / 'foreign') if context != 'BMS_WEB_HOST_PORT' else '19080')
    with pytest.raises((tx.ConfigurationBlocked, OSError)):
        release.ProductionReleaseBackend(repo_root=ROOT, allow_first_install=True)
    if context != 'HOME': unchanged(original)


def test_shell_guard_detects_release_switch(fixture):
    backend = setup(fixture)
    code = f"""
from pathlib import Path
from scripts.biomodstack_release import ProductionReleaseBackend, BuildIdentity, BUILD_SERVICES
b = ProductionReleaseBackend(repo_root=Path({str(ROOT)!r}), allow_first_install=True)
b._candidate_running_image_ids = lambda: {{k: 'sha256:'+'b'*64 for k in BUILD_SERVICES}}
b.commit_known_good({{}}, BuildIdentity('a'*40, 'managed-first', '2026-09-07T00:00:00Z'))
"""
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    result = subprocess.run(['bash', '-c', 'source "$1"; python3 -B -c "$2"; bms_configuration_read_finish',
                             'bash', str(ROOT / 'scripts/configuration_read_guard.sh'), code],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 78, result.stderr
    assert 'changed during read' in result.stderr
    accepted(backend)




@pytest.mark.parametrize('point', POINTS)
def test_process_death_recovers_without_rebuilding(fixture, monkeypatch, point):
    import multiprocessing
    backend = setup(fixture)
    original = snapshot()
    adapters(monkeypatch, backend)
    def child():
        tx._checkpoint = lambda name: os._exit(91) if name == point else None
        release.execute_release(backend, IDENTITY)
    process = multiprocessing.get_context('fork').Process(target=child)
    process.start()
    process.join(10)
    assert process.exitcode == 91
    backend.recover_managed_known_good(IDENTITY.build_id)
    backend.recover_managed_known_good(IDENTITY.build_id)
    accepted(backend)
    unchanged(original)


def test_known_good_symlink_and_activation_temp_preserved(fixture, monkeypatch):
    backend = setup(fixture)
    adapters(monkeypatch, backend)
    original = snapshot()
    victim = fixture.parent / 'victim'
    victim.write_text('KEEP')
    conflict = tx.transaction_dir() / 'activate-collision'
    conflict.symlink_to(victim)
    with monkeypatch.context() as m:
        m.setattr(tx.uuid, 'uuid4', lambda: 'collision')
        with pytest.raises(tx.ManagedReleaseRecoveryRequired): release.execute_release(backend, IDENTITY)
    assert conflict.is_symlink() and victim.read_text() == 'KEEP'
    backend.state_dir.mkdir(parents=True, exist_ok=True)
    (backend.state_dir / 'known-good.json').symlink_to(victim)
    with pytest.raises(tx.ConfigurationBlocked): backend.recover_managed_known_good(IDENTITY.build_id)
    assert victim.read_text() == 'KEEP'
    unchanged(original)


def test_recovery_cli_keeps_authorization_gate():
    with pytest.raises(SystemExit, match='confirm-runtime-activation'):
        release.main(['recover-managed', '--release-id', IDENTITY.build_id])
