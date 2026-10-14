"""Explicit one-image recovery; real SIF tools and installed-helper JSON dispatch."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest
from tools import bms_artifact_cache as helper


@pytest.fixture
def maintenance(tmp_path):
    if not all(shutil.which(tool) for tool in ('apptainer', 'mksquashfs', 'unsquashfs')):
        pytest.skip('real SIF tools required')
    cache = helper.Cache(tmp_path / 'cache/artifacts/v1')
    life = helper.runtime_lifecycle()
    views = importlib.import_module('.runtime_image_views', package=life.__package__)
    shared = importlib.import_module('.shared_runtime_images', package=life.__package__)
    tree = tmp_path / 'science-input'
    tree.mkdir()
    (tree / 'data').write_bytes(b'preserved scientific input')
    output = tmp_path / 'science-output'
    output.mkdir()
    (output / 'result').write_bytes(b'preserved scientific output')
    source = tmp_path / 'fixture.sif'
    squash = tmp_path / 'fixture.squashfs'
    def run(*args):
        subprocess.run(args, check=True, capture_output=True)
    run('mksquashfs', str(tree), str(squash), '-noappend', '-processors', '1')
    run('apptainer', 'sif', 'new', str(source))
    run('apptainer', 'sif', 'add', str(source), str(squash), '--datatype', '4',
        '--parttype', '2', '--partfs', '1', '--partarch', '2')
    item = dict(kind='runtime_image', sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                size_bytes=source.stat().st_size)
    image = shared.publish_image(source, cache.image_store, item['sha256'])
    views.prepare_image(cache.image_store, item['sha256'])
    cache.pin_runtime(item, 'cache-artifact:' + item['sha256'])
    for _ in range(6):
        cache.pin_runtime(item, 'attempt:' + str(uuid.uuid4()) + ':image:' + item['sha256'])
    leases = copy.deepcopy(life.load_state(cache.image_store)['leases'])
    old = shared.image_identity(image, item['sha256'])
    # Same bytes, new ctime: ordinary preparation must continue to refuse this.
    image.chmod(0o600)
    image.chmod(0o400)
    assert old != shared.image_identity(image, item['sha256'])
    request = dict(action='rebuild_runtime_image', artifact=item,
                   operation_id=str(uuid.uuid4()), source_identity=old,
                   retire_leases=leases, maintenance_authorization='explicit fixture maintenance')
    # Deliver the consumed helper peers in their installed layout (no source imports).
    installed = tmp_path / 'runner/cache-fixture'
    installed.mkdir(parents=True)
    shutil.copyfile(helper.__file__, installed / 'bms_artifact_cache.py')
    for name in ('shared_runtime_images.py', 'runtime_image_lifecycle.py', 'runtime_image_views.py'):
        shutil.copyfile(Path(str(life.__file__)).with_name(name), installed / name)
    def dispatch(request, check=True):
        result = subprocess.run([sys.executable, str(installed / 'bms_artifact_cache.py'),
                                 '--root', str(cache.root)], input=json.dumps(request),
                                text=True, capture_output=True, timeout=60)
        if check:
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        return result
    yield cache, life, views, shared, image, request, dispatch
    for folder, _, _ in os.walk(tmp_path):
        os.chmod(folder, 0o700)


def apply(fixture):
    cache, _, _, _, _, request, _ = fixture
    return cache.rebuild_runtime_image(request['artifact'], **{
        key: value for key, value in request.items() if key not in {'action', 'artifact'}})


def test_installed_dispatch_real_rebuild_and_warm(maintenance, tmp_path):
    cache, life, views, shared, image, request, dispatch = maintenance
    item = request['artifact']
    unrelated = tmp_path / 'unrelated.sif'
    unrelated.write_bytes(b'unrelated canonical allocation')
    digest = hashlib.sha256(unrelated.read_bytes()).hexdigest()
    other_image = shared.publish_image(unrelated, cache.image_store, digest)
    def extract(fd, path):
        path.mkdir()
        (path / 'other').write_bytes(b'unchanged')
    views.prepare_image(cache.image_store, digest, extract)
    token, _ = life.ensure_lease(cache.image_store, [digest], owner='unrelated-job')
    other = views.snapshot_tree(views.derived_path(cache.image_store, digest))
    other_row = life.load_state(cache.image_store)['leases'][token]
    other_identity = shared.image_identity(other_image, digest)
    identity = shared.verify_image(image, item['sha256'])
    science = {name: views.snapshot_tree(tmp_path / name) for name in ('science-input', 'science-output')}
    prepare = dict(action='prepare_runtime_image', artifact=item, backend='udocker', operation_id=str(uuid.uuid4()))
    assert dispatch(prepare, check=False).returncode != 0
    result = dispatch(request)
    assert result['state'] == 'ready'
    assert shared.verify_image(image, item['sha256']) == identity
    state = life.load_state(cache.image_store)
    assert not (set(request['retire_leases']) & set(state['leases']))
    assert state['leases'][result['cache_lease']]['identities'] == {item['sha256']: identity}
    assert len(state['leases']) == 2
    assert state['leases'][token] == other_row
    assert shared.image_identity(other_image, digest) == other_identity
    assert views.snapshot_tree(views.derived_path(cache.image_store, digest)) == other
    assert all(views.snapshot_tree(tmp_path / name) == snapshot for name, snapshot in science.items())
    assert Path(result['quarantine']).is_dir()
    fresh = views.snapshot_tree(views.derived_path(cache.image_store, item['sha256']))
    assert dispatch(request) == result
    assert dispatch(prepare)['state'] == 'ready'
    assert views.snapshot_tree(views.derived_path(cache.image_store, item['sha256'])) == fresh
    views.verify_derivation(views.derived_path(cache.image_store, item['sha256']), identity)


@pytest.mark.parametrize('boundary', ['receipt', 'rename', 'references', 'extract'])
def test_interruption_preserves_bytes_and_retries(maintenance, monkeypatch, boundary):
    cache, life, views, shared, image, request, dispatch = maintenance
    identity = shared.verify_image(image, request['artifact']['sha256'])
    fired = False
    if boundary in {'receipt', 'references'}:
        original = life.atomic_write
        def interrupted_write(path, text):
            nonlocal fired
            original(path, text)
            match = path.name.startswith('rebuild-') if boundary == 'receipt' else path.name == 'state.json'
            if match and not fired:
                fired = True
                raise RuntimeError('interrupted')
        monkeypatch.setattr(life, 'atomic_write', interrupted_write)
    elif boundary == 'rename':
        original = os.rename
        def interrupted_rename(src, dst, **kwargs):
            nonlocal fired
            original(src, dst, **kwargs)
            if str(dst).startswith('.rebuild-') and not fired:
                fired = True
                raise RuntimeError('interrupted')
        monkeypatch.setattr(os, 'rename', interrupted_rename)
    else:
        def interrupted_extract(fd, path):
            nonlocal fired
            fired = True
            path.mkdir()
            (path / 'partial').write_bytes(b'partial')
            raise RuntimeError('interrupted')
        monkeypatch.setattr(views, 'extract_sif', interrupted_extract)
    with pytest.raises(RuntimeError, match='interrupted'):
        apply(maintenance)
    assert fired
    assert shared.verify_image(image, request['artifact']['sha256']) == identity
    # Also cover a SIGKILL-style leftover owned extraction stage and preserve a
    # different digest's stage: only the selected digest may be recovered.
    objects = image.parent.parent
    selected = objects / ('.derive-' + request['artifact']['sha256'] + '-' + uuid.uuid4().hex)
    selected.mkdir()
    (selected / 'rootfs').mkdir()
    other = objects / ('.derive-' + 'f' * 64 + '-' + uuid.uuid4().hex)
    other.mkdir()
    (other / 'rootfs').mkdir()
    result = dispatch(request)
    assert result['state'] == 'ready' and not selected.exists() and other.exists()
    assert shared.verify_image(image, request['artifact']['sha256']) == identity
    assert not set(request['retire_leases']) & set(life.load_state(cache.image_store)['leases'])


def test_retry_rejects_changed_request_and_rebuilt_corruption(maintenance):
    cache, life, views, shared, image, request, dispatch = maintenance
    dispatch(request)
    changed = dict(request, maintenance_authorization='different request')
    before = life.load_state(cache.image_store)
    assert dispatch(changed, check=False).returncode != 0
    assert life.load_state(cache.image_store) == before
    leaf = views.derived_path(cache.image_store, request['artifact']['sha256']) / 'rootfs/data'
    leaf.chmod(0o600)
    leaf.write_bytes(b'corruption after recovery')
    leaf.chmod(0o400)
    assert dispatch(request, check=False).returncode != 0
    assert life.load_state(cache.image_store) == before


@pytest.mark.parametrize('corrupt', ['canonical', 'derivative', 'lease', 'unselected'])
def test_corruption_and_changed_scope_reject_without_retirement(maintenance, corrupt):
    cache, life, views, shared, image, request, dispatch = maintenance
    if corrupt == 'canonical':
        image.chmod(0o600)
        data = image.read_bytes()
        image.write_bytes(b'X' + data[1:])
        image.chmod(0o400)
    elif corrupt == 'derivative':
        leaf = views.derived_path(cache.image_store, request['artifact']['sha256']) / 'rootfs/data'
        leaf.chmod(0o600)
        leaf.write_bytes(b'corrupt rootfs')
        leaf.chmod(0o400)
    elif corrupt == 'lease':
        next(iter(request['retire_leases'].values()))['created_ns'] += 1
    else:
        life.acquire_lease(cache.image_store, [request['artifact']['sha256']], owner='unselected-job')
    before = life.load_state(cache.image_store)
    assert dispatch(request, check=False).returncode != 0
    assert life.load_state(cache.image_store) == before
    assert views.derived_path(cache.image_store, request['artifact']['sha256']).is_dir()
