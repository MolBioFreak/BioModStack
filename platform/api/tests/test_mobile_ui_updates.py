from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from routers import mobile_ui_updates


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(mobile_ui_updates.router, prefix='/api')
    return TestClient(app)


def test_manifest_endpoint_returns_channel_manifest(monkeypatch, tmp_path: Path) -> None:
    updates_root = tmp_path / 'mobile-ui-updates'
    manifest_path = updates_root / 'channels' / 'phone' / 'manifest.json'
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        '{"channel": "phone", "version": "2026.04.20-phone-01", "download_url": "https://example.test/api/mobile-ui/bundles/phone/2026.04.20-phone-01.zip", "sha256": "abc123", "shell_api_version": 1}',
        encoding='utf-8',
    )
    monkeypatch.setattr(mobile_ui_updates, 'get_mobile_ui_updates_dir', lambda: updates_root)

    with build_client() as client:
        response = client.get('/api/mobile-ui/channels/phone/manifest')

    assert response.status_code == 200
    assert response.json()['channel'] == 'phone'
    assert response.json()['version'] == '2026.04.20-phone-01'


def test_bundle_endpoint_serves_versioned_zip(monkeypatch, tmp_path: Path) -> None:
    updates_root = tmp_path / 'mobile-ui-updates'
    bundle_path = updates_root / 'bundles' / 'phone' / '2026.04.20-phone-01.zip'
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_bytes(b'fake-zip-payload')
    monkeypatch.setattr(mobile_ui_updates, 'get_mobile_ui_updates_dir', lambda: updates_root)

    with build_client() as client:
        response = client.get('/api/mobile-ui/bundles/phone/2026.04.20-phone-01.zip')

    assert response.status_code == 200
    assert response.content == b'fake-zip-payload'
    assert response.headers['content-type'] == 'application/zip'


def test_file_endpoint_serves_versioned_asset(monkeypatch, tmp_path: Path) -> None:
    updates_root = tmp_path / 'mobile-ui-updates'
    asset_path = updates_root / 'files' / 'phone' / '2026.04.20-phone-01' / 'assets' / 'index-abc.js'
    asset_path.parent.mkdir(parents=True)
    asset_path.write_text('console.log("hello")', encoding='utf-8')
    monkeypatch.setattr(mobile_ui_updates, 'get_mobile_ui_updates_dir', lambda: updates_root)

    with build_client() as client:
        response = client.get('/api/mobile-ui/files/phone/2026.04.20-phone-01/assets/index-abc.js')

    assert response.status_code == 200
    assert response.text == 'console.log("hello")'


def test_manifest_endpoint_rejects_unknown_channel_shape(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mobile_ui_updates, 'get_mobile_ui_updates_dir', lambda: tmp_path)

    with build_client() as client:
        response = client.get('/api/mobile-ui/channels/../manifest/manifest')

    assert response.status_code in {400, 404}
