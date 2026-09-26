"""Worker preparation contract with synthetic bytes; no native execution."""
import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from tools import bms_artifact_cache as helper


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    cache = helper.Cache(tmp_path / 'cache/artifacts/v1')
    lifecycle = helper.runtime_lifecycle()
    views = importlib.import_module('.runtime_image_views', package=lifecycle.__package__)
    shared = importlib.import_module('.shared_runtime_images', package=lifecycle.__package__)
    source = tmp_path / 'source.sif'
    source.write_bytes(b'fake image')
    item = dict(kind='runtime_image', sha256=hashlib.sha256(source.read_bytes()).hexdigest(), size_bytes=source.stat().st_size)
    shared.publish_image(source, cache.image_store, item['sha256'])
    operation = str(uuid.uuid4())
    calls = []
    def extract(fd, destination):
        calls.append(destination)
        assert any(r['owner'] == 'preload:' + operation + ':image:' + item['sha256']
                   for r in lifecycle.load_state(cache.image_store)['leases'].values())
        destination.mkdir()
        (destination / 'data').write_bytes(b'rootfs')
    def forbidden(*args, **kwargs):
        pytest.fail('native execution/private cloning forbidden')
    monkeypatch.setattr(views, 'extract_sif', extract)
    monkeypatch.setattr(views, '_clone', forbidden)
    monkeypatch.setattr(views, 'private_image_view', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    yield cache, item, operation, calls, lifecycle, views
    for folder, _, _ in os.walk(tmp_path):
        os.chmod(folder, 0o700)


@pytest.mark.parametrize('backend', ['udocker', 'apptainer'])
def test_json_action_cold_and_warm(prepared, monkeypatch, capsys, backend):
    cache, item, operation, calls, lifecycle, views = prepared
    request = dict(action='prepare_runtime_image', artifact=item, backend=backend, operation_id=operation)
    monkeypatch.setattr(sys, 'argv', ['cache', '--root', str(cache.root)])
    results = []
    for _ in range(2):
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(request).encode())))
        helper.main()
        results.append(json.loads(capsys.readouterr().out))
    rootfs = str(views.derived_path(cache.image_store, item['sha256']) / 'rootfs') if backend == 'udocker' else None
    assert results == [{**item, 'state': 'ready', 'backend': backend, 'rootfs': rootfs}] * 2
    assert len(calls) == (1 if backend == 'udocker' else 0)
    assert not lifecycle.load_state(cache.image_store)['leases']
    assert not list(Path(cache.root).glob('objects/sha256/*/*'))


@pytest.mark.parametrize('backend', ['udocker', 'apptainer'])
def test_bad_image_and_size_rejected(prepared, backend):
    cache, item, operation, calls, _, _ = prepared
    with pytest.raises((ValueError, RuntimeError)):
        cache.prepare_runtime_image({**item, 'size_bytes': 0}, backend, operation)
    assert not calls
    path = cache.image_path(item)
    path.chmod(0o600)
    path.write_bytes(b'bad image!')
    path.chmod(0o400)
    with pytest.raises((ValueError, RuntimeError)):
        cache.prepare_runtime_image(item, backend, operation)
    assert not calls


def test_shared_weights_existing_api_warm_is_metadata_only(tmp_path, monkeypatch):
    cache = helper.Cache(tmp_path / 'cache/artifacts/v1')
    data = b'weight fixture'
    item = dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    incoming = Path(cache.root) / 'incoming/file'
    incoming.write_bytes(data)
    cache.ingest(item, incoming)
    rows = [dict(item, name='model/weights', mode=0o444)]
    cold = cache.weights(rows, install=True)
    checks = []
    verify = helper.verified
    def measured(fd, item, *args, **kwargs):
        checks.append(item['sha256'])
        return verify(fd, item, *args, **kwargs)
    monkeypatch.setattr(helper, 'verified', measured)
    assert cache.weights(rows, install=True) == cold
    assert item['sha256'] not in checks
    leaf = Path(cold['root']) / 'model/weights'
    with cache.objects(item) as fd:
        assert os.stat(item['sha256'], dir_fd=fd).st_ino == leaf.stat().st_ino
    leaf.chmod(0o644)
    with pytest.raises(ValueError, match='weight_identity_changed'):
        cache.weights(rows, install=True)
    for folder, _, _ in os.walk(tmp_path):
        os.chmod(folder, 0o700)
