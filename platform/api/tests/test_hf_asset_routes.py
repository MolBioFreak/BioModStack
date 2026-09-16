"""Actual safe HF status routes; network/provider is a declared controller double."""
import json

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from routers.execution_targets import router
from services.remote_execution import hf_assets


@pytest.fixture
def app(monkeypatch):
    for key in hf_assets._KEYS:
        monkeypatch.delenv(key, raising=False)
    value = FastAPI()
    value.include_router(router, prefix='/api/execution-targets')
    return value


def configure(tmp_path, monkeypatch):
    token = tmp_path / 'hf-credential'
    token.write_text('hf_fixturecredentialnotreal12345')
    token.chmod(0o600)
    monkeypatch.setenv('BMS_HF_ASSET_BUCKET', 'fixture/private-assets')
    monkeypatch.setenv('BMS_HF_TOKEN_FILE', str(token))
    return token


@pytest.mark.asyncio
async def test_status_is_safe_and_not_authenticated_by_local_configuration(app, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Read-only local status must not invoke cloud operations')
    monkeypatch.setattr(hf_assets, '_invoke', forbidden)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        unconfigured = await client.get('/api/execution-targets/providers/huggingface')
        assert unconfigured.status_code == 200
        assert unconfigured.json()['mode'] == 'ssh'
        token = configure(tmp_path, monkeypatch)
        result = await client.get('/api/execution-targets/providers/huggingface')
        assert result.status_code == 200
        assert result.json()['configured'] is True
        assert result.json()['credential_configured'] is True
        assert result.json()['available'] is False
        assert result.json()['mode'] == 'hf_preferred'
        assert str(token) not in result.text and token.read_text() not in result.text
        assert set(result.json()) == {'configured', 'available', 'bucket', 'mode', 'credential_configured', 'allow_publish', 'message'}


@pytest.mark.asyncio
async def test_explicit_check_is_read_only_and_reports_access_not_science(app, tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    calls = []
    async def invoke(request, **kwargs):
        calls.append(request)
        return {'available': True}
    monkeypatch.setattr(hf_assets, '_invoke', invoke)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post('/api/execution-targets/providers/huggingface/check')
    assert result.status_code == 200 and result.json()['available'] is True
    assert calls == [{'action': 'check'}]
    assert 'no objects were changed' in result.json()['message']
    assert 'scientific_ready' not in result.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['token', 'url', 'bucket', 'path', 'allow_publish'])
async def test_check_cannot_supply_credentials_or_acquisition_overrides(app, monkeypatch, key):
    async def forbidden():
        raise AssertionError('Invalid request must not reach controller')
    monkeypatch.setattr(hf_assets, 'check_connection', forbidden)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post('/api/execution-targets/providers/huggingface/check', json={key: 'untrusted-fixture'})
    assert result.status_code == 422


@pytest.mark.asyncio
async def test_bad_local_config_is_visible_not_ssh_fallback(app, monkeypatch):
    monkeypatch.setenv('BMS_HF_ASSET_BUCKET', 'fixture/private-assets')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        result = await client.get('/api/execution-targets/providers/huggingface')
    assert result.status_code == 200
    assert result.json()['mode'] == 'hf_preferred' and result.json()['available'] is False
    assert result.json()['credential_configured'] is False


@pytest.mark.asyncio
async def test_response_contract_does_not_serialize_undeclared_secret_fields(app, monkeypatch):
    status = hf_assets.readiness()
    monkeypatch.setattr(hf_assets, 'readiness', lambda: {**status, 'token': 'secret-fixture-do-not-return'})
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
        result = await client.get('/api/execution-targets/providers/huggingface')
    assert result.status_code == 500
    assert 'secret-fixture' not in result.text
