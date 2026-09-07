import hashlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys

import pytest

TOOL = Path(__file__).parents[1] / 'tools/bms_artifact_cache.py'
spec = importlib.util.spec_from_file_location('cache_tool', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def identity(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}


def upload(root, data):
    cache = module.Cache(root)
    source = root / 'incoming' / 'upload'
    source.write_bytes(data)
    return cache, source


def test_cold_warm_corrupt_and_materialization_isolation(tmp_path):
    data = b'model bytes' * 10000
    item = identity(data)
    cache, source = upload(tmp_path / 'cache', data)
    assert cache.probe(item)['state'] == 'missing'
    assert cache.ingest(item, source)['cache_hit'] is False
    source.unlink()
    assert cache.ingest(item, source)['cache_hit'] is True
    destination = tmp_path / 'runtime' / 'model'
    cache.materialize(item, destination, tmp_path / 'runtime', 0o755)
    assert destination.read_bytes() == data
    destination.write_bytes(b'changed by inference')
    assert cache.probe(item)['state'] == 'cache_hit'
    obj = tmp_path / 'cache/objects/sha256' / item['sha256'][:2] / item['sha256']
    obj.chmod(0o600)
    obj.write_bytes(b'corrupt')
    assert cache.probe(item)['state'] == 'corrupt'
    with pytest.raises(ValueError):
        cache.materialize(item, destination, tmp_path / 'runtime')
    source.write_bytes(data)
    assert cache.ingest(item, source)['cache_hit'] is False
    assert cache.probe(item)['state'] == 'cache_hit'


def test_bad_partial_never_published(tmp_path):
    cache, source = upload(tmp_path / 'cache', b'partial')
    item = identity(b'complete')
    with pytest.raises(ValueError):
        cache.ingest(item, source)
    assert cache.probe(item)['state'] == 'missing'
    assert not list((tmp_path / 'cache/objects').rglob('.partial-*'))


def _concurrent(root, item, source, output):
    output.put(module.Cache(root).ingest(item, source)['cache_hit'])


def test_concurrent_single_publication(tmp_path):
    data = b'x' * (2 * 1024 * 1024)
    root = tmp_path / 'cache'
    _, source = upload(root, data)
    ctx = multiprocessing.get_context('fork')
    output = ctx.Queue()
    children = [ctx.Process(target=_concurrent, args=(root, identity(data), source, output)) for _ in range(4)]
    for child in children:
        child.start()
    for child in children:
        child.join(15)
        assert child.exitcode == 0
    assert sorted(output.get(timeout=1) for _ in children) == [False, True, True, True]


def test_symlink_and_traversal_rejected(tmp_path):
    cache, source = upload(tmp_path / 'cache', b'abc')
    item = identity(b'abc')
    cache.ingest(item, source)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'escape').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        cache.materialize(item, tmp_path / 'escape/model', tmp_path)
    with pytest.raises(ValueError):
        cache.materialize(item, tmp_path / '../escape', tmp_path)
    with pytest.raises(ValueError):
        cache.ingest(item, tmp_path / 'untrusted')


def test_json_cli_and_events(tmp_path):
    root = tmp_path / 'cache'
    _, source = upload(root, b'abc')
    events = tmp_path / 'events.jsonl'
    result = subprocess.run([sys.executable, str(TOOL), '--root', str(root), '--events-jsonl', str(events)],
                            input=json.dumps({'action': 'ingest', 'artifact': identity(b'abc'), 'source': str(source)}),
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['state'] == 'ready'
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    assert rows[-1]['state'] == 'ready'
    assert any(row['state'] == 'publishing' for row in rows)


@pytest.mark.parametrize('target', ['/etc/passwd', '../../outside', '../escape/file'])
def test_runtime_link_escape_rejected(tmp_path, target):
    cache = module.Cache(tmp_path / 'cache')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'escape').symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError):
        cache.materialize_link(identity(target.encode()), runtime / 'weights/alias', runtime, target)


def test_runtime_links_verified_atomic_and_no_cache_alias(tmp_path):
    cache, source = upload(tmp_path / 'cache', b'model')
    runtime = tmp_path / 'runtime'
    cache.ingest(identity(b'model'), source)
    cache.materialize(identity(b'model'), runtime / 'model', runtime)
    alias = runtime / 'nested/alias'
    cache.materialize_link(identity(b'../model'), alias, runtime, '../model')
    assert alias.read_bytes() == b'model'
    alias.write_bytes(b'changed')
    assert cache.probe(identity(b'model'))['state'] == 'cache_hit'
    with pytest.raises(ValueError, match='link_identity_mismatch'):
        cache.materialize_link(identity(b'wrong'), alias, runtime, '../model')
    assert alias.is_symlink()
    with pytest.raises(ValueError):
        cache.materialize_link(identity(b'../cache'), runtime / 'cache-link', runtime, '../cache')
    (runtime / 'parent').symlink_to(tmp_path)
    with pytest.raises((OSError, ValueError)):
        cache.materialize_link(identity(b'model'), runtime / 'parent/alias', runtime, 'model')
    assert not list(runtime.rglob('.link-*'))
