import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest

TOOL = Path(__file__).parents[1] / 'tools/bms_artifact_cache.py'
spec = importlib.util.spec_from_file_location('shared_weights_tool', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def installed(tmp_path):
    cache = module.Cache(tmp_path / 'cache')
    data = b'exact checkpoint bytes' * 1000
    item = dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    source = tmp_path / 'cache/incoming/model'
    source.write_bytes(data)
    cache.ingest(item, source)
    rows = [dict(name='model/checkpoint/model.pt', mode=0o644, **item)]
    result = cache.weights(rows, install=True)
    yield cache, rows, Path(result['root']), data
    # Disposable fixture trees are private to this test; production never thaws.
    for path in sorted(tmp_path.rglob('*'), key=lambda p: len(p.parts)):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)


def test_warm_weights_reuse_same_installed_bytes_without_copy_or_rehash(installed, monkeypatch):
    cache, rows, root, data = installed
    model = root / rows[0]['name']
    original = model.stat()
    obj = Path(cache.root) / 'objects/sha256' / rows[0]['sha256'][:2] / rows[0]['sha256']
    assert original.st_ino == obj.stat().st_ino
    assert stat.S_IMODE(original.st_mode) == 0o444
    assert stat.S_IMODE(root.stat().st_mode) == 0o555
    verified = module.verified
    def metadata_only(fd, item, *args, **kwargs):
        assert item['sha256'] != rows[0]['sha256'], 'warm staging re-read model bytes'
        return verified(fd, item, *args, **kwargs)
    monkeypatch.setattr(module, 'verified', metadata_only)
    monkeypatch.setattr(cache, '_publish_copy', lambda *a, **k: pytest.fail('warm weight copy'))
    for _ in range(3):
        assert cache.weights(rows, install=True)['root'] == str(root)
        assert model.stat().st_ino == original.st_ino
    assert model.read_bytes() == data


def test_layout_identity_tracks_named_content_not_job_or_order(installed):
    cache, rows, root, _ = installed
    alias = '../checkpoint/model.pt'
    linked = dict(name='model/aliases/current', target=alias, mode=0o777,
                  sha256=hashlib.sha256(alias.encode()).hexdigest(), size_bytes=len(alias))
    selected = rows + [linked]
    assert module.weight_layout(selected) == module.weight_layout(list(reversed(selected)))
    other = cache.weights(selected, install=True)
    assert other['root'] != str(root)
    assert (Path(other['root']) / linked['name']).read_bytes() == (root / rows[0]['name']).read_bytes()
    assert cache.weights(selected, full=True)['state'] == 'ready'
    assert (Path(other['root']) / rows[0]['name']).stat().st_ino == (root / rows[0]['name']).stat().st_ino


@pytest.mark.parametrize('damage', ['bytes', 'mode', 'missing', 'extra', 'symlink', 'manifest'])
def test_published_weight_damage_is_not_repaired_or_executed(installed, damage):
    cache, rows, root, data = installed
    path = root / rows[0]['name']
    path.parent.chmod(0o755)
    if damage == 'bytes':
        path.chmod(0o644)
        path.write_bytes(b'x' * len(data))
        path.chmod(0o444)
    elif damage == 'mode':
        path.chmod(0o644)
    elif damage == 'missing':
        path.unlink()
    elif damage == 'extra':
        (path.parent / 'unselected.pt').write_bytes(b'unselected')
    elif damage == 'symlink':
        path.unlink()
        path.symlink_to('/etc/passwd')
    else:
        meta = root / '.bms-weights.json'
        meta.chmod(0o644)
        meta.write_bytes(b'x' * meta.stat().st_size)
        meta.chmod(0o444)
    path.parent.chmod(0o555)
    with pytest.raises((ValueError, OSError)):
        cache.weights(rows, full=True, install=True)


@pytest.mark.parametrize('target', ['/etc/passwd', '../../../escape', '../aliases/second'])
def test_invalid_alias_not_published(installed, target):
    cache, rows, _, _ = installed
    linked = dict(name='model/aliases/current', target=target, mode=0o777,
                  sha256=hashlib.sha256(target.encode()).hexdigest(), size_bytes=len(target))
    with pytest.raises(ValueError):
        cache.weights(rows + [linked], install=True)


def test_execution_verifies_weights_before_starting_command(installed, tmp_path, monkeypatch):
    cache, rows, root, data = installed
    runtime = tmp_path / 'attempt/runtime'
    runtime.mkdir(parents=True)
    manifest = runtime / '.bms-runtime-images.json'
    payload = json.dumps(dict(schema='bms.runtime-image-references.v1', runtime_root=str(runtime),
                              images=[], weights=rows)).encode()
    manifest.write_bytes(payload)
    calls = []
    monkeypatch.setenv('BMS_SHARED_WEIGHTS_ROOT', '')
    monkeypatch.setattr(os, 'execvp', lambda *args: calls.append(args))
    digest = hashlib.sha256(payload).hexdigest()
    cache.execute_runtime(manifest, ['true'], digest)
    assert calls == [('true', ['true'])]
    assert os.environ['BMS_SHARED_WEIGHTS_ROOT'] == str(root)
    path = root / rows[0]['name']
    path.chmod(0o644)
    path.write_bytes(b'z' * len(data))
    path.chmod(0o444)
    with pytest.raises(ValueError, match='weight_hash_mismatch'):
        cache.execute_runtime(manifest, ['true'], digest)
    assert len(calls) == 1


def test_missing_content_never_publishes_a_partial_layout(tmp_path):
    cache = module.Cache(tmp_path / 'cache')
    rows = [dict(name='model/checkpoint.pt', mode=0o644, sha256='a' * 64, size_bytes=20)]
    expected = cache.weights(rows)
    assert expected['state'] == 'missing'
    with pytest.raises(FileNotFoundError):
        cache.weights(rows, install=True)
    assert not Path(expected['root']).exists()
    assert cache.weights(rows)['state'] == 'missing'
