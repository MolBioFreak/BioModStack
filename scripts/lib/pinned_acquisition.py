"""Pinned byte acquisition; not scientific qualification or runtime activation.

Callers must obtain manifests from the reviewed model registry, never a request
body. This module reuses the shared runtime content-addressed publication and
verification authority. Private staging and weights have separate namespaces.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .reviewed_transport import TransportError, validate_policy, open_response

from .shared_runtime_images import (
    _directory, _file, _hash, _check_file, _lock, publish_image, verify_image,
)


class AcquisitionError(RuntimeError):
    """Fail-closed acquisition blocker; no activation should follow."""


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    url: str
    sha256: str
    size_bytes: int
    source_authority: str
    approval_ref: str
    license_id: str | None = None
    redirect_policy: dict | None = None

    def validate(self, *, test_only: bool = False) -> None:
        if self.artifact_id in {'.', '..'} or not re.fullmatch(r'[A-Za-z0-9_.-]+', self.artifact_id):
            raise AcquisitionError('invalid artifact identity')
        if self.kind not in {'image', 'weights'}:
            raise AcquisitionError('unsupported artifact kind')
        if not re.fullmatch(r'[0-9a-f]{64}', self.sha256):
            raise AcquisitionError('missing pinned SHA-256')
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise AcquisitionError('missing exact byte size')
        try:
            validate_policy(self.redirect_policy, self.url)
        except TransportError as exc:
            raise AcquisitionError(str(exc)) from None
        parsed = urllib.parse.urlsplit(self.url)
        fixture = test_only and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
        if (parsed.scheme != 'https' and not fixture) or not parsed.hostname:
            raise AcquisitionError('approved HTTPS source required')
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise AcquisitionError('credentials/fragments forbidden in source URL')
        if parsed.netloc != self.source_authority or not self.approval_ref.strip():
            raise AcquisitionError('source authority or release approval missing')
        if self.kind == 'weights' and not self.license_id:
            raise AcquisitionError('weights require explicit license identity')
        if self.kind == 'image' and self.license_id:
            raise AcquisitionError('licensed weight payload must not be classified as an image')

    @property
    def manifest_digest(self) -> str:
        payload = asdict(self)
        if self.redirect_policy is None:
            payload.pop('redirect_policy')  # Preserve existing no-redirect checkpoints.
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()


def _write_state(directory: Path, payload: dict) -> None:
    # The caller holds the per-artifact lock; no symlink following or replacement
    # of unrelated paths. fsync bytes, rename, fsync parent.
    with _directory(directory) as parent:
        name = 'state.new'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(payload, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, 'state.json', src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass


def _observed(path: Path) -> tuple[str, int]:
    with _file(path) as (fd, parent, before):
        if before.st_nlink != 1:
            raise AcquisitionError('staging file has multiple links')
        digest = _hash(fd)
        _check_file(path, fd, parent, before)
        return digest, before.st_size


def acquire(artifact: Artifact, store_root: Path, *, accepted_licenses=(),
            attempts: int = 3, timeout: float = 30, total_timeout: float = 300,
            test_only: bool = False, weights_root: Path | None = None) -> dict:
    """Download/resume one reviewed artifact, verify, atomically publish, receipt.

    A changed manifest for the same artifact ID, altered checkpoint bytes, or
    corrupt published object blocks instead of silently repairing. Retries bound
    network attempts per invocation; checkpoints survive process restarts. HTTP
    Range is accepted only with an exact Content-Range; a 200 restarts safely.
    test_only uses a segregated fixture store and cannot produce production paths.
    Weights are opaque pinned files: no archive extraction or license acceptance.
    """
    artifact = replace(artifact, redirect_policy=copy.deepcopy(artifact.redirect_policy))
    artifact.validate(test_only=test_only)
    if artifact.kind == 'weights' and artifact.license_id not in accepted_licenses:
        raise AcquisitionError(f'license acceptance required: {artifact.license_id}')
    if type(attempts) is not int or not 1 <= attempts <= 5:
        raise AcquisitionError('attempts must be between 1 and 5')
    if not 0 < timeout <= 60 or not 0 < total_timeout <= 3600:
        raise AcquisitionError('invalid timeout bounds')
    root = Path(store_root).absolute()
    if test_only:
        root = root / 'test-fixtures-not-scientific-assets'
    cache = root if artifact.kind == 'image' else (
        Path(weights_root).absolute() if weights_root is not None else root / 'weights')
    if test_only and weights_root is not None and artifact.kind == 'weights':
        cache /= 'test-fixtures-not-scientific-assets'
    stage = cache / '.acquisition' / artifact.artifact_id
    deadline = time.monotonic() + total_timeout
    with _lock(cache / '.acquisition', hashlib.sha256(artifact.artifact_id.encode()).hexdigest()):
        with _directory(stage, create=True) as parent:
            # Recover an interrupted state write: the committed state remains
            # authoritative; an uncommitted partial is rejected below.
            try:
                os.unlink('state.new', dir_fd=parent)
            except FileNotFoundError:
                pass
        state_path, partial = stage / 'state.json', stage / 'payload.part'
        try:
            with _file(state_path) as (fd, _, _):
                with os.fdopen(os.dup(fd)) as stream:
                    state = json.load(stream)
        except FileNotFoundError:
            state = {'store_root': str(cache), 'manifest_digest': artifact.manifest_digest, 'bytes': 0,
                     'partial_sha256': hashlib.sha256(b'').hexdigest()}
            _write_state(stage, state)
        except (ValueError, OSError) as exc:
            raise AcquisitionError('invalid durable acquisition state') from exc
        if not isinstance(state, dict):
            raise AcquisitionError('invalid durable acquisition state')
        if state.get('store_root') != str(cache):
            raise AcquisitionError('stale acquisition store: explicit operator reconciliation required')
        if state.get('rejected'):
            raise AcquisitionError('previous acquisition rejected: explicit operator reconciliation required')
        if state.get('manifest_digest') != artifact.manifest_digest:
            raise AcquisitionError('manifest changed: explicit operator reconciliation required')
        if (type(state.get('bytes')) is not int or not 0 <= state['bytes'] <= artifact.size_bytes
                or not isinstance(state.get('partial_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', state['partial_sha256'])):
            raise AcquisitionError('invalid durable acquisition checkpoint')
        result = cache / 'objects' / 'sha256' / artifact.sha256 / 'runtime.sif'
        if os.path.lexists(result.parent):
            with _lock(cache, 'lifecycle'):
                receipt = verify_image(result, artifact.sha256)
            if receipt['size'] != artifact.size_bytes:
                raise AcquisitionError('cached byte size differs from manifest')
            if state.get('status') == 'verified' and state.get('receipt') != receipt:
                raise AcquisitionError('published acquisition identity drift: explicit reconciliation required')
        else:
            if state.get('status') == 'verified':
                raise AcquisitionError('published acquisition object missing: explicit reconciliation required')
            if os.path.lexists(partial):
                digest, size = _observed(partial)
                if (digest, size) != (state.get('partial_sha256'), state.get('bytes')):
                    raise AcquisitionError('corrupt or uncheckpointed staging bytes')
            elif state.get('bytes'):
                raise AcquisitionError('durable checkpoint payload missing')
            for attempt in range(attempts):
                offset = state['bytes']
                if offset == artifact.size_bytes:
                    break
                if time.monotonic() >= deadline:
                    raise AcquisitionError('total acquisition deadline exceeded')
                try:
                    try:
                        response = open_response(artifact.url, policy=artifact.redirect_policy,
                            offset=offset, timeout=timeout, deadline=deadline, test_only=test_only)
                    except TransportError as exc:
                        raise AcquisitionError(str(exc)) from None
                    with response:
                        if response.headers.get('Content-Encoding', 'identity') != 'identity':
                            raise AcquisitionError('encoded response forbidden')
                        if response.status == 206:
                            expected = f'bytes {offset}-{artifact.size_bytes-1}/{artifact.size_bytes}'
                            if response.headers.get('Content-Range') != expected:
                                raise AcquisitionError('invalid resume Content-Range')
                        elif response.status == 200:
                            offset = 0
                        else:
                            raise OSError('unexpected HTTP status')
                        with _directory(stage) as parent:
                            fd = os.open('payload.part', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                         0o600, dir_fd=parent)
                            info = os.fstat(fd)
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                os.close(fd)
                                raise AcquisitionError('invalid staging file')
                        with os.fdopen(fd, 'r+b') as stream:
                            if not offset:
                                stream.truncate(0)
                            stream.seek(offset)
                            try:
                                while True:
                                    if time.monotonic() >= deadline:
                                        raise AcquisitionError('total acquisition deadline exceeded')
                                    chunk = response.read1(min(65536, artifact.size_bytes-offset+1))
                                    if not chunk:
                                        break
                                    if offset + len(chunk) > artifact.size_bytes:
                                        state['rejected'] = True
                                        raise AcquisitionError('download exceeds pinned byte size')
                                    stream.write(chunk)
                                    offset += len(chunk)
                            finally:
                                stream.flush()
                                os.fsync(stream.fileno())
                                digest, size = _observed(partial)
                                state.update(bytes=size, partial_sha256=digest)
                                _write_state(stage, state)
                        if offset != artifact.size_bytes:
                            raise OSError('interrupted download: short body')
                    break
                except (OSError, urllib.error.URLError) as exc:
                    if attempt + 1 == attempts:
                        raise AcquisitionError(f'download failed after {attempts} attempt(s)') from None
            digest, size = _observed(partial)
            if (digest, size) != (artifact.sha256, artifact.size_bytes):
                raise AcquisitionError('downloaded bytes differ from pinned SHA-256/size')
            result = publish_image(partial, cache, artifact.sha256)
            receipt = verify_image(result, artifact.sha256)
        state.update(status='verified', receipt=receipt)
        _write_state(stage, state)
        # Only discard our authenticated download staging, never a published
        # object, reference, legacy image, alias or operator-owned source.
        if os.path.lexists(partial):
            with _file(partial) as (fd, parent, before):
                if (before.st_nlink != 1 or before.st_size != state['bytes']
                        or _hash(fd) != state['partial_sha256']):
                    raise AcquisitionError('unrecognized acquisition staging; reconciliation required')
                _check_file(partial, fd, parent, before)
                os.unlink(partial.name, dir_fd=parent)
                os.fsync(parent)
        return {'artifact_id': artifact.artifact_id, 'kind': artifact.kind,
                'path': str(result), 'manifest_digest': artifact.manifest_digest,
                'verification': receipt, 'qualification': 'not_checked',
                'test_only': test_only}
