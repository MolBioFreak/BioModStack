from __future__ import annotations

import importlib.util
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from threading import BoundedSemaphore, Event, Lock, Thread

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

router_spec = importlib.util.spec_from_file_location('mobile_apk_updates_router', API_ROOT / 'routers' / 'mobile_apk_updates.py')
assert router_spec and router_spec.loader
mobile_apk_updates = importlib.util.module_from_spec(router_spec)
router_spec.loader.exec_module(mobile_apk_updates)
from services.mobile_apk_updates import (
    MAX_APK_SIZE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_VERIFIED_CACHE_ENTRIES,
    MobileApkReleaseIntegrityError,
    MobileApkUpdateService,
)


def write_release(root: Path, apk_bytes: bytes = b'biomodstack-apk-v200') -> tuple[Path, dict[str, object]]:
    channel = root / 'stable'
    channel.mkdir(parents=True)
    apk = channel / f'biomodstack-0.2.0-vc200-{sha256(apk_bytes).hexdigest()}.apk'
    apk.write_bytes(apk_bytes)
    manifest: dict[str, object] = {
        'channel': 'stable', 'version_code': 200, 'version_name': '0.2.0', 'min_sdk': 24,
        'sha256': sha256(apk_bytes).hexdigest(), 'size_bytes': len(apk_bytes), 'filename': apk.name,
        'package_id': 'org.biomodstack.mobile',
        'signing_certificate_sha256': 'be29e786d6fa625465d2c63bde43e118a00f176314f31d34cf2e8e5354cc855e',  # pragma: allowlist secret
        'published_at': '2026-07-18T12:00:00Z', 'changelog': ['Secure native updater.'],
    }
    (channel / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return apk, manifest


def client_for(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    identity: str | None = 'christian@example.com',
    trusted_proxy: str = 'testclient',
) -> TestClient:
    monkeypatch.setenv('BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS', trusted_proxy)
    monkeypatch.setenv('BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS', 'christian@example.com')
    monkeypatch.setattr(mobile_apk_updates, 'get_mobile_apk_updates_dir', lambda: root)
    app = FastAPI()
    app.include_router(mobile_apk_updates.router, prefix='/api')
    headers = {'Tailscale-User-Login': identity} if identity is not None else {}
    return TestClient(app, base_url='https://api.example.test', headers=headers)


def test_manifest_and_exact_range_download_are_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk, expected = write_release(tmp_path)
    with client_for(tmp_path, monkeypatch) as client:
        manifest = client.get('/api/mobile-apk/channels/stable/manifest')
        assert manifest.status_code == 200
        assert manifest.headers['cache-control'] == 'no-store'
        assert manifest.json() == {**expected, 'download_url': f'/api/mobile-apk/channels/stable/files/{apk.name}'}
        whole = client.get(manifest.json()['download_url'])
        assert whole.status_code == 200
        assert whole.content == apk.read_bytes()
        assert whole.headers['accept-ranges'] == 'bytes'
        partial = client.get(manifest.json()['download_url'], headers={'Range': 'bytes=2-7'})
        assert partial.status_code == 206
        assert partial.content == apk.read_bytes()[2:8]
        assert partial.headers['content-range'] == f'bytes 2-7/{apk.stat().st_size}'
        suffix = client.get(manifest.json()['download_url'], headers={'Range': 'bytes=-4'})
        assert suffix.status_code == 206
        assert suffix.content == apk.read_bytes()[-4:]
        invalid = client.get(manifest.json()['download_url'], headers={'Range': 'bytes=999-'})
        assert invalid.status_code == 416
        assert invalid.headers['content-range'] == f'bytes */{apk.stat().st_size}'


def test_missing_and_forged_tailscale_identity_are_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    write_release(tmp_path)

    with client_for(tmp_path, monkeypatch, identity=None) as client:
        response = client.get('/api/mobile-apk/channels/stable/manifest')
        assert response.status_code == 401

    with client_for(tmp_path, monkeypatch, identity='intruder@example.com') as client:
        response = client.get('/api/mobile-apk/channels/stable/manifest')
        assert response.status_code == 403

    with client_for(
        tmp_path,
        monkeypatch,
        identity='christian@example.com',
        trusted_proxy='127.0.0.1',
    ) as client:
        response = client.get('/api/mobile-apk/channels/stable/manifest')
        assert response.status_code == 401


def test_apk_update_authentication_fails_closed_when_proxy_policy_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    write_release(tmp_path)
    monkeypatch.delenv('BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS', raising=False)
    monkeypatch.delenv('BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS', raising=False)
    monkeypatch.setattr(mobile_apk_updates, 'get_mobile_apk_updates_dir', lambda: tmp_path)
    app = FastAPI()
    app.include_router(mobile_apk_updates.router, prefix='/api')

    with TestClient(app, base_url='https://api.example.test') as client:
        response = client.get(
            '/api/mobile-apk/channels/stable/manifest',
            headers={'Tailscale-User-Login': 'christian@example.com'},
        )
    assert response.status_code == 503


def test_manifest_rejects_size_json_shape_and_strict_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk, manifest = write_release(tmp_path)
    path = tmp_path / 'stable' / 'manifest.json'
    with client_for(tmp_path, monkeypatch) as client:
        invalid_values = [
            {'channel': 'beta'}, {'filename': '../evil.apk'}, {'package_id': 'evil.example'},
            {'version_code': True}, {'version_code': 0}, {'size_bytes': MAX_APK_SIZE_BYTES + 1},
            {'sha256': 'bad'}, {'version_name': 'v' * 129}, {'changelog': ['x'] * 51},
        ]
        for override in invalid_values:
            path.write_text(json.dumps({**manifest, **override}), encoding='utf-8')
            assert client.get('/api/mobile-apk/channels/stable/manifest').status_code == 503, override
        path.write_text('[]', encoding='utf-8')
        assert client.get('/api/mobile-apk/channels/stable/manifest').status_code == 503
        path.write_bytes(b'{' + b'x' * MAX_MANIFEST_BYTES)
        assert client.get('/api/mobile-apk/channels/stable/manifest').status_code == 503
        path.write_text(json.dumps(manifest), encoding='utf-8')
        apk.write_bytes(b'tampered')
        assert client.get('/api/mobile-apk/channels/stable/manifest').status_code == 503


def test_download_refuses_wrong_filename_and_symlink_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk, manifest = write_release(tmp_path)
    with client_for(tmp_path, monkeypatch) as client:
        assert client.get('/api/mobile-apk/channels/stable/files/wrong.apk').status_code == 404
        outside = tmp_path.parent / 'outside.apk'
        outside.write_bytes(apk.read_bytes())
        apk.unlink()
        apk.symlink_to(outside)
        assert client.get('/api/mobile-apk/channels/stable/manifest').status_code == 503


def test_wrong_filename_and_malformed_range_are_rejected_before_verified_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    apk, _ = write_release(tmp_path)
    snapshot_copies = 0
    real_copy = MobileApkUpdateService._copy_verified_apk

    def counting_copy(self, release, apk_file):
        nonlocal snapshot_copies
        snapshot_copies += 1
        return real_copy(self, release, apk_file)

    monkeypatch.setattr(MobileApkUpdateService, '_copy_verified_apk', counting_copy)
    with client_for(tmp_path, monkeypatch) as client:
        wrong_filename = client.get('/api/mobile-apk/channels/stable/files/wrong.apk')
        malformed_range = client.get(
            f'/api/mobile-apk/channels/stable/files/{apk.name}',
            headers={'Range': 'bytes=not-a-range'},
        )

    assert wrong_filename.status_code == 404
    assert malformed_range.status_code == 416
    assert malformed_range.headers['content-range'] == f'bytes */{apk.stat().st_size}'
    assert snapshot_copies == 0


def test_open_release_hashes_the_opened_inode_once_and_streams_that_handle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk, _ = write_release(tmp_path)
    service = MobileApkUpdateService(tmp_path)
    release, handle = service.open_release('stable')
    try:
        original_inode = handle.fileno()
        apk.unlink()
        apk.write_bytes(b'mutable-substitute')
        assert handle.fileno() == original_inode
        assert handle.read() == b'biomodstack-apk-v200'
        assert release.size_bytes == len(b'biomodstack-apk-v200')
    finally:
        handle.close()


def test_release_reverification_rejects_same_inode_size_and_mtime_tampering(tmp_path: Path) -> None:
    apk, _ = write_release(tmp_path)
    service = MobileApkUpdateService(tmp_path)
    service.load_release('stable')
    original_stat = apk.stat()
    apk.write_bytes(b'x' * original_stat.st_size)
    os.utime(apk, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    with pytest.raises(MobileApkReleaseIntegrityError, match='checksum'):
        service.load_release('stable')


def test_manifest_verification_cache_is_bounded_and_avoids_rehashing_unchanged_inode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import services.mobile_apk_updates as service_module

    write_release(tmp_path)
    service = MobileApkUpdateService(tmp_path)
    service._verified_files.clear()
    real_sha256 = service_module.sha256
    digest_creations = 0

    def counting_sha256(*args, **kwargs):
        nonlocal digest_creations
        digest_creations += 1
        return real_sha256(*args, **kwargs)

    monkeypatch.setattr(service_module, 'sha256', counting_sha256)
    service.load_release('stable')
    service.load_release('stable')

    assert digest_creations == 1
    assert len(service._verified_files) <= MAX_VERIFIED_CACHE_ENTRIES


def test_verified_snapshot_copy_admits_at_most_two_concurrent_hashers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import services.mobile_apk_updates as service_module

    write_release(tmp_path)
    service = MobileApkUpdateService(tmp_path)
    monkeypatch.setattr(service, '_snapshot_slots', BoundedSemaphore(2))
    real_sha256 = service_module.sha256
    entered = 0
    entered_lock = Lock()
    two_entered = Event()
    release_hashers = Event()
    errors: list[BaseException] = []

    def blocking_sha256(*args, **kwargs):
        nonlocal entered
        with entered_lock:
            entered += 1
            if entered == 2:
                two_entered.set()
        if not release_hashers.wait(timeout=5):
            raise TimeoutError('snapshot hasher release timed out')
        return real_sha256(*args, **kwargs)

    def worker() -> None:
        try:
            _, handle = service.open_release('stable')
            handle.close()
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)

    monkeypatch.setattr(service_module, 'sha256', blocking_sha256)
    workers = [Thread(target=worker) for _ in range(3)]
    for worker_thread in workers:
        worker_thread.start()
    assert two_entered.wait(timeout=5)
    with entered_lock:
        assert entered == 2
    release_hashers.set()
    for worker_thread in workers:
        worker_thread.join(timeout=5)

    assert all(not worker_thread.is_alive() for worker_thread in workers)
    assert not errors
    assert entered == 3


def test_download_streams_verified_snapshot_when_source_changes_after_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original = b'biomodstack-apk-v200'
    apk, _ = write_release(tmp_path, apk_bytes=original)
    original_stat = apk.stat()
    real_stream_open_file = mobile_apk_updates._stream_open_file

    def mutate_source_then_stream(apk_file, start, end):
        apk.write_bytes(b'x' * len(original))
        os.utime(apk, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        yield from real_stream_open_file(apk_file, start, end)

    monkeypatch.setattr(mobile_apk_updates, '_stream_open_file', mutate_source_then_stream)
    with client_for(tmp_path, monkeypatch) as client:
        response = client.get(f'/api/mobile-apk/channels/stable/files/{apk.name}')

    assert response.status_code == 200
    assert response.content == original
