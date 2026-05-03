from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import bioxp


def test_default_linkage_state_path_uses_bms_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state_root = tmp_path / 'bms-state'
    monkeypatch.setenv('BMS_DATA', str(state_root))
    monkeypatch.delenv('BIOXP_LINKAGE_STATE_PATH', raising=False)

    reloaded = importlib.reload(bioxp)
    try:
        assert reloaded.LINKAGE_STATE_PATH == state_root.resolve() / 'bioxp_linkage_url'
    finally:
        monkeypatch.delenv('BMS_DATA', raising=False)
        importlib.reload(reloaded)


@pytest.mark.asyncio
async def test_set_linkage_normalizes_and_persists_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state_path = tmp_path / 'bioxp_linkage_url'
    monkeypatch.setattr(bioxp, 'LINKAGE_STATE_PATH', state_path)
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', None)

    def fake_getaddrinfo(host: str, port: int | None, *args: object, **kwargs: object):
        assert host == 'robot'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('bioxp-runtime.example.invalid', port or 0))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

    response = await bioxp.set_linkage(bioxp.LinkageRequest(url='robot:8123/'))

    assert response['url'] == 'http://bioxp-runtime.example.invalid:8123'
    assert response['configured'] is True
    assert state_path.read_text(encoding='utf-8') == 'http://bioxp-runtime.example.invalid:8123'


def test_recommended_linkage_url_prefers_resolved_robot_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioxp, 'ROBOT_SSH_HOST', 'robot')
    monkeypatch.setattr(bioxp, 'ROBOT_DAEMON_PORT', 8123)
    monkeypatch.delenv('BIOXP_SERVER_URL', raising=False)

    def fake_getaddrinfo(host: str, port: int | None, *args: object, **kwargs: object):
        assert host == 'robot'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('bioxp-runtime.example.invalid', port or 0))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

    assert bioxp._recommended_linkage_url() == 'http://bioxp-runtime.example.invalid:8123'


def test_recommended_linkage_url_prefers_explicit_bioxp_server_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BIOXP_SERVER_URL', 'http://bioxp-runtime.example.invalid:8123/')
    monkeypatch.setattr(bioxp, 'ROBOT_SSH_HOST', 'robot')
    monkeypatch.setattr(bioxp, 'ROBOT_DAEMON_PORT', 8123)

    def failing_getaddrinfo(*args: object, **kwargs: object):
        raise AssertionError('robot hostname should not be resolved when BIOXP_SERVER_URL is set')

    monkeypatch.setattr(socket, 'getaddrinfo', failing_getaddrinfo)

    assert bioxp._recommended_linkage_url() == 'http://bioxp-runtime.example.invalid:8123'


def test_read_persisted_linkage_canonicalizes_robot_alias_and_rewrites_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / 'bioxp_linkage_url'
    state_path.write_text('http://robot:8123/\n', encoding='utf-8')
    monkeypatch.setattr(bioxp, 'LINKAGE_STATE_PATH', state_path)

    def fake_getaddrinfo(host: str, port: int | None, *args: object, **kwargs: object):
        assert host == 'robot'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('bioxp-runtime.example.invalid', port or 0))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

    assert bioxp._read_persisted_linkage() == 'http://bioxp-runtime.example.invalid:8123'
    assert state_path.read_text(encoding='utf-8') == 'http://bioxp-runtime.example.invalid:8123'


@pytest.mark.asyncio
async def test_disconnect_linkage_clears_persisted_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state_path = tmp_path / 'bioxp_linkage_url'
    state_path.write_text('http://robot:8123', encoding='utf-8')
    monkeypatch.setattr(bioxp, 'LINKAGE_STATE_PATH', state_path)
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', 'http://robot:8123')

    response = await bioxp.disconnect_linkage()

    assert response['configured'] is False
    assert response['url'] is None
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_get_status_without_linkage_returns_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', None)

    response = await bioxp.get_status()

    assert response['status'] == 'not_configured'
    assert response['hardware_connected'] is False
    assert response['linkage_configured'] is False
    assert response['recommended_url'] == bioxp._recommended_linkage_url()


@pytest.mark.asyncio
async def test_daemon_status_without_linkage_reports_runtime_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', None)

    response = await bioxp.daemon_status()

    assert response['linkage_configured'] is False
    assert response['linked_runtime_reachable'] is False
    assert response['hardware_connected'] is False
    assert response['admin_control_available'] is False
    assert response['maintenance_mode'] == 'robot-local'
    assert response['runtime_url'] is None
    assert response['running'] is False
    assert response['healthy'] is False
    assert 'linkage' in response['detail'].lower()


@pytest.mark.asyncio
async def test_daemon_status_uses_proxy_status_as_single_runtime_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', 'http://robot:8123')

    async def fake_proxy_request(method: str, path: str, timeout: float = 65.0, **_: object) -> dict:
        assert method == 'GET'
        assert path == '/status'
        assert timeout == 10.0
        return {
            'status': 'ok',
            'transport': 'proxy',
            'hardware_connected': True,
            'board_status': {'door_closed': 1},
        }

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.daemon_status()

    assert response['linkage_configured'] is True
    assert response['linked_runtime_reachable'] is True
    assert response['hardware_connected'] is True
    assert response['admin_control_available'] is False
    assert response['maintenance_mode'] == 'robot-local'
    assert response['runtime_url'] == 'http://robot:8123'
    assert response['running'] is True
    assert response['healthy'] is True
    assert response['detail']
    assert 'ssh' not in response['detail'].lower()


@pytest.mark.asyncio
async def test_daemon_status_reports_runtime_unreachable_without_process_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', 'http://robot:8123')

    async def fake_proxy_request(method: str, path: str, timeout: float = 65.0, **_: object) -> dict:
        raise HTTPException(status_code=503, detail='Cannot connect to BioXP hardware node at http://robot:8123.')

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    response = await bioxp.daemon_status()

    assert response['linkage_configured'] is True
    assert response['linked_runtime_reachable'] is False
    assert response['hardware_connected'] is False
    assert response['admin_control_available'] is False
    assert response['maintenance_mode'] == 'robot-local'
    assert response['running'] is False
    assert response['healthy'] is False
    assert response['proxy_error'] == {
        'status_code': 503,
        'detail': 'Cannot connect to BioXP hardware node at http://robot:8123.',
    }


@pytest.mark.asyncio
async def test_daemon_start_is_disabled_in_favor_of_robot_local_supervision() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await bioxp.daemon_start()

    assert exc_info.value.status_code == 409
    assert 'robot-local' in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_daemon_stop_is_disabled_in_favor_of_robot_local_supervision() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await bioxp.daemon_stop()

    assert exc_info.value.status_code == 409
    assert 'robot-local' in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_bioxp_capabilities_reports_robot_local_route_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', 'http://robot:8123')

    response = await bioxp.bioxp_capabilities()

    assert response['linkage_url'] == 'http://robot:8123'
    assert response['robot_hardware_assumption'] == 'functional_under_oem'
    assert response['truth_source'] == 'robot_local_oem_compat_layer'
    assert response['bms_role'] == 'thin_operator_surface'
    assert response['bms_proxy_routes']['/oem-compat/capabilities/test-prep'] is True
    assert response['bms_proxy_routes']['/liquid/status'] is True
    assert response['bms_proxy_routes']['/motion/reference/status'] is True
    assert response['bms_proxy_routes']['/motion/axes/current'] is True
    assert response['bms_proxy_routes']['/camera/stream_state'] is True
    assert response['bms_proxy_routes']['/vision/inspect'] is True
    assert response['robot_local_expected_routes']['/liquid/aspirate'] is True
    assert response['robot_local_expected_routes']['/motion/axes/current'] is True


@pytest.mark.asyncio
async def test_bioxp_oem_test_prep_capabilities_proxy_to_robot_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None, dict | None, float]] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append((method, path, json_data, params, timeout))
        return {'schema_version': 'bioxp.oem_compat.capability_matrix.v1'}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    assert await bioxp.bioxp_oem_test_prep_capabilities() == {'schema_version': 'bioxp.oem_compat.capability_matrix.v1'}
    assert calls == [('GET', '/oem-compat/capabilities/test-prep', None, None, 20.0)]


@pytest.mark.asyncio
async def test_liquid_routes_proxy_to_robot_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None, dict | None, float]] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append((method, path, json_data, params, timeout))
        return {'ok': True, 'path': path}

    class RequestStub:
        async def json(self):
            return {'volume_ul': 12.5, 'source': {'location_id': 'reagent_rack', 'well_id': 'A1'}}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    assert await bioxp.liquid_status() == {'ok': True, 'path': '/liquid/status'}
    assert await bioxp.liquid_init(RequestStub()) == {'ok': True, 'path': '/liquid/init'}
    assert await bioxp.liquid_tip(RequestStub()) == {'ok': True, 'path': '/liquid/tip'}
    assert await bioxp.liquid_aspirate(RequestStub()) == {'ok': True, 'path': '/liquid/aspirate'}
    assert await bioxp.liquid_dispense(RequestStub()) == {'ok': True, 'path': '/liquid/dispense'}
    assert await bioxp.liquid_mix(RequestStub()) == {'ok': True, 'path': '/liquid/mix'}

    assert [call[1] for call in calls] == [
        '/liquid/status',
        '/liquid/init',
        '/liquid/tip',
        '/liquid/aspirate',
        '/liquid/dispense',
        '/liquid/mix',
    ]
    assert calls[0] == ('GET', '/liquid/status', None, None, 20.0)
    assert calls[3][0] == 'POST'
    assert calls[3][2]['volume_ul'] == 12.5


@pytest.mark.asyncio
async def test_motion_reference_camera_and_vision_routes_proxy_to_robot_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None, dict | None, float]] = []

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append((method, path, json_data, params, timeout))
        return {'ok': True, 'path': path, 'params': params}

    class RequestStub:
        async def json(self):
            return {'axes': ['x'], 'reason': 'operator_verified'}

    monkeypatch.setattr(bioxp, 'proxy_request', fake_proxy_request)

    assert await bioxp.motion_reference_status('x,y,z,g,door') == {'ok': True, 'path': '/motion/reference/status', 'params': {'axes': 'x,y,z,g,door'}}
    assert await bioxp.motion_reference_mark_referenced(RequestStub()) == {'ok': True, 'path': '/motion/reference/mark_referenced', 'params': None}
    assert await bioxp.motion_reference_mark_desynced(RequestStub()) == {'ok': True, 'path': '/motion/reference/mark_desynced', 'params': None}
    assert await bioxp.motion_axes_current(RequestStub()) == {'ok': True, 'path': '/motion/axes/current', 'params': None}
    assert await bioxp.camera_stream_state() == {'ok': True, 'path': '/camera/stream_state', 'params': None}
    assert await bioxp.vision_inspect(RequestStub()) == {'ok': True, 'path': '/vision/inspect', 'params': None}
    assert await bioxp.vision_barcode_read(RequestStub()) == {'ok': True, 'path': '/vision/barcode/read', 'params': None}

    assert calls == [
        ('GET', '/motion/reference/status', None, {'axes': 'x,y,z,g,door'}, 20.0),
        ('POST', '/motion/reference/mark_referenced', {'axes': ['x'], 'reason': 'operator_verified'}, None, 30.0),
        ('POST', '/motion/reference/mark_desynced', {'axes': ['x'], 'reason': 'operator_verified'}, None, 30.0),
        ('POST', '/motion/axes/current', {'axes': ['x'], 'reason': 'operator_verified'}, None, 35.0),
        ('GET', '/camera/stream_state', None, None, 20.0),
        ('POST', '/vision/inspect', {'axes': ['x'], 'reason': 'operator_verified'}, None, 45.0),
        ('POST', '/vision/barcode/read', {'axes': ['x'], 'reason': 'operator_verified'}, None, 45.0),
    ]
