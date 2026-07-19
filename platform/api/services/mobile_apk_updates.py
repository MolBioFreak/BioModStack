from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryFile
from threading import BoundedSemaphore, Lock
from typing import BinaryIO


CHANNEL_PATTERN = re.compile(r'^[a-z0-9][a-z0-9._-]{0,31}$')
FILENAME_PATTERN = re.compile(r'^[A-Za-z0-9._-]+\.apk$')
SHA256_PATTERN = re.compile(r'^[a-fA-F0-9]{64}$')
MAX_APK_SIZE_BYTES = 250 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_VERSION_NAME_LENGTH = 128
MAX_CHANGELOG_ITEMS = 50
MAX_CHANGELOG_ITEM_LENGTH = 1_000
MAX_PUBLISHED_AT_LENGTH = 64
EXPECTED_PACKAGE_ID = 'org.biomodstack.mobile'
MAX_VERIFIED_CACHE_ENTRIES = 128


class MobileApkUpdateError(RuntimeError):
    pass


class MobileApkReleaseNotPublishedError(MobileApkUpdateError):
    pass


class MobileApkReleaseIntegrityError(MobileApkUpdateError):
    pass


@dataclass(frozen=True)
class MobileApkRelease:
    channel: str
    version_code: int
    version_name: str
    min_sdk: int
    sha256: str
    size_bytes: int
    filename: str
    package_id: str
    signing_certificate_sha256: str
    published_at: str
    changelog: tuple[str, ...]
    apk_path: Path

    def response_payload(self) -> dict[str, object]:
        return self.manifest(
            download_url=f'/api/mobile-apk/channels/{self.channel}/files/{self.filename}',
        )

    def manifest(self, *, download_url: str) -> dict[str, object]:
        return {
            'channel': self.channel,
            'version_code': self.version_code,
            'version_name': self.version_name,
            'min_sdk': self.min_sdk,
            'sha256': self.sha256,
            'size_bytes': self.size_bytes,
            'filename': self.filename,
            'package_id': self.package_id,
            'signing_certificate_sha256': self.signing_certificate_sha256,
            'published_at': self.published_at,
            'changelog': list(self.changelog),
            'download_url': download_url,
        }


class MobileApkUpdateService:
    _verified_files: OrderedDict[tuple[int, int, int, int, int, str], None] = OrderedDict()
    _verified_files_lock = Lock()
    _snapshot_slots = BoundedSemaphore(2)

    def __init__(self, updates_dir: Path):
        self.updates_dir = updates_dir.expanduser().resolve()

    def load_release(self, channel: str) -> MobileApkRelease:
        release = self._load_release_metadata(channel)
        try:
            with release.apk_path.open('rb') as apk_file:
                self._verify_open_apk(release, apk_file)
        except MobileApkUpdateError:
            raise
        except OSError as error:
            raise MobileApkReleaseIntegrityError('APK update artifact is unavailable.') from error
        return release

    def open_release(self, channel: str) -> tuple[MobileApkRelease, BinaryIO]:
        return self.open_verified_release(self.release_metadata(channel))

    def release_metadata(self, channel: str) -> MobileApkRelease:
        return self._load_release_metadata(channel)

    def open_verified_release(self, release: MobileApkRelease) -> tuple[MobileApkRelease, BinaryIO]:
        try:
            with release.apk_path.open('rb') as apk_file:
                verified_snapshot = self._copy_verified_apk(release, apk_file)
        except MobileApkUpdateError:
            raise
        except OSError as error:
            raise MobileApkReleaseIntegrityError('APK update artifact is unavailable.') from error
        return release, verified_snapshot

    def _load_release_metadata(self, channel: str) -> MobileApkRelease:
        if not CHANNEL_PATTERN.fullmatch(channel):
            raise MobileApkReleaseIntegrityError('Invalid APK update channel.')

        channel_dir = (self.updates_dir / channel).resolve()
        if self.updates_dir not in channel_dir.parents:
            raise MobileApkReleaseIntegrityError('Invalid APK update channel path.')
        manifest_path = channel_dir / 'manifest.json'
        try:
            with manifest_path.open('rb') as manifest_file:
                raw_manifest = manifest_file.read(MAX_MANIFEST_BYTES + 1)
        except FileNotFoundError as error:
            raise MobileApkReleaseNotPublishedError('APK update channel is not published.') from error
        except OSError as error:
            raise MobileApkReleaseIntegrityError('APK update manifest is unavailable.') from error
        if len(raw_manifest) > MAX_MANIFEST_BYTES:
            raise MobileApkReleaseIntegrityError('APK update manifest exceeds the maximum size.')
        try:
            payload = json.loads(raw_manifest.decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise MobileApkReleaseIntegrityError('APK update manifest is unavailable.') from error
        if not isinstance(payload, dict):
            raise MobileApkReleaseIntegrityError('APK update manifest must be a JSON object.')

        try:
            filename = payload['filename']
            if (
                not isinstance(filename, str)
                or len(filename) > 160
                or not FILENAME_PATTERN.fullmatch(filename)
                or Path(filename).name != filename
            ):
                raise ValueError('unsafe filename')
            manifest_channel = payload['channel']
            if manifest_channel != channel:
                raise ValueError('channel mismatch')
            version_code = self._bounded_int(payload['version_code'], 1, 2_100_000_000)
            min_sdk = self._bounded_int(payload['min_sdk'], 1, 100)
            size_bytes = self._bounded_int(payload['size_bytes'], 1, MAX_APK_SIZE_BYTES)
            version_name = self._bounded_string(payload['version_name'], MAX_VERSION_NAME_LENGTH)
            published_at = self._bounded_string(payload['published_at'], MAX_PUBLISHED_AT_LENGTH)
            digest = self._sha256_value(payload['sha256'])
            signer_digest = self._sha256_value(payload['signing_certificate_sha256'])
            package_id = payload['package_id']
            if package_id != EXPECTED_PACKAGE_ID:
                raise ValueError('package ID mismatch')
            changelog = self._validate_changelog(payload.get('changelog', []))
        except (KeyError, TypeError, ValueError) as error:
            raise MobileApkReleaseIntegrityError('APK update manifest is invalid.') from error

        apk_path = (channel_dir / filename).resolve()
        if channel_dir not in apk_path.parents:
            raise MobileApkReleaseIntegrityError('APK update artifact path escapes its channel directory.')

        return MobileApkRelease(
            channel=channel,
            version_code=version_code,
            version_name=version_name,
            min_sdk=min_sdk,
            sha256=digest,
            size_bytes=size_bytes,
            filename=filename,
            package_id=package_id,
            signing_certificate_sha256=signer_digest,
            published_at=published_at,
            changelog=changelog,
            apk_path=apk_path,
        )

    @staticmethod
    def _bounded_int(value: object, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError('integer out of bounds')
        return value

    @staticmethod
    def _bounded_string(value: object, maximum: int) -> str:
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise ValueError('string out of bounds')
        return value

    @staticmethod
    def _sha256_value(value: object) -> str:
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            raise ValueError('invalid SHA-256')
        return value.lower()

    @staticmethod
    def _validate_changelog(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > MAX_CHANGELOG_ITEMS:
            raise ValueError('invalid changelog')
        items: list[str] = []
        for item in value:
            if not isinstance(item, str) or len(item) > MAX_CHANGELOG_ITEM_LENGTH:
                raise ValueError('invalid changelog item')
            items.append(item)
        return tuple(items)

    def _copy_verified_apk(self, release: MobileApkRelease, apk_file: BinaryIO) -> BinaryIO:
        verified_snapshot = TemporaryFile(mode='w+b')
        try:
            with self._snapshot_slots:
                digest = sha256()
                total = 0
                while total <= release.size_bytes:
                    chunk = apk_file.read(min(1024 * 1024, release.size_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > release.size_bytes:
                        raise MobileApkReleaseIntegrityError('APK update artifact size does not match its manifest.')
                    digest.update(chunk)
                    verified_snapshot.write(chunk)
            if total != release.size_bytes:
                raise MobileApkReleaseIntegrityError('APK update artifact size does not match its manifest.')
            if digest.hexdigest() != release.sha256:
                raise MobileApkReleaseIntegrityError('APK update artifact checksum does not match its manifest.')
            verified_snapshot.seek(0)
            return verified_snapshot
        except Exception:
            verified_snapshot.close()
            raise

    def _verify_open_apk(self, release: MobileApkRelease, apk_file: BinaryIO) -> None:
        stat = os.fstat(apk_file.fileno())
        if stat.st_size != release.size_bytes:
            raise MobileApkReleaseIntegrityError('APK update artifact size does not match its manifest.')
        cache_key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, release.sha256)
        with self._verified_files_lock:
            if cache_key in self._verified_files:
                self._verified_files.move_to_end(cache_key)
                return
        digest = sha256()
        while chunk := apk_file.read(1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != release.sha256:
            raise MobileApkReleaseIntegrityError('APK update artifact checksum does not match its manifest.')
        with self._verified_files_lock:
            self._verified_files[cache_key] = None
            while len(self._verified_files) > MAX_VERIFIED_CACHE_ENTRIES:
                self._verified_files.popitem(last=False)
