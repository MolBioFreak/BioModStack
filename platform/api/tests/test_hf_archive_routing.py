"""Packing catalogs choose a byte route; they are not execution authority."""
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from services.remote_execution import cache
from test_hf_weight_archive import cloud, row, weight_bundle
from test_remote_cache_integration import local_transport


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, 'get_data_root', lambda: tmp_path)
    digest, size = 'a' * 64, 32 * 1024 * 1024
    monkeypatch.setenv('BMS_HF_WEIGHT_ARCHIVE', f'{digest}:{size}')
    path = tmp_path / 'remote-execution' / 'hf-archives' / digest / 'index.json'
    path.parent.mkdir(parents=True)
    return path, {'archive': {'sha256': digest, 'size_bytes': size}, 'digest_sizes': {}}


@pytest.mark.parametrize('state', ['missing', 'malformed', 'wrong_identity', 'wrong_shape'])
def test_absent_or_bad_catalog_preserves_configured_route(catalog, state):
    path, index = catalog
    if state == 'malformed':
        path.write_text('not json')
    elif state == 'wrong_shape':
        index['digest_sizes'] = []
        path.write_text(json.dumps(index))
    elif state == 'wrong_identity':
        index['archive']['sha256'] = 'b' * 64
        path.write_text(json.dumps(index))
    entry = SimpleNamespace(sha256='c' * 64, size_bytes=17)
    assert cache._weights_archive_artifact([entry]) is not None


@pytest.mark.parametrize('selected', ['unrelated', 'one_small_member', 'changed_size'])
def test_irrelevant_or_oversized_archive_uses_ordinary_route(catalog, selected):
    path, index = catalog
    index['digest_sizes'] = {'b' * 64: 17}
    path.write_text(json.dumps(index))
    entry = SimpleNamespace(sha256='c' * 64 if selected == 'unrelated' else 'b' * 64,
                            size_bytes=18 if selected == 'changed_size' else 17)
    assert cache._weights_archive_artifact([entry]) is None


def test_many_small_members_keep_packed_delivery(catalog):
    path, index = catalog
    entries = [SimpleNamespace(sha256=f'{i:064x}', size_bytes=4096)
               for i in range(cache.BATCH_COUNT + 1)]
    index['digest_sizes'] = {e.sha256: e.size_bytes for e in entries}
    path.write_text(json.dumps(index))
    result = cache._weights_archive_artifact(entries)
    assert result is not None and result.sha256 == index['archive']['sha256']


def test_compact_archive_keeps_delivery_even_for_one_member(catalog):
    path, index = catalog
    entry = SimpleNamespace(sha256='b' * 64, size_bytes=index['archive']['size_bytes'] * 2)
    index['digest_sizes'] = {entry.sha256: entry.size_bytes}
    path.write_text(json.dumps(index))
    assert cache._weights_archive_artifact([entry]) is not None


@pytest.mark.asyncio
async def test_skipped_archive_never_contacts_hf_or_worker(catalog, monkeypatch):
    path, index = catalog
    path.write_text(json.dumps(index))
    def forbidden(*args, **kwargs):
        raise AssertionError('No archive route work is needed')
    monkeypatch.setattr(cache.hf_assets, 'configuration', forbidden)
    monkeypatch.setattr(cache, 'run_remote', forbidden)
    result = await cache._install_weight_archive(connection=None, helper=None, layout=None,
        operation_id=None, progress=None, check_fence=None,
        artifacts=[SimpleNamespace(sha256='b' * 64, size_bytes=17)])
    assert result is None


@pytest.mark.asyncio
async def test_irrelevant_archive_stages_the_same_selected_bytes(catalog, tmp_path, monkeypatch, local_transport):
    path, index = catalog
    path.write_text(json.dumps(index))
    members = {'runtime/small.dat': b'P' * 4096}
    absent = {'row': row('runtime/other.dat', b'Q' * 2048), 'payload': b'Q' * 2048}
    connection, bundle = weight_bundle(tmp_path, members, absent)
    _, downloads, calls, uploads = cloud(monkeypatch, local_transport, {})
    await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert downloads == []
    assert not any(r['action'] in {'weights_archive_probe', 'unpack_weights_archive'} for r in calls)
    assert len(uploads) == 2  # listing plus one small-file batch
    root = Path(bundle.envelope.environment['BMS_WEIGHTS'])
    assert (root / 'runtime/small.dat').read_bytes() == members['runtime/small.dat']
    assert (root / 'runtime/other.dat').read_bytes() == absent['payload']
