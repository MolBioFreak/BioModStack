from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

import pytest

PUBLISHER = Path(__file__).resolve().parents[1] / 'tools' / 'publish_apk_update.py'


def load_publisher():
    spec = importlib.util.spec_from_file_location('bms_apk_publisher', PUBLISHER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def existing_manifest(version_code: int = 200, signer: str = 'a' * 64) -> dict[str, object]:
    return {
        'channel': 'stable', 'version_code': version_code, 'version_name': '0.2.0', 'min_sdk': 24,
        'sha256': 'b' * 64, 'size_bytes': 123, 'filename': 'old.apk',
        'package_id': 'org.biomodstack.mobile', 'signing_certificate_sha256': signer,
        'published_at': '2026-07-18T12:00:00Z', 'changelog': [],
    }


def test_candidate_policy_fails_closed_for_package_signer_debuggable_sdk_and_bounds() -> None:
    p = load_publisher()
    baseline = dict(package_id='org.biomodstack.mobile', expected_package_id='org.biomodstack.mobile',
                    version_code=201, min_sdk=24, signing_digest='a' * 64,
                    expected_signing_digest='a' * 64, debuggable=False,
                    apk_size=1024, version_name='0.2.1', changelog=['safe'])
    p.validate_candidate_policy(**baseline)
    for override, match in [
        ({'package_id': 'evil'}, 'package'), ({'version_code': 0}, 'version code'),
        ({'min_sdk': 0}, 'minimum SDK'), ({'signing_digest': 'b' * 64}, 'certificate'),
        ({'debuggable': True}, 'debuggable'), ({'apk_size': p.MAX_APK_BYTES + 1}, 'size'),
        ({'version_name': 'x' * 129}, 'version name'), ({'changelog': ['x' * 1001]}, 'changelog'),
    ]:
        with pytest.raises(SystemExit, match=match):
            p.validate_candidate_policy(**{**baseline, **override})
    with pytest.raises(SystemExit, match='debuggable'):
        p.validate_candidate_policy(**{**baseline, 'debuggable': True})


def test_channel_requires_strictly_newer_version_and_same_signer(tmp_path: Path) -> None:
    p = load_publisher()
    channel = tmp_path / 'stable'
    channel.mkdir()
    (channel / 'manifest.json').write_text(json.dumps(existing_manifest()), encoding='utf-8')
    for version, signer, match in [(200, 'a' * 64, 'newer'), (199, 'a' * 64, 'newer'), (201, 'c' * 64, 'certificate')]:
        with pytest.raises(SystemExit, match=match):
            p.validate_existing_channel(channel, candidate_version_code=version, candidate_signing_digest=signer)


def test_content_addressed_artifact_collision_refused_and_manifest_atomic(tmp_path: Path) -> None:
    p = load_publisher()
    source = tmp_path / 'candidate.apk'
    source.write_bytes(b'candidate')
    channel = tmp_path / 'stable'
    channel.mkdir()
    digest = p.file_sha256(source)
    name = p.artifact_filename('0.2.1', 201, digest)
    assert digest in name
    destination = channel / name
    p.publish_immutable_artifact(source, destination, digest)
    assert destination.read_bytes() == b'candidate'
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644
    destination.write_bytes(b'collision')
    with pytest.raises(SystemExit, match='immutable'):
        p.publish_immutable_artifact(source, destination, digest)
    manifest = existing_manifest(201)
    p.atomic_write_manifest(channel / 'manifest.json', manifest)
    assert json.loads((channel / 'manifest.json').read_text()) == manifest
    assert stat.S_IMODE((channel / 'manifest.json').stat().st_mode) == 0o644
    assert not list(channel.glob('.manifest-*'))


def test_private_snapshot_rejects_oversize_while_copying(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = load_publisher()
    monkeypatch.setattr(p, 'MAX_APK_BYTES', 8)
    source = tmp_path / 'oversize.apk'
    source.write_bytes(b'123456789')

    with pytest.raises(SystemExit, match='size'):
        with p.immutable_apk_snapshot(source):
            pytest.fail('oversize snapshot must not be yielded')


def test_removed_production_policy_overrides_are_rejected_by_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    p = load_publisher()
    source = tmp_path / 'candidate.apk'
    source.write_bytes(b'candidate')
    for retired_flag in ('--allow-debuggable', '--expected-package-id'):
        monkeypatch.setattr(
            sys,
            'argv',
            [
                str(PUBLISHER), '--apk', str(source), '--updates-dir', str(tmp_path / 'updates'),
                '--expected-signing-certificate-sha256', 'a' * 64, retired_flag,
            ],
        )
        with pytest.raises(SystemExit) as error:
            p.main()
        assert error.value.code == 2


def test_channel_lock_is_a_real_advisory_lock(tmp_path: Path) -> None:
    p = load_publisher()
    channel = tmp_path / 'stable'
    with p.lock_channel(channel):
        assert (channel / '.publish.lock').is_file()


def test_channel_lock_enforces_deployment_traversal_modes_under_restrictive_umask(tmp_path: Path) -> None:
    p = load_publisher()
    updates_root = tmp_path / 'updates'
    channel = updates_root / 'stable'
    previous_umask = os.umask(0o077)
    try:
        with p.lock_channel(channel):
            assert stat.S_IMODE(updates_root.stat().st_mode) == 0o755
            assert stat.S_IMODE(channel.stat().st_mode) == 0o755
    finally:
        os.umask(previous_umask)


def test_main_inspects_hashes_and_publishes_one_immutable_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    p = load_publisher()
    source = tmp_path / 'candidate.apk'
    original_bytes = b'candidate-that-was-inspected'
    replacement_bytes = b'replacement-after-inspection'
    source.write_bytes(original_bytes)
    updates_root = tmp_path / 'updates'
    inspected_paths: list[Path] = []

    def inspect_and_replace(path: Path, _apkanalyzer: Path, _apksigner: Path):
        inspected_paths.append(path)
        inspected_bytes = path.read_bytes()
        source.write_bytes(replacement_bytes)
        assert inspected_bytes == original_bytes
        return p.ApkMetadata(
            package_id='org.biomodstack.mobile',
            version_code=201,
            version_name='0.2.1',
            min_sdk=24,
            debuggable=False,
            signing_digest='a' * 64,
        )

    monkeypatch.setattr(p, 'discover_sdk_tool', lambda explicit, name: tmp_path / name)
    monkeypatch.setattr(p, 'inspect_apk', inspect_and_replace)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            str(PUBLISHER),
            '--apk', str(source),
            '--updates-dir', str(updates_root),
            '--expected-signing-certificate-sha256', 'a' * 64,
        ],
    )

    p.main()

    manifest = json.loads((updates_root / 'stable' / 'manifest.json').read_text(encoding='utf-8'))
    artifact = updates_root / 'stable' / manifest['filename']
    assert inspected_paths and inspected_paths[0] != source
    assert artifact.read_bytes() == original_bytes
    assert manifest['size_bytes'] == len(original_bytes)
    assert manifest['sha256'] == hashlib.sha256(original_bytes).hexdigest()
