"""Installer -> pinned shared authority integration, tiny fixture bytes only.

These are publication/placement/identity tests, not scientific inference.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_provision_cli import cli, identity, ROOT
from test_model_qualification_handoff import _test_only_attestation


@pytest.fixture(autouse=True)
def imports(monkeypatch):
    for path in (ROOT, ROOT / 'scripts', ROOT / 'platform/api'):
        monkeypatch.syspath_prepend(str(path))


def configured(cli, placement):
    run, handler, root, env, fixture = cli
    if placement == 'override':
        env['BMS_RUNTIME_IMAGE_STORE'] = str(root / 'explicit-store')
        store = root / 'explicit-store'
    elif placement == 'profile':
        env.pop('BMS_CONTAINER_DIR')
        profile = Path(env['XDG_CONFIG_HOME']) / 'biomodstack/install_profile.json'
        profile.parent.mkdir(parents=True)
        profile.write_text(json.dumps({'container_dir': str(root / 'profile-images')}))
        store = root / 'profile-images/.image-store'
    else:
        store = root / 'images/.image-store'
    return store


@pytest.mark.parametrize('cli', ['protenix'], indirect=True)
@pytest.mark.parametrize('placement', ['default', 'override', 'profile'])
def test_acquisition_lane_readers_and_handoff_share_one_object(cli, placement, monkeypatch):
    from lib.shared_runtime_images import verify_image
    from lib.runtime_image_lifecycle import load_state
    from publish_runtime_images import publish_references
    from prepare_runtime_image_attestation import create_verified_image_reference, resolve_verified_image_reference
    from dorado_p4_preflight import _verified_runtime_location
    from services.frustrampnn import runtime

    store = configured(cli, placement)
    run, handler, root, env, _ = cli
    args = identity(run)
    _, preview = run('provision-plan')
    assert preview['plan']['store_roots']['runtime_image_store'] == str(store)
    code, result = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 0, result
    image = result['models'][0]['receipt']['artifacts'][0]
    path, observed = Path(image['path']), image['verification']
    fixture_store = store / 'test-fixtures-not-scientific-assets'
    assert path == fixture_store / 'objects/sha256' / observed['sha256'] / 'runtime.sif'
    assert not (fixture_store / 'references').exists()
    assert not list(fixture_store.rglob('payload.part'))
    assert not (root / 'images/protenix.sif').exists()
    assert not (root / 'images/objects').exists()

    # Lane selection is an explicit, separate lifecycle-owner operation.
    key = 'BMS_PROTENIX_CONTAINER_PATH'
    publish_references(fixture_store, 'development', {key: {'source': str(path), 'sha256': observed['sha256']}})
    first = load_state(fixture_store)['current']['development']
    publish_references(fixture_store, 'production', {key: {'source': str(path), 'sha256': observed['sha256']}})
    publish_references(fixture_store, 'development', {key: {'source': str(path), 'sha256': observed['sha256']}})
    state = load_state(fixture_store)
    assert first in state['releases']
    assert set(state['current']) == {'development', 'production'}
    references = {p.name: p.read_bytes() for p in (fixture_store / 'references').iterdir()}

    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(fixture_store))
    import biomodstack_services as manager
    monkeypatch.setattr(manager, 'install_profile_snapshot', lambda **kwargs: {'resolved': {
        'container_dir': str(store.parent), 'data_root': str(root / 'data')}})
    monkeypatch.delenv('BMS_TELEMETRY_DB_PATH', raising=False)
    rendered = manager.render_user_units(ROOT, runtime_mode='dev')
    for name in (manager.API_SERVICE, manager.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE):
        text = rendered[name]
        assert 'EnvironmentFile=' + manager.systemd_value(fixture_store / 'references/development.env') in text
        assert 'EnvironmentFile=-' + manager.systemd_value(fixture_store / 'references/development.env') not in text
        assert 'references/production.env' not in text
        assert 'Environment=BMS_RUNTIME_IMAGE_LANE=development' in text
    assert _verified_runtime_location(path, observed['sha256']) == (path, observed)
    selected_identity = replace(runtime.FRUSTRAMPNN_RUNTIME_IDENTITY,
                                configured_sif_path=str(path), sif_sha256=observed['sha256'])
    monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(path))
    selected = runtime.validate_configured_container_path(path, identity=selected_identity)
    with runtime.open_verified_container(selected, observed['sha256']) as pinned:
        assert os.fstat(pinned.fd).st_ino == observed['inode']
    reference, receipt = root / 'reference.json', root / 'receipt.json'
    create_verified_image_reference(image=path, expected_sha256=observed['sha256'],
                                    store_root=fixture_store, reference=reference, receipt=receipt)
    resolve_verified_image_reference(reference=reference, receipt=receipt,
                                     expected_sha256=observed['sha256'], store_root=fixture_store)
    evidence = _test_only_attestation(root, result['models'][0], monkeypatch)
    requests = list(handler.requests)
    assert run('resume', *args)[0] == 0
    code, checked = run('verify', *args, '--runtime-attestation', str(evidence))
    assert code == 3 and checked['models'][0]['validator']['provision_binding'] == 'matched', checked
    assert not checked['ready'] and not checked['registered']
    assert handler.requests == requests
    assert verify_image(path, observed['sha256']) == observed
    assert len(list(fixture_store.glob('objects/sha256/*/runtime.sif'))) == 1
    assert references == {p.name: p.read_bytes() for p in (fixture_store / 'references').iterdir()}
    assert not list(fixture_store.rglob('.quarantine-*'))


@pytest.mark.parametrize('action', ['resume', 'verify'])
@pytest.mark.parametrize('change', ['override', 'old-journal'])
def test_stale_store_fails_without_migration(cli, action, change):
    run, handler, root, env, _ = cli
    args = identity(run)
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 0
    journal = Path(report['journal_path'])
    if change == 'override':
        env['BMS_RUNTIME_IMAGE_STORE'] = str(root / 'new-store')
    else:
        saved = json.loads(journal.read_text())
        roots = saved['plan']['store_roots']
        roots['container_dir'] = roots.pop('runtime_image_store')
        journal.write_text(json.dumps(saved))
    before, requests = journal.read_bytes(), list(handler.requests)
    code, report = run(action, *args)
    assert code == 3 and ('stale_plan' in str(report) or 'journal_plan_mismatch' in str(report))
    assert journal.read_bytes() == before and handler.requests == requests
    if change == 'override':
        _, fresh = run('provision-plan')
        new_args = list(args)
        new_args[1] = fresh['plan_digest']
        code, report = run(action, *new_args)
        assert code == 3 and 'journal_plan_mismatch' in str(report)
        assert journal.read_bytes() == before and handler.requests == requests
    assert not (root / 'new-store').exists()


@pytest.mark.parametrize('fault', ['inode', 'corrupt', 'missing', 'symlink'])
def test_resume_never_clobbers_or_recreates_published_identity(cli, fault):
    run, handler, root, env, _ = cli
    args = identity(run)
    _, result = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    path = Path(result['models'][0]['receipt']['artifacts'][0]['path'])
    path.parent.chmod(0o700)
    if fault == 'inode':
        replacement = root / 'replacement'
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o400)
        replacement.replace(path)
    elif fault == 'corrupt':
        path.chmod(0o600)
        path.write_bytes(b'x' * path.stat().st_size)
        path.chmod(0o400)
    elif fault == 'missing':
        path.unlink()
        path.parent.rmdir()
    else:
        path.unlink()
        path.symlink_to(root / 'must-not-be-opened')
    if path.parent.exists():
        path.parent.chmod(0o500)
    before = path.lstat() if os.path.lexists(path) else None
    requests = list(handler.requests)
    code, report = run('resume', *args)
    assert code == 3 and not report['ready']
    assert handler.requests == requests
    assert (path.lstat() if os.path.lexists(path) else None) == before


def test_same_digest_artifact_names_reuse_and_stale_checkpoint_blocks(cli):
    from lib.pinned_acquisition import Artifact, AcquisitionError, acquire
    run, handler, root, env, fixture = cli
    entry = json.loads(fixture.read_text())['entries'][0]
    artifact = Artifact(**{k: v for k, v in entry.items() if k not in {'dependency', 'member_path'}}, kind='image')
    store = root / 'reuse-store'
    first = acquire(artifact, store, test_only=True)
    second = acquire(replace(artifact, artifact_id='another-model-name'), store, test_only=True)
    assert first['path'] == second['path'] and first['verification'] == second['verification']
    assert len(handler.requests) == 1
    actual = store / 'test-fixtures-not-scientific-assets'
    assert len(list(actual.glob('objects/sha256/*/runtime.sif'))) == 1
    assert not list(actual.rglob('payload.part'))
    checkpoint = actual / '.acquisition' / artifact.artifact_id / 'state.json'
    state = json.loads(checkpoint.read_text())
    state.pop('store_root')  # pre-alignment or copied checkpoint cannot migrate implicitly
    checkpoint.write_text(json.dumps(state))
    before = checkpoint.read_bytes()
    with pytest.raises(AcquisitionError, match='stale acquisition store'):
        acquire(artifact, store, test_only=True)
    assert checkpoint.read_bytes() == before and len(handler.requests) == 1
    assert not (actual / 'references').exists()


@pytest.mark.parametrize('value', ['relative/store', '/tmp/../not-a-canonical-store'])
def test_invalid_store_plan_is_read_only(cli, value):
    run, handler, root, env, _ = cli
    env['BMS_RUNTIME_IMAGE_STORE'] = value
    code, report = run('provision-plan')
    assert code == 3 and 'invalid_runtime_image_store' in str(report)
    assert not handler.requests and not list((root / 'home').rglob('journal.json'))


def test_shared_publication_and_reuse_obey_lifecycle_fence(cli):
    run, handler, root, env, fixture = cli
    from lib.shared_runtime_images import _lock
    # The same real acquisition library as CLI, independently executed while
    # this process owns the shared lifecycle fence. No fake lock/publisher.
    entry = json.loads(fixture.read_text())['entries'][0]
    artifact = {k: v for k, v in entry.items() if k not in {'dependency', 'member_path'}}
    artifact['kind'] = 'image'
    store = root / 'fenced-store'
    fixture_store = store / 'test-fixtures-not-scientific-assets'
    code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lib.pinned_acquisition import Artifact, acquire
print('started', flush=True)
print(json.dumps(acquire(Artifact(**json.loads(sys.argv[2])), Path(sys.argv[3]), test_only=True)), flush=True)
'''
    original = None
    for repeat in range(2):
        with _lock(fixture_store, 'lifecycle'):
            process = subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'scripts'), json.dumps(artifact), str(store)],
                                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                assert process.stdout is not None
                assert process.stdout.readline().strip() == 'started'
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=.3)
            except BaseException:
                process.kill()
                process.wait()
                raise
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        receipt = json.loads(stdout)
        if repeat:
            assert receipt['verification'] == original
        original = receipt['verification']
    assert len(handler.requests) == 1
    assert not (fixture_store / 'references').exists()
