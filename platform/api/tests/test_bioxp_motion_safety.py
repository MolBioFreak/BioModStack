from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import bioxp


class JsonRequest:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    async def json(self) -> dict[str, Any]:
        return self.payload


@pytest.mark.asyncio
async def test_axes_current_defaults_to_oem_idle_run_and_standby(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, float]] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append((method, path, json_data, params, timeout))
        return {'ok': True, 'payload': json_data}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.motion_axes_current(JsonRequest({'axes': ['x']}))

    assert response['payload'] == {'axes': ['x'], 'run_current': 10, 'standby_current': 10}
    assert calls == [('POST', '/motion/axes/current', {'axes': ['x'], 'run_current': 10, 'standby_current': 10}, None, 35.0)]


@pytest.mark.asyncio
async def test_axes_current_allows_explicit_oem_run_current_with_idle_standby(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, float]] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append((method, path, json_data, params, timeout))
        return {'ok': True, 'payload': json_data}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.motion_axes_current(JsonRequest({'axes': ['x'], 'run_current': 31}))

    assert response['payload'] == {'axes': ['x'], 'run_current': 31, 'standby_current': 10}
    assert calls == [('POST', '/motion/axes/current', {'axes': ['x'], 'run_current': 31, 'standby_current': 10}, None, 35.0)]


@pytest.mark.asyncio
async def test_axes_current_rejects_hot_standby_without_commissioning_override(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_proxy_request(*args: object, **kwargs: object):
        raise AssertionError('unsafe hot standby current must not be proxied')

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    with pytest.raises(HTTPException) as exc_info:
        await bioxp.motion_axes_current(JsonRequest({'axes': ['x'], 'run_current': 31, 'standby_current': 31}))

    assert exc_info.value.status_code == 400
    assert 'standby_current above OEM idle' in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_absolute_move_requires_referenced_axis_before_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append(path)
        if path == '/motion/reference/status':
            return {'rows': {'x': {'state': 'unknown'}}}
        if path == '/motion/range/status':
            raise AssertionError('range should not be queried after unreferenced axis blocks')
        raise AssertionError(f'absolute move should not be proxied to {path}')

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    with pytest.raises(HTTPException) as exc_info:
        await bioxp.move_axis_absolute(JsonRequest({'axis': 'x', 'position_steps': 1000}))

    assert exc_info.value.status_code == 409
    assert 'reference state is unknown' in str(exc_info.value.detail)
    assert calls == ['/motion/reference/status']


@pytest.mark.asyncio
async def test_absolute_move_blocks_door_when_runtime_range_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append(path)
        if path == '/motion/reference/status':
            return {'rows': {'door': {'state': 'referenced'}}}
        if path == '/motion/range/status':
            return {'rows': {'door': {}}}
        raise AssertionError(f'door absolute move should not be proxied to {path}')

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    with pytest.raises(HTTPException) as exc_info:
        await bioxp.move_axis_absolute(JsonRequest({'axis': 'door', 'position_steps': 100}))

    assert exc_info.value.status_code == 409
    assert 'configured range is unavailable' in str(exc_info.value.detail)
    assert calls == ['/motion/reference/status', '/motion/range/status']


@pytest.mark.asyncio
async def test_absolute_move_proxies_only_when_referenced_and_within_runtime_range(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, float]] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append((method, path, json_data, params, timeout))
        if path == '/motion/reference/status':
            return {'rows': {'x': {'state': 'referenced'}}}
        if path == '/motion/range/status':
            return {'rows': {'x': {'limits': {'min': 0, 'max': 91919}}}}
        if path == '/motion/axis/absolute':
            return {'ok': True, 'payload': json_data}
        raise AssertionError(path)

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.move_axis_absolute(JsonRequest({'axis': 'x', 'position_steps': 1000}))

    assert response == {'ok': True, 'payload': {'axis': 'x', 'position_steps': 1000}}
    assert [call[1] for call in calls] == ['/motion/reference/status', '/motion/range/status', '/motion/axis/absolute']
    assert calls[-1] == ('POST', '/motion/axis/absolute', {'axis': 'x', 'position_steps': 1000}, None, 65.0)


@pytest.mark.asyncio
async def test_z_positive_relative_blocks_when_reference_unknown_and_position_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append(path)
        if path == '/motion/reference/status':
            return {'rows': {'z': {'state': 'unknown'}}}
        if path == '/motion/range/status':
            return {'rows': {'z': {'position': -150000, 'limits': {'min': 0, 'max': 160000}}}}
        raise AssertionError(f'unsafe z positive move should not be proxied to {path}')

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    with pytest.raises(HTTPException) as exc_info:
        await bioxp.move_axis_relative(JsonRequest({'axis': 'z', 'steps': 50}))

    assert exc_info.value.status_code == 409
    assert 'Z positive/down move blocked' in str(exc_info.value.detail)
    assert calls == ['/motion/reference/status', '/motion/range/status']


@pytest.mark.asyncio
async def test_switch_home_requires_capture_bundle_and_operator_note(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_proxy_request(*args: object, **kwargs: object):
        raise AssertionError('home without evidence must not be proxied')

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    with pytest.raises(HTTPException) as exc_info:
        await bioxp.home_axis(JsonRequest({'axis': 'g', 'timeout_s': 9.0}))

    assert exc_info.value.status_code == 400
    assert 'capture_bundle=true' in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_robot_capabilities_reports_missing_expected_robot_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        assert method == 'GET'
        assert path == '/openapi.json'
        return {'openapi': '3.1.0', 'paths': {'/status': {}, '/motion/range/status': {}}}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', 'http://robot:8123')

    response = await bioxp.robot_capabilities()

    assert response['schema_version'] == 'bioxp.robot_capabilities.v1'
    assert response['robot_openapi_reachable'] is True
    assert response['supported_routes']['/motion/range/status'] is True
    assert response['missing_expected_routes']['/motion/reference/status'] is True
    assert response['missing_expected_routes']['/motion/axes/current'] is True
