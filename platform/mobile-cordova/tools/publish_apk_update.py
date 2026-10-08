#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Iterator, Sequence, TypedDict


MAX_APK_BYTES = 250 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_VERSION_NAME_CHARS = 128
MAX_CHANGELOG_ITEMS = 50
MAX_CHANGELOG_ITEM_CHARS = 1000
DEFAULT_PACKAGE_ID = 'org.biomodstack.mobile'
SHA256_PATTERN = re.compile(r'^[A-F0-9]{64}$')


class ApkMetadata(TypedDict):
    package_id: str
    version_code: int
    version_name: str
    min_sdk: int
    signing_digest: str
    debuggable: bool


def run_tool(command: Sequence[str]) -> str:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'
        raise SystemExit(f"{' '.join(command)} failed: {detail}")
    return result.stdout.strip()


def discover_sdk_tool(explicit: str | None, name: str) -> str:
    if explicit:
        return explicit
    found = shutil.which(name)
    if found:
        return found
    sdk_root = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if sdk_root:
        root = Path(sdk_root).expanduser()
        candidates = [root / 'cmdline-tools' / 'latest' / 'bin' / name]
        build_tools = root / 'build-tools'
        if build_tools.is_dir():
            candidates.extend(
                version / name
                for version in sorted(build_tools.iterdir(), reverse=True)
                if version.is_dir()
            )
        candidates.extend(root.glob(f'cmdline-tools/*/bin/{name}'))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise SystemExit(f'{name} was not found; set ANDROID_HOME or pass --{name}')


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_signing_digest(value: str, *, label: str) -> str:
    normalized = value.replace(':', '').strip().upper()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise SystemExit(f'{label} must be exactly 64 hexadecimal SHA-256 characters')
    return normalized


def inspect_apk(apk: Path, apkanalyzer: str, apksigner: str) -> ApkMetadata:
    package_id = run_tool([apkanalyzer, 'manifest', 'application-id', str(apk)]).strip()
    version_code = int(run_tool([apkanalyzer, 'manifest', 'version-code', str(apk)]))
    version_name = run_tool([apkanalyzer, 'manifest', 'version-name', str(apk)]).strip()
    min_sdk = int(run_tool([apkanalyzer, 'manifest', 'min-sdk', str(apk)]))
    manifest_xml = run_tool([apkanalyzer, 'manifest', 'print', str(apk)])
    debuggable = bool(
        re.search(r'<application\b[^>]*\bandroid:debuggable\s*=\s*["\']true["\']', manifest_xml, re.DOTALL)
    )

    signer_output = run_tool([apksigner, 'verify', '--verbose', '--print-certs', str(apk)])
    digests = {
        normalize_signing_digest(match, label='APK signing certificate')
        for match in re.findall(r'certificate SHA-256 digest:\s*([A-Fa-f0-9:]+)', signer_output)
    }
    if len(digests) != 1:
        raise SystemExit('APK must expose exactly one signing certificate SHA-256 digest')

    return {
        'package_id': package_id,
        'version_code': version_code,
        'version_name': version_name,
        'min_sdk': min_sdk,
        'debuggable': debuggable,
        'signing_digest': next(iter(digests)),
    }


def validate_candidate_policy(
    *,
    package_id: str,
    expected_package_id: str,
    version_code: int,
    min_sdk: int,
    signing_digest: str,
    expected_signing_digest: str,
    debuggable: bool,

    apk_size: int,
    version_name: str,
    changelog: Sequence[str],
) -> None:
    if package_id != expected_package_id:
        raise SystemExit(f'APK package ID {package_id!r} does not match expected {expected_package_id!r}')
    if not 1 <= version_code <= 2_100_000_000:
        raise SystemExit('APK version code is outside Android publication bounds')
    if not 1 <= min_sdk <= 100:
        raise SystemExit('APK minimum SDK is outside publication bounds')
    if normalize_signing_digest(signing_digest, label='APK signing certificate') != normalize_signing_digest(
        expected_signing_digest,
        label='Expected signing certificate',
    ):
        raise SystemExit('APK signing certificate does not match the configured release certificate')
    if debuggable:
        raise SystemExit('APK is debuggable; production publication requires a non-debuggable release APK')
    if apk_size <= 0 or apk_size > MAX_APK_BYTES:
        raise SystemExit(f'APK size must be between 1 and {MAX_APK_BYTES} bytes')
    if not version_name or len(version_name) > MAX_VERSION_NAME_CHARS:
        raise SystemExit(f'APK version name must contain 1-{MAX_VERSION_NAME_CHARS} characters')
    if len(changelog) > MAX_CHANGELOG_ITEMS:
        raise SystemExit(f'changelog must contain at most {MAX_CHANGELOG_ITEMS} entries')
    if any(not item or len(item) > MAX_CHANGELOG_ITEM_CHARS for item in changelog):
        raise SystemExit(f'each changelog entry must contain 1-{MAX_CHANGELOG_ITEM_CHARS} characters')


def load_existing_manifest(channel_root: Path) -> dict[str, object] | None:
    manifest_path = channel_root / 'manifest.json'
    if not manifest_path.exists():
        return None
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise SystemExit('existing channel manifest exceeds the allowed size')
    try:
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f'existing channel manifest is invalid: {exc}') from exc
    if not isinstance(payload, dict):
        raise SystemExit('existing channel manifest must be a JSON object')
    return payload


def validate_existing_channel(
    channel_root: Path,
    *,
    candidate_version_code: int,
    candidate_signing_digest: str,
) -> None:
    existing = load_existing_manifest(channel_root)
    if existing is None:
        return
    try:
        published_version_code = int(existing['version_code'])
        published_signer = normalize_signing_digest(
            str(existing['signing_certificate_sha256']),
            label='Published signing certificate',
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit('existing channel manifest lacks valid version/signing metadata') from exc
    if candidate_version_code <= published_version_code:
        raise SystemExit(
            f'candidate versionCode {candidate_version_code} must be newer than published versionCode {published_version_code}'
        )
    if normalize_signing_digest(candidate_signing_digest, label='Candidate signing certificate') != published_signer:
        raise SystemExit('candidate signing certificate does not match the published channel signing certificate')


def artifact_filename(version_name: str, version_code: int, apk_sha256: str) -> str:
    safe_version = re.sub(r'[^A-Za-z0-9._-]+', '-', version_name).strip('-.')[:32] or 'release'
    return f'biomodstack-{safe_version}-vc{version_code}-{apk_sha256.lower()}.apk'


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def immutable_apk_snapshot(source: Path) -> Iterator[Path]:
    """Copy the source once; inspect, hash, and publish only these exact private bytes."""
    with tempfile.TemporaryDirectory(prefix='bms-apk-publish-') as temporary_root:
        snapshot = Path(temporary_root) / 'candidate.apk'
        with source.open('rb') as source_handle, snapshot.open('xb') as snapshot_handle:
            total = 0
            while True:
                chunk = source_handle.read(min(1024 * 1024, MAX_APK_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_APK_BYTES:
                    raise SystemExit(f'APK size must be between 1 and {MAX_APK_BYTES} bytes')
                snapshot_handle.write(chunk)
            snapshot_handle.flush()
            os.fsync(snapshot_handle.fileno())
        snapshot.chmod(0o400)
        yield snapshot


def publish_immutable_artifact(source: Path, destination: Path, expected_sha256: str) -> None:
    expected = expected_sha256.lower()
    if destination.exists():
        if file_sha256(destination) != expected:
            raise SystemExit(f'published artifact {destination.name} is immutable and has different bytes')
        destination.chmod(0o644)
        return

    descriptor, temporary_name = tempfile.mkstemp(prefix='.apk-stage-', dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as output, source.open('rb') as input_file:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if file_sha256(temporary) != expected:
            raise SystemExit('staged APK failed SHA-256 verification')
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if file_sha256(destination) != expected:
                raise SystemExit(f'published artifact {destination.name} is immutable and has different bytes')
        destination.chmod(0o644)
        fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_manifest(path: Path, payload: dict[str, object]) -> None:
    serialized = (json.dumps(payload, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if len(serialized) > MAX_MANIFEST_BYTES:
        raise SystemExit('generated update manifest exceeds the allowed size')
    descriptor, temporary_name = tempfile.mkstemp(prefix='.manifest-', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o644)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock_channel(channel_root: Path) -> Iterator[None]:
    updates_root = channel_root.parent
    updates_root.mkdir(parents=True, exist_ok=True)
    updates_root.chmod(0o755)
    channel_root.mkdir(exist_ok=True)
    channel_root.chmod(0o755)
    lock_path = channel_root / '.publish.lock'
    with lock_path.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description='Publish a verified BioModStack Android APK update manifest and artifact.')
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--updates-dir', type=Path, required=True)
    parser.add_argument('--channel', default='stable', choices=('stable', 'beta'))

    parser.add_argument(
        '--expected-signing-certificate-sha256',
        default=os.environ.get('BMS_ANDROID_EXPECTED_SIGNER_SHA256'),
        help='Durable release certificate SHA-256 (or BMS_ANDROID_EXPECTED_SIGNER_SHA256).',
    )

    parser.add_argument('--changelog', action='append', default=[])
    parser.add_argument('--apkanalyzer')
    parser.add_argument('--apksigner')
    arguments = parser.parse_args()

    apk = arguments.apk.resolve(strict=True)
    if apk.suffix.lower() != '.apk':
        raise SystemExit('--apk must point to an APK file')
    if not arguments.expected_signing_certificate_sha256:
        raise SystemExit(
            'expected release signing certificate is required; pass --expected-signing-certificate-sha256 '
            'or set BMS_ANDROID_EXPECTED_SIGNER_SHA256'
        )

    apkanalyzer = discover_sdk_tool(arguments.apkanalyzer, 'apkanalyzer')
    apksigner = discover_sdk_tool(arguments.apksigner, 'apksigner')
    with immutable_apk_snapshot(apk) as snapshot:
        metadata = inspect_apk(snapshot, apkanalyzer, apksigner)
        apk_size = snapshot.stat().st_size
        changelog = [str(item).strip() for item in arguments.changelog]
        expected_signer = normalize_signing_digest(
            arguments.expected_signing_certificate_sha256,
            label='Expected signing certificate',
        )
        validate_candidate_policy(
            package_id=str(metadata['package_id']),
            expected_package_id=DEFAULT_PACKAGE_ID,
            version_code=int(metadata['version_code']),
            min_sdk=int(metadata['min_sdk']),
            signing_digest=str(metadata['signing_digest']),
            expected_signing_digest=expected_signer,
            debuggable=bool(metadata['debuggable']),

            apk_size=apk_size,
            version_name=str(metadata['version_name']),
            changelog=changelog,
        )

        digest = file_sha256(snapshot)
        channel_root = arguments.updates_dir.resolve() / arguments.channel
        filename = artifact_filename(str(metadata['version_name']), int(metadata['version_code']), digest)
        destination = channel_root / filename
        manifest = {
            'channel': arguments.channel,
            'version_code': int(metadata['version_code']),
            'version_name': str(metadata['version_name']),
            'min_sdk': int(metadata['min_sdk']),
            'sha256': digest,
            'size_bytes': apk_size,
            'filename': filename,
            'package_id': str(metadata['package_id']),
            'signing_certificate_sha256': str(metadata['signing_digest']),
            'published_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'changelog': changelog,
        }

        with lock_channel(channel_root):
            validate_existing_channel(
                channel_root,
                candidate_version_code=int(metadata['version_code']),
                candidate_signing_digest=str(metadata['signing_digest']),
            )
            publish_immutable_artifact(snapshot, destination, digest)
            atomic_write_manifest(channel_root / 'manifest.json', manifest)

    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
