"""BMS-DEV-61: verification happens once, at publication, and is receipted.

A verified object is not re-read by a warm path (probe, materialization, source
extraction, weight layout at the use boundary), while a missing, stale, foreign
or mismatched receipt still forces full verification of the bytes.

The counting fixture wraps the two places that hash published bytes - the
verifier and the copy publisher - so every assertion is about real hash passes,
not about which method was called.
"""
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

TOOL = Path(__file__).parents[1] / 'tools/bms_artifact_cache.py'
spec = importlib.util.spec_from_file_location('receipt_tool', TOOL)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def identity(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}


@pytest.fixture
def passes(monkeypatch):
    """One entry per full-file hash pass over published bytes."""
    counts = collections.Counter()
    verified, publish = module.verified, module.Cache._publish_copy

    def counting_verified(fd, item, *args, **kwargs):
        counts['verified'] += 1
        return verified(fd, item, *args, **kwargs)

    def counting_publish(self, source_fd, parent, name, item, mode):
        counts['publish'] += 1
        return publish(self, source_fd, parent, name, item, mode)

    monkeypatch.setattr(module, 'verified', counting_verified)
    monkeypatch.setattr(module.Cache, '_publish_copy', counting_publish)
    return counts


def upload(cache, data):
    source = Path(cache.root) / 'incoming/upload'
    source.write_bytes(data)
    return source


def receipt_path(cache, item):
    return Path(str(cache.object_receipt_path(item)))


def read_receipt(cache, item):
    return json.loads(receipt_path(cache, item).read_text())


def write_receipt(cache, item, document):
    path = receipt_path(cache, item)
    os.chmod(path, 0o600)
    module.write_receipt(cache.object_receipt_path(item), document)


def cache_with_object(tmp_path, data=b'model bytes' * 500):
    cache = module.Cache(tmp_path / 'cache')
    item = identity(data)
    cache.ingest(item, upload(cache, data))
    return cache, item, data


def test_ingest_verifies_once_and_warm_paths_never_re_read_object_bytes(tmp_path, passes):
    data = b'model bytes' * 500
    item = identity(data)
    cache = module.Cache(tmp_path / 'cache')
    source = upload(cache, data)
    assert cache.probe(item)['state'] == 'missing'
    # Cold ingest: the publisher hashes the bytes it writes, and nothing else.
    assert cache.ingest(item, source)['cache_hit'] is False
    assert (passes['publish'], passes['verified']) == (1, 0)
    assert receipt_path(cache, item).is_file()
    passes.clear()
    # Warm paths consume the receipt: no second read of the same bytes.
    assert cache.probe(item)['state'] == 'cache_hit'
    assert cache.ingest(item, source)['cache_hit'] is True
    destination = tmp_path / 'runtime/model'
    assert cache.materialize(item, destination, tmp_path / 'runtime')['state'] == 'ready'
    assert destination.read_bytes() == data
    assert passes == {'publish': 1}          # the copy verifies what it writes
    passes.clear()
    assert cache.probe(item)['state'] == 'cache_hit'
    assert passes == {}


def test_source_extraction_reuses_the_receipt_of_the_verified_archive(tmp_path, passes):
    import io
    import tarfile
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        info = tarfile.TarInfo('workflow.nf')
        info.size = len(b'workflow')
        archive.addfile(info, io.BytesIO(b'workflow'))
    data = buffer.getvalue()
    cache, item, _ = cache_with_object(tmp_path, data)
    passes.clear()
    assert cache.extract_source(item, tmp_path / 'extracted')['state'] == 'ready'
    assert (tmp_path / 'extracted/workflow.nf').read_bytes() == b'workflow'
    assert passes == {}


@pytest.mark.parametrize('damage', [
    'absent', 'malformed', 'empty', 'wrong_digest', 'wrong_size', 'foreign_verifier',
    'foreign_schema', 'other_object_signature', 'not_a_document', 'oversized',
])
def test_absent_stale_or_foreign_receipt_forces_full_verification(tmp_path, passes, damage):
    cache, item, data = cache_with_object(tmp_path)
    document = read_receipt(cache, item)
    path = receipt_path(cache, item)
    if damage == 'absent':
        path.unlink()
    elif damage == 'malformed':
        os.chmod(path, 0o600)
        path.write_bytes(b'{not a receipt')
    elif damage == 'empty':
        os.chmod(path, 0o600)
        path.write_bytes(b'')
    elif damage == 'wrong_digest':
        write_receipt(cache, item, {**document, 'sha256': 'f' * 64})
    elif damage == 'wrong_size':
        write_receipt(cache, item, {**document, 'size_bytes': document['size_bytes'] + 1})
    elif damage == 'foreign_verifier':
        write_receipt(cache, item, {**document, 'verifier': 'f' * 64})
    elif damage == 'foreign_schema':
        write_receipt(cache, item, {**document, 'schema': 'bms.older-receipt.v0'})
    elif damage == 'other_object_signature':
        write_receipt(cache, item, {**document, 'object': [1, 2, 3, 4, 5, 6]})
    elif damage == 'not_a_document':
        write_receipt(cache, item, [1, 2, 3])
    else:
        os.chmod(path, 0o600)
        path.write_bytes(b'{"schema": "' + b'x' * (module.MAX_RECEIPT_BYTES + 1) + b'"}')
    passes.clear()
    # The unusable receipt is not trusted: the bytes are read and verified.
    assert cache.probe(item)['state'] == 'cache_hit'
    assert passes['verified'] == 1
    passes.clear()
    # Verification republished a usable receipt, so the next warm read is free.
    assert cache.probe(item)['state'] == 'cache_hit'
    assert passes == {}


def test_receipt_never_promotes_rewritten_or_replaced_object_bytes(tmp_path, passes):
    cache, item, data = cache_with_object(tmp_path)
    obj = Path(cache.root) / 'objects/sha256' / item['sha256'][:2] / item['sha256']
    # Same-size rewrite inside the same inode: the signature moved, so the
    # receipt is stale and the bytes are re-read and rejected.
    obj.chmod(0o600)
    obj.write_bytes(b'z' * len(data))
    obj.chmod(0o444)
    passes.clear()
    assert cache.probe(item)['state'] == 'corrupt'
    assert passes['verified'] == 1
    with pytest.raises(ValueError):
        cache.materialize(item, tmp_path / 'runtime/model', tmp_path / 'runtime')
    assert not (tmp_path / 'runtime/model').exists()
    # Replacement with different bytes of the published size is refused too.
    obj.unlink()
    obj.write_bytes(b'q' * len(data))
    obj.chmod(0o444)
    assert cache.probe(item)['state'] == 'corrupt'
    # Replacement with the correct bytes is re-verified from bytes, never assumed.
    obj.unlink()
    obj.write_bytes(data)
    obj.chmod(0o444)
    passes.clear()
    assert cache.probe(item)['state'] == 'cache_hit'
    assert passes['verified'] == 1


def test_explicit_integrity_sweep_re_reads_bytes_and_re_derives_receipts(tmp_path, passes):
    cache = module.Cache(tmp_path / 'cache')
    items = []
    for index in range(3):
        data = f'object-{index}'.encode() * 100
        item = identity(data)
        cache.ingest(item, upload(cache, data))
        (Path(cache.root) / 'incoming/upload').unlink()
        items.append(item)
    passes.clear()
    for item in items:
        assert cache.probe(item)['state'] == 'cache_hit'
    assert passes == {}                       # warm store: receipts only
    assert cache.sweep(('objects',)) == {'state': 'ready', 'objects': 3, 'verified': 3,
                                         'layouts': 0, 'layouts_verified': 0, 'damaged': []}
    assert passes['verified'] == 3            # the sweep re-read every object
    passes.clear()
    assert cache.probe(items[0])['state'] == 'cache_hit'
    assert passes == {}                       # and re-derived usable receipts
    # A receipt that a worker-local writer forged to match tampered bytes is not
    # an authority: the sweep re-derives the digest from the bytes themselves.
    obj = Path(cache.root) / 'objects/sha256' / items[1]['sha256'][:2] / items[1]['sha256']
    obj.chmod(0o600)
    obj.write_bytes(b'forged bytes' * 100)
    obj.chmod(0o444)
    write_receipt(cache, items[1], {**read_receipt(cache, items[1]), 'object': module.signature(obj.stat())})
    assert cache.probe(items[1])['state'] == 'cache_hit'
    report = cache.sweep(('objects',))
    assert report['objects'] == 3 and report['verified'] == 2
    assert report['damaged'] == ['objects/' + items[1]['sha256'][:2] + '/' + items[1]['sha256']]
    assert cache.probe(items[1])['state'] == 'corrupt'


def test_sweep_action_is_bounded_and_names_damage(tmp_path):
    cache, item, data = cache_with_object(tmp_path)
    root = str(cache.root)
    result = subprocess.run([sys.executable, str(TOOL), '--root', root],
                            input=json.dumps({'action': 'sweep'}), capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {'state': 'ready', 'objects': 1, 'verified': 1,
                                        'layouts': 0, 'layouts_verified': 0, 'damaged': []}
    result = subprocess.run([sys.executable, str(TOOL), '--root', root],
                            input=json.dumps({'action': 'sweep', 'include': ['objects', 'elsewhere']}),
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stderr) == {'state': 'failed', 'error': 'ValueError'}


def weight_rows(*members):
    return [dict(name=name, mode=mode, **identity(data)) for name, data, mode in members]


def test_weight_layout_receipt_is_consumed_at_the_use_boundary(tmp_path, passes):
    cache = module.Cache(tmp_path / 'cache')
    linked = b'linkable checkpoint bytes' * 40
    copied = b'executable projection bytes' * 40
    for data in (linked, copied):
        cache.ingest(identity(data), upload(cache, data))
        (Path(cache.root) / 'incoming/upload').unlink()
    rows = weight_rows(('model/checkpoint.pt', linked, 0o644), ('bin/runner', copied, 0o755))
    passes.clear()
    assert cache.weights(rows, install=True)['state'] == 'ready'
    # Publication verified each member against the CAS object it published and
    # published the listing: one read per file, then never again. Only the
    # executable projection is a copy; the read-only member is an alias of the
    # CAS object it was verified from.
    assert passes['verified'] == 3 and passes['publish'] == 1
    passes.clear()
    assert cache.weights(rows)['state'] == 'ready'              # warm probe
    assert passes == {}
    # The use boundary (execute_runtime) no longer re-reads every weight.
    assert cache.weights(rows, full=True)['state'] == 'ready'
    assert passes == {}
    passes.clear()
    # A missing receipt forces the full read of every member again.
    Path(str(cache.layout_receipt_path(module.weight_layout(rows)[0]))).unlink()
    assert cache.weights(rows, full=True)['state'] == 'ready'
    assert passes['verified'] == 3                              # two members + the listing
    passes.clear()
    assert cache.weights(rows, full=True)['state'] == 'ready'
    assert passes == {}
    # The explicit sweep re-reads everything and rewrites the receipt.
    assert cache.weights(rows, full=True, refresh=True)['state'] == 'ready'
    assert passes['verified'] == 3
    passes.clear()
    assert cache.weights(rows, full=True)['state'] == 'ready'
    assert passes == {}


@pytest.mark.parametrize('damage', ['bytes', 'listing'])
def test_damaged_weight_member_is_still_refused_with_a_receipt(tmp_path, passes, damage):
    cache = module.Cache(tmp_path / 'cache')
    data = b'checkpoint bytes' * 200
    cache.ingest(identity(data), upload(cache, data))
    (Path(cache.root) / 'incoming/upload').unlink()
    rows = weight_rows(('model/checkpoint.pt', data, 0o444))
    root = Path(cache.weights(rows, install=True)['root'])
    member = root / 'model/checkpoint.pt'
    member.parent.chmod(0o755)
    if damage == 'bytes':
        member.chmod(0o644)
        member.write_bytes(b'x' * len(data))
        member.chmod(0o444)
    else:
        listing = root / '.bms-weights.json'
        listing.chmod(0o644)
        listing.write_bytes(b'x' * listing.stat().st_size)
        listing.chmod(0o444)
    member.parent.chmod(0o555)
    with pytest.raises(ValueError):
        cache.weights(rows, full=True)


def test_mismatched_layout_receipt_row_costs_one_verification_only(tmp_path, passes):
    """A receipt that does not describe the member falls back to reading it."""
    cache = module.Cache(tmp_path / 'cache')
    data = b'checkpoint bytes' * 200
    cache.ingest(identity(data), upload(cache, data))
    (Path(cache.root) / 'incoming/upload').unlink()
    rows = weight_rows(('model/checkpoint.pt', data, 0o444))
    digest = module.weight_layout(rows)[0]
    assert cache.weights(rows, install=True)['state'] == 'ready'
    document = json.loads(Path(str(cache.layout_receipt_path(digest))).read_text())
    document['members']['model/checkpoint.pt'] = [1, 2, 3, 4, 5, 6]
    assert module.write_receipt(cache.layout_receipt_path(digest), document)
    passes.clear()
    # The member the receipt does not describe is read and verified again; the
    # layout is still accepted because the bytes are correct.
    assert cache.weights(rows, full=True)['state'] == 'ready'
    assert passes['verified'] == 1
    # A receipt written by another helper generation is not trusted either.
    document = json.loads(Path(str(cache.layout_receipt_path(digest))).read_text())
    assert module.write_receipt(cache.layout_receipt_path(digest),
                                {**document, 'verifier': 'f' * 64})
    passes.clear()
    assert cache.weights(rows, full=True)['state'] == 'ready'
    assert passes['verified'] == 2


def test_sweep_re_verifies_a_published_layout_from_its_own_listing(tmp_path, passes):
    cache = module.Cache(tmp_path / 'cache')
    data = b'checkpoint bytes' * 200
    cache.ingest(identity(data), upload(cache, data))
    (Path(cache.root) / 'incoming/upload').unlink()
    rows = weight_rows(('model/checkpoint.pt', data, 0o644))
    root = Path(cache.weights(rows, install=True)['root'])
    passes.clear()
    report = cache.sweep(('weights',))
    assert report == {'state': 'ready', 'objects': 0, 'verified': 0,
                      'layouts': 1, 'layouts_verified': 1, 'damaged': []}
    assert passes['verified'] == 2                              # member + listing
    root.chmod(0o755)
    (root / 'model').chmod(0o755)
    (root / 'model/checkpoint.pt').chmod(0o644)
    (root / 'model/checkpoint.pt').write_bytes(b'q' * len(data))
    (root / 'model/checkpoint.pt').chmod(0o444)
    (root / 'model').chmod(0o555)
    root.chmod(0o555)
    assert cache.sweep(('weights',))['damaged'] == ['weights/' + module.weight_layout(rows)[0]]
