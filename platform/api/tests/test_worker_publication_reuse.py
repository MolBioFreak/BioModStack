"""Mechanism proofs using real worker publication owners, no model execution."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

import pytest

from tools import bms_artifact_cache as module


def item(data, **extra) -> dict:
    return dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), **extra)


@pytest.fixture
def cache(tmp_path):
    value = module.Cache(tmp_path / 'cache/artifacts/v1')
    yield value
    for path in tmp_path.rglob('*'):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)


def publish(cache, data, **extra):
    row = item(data, **extra)
    source = Path(cache.root / 'incoming') / uuid.uuid4().hex
    source.write_bytes(data)
    cache.ingest(row, source)
    source.unlink()
    return row


def forbid_shared_reads(monkeypatch, cache):
    read = module.os.read
    def checked(fd, count):
        path = os.readlink(f'/proc/self/fd/{fd}')
        shared = str(cache.root / 'objects') in path or str(cache.image_store / 'objects') in path
        if str(cache.root / 'weights') in path:
            shared = not path.endswith('/.bms-weights.json')
        assert not shared, f'warm shared body read: {path}'
        return read(fd, count)
    monkeypatch.setattr(module.os, 'read', checked)
    monkeypatch.setattr(module.os, 'listdir', lambda *a, **k: pytest.fail('warm tree walk'))
    monkeypatch.setattr(cache, '_publish_copy', lambda *a, **k: pytest.fail('warm copy'))


def test_warm_cas_probe_and_ingest_have_zero_body_reads(cache, monkeypatch):
    row = publish(cache, b'non-scientific weight')
    forbid_shared_reads(monkeypatch, cache)
    for _ in range(3):
        assert cache.probe(row)['state'] == 'cache_hit'
        assert cache.ingest(row, cache.root / 'incoming/no-longer-present')['cache_hit']


def test_warm_runtime_probe_ingest_alias_execute_preserve_leases_without_reads(cache, tmp_path, monkeypatch):
    row = publish(cache, b'inert image bytes, not a SIF', kind='runtime_image')
    attempt = str(uuid.uuid4())
    runtime = tmp_path / 'attempts' / attempt / 'materialized/runtime'
    runtime.mkdir(parents=True)
    alias = runtime / 'selected.sif'
    cache.runtime_alias(row, alias, runtime)
    authority = module.runtime_lifecycle()
    before = authority.load_state(cache.image_store)['leases']
    payload = json.dumps(dict(schema=module.LAYOUT_SCHEMA, runtime_root=str(runtime),
                              images=[dict(row, aliases=[str(alias)])], weights=[])).encode()
    manifest = runtime / module.LAYOUT_DOCUMENT
    manifest.write_bytes(payload)
    forbid_shared_reads(monkeypatch, cache)
    calls = []
    monkeypatch.setattr(os, 'execvp', lambda *args: calls.append(args))
    for _ in range(3):
        assert cache.probe(row)['state'] == 'cache_hit'
        assert cache.ingest(row, cache.root / 'incoming/absent')['cache_hit']
        cache.runtime_alias(row, alias, runtime, check=True)
        assert cache.prepare_runtime_image(row, 'apptainer', str(uuid.uuid4()))['state'] == 'ready'
        cache.execute_runtime(manifest, ['true'], hashlib.sha256(payload).hexdigest())
    assert len(calls) == 3
    assert authority.load_state(cache.image_store)['leases'] == before


def test_warm_runtime_changed_inode_retains_existing_lease_fence(cache):
    row = publish(cache, b'inert image', kind='runtime_image')
    path = cache.image_path(row)
    path.parent.chmod(0o700)
    replacement = path.with_name('replacement')
    replacement.write_bytes(b'inert image')
    replacement.chmod(0o400)
    replacement.replace(path)
    path.parent.chmod(0o500)
    with pytest.raises(Exception, match='leased image identity changed'):
        cache.ingest(row, cache.root / 'incoming/absent')


def test_cold_runtime_mismatch_rejected_by_actual_publication_without_helper_preread(cache, monkeypatch):
    row = item(b'good image', kind='runtime_image')
    source = Path(cache.root / 'incoming/upload')
    source.write_bytes(b'bad image!')  # same size, actual digest mismatch
    monkeypatch.setattr(module, 'verified', lambda *a, **k: pytest.fail('redundant helper preread'))
    with pytest.raises(RuntimeError, match='SHA-256'):
        cache.ingest(row, source)
    assert cache.probe(row)['state'] == 'missing'


def test_cold_marker_is_last_after_member_publication(cache, monkeypatch):
    row = publish(cache, b'weight')
    rows = [dict(row, name='model/sub/weight', mode=0o444)]
    original = module.os.open
    markers = []
    def accounted(path, flags, *args, **kwargs):
        if path == '.bms-weights.json' and flags & os.O_CREAT:
            parent = Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}"))
            leaf = parent / 'model/sub/weight'
            assert leaf.is_file() and leaf.stat().st_size == row['size_bytes']
            assert stat.S_IMODE(leaf.stat().st_mode) == 0o444
            assert stat.S_IMODE(leaf.parent.stat().st_mode) == 0o555
            markers.append(parent)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(module.os, 'open', accounted)
    assert cache.weights(rows, install=True)['state'] == 'ready'
    assert len(markers) == 1


@pytest.mark.parametrize('damage', ['size', 'mode', 'symlink', 'fifo'])
def test_warm_cas_envelope_checks_without_hashing(cache, tmp_path, damage):
    row = publish(cache, b'weight')
    path = Path(cache.root / 'objects/sha256') / row['sha256'][:2] / row['sha256']
    if damage == 'size':
        path.chmod(0o600)
        path.write_bytes(b'truncated')
        path.chmod(0o444)
    elif damage == 'mode':
        path.chmod(0o600)
    else:
        path.unlink()
        if damage == 'symlink':
            target = tmp_path / 'foreign'
            target.write_bytes(b'weight')
            path.symlink_to(target)
        else:
            os.mkfifo(path)
    assert cache.probe(row)['state'] == 'corrupt'


def test_new_overlapping_layouts_link_without_body_reads_or_finished_walk(cache, monkeypatch):
    row = publish(cache, b'weight')
    rows = [dict(row, name='model/weight.pt', mode=0o444)]
    read = module.os.read
    def marker_or_metadata(fd, count):
        path = os.readlink(f'/proc/self/fd/{fd}')
        assert str(cache.root / 'objects') not in path, 'cold layout CAS rehash'
        return read(fd, count)
    monkeypatch.setattr(module.os, 'read', marker_or_metadata)
    monkeypatch.setattr(module.os, 'listdir', lambda *a, **k: pytest.fail('finished stage walk'))
    first = cache.weights(rows, install=True)
    second = cache.weights([dict(rows[0], name='other/weight.pt')], install=True)
    assert first['state'] == second['state'] == 'ready'
    assert (Path(first['root']) / rows[0]['name']).stat().st_ino == (
        Path(second['root']) / 'other/weight.pt').stat().st_ino


def test_permission_projection_hashes_only_the_checked_copy(cache, monkeypatch):
    data = b'inert executable weight' * 1000
    row = publish(cache, data)
    rows = [dict(row, name='model/run', mode=0o555)]
    read, observed = module.os.read, []
    def tracked(fd, count):
        payload = read(fd, count)
        if str(cache.root / 'objects') in os.readlink(f'/proc/self/fd/{fd}'):
            observed.append(len(payload))
        return payload
    monkeypatch.setattr(module.os, 'read', tracked)
    result = cache.weights(rows, install=True)
    assert sum(observed) == len(data)
    assert (Path(result['root']) / 'model/run').read_bytes() == data
    assert stat.S_IMODE((Path(result['root']) / 'model/run').stat().st_mode) == 0o555


def test_permission_projection_actual_mismatch_never_publishes_layout(cache):
    row = publish(cache, b'weight')
    path = Path(cache.root / 'objects/sha256') / row['sha256'][:2] / row['sha256']
    path.chmod(0o600)
    path.write_bytes(b'wrong!')
    path.chmod(0o444)
    rows = [dict(row, name='model/run', mode=0o555)]
    with pytest.raises(ValueError, match='hash_mismatch'):
        cache.weights(rows, install=True)
    assert cache.weights(rows)['state'] == 'missing'


@pytest.mark.parametrize('failure', ['digest', 'size', 'cancel'])
def test_cold_checked_copy_failure_has_no_publication_or_temporary(cache, failure):
    data = b'new bytes'
    row = item(data)
    source = Path(cache.root / 'incoming/upload')
    source.write_bytes(b'bad bytes' if failure == 'digest' else data[:-1] if failure == 'size' else data)
    if failure == 'cancel':
        def cancelled(event):
            if event['state'] == 'transferring':
                raise KeyboardInterrupt('owned writer cancelled')
        cache.events = cancelled
    with pytest.raises(KeyboardInterrupt if failure == 'cancel' else ValueError):
        cache.ingest(row, source)
    cache.events = lambda event: None
    assert cache.probe(row)['state'] == 'missing'
    assert not list(Path(cache.root / 'objects').rglob('.partial-*'))
    assert source.exists()  # interrupted incoming data remains owned/recoverable


def test_cancelled_layout_never_writes_completion_marker(cache):
    row = publish(cache, b'weight')
    rows = [dict(row, name='model/run', mode=0o555)]
    def cancelled(event):
        if event['state'] == 'transferring':
            raise KeyboardInterrupt('owned layout writer cancelled')
    cache.events = cancelled
    with pytest.raises(KeyboardInterrupt):
        cache.weights(rows, install=True)
    assert cache.weights(rows)['state'] == 'missing'
    assert not list(Path(cache.root / 'weights').glob('.partial-*'))


@pytest.mark.parametrize('damage', ['marker_missing', 'marker_mode', 'marker_body', 'root_mode'])
def test_existing_layout_envelope_refusal_is_retained(cache, damage):
    row = publish(cache, b'weight')
    rows = [dict(row, name='model/weight', mode=0o444)]
    root = Path(cache.weights(rows, install=True)['root'])
    marker = root / '.bms-weights.json'
    if damage == 'root_mode':
        root.chmod(0o755)
    elif damage == 'marker_missing':
        root.chmod(0o755)
        marker.unlink()
        root.chmod(0o555)
    elif damage == 'marker_mode':
        marker.chmod(0o644)
    else:
        marker.chmod(0o644)
        marker.write_bytes(b'x' * marker.stat().st_size)
        marker.chmod(0o444)
    with pytest.raises(ValueError):
        cache.weights(rows, install=True)
