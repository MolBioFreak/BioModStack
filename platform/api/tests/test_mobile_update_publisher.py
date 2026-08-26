from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]


def load_publisher_module():
    app_path = API_ROOT / "mobile_update_publisher_app.py"
    assert app_path.is_file(), "the independent mobile update publisher app is missing"
    spec = importlib.util.spec_from_file_location("mobile_update_publisher_app_test", app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publisher_exposes_only_health_and_mobile_update_routes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BMS_BUILD_SHA", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("BMS_BUILD_ID", "test-0123456789ab")
    monkeypatch.setenv("BMS_BUILD_TIME", "2026-08-26T12:00:00Z")
    monkeypatch.setenv("BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS", "testclient")
    monkeypatch.setenv("BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS", "christian@example.test")
    module = load_publisher_module()

    ui_root = tmp_path / "mobile-ui-updates"
    ui_manifest = ui_root / "channels" / "phone" / "manifest.json"
    ui_manifest.parent.mkdir(parents=True)
    ui_manifest.write_text(
        json.dumps({
            "channel": "phone",
            "version": "2026.08.26-phone-01",
            "descriptor": {
                "version": "2026.08.26-phone-01",
                "shellApiVersion": 1,
                "entryCss": [],
                "entryJs": ["assets/index.js"],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(module.mobile_ui_updates, "get_mobile_ui_updates_dir", lambda: ui_root)

    apk_root = tmp_path / "mobile-apk-updates"
    apk_channel = apk_root / "stable"
    apk_channel.mkdir(parents=True)
    apk_bytes = b"biomodstack-mobile-update-publisher-test"
    apk_sha = sha256(apk_bytes).hexdigest()
    apk_filename = f"biomodstack-0.4.4-vc404-{apk_sha}.apk"
    (apk_channel / apk_filename).write_bytes(apk_bytes)
    (apk_channel / "manifest.json").write_text(
        json.dumps({
            "channel": "stable",
            "version_code": 404,
            "version_name": "0.4.4",
            "min_sdk": 24,
            "sha256": apk_sha,
            "size_bytes": len(apk_bytes),
            "filename": apk_filename,
            "package_id": "org.biomodstack.mobile",
            "signing_certificate_sha256": "a" * 64,
            "published_at": "2026-08-26T12:00:00Z",
            "changelog": ["Independent update publisher."],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(module.mobile_apk_updates, "get_mobile_apk_updates_dir", lambda: apk_root)

    headers = {"Tailscale-User-Login": "christian@example.test"}
    with TestClient(module.app, base_url="https://updates.example.test", headers=headers) as client:
        health = client.get("/health")
        ui = client.get("/api/mobile-ui/channels/phone/manifest")
        apk = client.get("/api/mobile-apk/channels/stable/manifest")
        unrelated = client.get("/api/jobs")
        preflight = client.options(
            "/api/mobile-ui/channels/phone/manifest",
            headers={
                "Origin": "https://localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )

    assert health.status_code == 200
    assert health.json() == {
        "status": "healthy",
        "service": "biomodstack-mobile-update-publisher",
        "build": {
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "build_id": "test-0123456789ab",
            "build_time": "2026-08-26T12:00:00Z",
        },
    }
    assert ui.status_code == 200
    assert ui.json()["channel"] == "phone"
    assert apk.status_code == 200
    assert apk.json()["version_code"] == 404
    assert unrelated.status_code == 404
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://localhost"
    assert preflight.headers["access-control-allow-private-network"] == "true"
