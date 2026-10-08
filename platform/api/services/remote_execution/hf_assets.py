"""Controller-only HF delivery broker. Capabilities are never persistence data."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
import time
from typing import Literal, TypedDict

MIN_BYTES = 8 * 1024 * 1024
ENDPOINT = 'https://huggingface.co'
# Roles whose selected assets may mirror into the private bucket. Weights are a
# role of their own so the shared weight tree is a declared delivery class, not
# an anonymous runtime leaf.
DELIVERY_ROLES = frozenset({'image', 'runtime', 'source', 'weights'})
_KEYS = ('BMS_HF_ASSET_BUCKET', 'BMS_HF_TOKEN_FILE', 'BMS_HF_ASSET_ALLOW_PUBLISH', 'BMS_HF_ASSET_MAX_BYTES')
# Optional, and deliberately outside _KEYS: naming the packed weight archive
# neither enables HF nor changes readiness, it only lets the shared weight tree
# travel as one already-published object.
WEIGHTS_ARCHIVE_KEY = 'BMS_HF_WEIGHT_ARCHIVE'
_ARCHIVE = re.compile(r'[0-9a-f]{64}:[0-9]{1,16}\Z')
_BUCKET = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')


class HFAssetError(RuntimeError):
    """Finite safe diagnostic, never an SDK exception or capability."""


ERROR_MESSAGES = {
    'configuration': 'HF asset configuration is missing or unsafe',
    'credential': 'HF credential file is unavailable or unsafe',
    'file': 'HF asset file configuration is invalid',
    'private_bucket': 'HF bucket is not the configured private bucket',
    'byte_limit': 'HF bucket exceeds configured byte limit',
    'metadata': 'HF object metadata does not match the selected artifact',
    'local_identity': 'HF local artifact identity changed',
    'identity': 'HF asset identity or role is invalid',
    'weights_archive': 'HF weight archive configuration is invalid',
    'publish_disabled': 'HF artifact is absent and publishing is not authorized',
    'upload_uncertain': 'HF upload outcome requires a fresh source check',
    'upload_unacknowledged': 'HF upload metadata was not acknowledged',
    'capability': 'HF returned an invalid source capability',
    'resolve': 'HF object did not return an approved read capability',
    'operation': 'HF asset operation is invalid',
    'authorization': 'HF asset authorization failed; check bucket access and token permissions',
    'quota': 'HF asset provider quota or payment limit was reached',
    'throttled': 'HF asset provider rate limit was reached; retry later',
    'unavailable': 'HF asset provider is temporarily unavailable',
    'failed': 'HF asset source operation failed',
}


@dataclass(frozen=True)
class Configuration:
    bucket: str
    token_file: Path = field(repr=False)
    allow_publish: bool = False
    max_bytes: int = 10**12


class Readiness(TypedDict):
    configured: bool
    available: bool
    bucket: str | None
    mode: Literal['hf_preferred', 'ssh']
    credential_configured: bool
    allow_publish: bool
    message: str


def _open_regular(path: Path) -> int:
    """Walk every component with no-follow, pinning directories against swaps."""
    if not path.is_absolute() or '..' in path.parts:
        raise HFAssetError('HF asset file configuration is invalid')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise HFAssetError('HF asset file configuration is invalid')
        return fd
    except OSError:
        raise HFAssetError('HF asset file configuration is invalid') from None
    finally:
        os.close(directory)


def _token(path: Path) -> str:
    try:
        with os.fdopen(_open_regular(path), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                    or info.st_nlink != 1 or not 1 <= info.st_size <= 4096):
                raise ValueError()
            raw = stream.read(4097)
            value = raw.decode('ascii').strip()
            if not re.fullmatch(r'hf_[A-Za-z0-9]{8,512}', value):
                raise ValueError()
            return value
    except Exception:
        raise HFAssetError('HF credential file is unavailable or unsafe') from None


def configuration() -> Configuration | None:
    if not any(key in os.environ for key in _KEYS):
        return None
    try:
        bucket = os.environ.get(_KEYS[0], '')
        if not _BUCKET.fullmatch(bucket) or '..' in bucket:
            raise ValueError()
        allow = os.environ.get(_KEYS[2], '0')
        limit = os.environ.get(_KEYS[3], '1000000000000')
        if allow not in ('0', '1') or not re.fullmatch(r'[0-9]{1,16}', limit) or int(limit) < 1:
            raise ValueError()
        path = Path(os.environ.get(_KEYS[1], ''))
        _token(path)
        return Configuration(bucket, path, allow == '1', int(limit))
    except Exception:
        raise HFAssetError('HF asset configuration is missing or unsafe') from None


def weights_archive() -> tuple[str, int] | None:
    """The one already-published object holding the packed shared weight tree.

    A declaration, not a capability: it names an object the operator packed and
    published out of band. Absent means the shared weight tree keeps the existing
    per-file relay, so a deployment that never packs one is unchanged.
    """
    raw = os.environ.get(WEIGHTS_ARCHIVE_KEY)
    if raw is None:
        return None
    try:
        if not _ARCHIVE.fullmatch(raw):
            raise ValueError()
        digest, _, size = raw.partition(':')
        # Form only. The broker's delivery floor still decides whether an object
        # of that size is obtained from HF or relayed as files.
        if int(size) < 1:
            raise ValueError()
        return digest, int(size)
    except Exception:
        raise HFAssetError(ERROR_MESSAGES['weights_archive']) from None


def readiness() -> Readiness:
    configured = any(key in os.environ for key in _KEYS)
    result: Readiness = {'configured': configured, 'available': False, 'bucket': None,
                         'mode': 'hf_preferred' if configured else 'ssh',
                         'credential_configured': False, 'allow_publish': False,
                         'message': 'HF is not configured; SSH delivery is available'}
    try:
        config = configuration()
    except HFAssetError:
        result['message'] = 'HF asset configuration is missing or unsafe'
        return result
    if config:
        result.update(bucket=config.bucket, credential_configured=True,
                      allow_publish=config.allow_publish,
                      message='HF configured locally; connection has not been checked')
    return result


async def _noop():
    pass


async def _invoke(request: dict, *, check_fence, timeout: float = 3600) -> dict:
    """Own child lifetime; periodically recheck caller lease while SDK blocks."""
    await check_fence()
    process = None
    task = None
    state = tempfile.TemporaryDirectory(prefix='bms-hf-owned-')
    environment = dict(os.environ, BMS_HF_SDK_STATE=state.name)
    try:
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name('hf_assets_worker.py')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True, env=environment))
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # Cancellation during spawn must not orphan a newly created child.
            while not spawning.done():
                try:
                    await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    continue
            process = spawning.result()
            raise
        task = asyncio.create_task(process.communicate(json.dumps(request).encode()))
        deadline = time.monotonic() + timeout
        while not task.done():
            await asyncio.wait({task}, timeout=min(5.0, max(0, deadline - time.monotonic())))
            await check_fence()
            if time.monotonic() >= deadline:
                raise HFAssetError('HF asset operation timed out')
        output, _ = await task
        if len(output) > 128 * 1024:
            raise HFAssetError(ERROR_MESSAGES['failed'])
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError()
        if set(result) == {'error'} and isinstance(result['error'], str):
            raise HFAssetError(ERROR_MESSAGES.get(result['error'], ERROR_MESSAGES['failed']))
        if process.returncode:
            raise HFAssetError(ERROR_MESSAGES['failed'])
        await check_fence()
        return result
    except (asyncio.CancelledError, HFAssetError):
        raise
    except Exception:
        raise HFAssetError('HF asset source operation failed') from None
    finally:
        async def cleanup():
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # SDK temporary state belongs to this invocation, not another cache.
            state.cleanup()
        cleanup_task = asyncio.create_task(cleanup())
        interrupted = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                interrupted = True
        cleanup_task.result()
        if interrupted:
            raise asyncio.CancelledError()


async def check_connection() -> Readiness:
    result = readiness()
    if not result['configured'] or not result['credential_configured']:
        return result
    try:
        answer = await _invoke({'action': 'check'}, check_fence=_noop, timeout=60)
        if answer != {'available': True}:
            raise HFAssetError('HF asset connection check failed')
        result.update(available=True, message='Private HF bucket access verified; no objects were changed')
    except HFAssetError as exc:
        message = str(exc) if str(exc) in ERROR_MESSAGES.values() else 'HF asset connection check failed'
        result.update(available=False, message=message)
    return result


def _validate_source(source):
    # Installed worker peer shares the one capability policy, loaded lazily.
    from tools.bms_hf_transfer import validate_source
    try:
        validate_source(source)
        if set(source) != {'url', 'expires_at'}:
            raise ValueError()
        return source
    except Exception:
        raise HFAssetError('HF returned an invalid source capability') from None


async def prepare_sources(entries, *, check_fence) -> dict:
    config = configuration()
    if config is None:
        return {}
    result = {}
    for entry in entries:
        if (entry.role not in DELIVERY_ROLES or
                not re.fullmatch('[0-9a-f]{64}', entry.sha256) or
                type(entry.size_bytes) is not int or entry.size_bytes < 0):
            raise HFAssetError('HF asset identity or role is invalid')
        if entry.size_bytes < MIN_BYTES:
            continue
        if entry.size_bytes > config.max_bytes:
            raise HFAssetError('HF asset exceeds configured byte limit')
        source = await _invoke(dict(action='prepare', role=entry.role, sha256=entry.sha256,
                                    size_bytes=entry.size_bytes, path=str(entry.source)),
                               check_fence=check_fence)
        result[(entry.role == 'image', entry.sha256)] = _validate_source(source)
    return result
