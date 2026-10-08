from __future__ import annotations

import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import bioxp


@pytest.mark.asyncio
async def test_set_linkage_normalizes_and_persists_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state_path = tmp_path / 'bioxp_linkage_url'
    monkeypatch.setattr(bioxp, 'LINKAGE_STATE_PATH', state_path)
    monkeypatch.setattr(bioxp, '_GLOBAL_LINKAGE_URL', None)

    response = await bioxp.set_linkage(bioxp.LinkageRequest(url='robot:8123/'))

    assert response['url'] == 'http://robot:8123'
    assert response['configured'] is True
    assert state_path.read_text(encoding='utf-8') == 'http://robot:8123'


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
    assert response['recommended_url'] == f'http://{bioxp.ROBOT_SSH_HOST}:{bioxp.ROBOT_DAEMON_PORT}'
