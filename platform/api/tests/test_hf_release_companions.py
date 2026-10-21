"""Release companions and local mirror resolution use the existing broker."""
import hashlib
from types import SimpleNamespace
from dataclasses import replace
import pytest
from test_hf_asset_sources import configured, capability, clean_env, Api, Http, request
from services.remote_execution import hf_assets as broker
from services.remote_execution import hf_assets_worker as worker


@pytest.fixture
def companion(tmp_path):
    path = tmp_path / 'config.json'
    path.write_bytes(b'{"native":true}')
    return SimpleNamespace(source=path, role='weights', size_bytes=path.stat().st_size,
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.asyncio
async def test_release_companion_does_not_lower_runtime_floor(configured, companion, capability, monkeypatch):
    calls = []
    async def invoke(request, **kwargs):
        calls.append(request)
        return capability
    monkeypatch.setattr(broker, '_invoke', invoke)
    assert await broker.prepare_sources([companion], check_fence=broker._noop) == {}
    assert not calls
    assert await broker.prepare_release_sources([companion], check_fence=broker._noop) == {
        (False, companion.sha256): capability}
    assert calls[0]['action'] == 'release'
    with pytest.raises(broker.HFAssetError):
        await broker.prepare_release_sources([companion] * 33, check_fence=broker._noop)
    companion.role = 'image'
    with pytest.raises(broker.HFAssetError):
        await broker.prepare_release_sources([companion], check_fence=broker._noop)
    assert len(calls) == 1


def test_small_publish_preserves_identity_and_cloud_reuse(configured, companion, capability, monkeypatch):
    config = broker.configuration()
    assert config is not None
    config = replace(config, allow_publish=True)
    api = Api(companion, False)
    http = Http(capability)
    with pytest.raises(broker.HFAssetError, match='publishing is not authorized'):
        worker._prepare(dict(request(companion), action='resolve'), config, api, http, 'fixture-token')
    assert not api.uploads
    with pytest.raises(broker.HFAssetError):
        worker._prepare(request(companion), config, api, http, 'fixture-token')
    req = dict(request(companion), action='release')
    assert worker._prepare(req, config, api, http, 'fixture-token') == capability
    companion.source.unlink()
    assert worker._prepare(req, config, api, http, 'fixture-token') == capability
    assert len(api.uploads) == 1
    assert api.uploads[0][0][1] == f'sha256/{companion.sha256}/artifact'


@pytest.mark.asyncio
async def test_resolution_never_supplies_publish_path(configured, companion, capability, monkeypatch):
    companion.kind = 'weights'
    async def invoke(request, **kwargs):
        assert request == dict(action='resolve', role='weights', sha256=companion.sha256,
                              size_bytes=companion.size_bytes)
        return capability
    monkeypatch.setattr(broker, '_invoke', invoke)
    assert await broker.resolve_acquisition_source(companion) == capability['url']
    async def missing(*args, **kwargs):
        raise broker.HFAssetError(broker.ERROR_MESSAGES['publish_disabled'])
    monkeypatch.setattr(broker, '_invoke', missing)
    assert await broker.resolve_acquisition_source(companion) is None
    async def mismatch(*args, **kwargs):
        raise broker.HFAssetError(broker.ERROR_MESSAGES['metadata'])
    monkeypatch.setattr(broker, '_invoke', mismatch)
    with pytest.raises(broker.HFAssetError, match='metadata'):
        await broker.resolve_acquisition_source(companion)
