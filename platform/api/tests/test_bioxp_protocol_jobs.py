from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import bioxp


def _json_request(payload: dict[str, Any]) -> Request:
    body = json.dumps(payload).encode('utf-8')

    async def receive() -> dict[str, Any]:
        return {'type': 'http.request', 'body': body, 'more_body': False}

    return Request({'type': 'http', 'method': 'POST', 'path': '/'}, receive)


@pytest.mark.asyncio
async def test_protocol_compile_route_delegates_to_proxy_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_proxy_request(method: str, path: str, json_data: dict[str, Any] | None = None, params: dict[str, Any] | None = None, timeout: float = 65.0) -> dict[str, Any]:
        captured.update({'method': method, 'path': path, 'json_data': json_data, 'params': params, 'timeout': timeout})
        return {'ok': True}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.protocol_compile(_json_request({'source_type': 'oem_xml', 'xml_path': '/home/molbiofreak/bioxp_re/testdata/oem_xml/demo.xml'}))

    assert response == {'ok': True}
    assert captured == {
        'method': 'POST',
        'path': '/protocol/compile',
        'json_data': {'source_type': 'oem_xml', 'xml_path': '/home/molbiofreak/bioxp_re/testdata/oem_xml/demo.xml'},
        'params': None,
        'timeout': 45.0,
    }


@pytest.mark.asyncio
async def test_protocol_execute_route_delegates_to_proxy_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_proxy_request(method: str, path: str, json_data: dict[str, Any] | None = None, params: dict[str, Any] | None = None, timeout: float = 65.0) -> dict[str, Any]:
        captured.update({'method': method, 'path': path, 'json_data': json_data, 'params': params, 'timeout': timeout})
        return {'job_id': 'protocol-123'}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    request = _json_request({'source_type': 'native', 'document': {'protocol_id': 'demo'}, 'dry_run': True})
    response = await bioxp.protocol_execute(request)

    assert response == {'job_id': 'protocol-123'}
    assert captured['method'] == 'POST'
    assert captured['path'] == '/protocol/execute'
    assert captured['json_data'] == {'source_type': 'native', 'document': {'protocol_id': 'demo'}, 'dry_run': True}
    assert captured['timeout'] == 90.0


@pytest.mark.asyncio
async def test_protocol_jobs_route_passes_limit_param(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_proxy_request(method: str, path: str, json_data: dict[str, Any] | None = None, params: dict[str, Any] | None = None, timeout: float = 65.0) -> dict[str, Any]:
        captured.update({'method': method, 'path': path, 'json_data': json_data, 'params': params, 'timeout': timeout})
        return {'rows': []}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.protocol_jobs(limit=7)

    assert response == {'rows': []}
    assert captured == {
        'method': 'GET',
        'path': '/protocol/jobs',
        'json_data': None,
        'params': {'limit': 7},
        'timeout': 30.0,
    }


@pytest.mark.asyncio
async def test_protocol_job_detail_route_delegates_to_proxy_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_proxy_request(method: str, path: str, json_data: dict[str, Any] | None = None, params: dict[str, Any] | None = None, timeout: float = 65.0) -> dict[str, Any]:
        captured.update({'method': method, 'path': path, 'json_data': json_data, 'params': params, 'timeout': timeout})
        return {'job_id': 'protocol-123'}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.protocol_job_detail('protocol-123')

    assert response == {'job_id': 'protocol-123'}
    assert captured == {
        'method': 'GET',
        'path': '/protocol/jobs/protocol-123',
        'json_data': None,
        'params': None,
        'timeout': 30.0,
    }


@pytest.mark.asyncio
async def test_protocol_job_review_route_delegates_to_proxy_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_proxy_request(method: str, path: str, json_data: dict[str, Any] | None = None, params: dict[str, Any] | None = None, timeout: float = 65.0) -> dict[str, Any]:
        captured.update({'method': method, 'path': path, 'json_data': json_data, 'params': params, 'timeout': timeout})
        return {'status': 'completed'}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.protocol_job_review('protocol-123', _json_request({'reviewer': 'operator', 'note': 'approved'}))

    assert response == {'status': 'completed'}
    assert captured == {
        'method': 'POST',
        'path': '/protocol/jobs/protocol-123/review',
        'json_data': {'reviewer': 'operator', 'note': 'approved'},
        'params': None,
        'timeout': 90.0,
    }
