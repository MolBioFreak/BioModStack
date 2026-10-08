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
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('100.124.140.56', port or 0))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

    response = await bioxp.set_linkage(bioxp.LinkageRequest(url='robot:8123/'))

    assert response['url'] == 'http://100.124.140.56:8123'
    assert response['configured'] is True
    assert state_path.read_text(encoding='utf-8') == 'http://100.124.140.56:8123'


def test_recommended_linkage_url_prefers_resolved_robot_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bioxp, 'ROBOT_SSH_HOST', 'robot')
    monkeypatch.setattr(bioxp, 'ROBOT_DAEMON_PORT', 8123)
    monkeypatch.delenv('BIOXP_SERVER_URL', raising=False)

    def fake_getaddrinfo(host: str, port: int | None, *args: object, **kwargs: object):
        assert host == 'robot'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('100.124.140.56', port or 0))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

    assert bioxp._recommended_linkage_url() == 'http://100.124.140.56:8123'


def test_recommended_linkage_url_prefers_explicit_bioxp_server_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BIOXP_SERVER_URL', 'http://100.124.140.56:8123/')
    monkeypatch.setattr(bioxp, 'ROBOT_SSH_HOST', 'robot')
    monkeypatch.setattr(bioxp, 'ROBOT_DAEMON_PORT', 8123)

    def failing_getaddrinfo(*args: object, **kwargs: object):
        raise AssertionError('robot hostname should not be resolved when BIOXP_SERVER_URL is set')

    monkeypatch.setattr(socket, 'getaddrinfo', failing_getaddrinfo)

    assert bioxp._recommended_linkage_url() == 'http://100.124.140.56:8123'


def test_read_persisted_linkage_canonicalizes_robot_alias_and_rewrites_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / 'bioxp_linkage_url'
    state_path.write_text('http://robot:8123/\n', encoding='utf-8')
    monkeypatch.setattr(bioxp, 'LINKAGE_STATE_PATH', state_path)

    def fake_getaddrinfo(host: str, port: int | None, *args: object, **kwargs: object):
        assert host == 'robot'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('100.124.140.56', port or 0))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

    assert bioxp._read_persisted_linkage() == 'http://100.124.140.56:8123'
    assert state_path.read_text(encoding='utf-8') == 'http://100.124.140.56:8123'


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
