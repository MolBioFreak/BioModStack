"""Combined supported CLI acceptance; fixtures are never scientific assets."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from test_provision_cli import cli, identity, ROOT

sys.path[:0] = [str(ROOT), str(ROOT / 'platform/api'), str(ROOT / 'scripts')]
import biomodstack_configuration as tx
import biomodstack_provision as provision
from scripts import biomodstack_release as release


def configure(cli, monkeypatch):
    run, handler, root, env, fixture = cli
    document = root / 'install.json'
    document.write_text(json.dumps({'schema_version': 'bms.install.v1', 'profile': {
        'container_dir': env.pop('BMS_CONTAINER_DIR'), 'weights_root': env.pop('BMS_WEIGHTS')},
        'ingress': {'mode': 'tailnet', 'target': 'development'}}))
    for key in list(os.environ):
        if key.startswith('BMS_'):
            monkeypatch.delenv(key)
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME'):
        monkeypatch.setenv(key, env[key])
    result = subprocess.run(['bash', str(ROOT / 'start_ui.sh'), 'configure', '--document', str(document), '--json'],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not json.loads(result.stdout)['ready']
    return release.ProductionReleaseBackend(repo_root=ROOT, allow_first_install=True)


def accept(backend, monkeypatch, root):
    images = {key: 'sha256:' + 'b' * 64 for key in release.BUILD_SERVICES}
    monkeypatch.setattr(backend, 'snapshot_known_good', lambda: {'images': {k: None for k in images}})
    for name in ('build_images', 'verify_generated_ownership', 'verify_image_provenance',
                 'stop_installed_owner', 'validate_candidate_release', 'stop_candidate',
                 'restore_known_good', 'restart_known_good', 'revalidate_known_good'):
        monkeypatch.setattr(backend, name, lambda *args: None)
    monkeypatch.setattr(backend, '_candidate_running_image_ids', lambda: images)
    external = []
    monkeypatch.setattr(backend, '_run', lambda command, **kwargs: external.append(command))
    monkeypatch.setattr(release.services, 'assert_production_core_listener_preflight', lambda **kwargs: None)
    monkeypatch.setattr(release.services, 'get_user_systemd_dir', lambda: root / 'units')
    release.execute_release(backend, release.BuildIdentity('a' * 40, 'combined-fixture', '2026-09-07T00:00:00Z'))
    assert external == [['systemctl', '--user', 'daemon-reload'],
                        ['systemctl', '--user', 'enable', *release.MANAGED_UNIT_NAMES],
                        ['systemctl', '--user', 'start', *release.MANAGED_UNIT_NAMES]]
    assert json.loads((backend.state_dir / 'known-good.json').read_text())['configuration_generation_id'] == tx.configuration_identity()


@pytest.mark.parametrize('cli', ['esmfold2', 'protenix'], indirect=True)
def test_configure_provision_resume_release_revalidate(cli, monkeypatch):
    run, handler, root, env, fixture = cli
    backend = configure(cli, monkeypatch)
    args = identity(run)
    handler.failed = True
    assert run('provision', *args, '--accept-license', 'TEST-LICENSE')[0] == 3
    handler.failed = False
    code, result = run('resume', *args)
    assert code == 0, result
    bindings = result['models'][0]['bindings']
    weight = next(b for b in bindings if b['dependency']['kind'] == 'weights')
    member = json.loads(fixture.read_text())['entries'][1]['member_path']
    assert (Path(weight['path']) / member).read_bytes() == handler.payload
    accept(backend, monkeypatch, root)
    tx.assert_configuration_readable()
    count = len(handler.requests)
    assert 'stale_plan' in str(run('resume', *args)[1])
    assert 'stale_plan' in str(run('verify', *args)[1])
    fresh = identity(run)
    assert fresh[1] != args[1]
    assert 'journal_plan_mismatch' in str(run('resume', *fresh)[1])
    fresh[-1] = 'post-release-revalidated'
    code, report = run('provision', *fresh, '--accept-license', 'TEST-LICENSE')
    assert code == 0, report
    assert report['models'][0]['bindings'] == bindings
    assert len(handler.requests) == count
    assert not report['ready'] and not report['registered']
    assert report['qualification'] == 'not-qualified'
    core_receipt = (backend.state_dir / 'known-good.json').read_bytes()
    code, verified = run('verify', *fresh)
    assert code == 3 and verified['status'] == 'validator-blocked', verified
    assert verified['models'][0]['bytes_materialized']
    assert verified['models'][0]['bindings'] == bindings
    assert not verified['models'][0]['scientifically_qualified'] and not verified['registered']
    assert (backend.state_dir / 'known-good.json').read_bytes() == core_receipt
    assert len(handler.requests) == count
    assert 'approved_acquisition_metadata_missing' in str(run('provision-plan', production=True)[1])
    active = tx.transaction_dir() / tx.configuration_identity() / 'core_runtime_env'
    active.write_text(active.read_text().replace('combined-fixture', 'tampered-fixture'))
    assert run('provision-plan', production=True)[0] == 3
    assert len(handler.requests) == count


def test_plan_rejects_interleaved_release(cli, monkeypatch):
    backend = configure(cli, monkeypatch)
    original = provision._authority
    switched = False
    def interleave(root):
        nonlocal switched
        if not switched:
            switched = True
            accept(backend, monkeypatch, cli[2])
        return original(root)
    monkeypatch.setattr(provision, '_authority', interleave)
    report = provision.provision_report('provision-plan', project_root=ROOT, models=['esmfold2'])
    assert 'configuration_changed' in str(report)


@pytest.mark.parametrize('corrupt', ['root', 'licenses', 'events'])
def test_corrupt_journal_returns_structured_blocker(cli, corrupt):
    run, handler, root, env, fixture = cli
    args = identity(run)
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 0
    path = Path(report['journal_path'])
    journal = json.loads(path.read_text())
    if corrupt == 'root': journal = []
    elif corrupt == 'licenses': journal['license_acceptance'] = []
    else: journal['events'] = {}
    path.write_text(json.dumps(journal))
    count = len(handler.requests)
    code, report = run('resume', *args)
    assert code == 3 and 'invalid_provision_journal' in str(report)
    assert len(handler.requests) == count


def test_same_size_authority_drift_and_journal_no_clobber(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    before = fixture.read_bytes()
    fixture.write_bytes(before.replace(b'TEST-ONLY-NOT-APPROVAL', b'FAIL-ONLY-NOT-APPROVAL'))
    assert fixture.stat().st_size == len(before)
    assert 'stale_plan' in str(run('provision', *args, '--accept-license', 'TEST-LICENSE')[1])
    fixture.write_bytes(before)
    victim = root / 'victim'
    victim.write_text('KEEP')
    journal = Path(env['XDG_CONFIG_HOME']) / 'biomodstack/provision-v1/fixture-operation/journal.json'
    journal.parent.mkdir(parents=True)
    journal.symlink_to(victim)
    assert 'operation_exists' in str(run('provision', *args)[1])
    assert run('resume', *args)[0] == 3
    assert victim.read_text() == 'KEEP' and journal.is_symlink()
    assert not handler.requests


def test_saved_receipt_not_trusted_on_resume(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 0
    path = Path(report['journal_path'])
    journal = json.loads(path.read_text())
    journal['models'][0]['receipt'] = {'path': '/fabricated/not-a-receipt'}
    path.write_text(json.dumps(journal))
    code, resumed = run('resume', *args)
    assert code == 0
    assert resumed['models'][0]['receipt'] == report['models'][0]['receipt']
    assert resumed['models'][0]['bindings'] == report['models'][0]['bindings']


@pytest.mark.parametrize('fault', ['receipt', 'journal', 'unlock'])
def test_later_failure_preserves_partial_model_outcomes(cli, monkeypatch, fault):
    # Control-flow unit fault injection. Real transport/layout bytes are exercised
    # above; these empty receipts are not model acceptance evidence.
    from types import SimpleNamespace
    run, handler, root, env, fixture = cli
    for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME'):
        monkeypatch.setenv(key, env[key])
    plan = {'plan_digest': 'd' * 64, 'store_roots': {'runtime_image_store': str(root / 'images/.image-store'),
            'weights_root': str(root / 'weights')}, 'models': [
            {'model_id': name, 'artifacts': [], 'blockers': [], 'plan_digest': 'a' * 64}
            for name in ('first', 'second')]}
    monkeypatch.setattr(provision, '_plan', lambda *args: plan)
    def acquire(name, *args, **kwargs):
        if name == 'second' and fault != 'unlock': return {}  # malformed new receipt: KeyError
        return {'artifacts': [], 'layouts': []}
    monkeypatch.setattr(provision, '_authority', lambda _: SimpleNamespace(acquire_model=acquire))
    if fault == 'unlock':
        from contextlib import contextmanager
        real_lock = provision.configuration_lock
        @contextmanager
        def failed_exit():
            with real_lock():
                yield
            raise OSError('provision lock exit failed')
        monkeypatch.setattr(provision, 'configuration_lock', failed_exit)
    if fault == 'journal':
        real_write = provision._write
        def fail_write(path, data, **kwargs):
            rows = json.loads(data)['models']
            if rows[1]['status'] == 'materializing':
                raise OSError('second-model journal failure')
            return real_write(path, data, **kwargs)
        monkeypatch.setattr(provision, '_write', fail_write)
    report = provision.provision_report('provision', project_root=ROOT,
        models=['first', 'second'], expected_plan_digest=plan['plan_digest'], operation_id='partial-unit')
    assert report['status'] == 'blocked' and not report['ready']
    assert [r['status'] for r in report['models']] == [
        'bytes-materialized', 'bytes-materialized' if fault == 'unlock' else 'blocked']
    if fault == 'receipt':
        journal = json.loads(Path(report['journal_path']).read_text())
        assert [r['status'] for r in journal['models']] == ['bytes-materialized', 'blocked']
