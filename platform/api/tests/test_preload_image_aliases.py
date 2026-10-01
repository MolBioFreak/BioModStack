"""Independent preload filesystem behavior with inert assets; no model execution."""
import hashlib
from types import SimpleNamespace
import uuid

import pytest

import paths
from services.remote_execution import cache
from test_remote_cache_integration import local_transport


@pytest.fixture
def assets(tmp_path, monkeypatch):
    containers = tmp_path / 'containers'
    weights = tmp_path / 'weights'
    containers.mkdir()
    weights.mkdir()
    monkeypatch.setattr(paths, 'get_container_dir', lambda: containers)
    monkeypatch.setattr(paths, 'get_weights_root', lambda: weights)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'image-store'))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'development')
    monkeypatch.delenv('BMS_RUNTIME_IMAGE_FIXTURE_SIF', raising=False)
    return containers, weights


def select(monkeypatch, kind='image', relative_path='fixture.sif'):
    dependency = SimpleNamespace(kind=kind, relative_path=relative_path)
    monkeypatch.setattr(cache, '_independent_dependencies', lambda _: (dependency,))


@pytest.mark.parametrize('selection_kind', ['image', 'model', 'workflow_pack'])
@pytest.mark.parametrize('alias_kind', ['relative', 'chain', 'external'])
def test_preload_inventories_image_alias_target_with_semantic_name(
        assets, tmp_path, monkeypatch, selection_kind, alias_kind):
    containers, _ = assets
    source = (tmp_path if alias_kind == 'external' else containers) / 'fixture-version.sif'
    data = b'inert image-transfer fixture; not a container'
    source.write_bytes(data)
    alias = containers / 'fixture.sif'
    if alias_kind == 'chain':
        intermediate = containers / 'fixture-current.sif'
        intermediate.symlink_to(source.name)
        alias.symlink_to(intermediate.name)
    else:
        alias.symlink_to(source if alias_kind == 'external' else source.name)
    select(monkeypatch)

    entries = cache.independent_plan(SimpleNamespace(kind=selection_kind))

    assert len(entries) == 1
    entry = entries[0]
    assert entry.source == source
    assert entry.remote_destination == 'containers/fixture.sif'
    assert entry.sha256 == hashlib.sha256(data).hexdigest()
    assert entry.size_bytes == len(data)
    assert entry.role == 'image'
    assert entry.link_target is None
    assert alias.is_symlink()


@pytest.mark.asyncio
async def test_image_alias_preloads_once_and_reuses_real_worker_cache(
        assets, tmp_path, monkeypatch, local_transport):
    containers, _ = assets
    source = containers / 'fixture-version.sif'
    data = b'inert preload-cache fixture; never executed'
    source.write_bytes(data)
    (containers / 'fixture.sif').symlink_to(source.name)
    select(monkeypatch)
    entries = cache.independent_plan(SimpleNamespace(kind='workflow_pack'))
    worker = tmp_path / 'worker'
    connection = SimpleNamespace(remote_root=str(worker))

    for _ in range(2):
        receipts = await cache._cache_artifacts(
            connection=connection, artifacts=entries, operation_id=str(uuid.uuid4()),
            progress=cache._noop, check_fence=cache._noop)
        assert len(receipts) == 1

    _, uploads = local_transport
    assert uploads == [str(source)]
    cached = worker / 'cache/runtime-images/objects/sha256' / entries[0].sha256 / 'runtime.sif'
    assert cached.read_bytes() == data


@pytest.mark.parametrize('fault', ['missing', 'cycle'])
def test_preload_does_not_invent_bytes_for_unresolvable_image_alias(
        assets, monkeypatch, fault):
    containers, _ = assets
    alias = containers / 'fixture.sif'
    alias.symlink_to('absent.sif' if fault == 'missing' else alias.name)
    select(monkeypatch)

    with pytest.raises((OSError, RuntimeError, ValueError)):
        cache.independent_plan(SimpleNamespace(kind='image'))


@pytest.mark.parametrize('alias_kind', ['internal_leaf', 'external_leaf', 'external_parent'])
def test_image_alias_support_does_not_relax_weight_containment(
        assets, tmp_path, monkeypatch, alias_kind):
    _, weights = assets
    outside = tmp_path / 'outside'
    outside.mkdir()
    source = (weights if alias_kind == 'internal_leaf' else outside) / 'payload.bin'
    source.write_bytes(b'inert weight-path containment fixture')
    if alias_kind == 'external_parent':
        (weights / 'alias').symlink_to(outside, target_is_directory=True)
        relative_path = 'alias/payload.bin'
    else:
        (weights / 'alias.bin').symlink_to(source)
        relative_path = 'alias.bin'
    select(monkeypatch, kind='weights', relative_path=relative_path)

    with pytest.raises(ValueError, match='contained regular asset'):
        cache.independent_plan(SimpleNamespace(kind='model'))
