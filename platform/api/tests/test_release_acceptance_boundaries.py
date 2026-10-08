"""Independent review's acceptance/rollback boundary reproducers."""
from contextlib import contextmanager
import os

import pytest
from test_managed_release_configuration import fixture, setup, adapters, accepted, IDENTITY, IMAGES, ROOT
import biomodstack_configuration as tx
from scripts import biomodstack_release as release


@pytest.mark.parametrize('failure', ['unlock', 'close', 'intent-write-and-unlock'])
def test_postintent_lock_exit_never_rolls_back(fixture, monkeypatch, failure):
    backend = setup(fixture)
    calls = adapters(monkeypatch, backend)
    if failure == 'close':
        real_lock = tx.configuration_lock
        @contextmanager
        def failing_close(**kwargs):
            with real_lock(**kwargs):
                yield
            if not kwargs:
                raise OSError('injected close failure')
        monkeypatch.setattr(tx, 'configuration_lock', failing_close)
    else:
        real_flock = tx.fcntl.flock
        def failing_unlock(fd, op):
            path = os.readlink('/proc/self/fd/' + str(fd if isinstance(fd, int) else fd.fileno()))
            if op == tx.fcntl.LOCK_UN and path.endswith('/configuration.lock'):
                raise OSError('injected unlock failure')
            return real_flock(fd, op)
        monkeypatch.setattr(tx.fcntl, 'flock', failing_unlock)
        if failure == 'intent-write-and-unlock':
            real_write = tx._write
            def failed_write(path, *args, **kwargs):
                real_write(path, *args, **kwargs)
                if path.name == 'release.json':
                    raise OSError('intent published then error')
            monkeypatch.setattr(tx, '_write', failed_write)
    with pytest.raises(tx.ManagedReleaseRecoveryRequired):
        release.execute_release(backend, IDENTITY)
    assert 'stop_candidate' not in calls and 'restore_known_good' not in calls
    monkeypatch.undo()
    adapters(monkeypatch, backend)
    # Restore fixture context removed by undo before reading/recovering it.
    monkeypatch.setenv('HOME', str(fixture.parent / 'home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(fixture.parent / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(fixture.parent / 'state'))
    for key in list(os.environ):
        if key.startswith('BMS_'):
            monkeypatch.delenv(key)
    backend.recover_managed_known_good(IDENTITY.build_id)
    accepted(backend)


def test_legacy_known_good_failure_preserves_postpublication_semantics(fixture, monkeypatch):
    # The fixture supplies an empty HOME; deliberately do not configure managed state.
    backend = release.ProductionReleaseBackend(repo_root=ROOT, allow_first_install=True)
    assert backend.managed_base is None
    backend.runtime_env_file.parent.mkdir(parents=True, exist_ok=True)
    backend.runtime_env_file.write_text('BMS_BUILD_SHA=' + 'c' * 40 + '\n')
    calls = adapters(monkeypatch, backend)
    monkeypatch.setattr(backend, 'snapshot_known_good', lambda: {'images': {k: 'sha256:' + 'c' * 64 for k in IMAGES}})
    def fail(*args):
        raise OSError('known-good state filesystem failed')
    monkeypatch.setattr(release, '_atomic_json_write', fail)
    with pytest.raises(OSError, match='state filesystem'):
        release.execute_release(backend, IDENTITY)
    assert 'stop_candidate' not in calls and 'restore_known_good' not in calls
    assert 'restart_known_good' not in calls
    assert IDENTITY.as_environment()['BMS_BUILD_SHA'] in backend.runtime_env_file.read_text()
    assert not (backend.state_dir / 'known-good.json').exists()
