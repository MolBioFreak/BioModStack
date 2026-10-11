"""Receiving owner fixtures: concrete native aliases and immutable input reads."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from services.remote_execution import bundle
from tools import bms_artifact_cache as cache
import model_registry
from test_remote_cache_integration import local_transport


def test_readonly_input_links_keep_identity_and_writable_guard(tmp_path):
    source = tmp_path / 'structure.pdb'
    source.write_bytes(b'ATOM tiny\n')
    source.chmod(0o444)
    selected = tmp_path / 'selected.pdb'
    os.link(source, selected)
    observed = {}
    record = bundle._record_file(selected, 'inputs/selected.pdb', 'input', input_inventory=observed)
    assert record.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert observed[str(selected)][0] == record.sha256
    source.chmod(0o644)
    with pytest.raises(bundle.RemoteBundleError, match='writable input'):
        bundle._record_file(selected, 'inputs/selected.pdb', 'input')
    link = tmp_path / 'redirect'
    link.symlink_to(source)
    with pytest.raises(bundle.RemoteBundleError, match='regular file'):
        bundle._record_file(link, 'inputs/redirect', 'input')


def pinned(tmp_path, monkeypatch):
    weights = tmp_path / 'weights'
    payload = b'pinned tiny checkpoint'
    sha = hashlib.sha256(payload).hexdigest()
    dependency = {'kind': 'weights', 'relative_path': 'tiny'}
    entries = [{'member_path': 'native/checkpoint.bin', 'manifest': {'sha256': sha, 'size_bytes': len(payload)}}]
    layout_sha = hashlib.sha256(json.dumps({'dependency': dependency, 'members': entries}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    obj = weights / 'objects/sha256' / sha / 'runtime.sif'
    obj.parent.mkdir(parents=True)
    obj.write_bytes(payload)
    obj.chmod(0o400)
    obj.parent.chmod(0o500)
    layout = weights / 'layouts/tiny' / layout_sha
    native = layout / 'native/checkpoint.bin'
    native.parent.mkdir(parents=True)
    native.symlink_to(os.path.relpath(obj, native.parent))
    native.parent.chmod(0o500)
    layout.chmod(0o500)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: weights)
    monkeypatch.setattr(model_registry, 'model_acquisition_plan', lambda _: {'artifacts': [dict(
        dependency=dependency, member_path=e['member_path'], manifest=e['manifest']) for e in entries]})
    return layout, native, obj


def test_selected_pinned_alias_transports_concrete_bytes_under_native_name(tmp_path, monkeypatch):
    layout, native, obj = pinned(tmp_path, monkeypatch)
    records = bundle._runtime_records(layout, 'runtime/weights/tiny', model_id='tiny')
    assert len(records) == 1
    record = records[0]
    assert record.relative_path == 'runtime/weights/tiny/native/checkpoint.bin'
    assert record.link_target is None and record.sha256 == obj.parent.name
    single = bundle._runtime_records(native, 'runtime/weights/tiny/native/checkpoint.bin', model_id='tiny')
    assert single == records
    assert bundle._selected_weight_members(layout, 'tiny')['native/checkpoint.bin'][0] == obj
    active = layout.parents[2] / 'tiny'
    active.symlink_to(layout)
    from services.remote_execution import cache as controller
    import paths
    monkeypatch.setattr(paths, 'get_weights_root', lambda: layout.parents[2])
    transferred = controller.independent_plan(SimpleNamespace(kind='model', model_id='tiny'),
        dependencies=[SimpleNamespace(kind='weights', relative_path='tiny')], publication={})
    assert len(transferred) == 1
    assert transferred[0].source == obj
    assert transferred[0].remote_destination == 'weights/tiny/native/checkpoint.bin'
    assert transferred[0].link_target is None
    # Real worker CAS uses the record's native name, never a controller target.
    storage = cache.Cache(tmp_path / 'worker/cache')
    item = dict(sha256=record.sha256, size_bytes=record.size_bytes)
    incoming = tmp_path / 'worker/cache/incoming' / record.sha256
    incoming.write_bytes(obj.read_bytes())
    storage.ingest(item, incoming)
    roots = [tmp_path / 'worker' / n for n in ('attempt-a', 'attempt-b')]
    for root in roots:
        storage.materialize(item, root / 'native/checkpoint.bin', root, record.mode)
    assert (roots[0] / 'native/checkpoint.bin').stat().st_ino == (roots[1] / 'native/checkpoint.bin').stat().st_ino
    assert not (roots[0] / 'native/checkpoint.bin').is_symlink()
    assert obj.stat().st_nlink == 1 and obj.stat().st_mode & 0o777 == 0o400


def test_pinned_alias_cannot_redirect_outside_its_manifest(tmp_path, monkeypatch):
    layout, native, obj = pinned(tmp_path, monkeypatch)
    native.parent.chmod(0o700)
    native.unlink()
    alien = tmp_path / 'alien'
    alien.write_bytes(obj.read_bytes())
    alien.chmod(0o400)
    native.symlink_to(alien)
    native.parent.chmod(0o500)
    from scripts.lib.pinned_acquisition import AcquisitionError
    with pytest.raises(AcquisitionError, match='selected acquisition object'):
        bundle._runtime_records(layout, 'runtime/weights/tiny', model_id='tiny')


def test_worker_writable_destination_does_not_link_to_immutable_donor(tmp_path):
    storage = cache.Cache(tmp_path / 'cache')
    data = b'checkpoint'
    item = dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    incoming = tmp_path / 'cache/incoming' / item['sha256']
    incoming.write_bytes(data)
    storage.ingest(item, incoming)
    root = tmp_path / 'dest'
    storage.materialize(item, root / 'mutable', root, 0o644)
    storage.materialize(item, root / 'readonly', root, 0o444)
    assert (root / 'mutable').stat().st_ino != (root / 'readonly').stat().st_ino
    (root / 'mutable').write_bytes(b'changed')
    assert (root / 'readonly').read_bytes() == data


def test_native_weight_aliases_inventory_content_not_absolute_link_targets(tmp_path):
    selected = tmp_path / 'selected'
    selected.mkdir()
    payload = selected / 'native.bin'
    payload.write_bytes(b'weight bytes')
    payload.chmod(0o444)
    (selected / 'absolute.bin').symlink_to(payload)
    (selected / 'relative.bin').symlink_to('native.bin')
    records = bundle._records_for_source(selected, 'runtime/weights/example', 'runtime')
    assert {r.relative_path for r in records} == {'runtime/weights/example/' + n for n in ('native.bin', 'absolute.bin', 'relative.bin')}
    assert all(r.link_target is None and r.sha256 == hashlib.sha256(b'weight bytes').hexdigest() for r in records)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'weight bytes')
    (selected / 'escape').symlink_to(outside)
    with pytest.raises(bundle.RemoteBundleError, match='escapes'):
        bundle._records_for_source(selected, 'runtime/weights/example', 'runtime')


@pytest.mark.asyncio
async def test_weight_acquisition_transport_never_borrows_a_hardlink(tmp_path, monkeypatch, local_transport):
    import uuid
    from services.remote_execution import cache as controller
    layout, native, obj = pinned(tmp_path, monkeypatch)
    record = bundle._runtime_records(layout, 'runtime/weights/tiny', model_id='tiny')[0]
    entry = bundle.CacheTransferArtifact(obj, 'weights/tiny/native/checkpoint.bin',
        record.sha256, record.size_bytes, record.mode, 'weights')
    real_link = os.link
    def link(source, *args, **kwargs):
        assert Path(source) != obj, 'single-link acquisition cannot be a batch hardlink donor'
        return real_link(source, *args, **kwargs)
    monkeypatch.setattr(os, 'link', link)
    transfer = controller.rsync_to_remote
    async def upload(connection, source, destination, **kwargs):
        assert obj.stat().st_nlink == 1
        await transfer(connection, source, destination, **kwargs)
        assert obj.stat().st_nlink == 1
    monkeypatch.setattr(controller, 'rsync_to_remote', upload)
    await controller._cache_artifacts(connection=SimpleNamespace(remote_root=str(tmp_path / 'receiver')),
        artifacts=[entry], operation_id=str(uuid.uuid4()), progress=controller._noop,
        check_fence=controller._noop, materialize=False)
    assert local_transport[1] == [str(obj)]
    assert obj.stat().st_nlink == 1


def test_remote_publication_metadata_does_not_require_local_weight_body(tmp_path, monkeypatch):
    from services.remote_execution import cache as controller
    import paths
    weights = tmp_path / 'absent-controller-weights'
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: weights)
    monkeypatch.setattr(paths, 'get_weights_root', lambda: weights)
    sha = hashlib.sha256(b'worker-published-body').hexdigest()
    publication = dict(dependencies=['weights/tiny'], artifacts=[dict(
        name='weights/tiny/native.bin', sha256=sha, size_bytes=len(b'worker-published-body'), mode=0o444)])
    entries = controller.independent_plan(SimpleNamespace(kind='model', model_id='tiny'),
        dependencies=[SimpleNamespace(kind='weights', relative_path='tiny')], publication=publication)
    assert len(entries) == 1 and entries[0].sha256 == sha
    assert entries[0].source == weights / 'tiny/native.bin'
    assert not weights.exists()


def test_selected_weight_inventory_is_reused_within_preparation(tmp_path, monkeypatch):
    layout, native, obj = pinned(tmp_path, monkeypatch)
    resolved = {}
    first = bundle._runtime_records(layout, 'runtime/weights/tiny', model_id='tiny', resolved=resolved)
    import scripts.lib.pinned_weight_layout as layouts
    monkeypatch.setattr(layouts, '_verify_tree', lambda *a, **kw: pytest.fail('selected layout rehashed'))
    second = bundle._runtime_records(native, 'runtime/weights/tiny/native/checkpoint.bin', model_id='tiny', resolved=resolved)
    assert first == second
    assert resolved[('runtime-source', first[0].relative_path)] == obj
