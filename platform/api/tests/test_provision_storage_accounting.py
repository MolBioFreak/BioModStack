"""Preview payload accounting against actual immutable cache publications."""
import hashlib
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.remote_execution import cache
from services.remote_execution.bundle import CacheTransferArtifact
from services.remote_execution.contracts import ProvisionSelection
from tools.bms_artifact_cache import Cache


@pytest.mark.parametrize('modes', [
    (0o644, 0o444, 0o666),
    (0o644, 0o755, 0o555, 0o777),
    (0o755, 0o555, 0o744, 0o544, 0o640),
])
def test_preview_counts_actual_payload_inodes_across_consumer_layouts(tmp_path, monkeypatch, modes):
    payload = b'tiny shared checkpoint'
    digest = hashlib.sha256(payload).hexdigest()
    storage = Cache(tmp_path / 'worker/cache/artifacts/v1')
    incoming = Path(storage.root) / 'incoming/checkpoint'
    incoming.write_bytes(payload)
    item = dict(sha256=digest, size_bytes=len(payload))
    storage.ingest(item, incoming)
    incoming.unlink()
    entries = [CacheTransferArtifact(tmp_path / 'absent-controller-body', f'weights/member-{i}',
               digest, len(payload), mode, 'runtime') for i, mode in enumerate(modes)]
    link = 'member-0'
    entries.append(CacheTransferArtifact(tmp_path / 'absent-link', 'weights/alias',
        hashlib.sha256(link.encode()).hexdigest(), len(link), 0o777, 'runtime', link_target=link))
    monkeypatch.setattr(cache, '_independent_dependencies', lambda selection: [])
    monkeypatch.setattr(cache, 'independent_plan', lambda *args, **kwargs: entries)
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a' * 40, 'b' * 40))
    monkeypatch.setattr(cache.hf_assets, 'publication_index', lambda: {})
    target = SimpleNamespace(id='target', host='worker', port=22, username='root',
                            remote_root='/worker', host_key_sha256='e' * 64)
    selection = ProvisionSelection(kind='model', model_id='boltz2')
    preview, selected = cache.independent_preview(selection, target)
    assert selected == entries
    assert preview.transfer_bytes == len(payload)
    assert preview.total_bytes == sum(e.size_bytes for e in entries)
    assert len(preview.artifacts) == len(entries)

    def publish(members):
        rows = [dict(name=e.remote_destination.removeprefix('weights/'), sha256=e.sha256,
                     size_bytes=e.size_bytes, mode=e.mode,
                     **({'target': e.link_target} if e.link_target is not None else {})) for e in members]
        return Path(storage.weights(rows, install=True)['root'])

    root = publish(entries)
    # A persistent consumer subset must reuse both the CAS and mode projections.
    subset = publish([replace(e, remote_destination='weights/consumer/' + Path(e.remote_destination).name)
                      for e in entries])
    assert (root / 'alias').is_symlink()
    assert (root / 'alias').read_bytes() == payload
    payload_paths = list((Path(storage.root) / 'objects').rglob(digest + '*'))
    payload_paths += [root / f'member-{i}' for i in range(len(modes))]
    payload_paths += [subset / f'consumer/member-{i}' for i in range(len(modes))]
    inodes = {(p.stat().st_dev, p.stat().st_ino): p.stat().st_size for p in payload_paths}
    assert preview.storage_bytes == sum(inodes.values())
    assert len(inodes) == len({0o444, *(mode & 0o555 for mode in modes)})
    for i, mode in enumerate(modes):
        member = root / f'member-{i}'
        assert stat.S_IMODE(member.stat().st_mode) == mode & 0o555
        assert member.samefile(subset / f'consumer/member-{i}')
    # A warm observation changes transfer, not the persistent footprint estimate.
    target.managed_inventory = SimpleNamespace(state='current', releases=[SimpleNamespace(
        artifacts=[SimpleNamespace(name=e.remote_destination, sha256=e.sha256,
                   size_bytes=e.size_bytes, state='verified') for e in entries])])
    warm, _ = cache.independent_preview(selection, target)
    assert warm.transfer_bytes == 0
    assert warm.storage_bytes == preview.storage_bytes
    assert warm.preview_sha256 == preview.preview_sha256


def test_preview_keeps_image_and_generic_object_namespaces_separate(tmp_path, monkeypatch):
    digest = hashlib.sha256(b'same body').hexdigest()
    entries = [CacheTransferArtifact(tmp_path / 'absent', name, digest, 9, mode, role)
               for name, mode, role in [('containers/a.sif', 0o755, 'image'),
                                        ('containers/b.sif', 0o755, 'image'),
                                        ('weights/a', 0o644, 'runtime')]]
    monkeypatch.setattr(cache, '_independent_dependencies', lambda selection: [])
    monkeypatch.setattr(cache, 'independent_plan', lambda *args, **kwargs: entries)
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a' * 40, 'b' * 40))
    monkeypatch.setattr(cache.hf_assets, 'publication_index', lambda: {})
    target = SimpleNamespace(id='target', host='worker', port=22, username='root',
                            remote_root='/worker', host_key_sha256='e' * 64)
    preview, _ = cache.independent_preview(ProvisionSelection(kind='model', model_id='boltz2'), target)
    assert preview.storage_bytes == preview.transfer_bytes == 18
    assert preview.total_bytes == 27
