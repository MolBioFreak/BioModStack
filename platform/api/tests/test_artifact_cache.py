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


def production_weight_rows(count=88037):
    """Production-shaped weight layout rows: real depth, name and size spread.

    The Fold-CP selection carries 88,037 `weights/` destinations (audit
    remote-bridge-polish/staging); these rows reproduce that shape and magnitude
    without any model bytes.
    """
    rows = []
    for index in range(count):
        name = (f'boltz/boltz/boltz2_{index:05d}.ckpt' if index % 3
                else f'protenix/{index // 997:03d}/params_{index:05d}.npz')
        rows.append(dict(name=name, sha256=hashlib.sha256(str(index).encode()).hexdigest(),
                         size_bytes=1024 + index % 65536, mode=0o444))
    return rows


def write_layout_listing(runtime, rows):
    """Write the bundle's runtime listing exactly as bundle.py does."""
    digest, normalized, _ = module.weight_layout(rows)
    runtime.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({'schema': 'bms.runtime-image-references.v1',
                          'runtime_root': str(runtime), 'weights': normalized, 'images': []},
                         sort_keys=True, separators=(',', ':')).encode()
    listing = runtime / '.bms-runtime-images.json'
    listing.write_bytes(payload)
    return digest, normalized, listing, {'path': str(listing),
                                         'sha256': hashlib.sha256(payload).hexdigest()}


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


def test_request_budget_is_declared_and_named(tmp_path):
    """An over-budget request is refused by name, never parsed from a truncation."""
    over = json.dumps({'action': 'probe', 'artifacts': [],
                       'padding': 'x' * (module.MAX_REQUEST_BYTES + 1)}).encode()
    result = subprocess.run([sys.executable, str(TOOL), '--root', str(tmp_path / 'cache')],
                            input=over, capture_output=True)
    assert result.returncode == 1
    assert json.loads(result.stderr) == {'state': 'failed', 'error': 'request_too_large'}
    legal = json.dumps({'action': 'init'}).encode()
    assert len(legal) < module.MAX_REQUEST_BYTES
    result = subprocess.run([sys.executable, str(TOOL), '--root', str(tmp_path / 'cache')],
                            input=legal, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['state'] == 'ready'


def test_production_weight_layout_is_referenced_not_inlined(tmp_path):
    """The 88,037-row layout is passed by reference to the authenticated listing."""
    runtime = tmp_path / 'attempts/abc/materialized/runtime'
    digest, normalized, listing, reference = write_layout_listing(runtime, production_weight_rows())
    inlined = json.dumps({'action': 'weights_probe', 'entries': normalized}).encode()
    assert len(inlined) > module.MAX_REQUEST_BYTES
    assert len(listing.read_bytes()) > module.MAX_REQUEST_BYTES
    assert len(json.dumps({'action': 'weights_probe', 'layout': reference}).encode()) < 1024
    assert module.weight_layout(module.weight_layout_reference(reference))[0] == digest
    assert module.Cache(tmp_path / 'cache').weights(normalized) == {
        'state': 'missing', 'root': str(tmp_path / 'cache/weights' / digest), 'sha256': digest}


@pytest.mark.parametrize('damage, code', [
    ('content', 'document_identity_mismatch'),
    ('declared', 'document_identity_mismatch'),
    ('nonhex', 'invalid_reference'),
    ('name', 'invalid_reference'),
    ('absent', 'document_unavailable'),
    ('schema', 'invalid_weight_layout_document'),
    ('placement', 'invalid_weight_layout_document'),
    ('oversized', 'document_too_large'),
])
def test_layout_reference_damage_is_refused_by_name(tmp_path, damage, code):
    runtime = tmp_path / 'attempts/abc/materialized/runtime'
    _, normalized, listing, reference = write_layout_listing(runtime, [
        dict(name='model/checkpoint.pt', sha256=hashlib.sha256(b'data').hexdigest(),
             size_bytes=4, mode=0o444)])
    if damage == 'content':
        listing.write_bytes(listing.read_bytes().replace(b'model', b'other'))
    elif damage == 'declared':
        reference = dict(reference, sha256='0' * 64)
    elif damage == 'nonhex':
        reference = dict(reference, sha256='z' * 64)
    elif damage == 'name':
        moved = listing.with_name('listing.json')
        listing.replace(moved)
        reference = dict(reference, path=str(moved))
    elif damage == 'absent':
        listing.unlink()
    elif damage == 'schema':
        listing.write_bytes(json.dumps({'schema': 'bms.other.v1', 'runtime_root': str(runtime),
                                        'weights': normalized}, sort_keys=True,
                                       separators=(',', ':')).encode())
        reference = dict(reference, sha256=hashlib.sha256(listing.read_bytes()).hexdigest())
    elif damage == 'placement':
        listing.write_bytes(json.dumps({'schema': 'bms.runtime-image-references.v1',
                                        'runtime_root': str(tmp_path / 'elsewhere'),
                                        'weights': normalized}, sort_keys=True,
                                       separators=(',', ':')).encode())
        reference = dict(reference, sha256=hashlib.sha256(listing.read_bytes()).hexdigest())
    else:
        listing.write_bytes(b'x' * (module.MAX_DOCUMENT_BYTES + 1))
        reference = dict(reference, sha256=hashlib.sha256(listing.read_bytes()).hexdigest())
    with pytest.raises(module.RequestBudgetError) as error:
        module.weight_layout_reference(reference)
    assert error.value.code == code
    assert error.value.code in module.REQUEST_CODES
