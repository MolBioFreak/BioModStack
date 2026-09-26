"""Private owned SDK process. Only a validated read capability leaves stdout."""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
from urllib.parse import parse_qs, quote, urlsplit

# Direct executable peer must import the worktree API rather than a deployment.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.remote_execution.hf_assets import (  # noqa: E402
    DELIVERY_ROLES, ENDPOINT, ERROR_MESSAGES, HFAssetError, MIN_BYTES, _open_regular, _token,
    _validate_source, configuration,
)


def _error_code(exc):
    if isinstance(exc, HFAssetError):
        for code, message in ERROR_MESSAGES.items():
            if str(exc) == message:
                return code
    status = getattr(getattr(exc, 'response', None), 'status_code', None)
    return _status_code(status)


def _status_code(status):
    if status in (401, 403):
        return 'authorization'
    if status in (402, 413, 507):
        return 'quota'
    if status == 429:
        return 'throttled'
    if type(status) is int and 500 <= status <= 599:
        return 'unavailable'
    return 'failed'


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private_bucket(api, config):
    info = api.bucket_info(config.bucket)
    # Exact namespace and bucket identity confirms the configured owner scope.
    if info.id != config.bucket or info.private is not True:
        raise HFAssetError('HF bucket is not the configured private bucket')
    if type(info.size) is not int or not 0 <= info.size <= config.max_bytes:
        raise HFAssetError('HF bucket exceeds configured byte limit')
    return info


def _object(api, config, key, size):
    rows = list(api.get_bucket_paths_info(config.bucket, [key]))
    if not rows:
        return False
    if len(rows) != 1 or rows[0].path != key or type(rows[0].size) is not int or rows[0].size != size:
        raise HFAssetError('HF object metadata does not match the selected artifact')
    return True


def _hash(stream, size, digest):
    stream.seek(0)
    hasher = hashlib.sha256()
    total = 0
    while chunk := stream.read(8 * 1024 * 1024):
        total += len(chunk)
        if total > size:
            raise HFAssetError('HF local artifact identity changed')
        hasher.update(chunk)
    if total != size or hasher.hexdigest() != digest:
        raise HFAssetError('HF local artifact identity changed')
    stream.seek(0)


def _prepare(request, config, api, http, token, *, publisher_lock=None):
    import re
    role, digest, size = request.get('role'), request.get('sha256'), request.get('size_bytes')
    if (role not in DELIVERY_ROLES or not isinstance(digest, str)
            or not re.fullmatch('[0-9a-f]{64}', digest) or type(size) is not int
            or not MIN_BYTES <= size <= config.max_bytes):
        raise HFAssetError('HF asset identity or role is invalid')
    key = f'sha256/{digest}/' + ('image.sif' if role == 'image' else 'artifact')
    info = _private_bucket(api, config)
    if not _object(api, config, key, size):
        if not config.allow_publish:
            raise HFAssetError('HF artifact is absent and publishing is not authorized')
        if publisher_lock is not None:
            # Only publishing needs serialization. Re-read presence and quota
            # under exclusive ownership: another publisher may have completed
            # after the initial, lock-free cloud lookup.
            fcntl.flock(publisher_lock, fcntl.LOCK_EX)
            try:
                return _prepare(request, config, api, http, token)
            finally:
                fcntl.flock(publisher_lock, fcntl.LOCK_UN)
        if info.size + size > config.max_bytes:
            raise HFAssetError('HF bucket exceeds configured byte limit')
        with os.fdopen(_open_regular(Path(request['path'])), 'rb') as stream:
            before = _identity(os.fstat(stream.fileno()))
            _hash(stream, size, digest)
            if _identity(os.fstat(stream.fileno())) != before:
                raise HFAssetError('HF local artifact identity changed')
            # Pinned Linux fd path: SDK/Xet opens the retained inode, not a swappable
            # source pathname; no extra full local copy or user artifact mutation.
            # Recheck after hashing (and under the credential-file writer lock).
            if not _object(api, config, key, size):
                try:
                    api.batch_bucket_files(config.bucket, add=[(f'/proc/self/fd/{stream.fileno()}', key)])
                except Exception as exc:
                    # A failed nontransactional call may have committed. Reconcile,
                    # but fail visibly; next caller checks presence before writing.
                    _object(api, config, key, size)
                    code = _error_code(exc)
                    if code != 'failed':
                        raise HFAssetError(ERROR_MESSAGES[code]) from None
                    raise HFAssetError('HF upload outcome requires a fresh source check') from None
            # The pinned inode was fully hashed before upload. Check its exact
            # generation after the SDK read instead of rereading the whole file;
            # worker acquisition still hashes every byte it receives.
            if _identity(os.fstat(stream.fileno())) != before:
                raise HFAssetError('HF local artifact identity changed')
            with os.fdopen(_open_regular(Path(request['path'])), 'rb') as current:
                if _identity(os.fstat(current.fileno())) != before:
                    raise HFAssetError('HF local artifact identity changed')
        if not _object(api, config, key, size):
            raise HFAssetError('HF upload metadata was not acknowledged')
    # Do not use the SDK metadata helper: it follows relative redirects. The
    # authenticated origin is fixed and this request never follows any redirect.
    response = http.head(f'{ENDPOINT}/buckets/{config.bucket}/resolve/{quote(key, safe="")}',
                         headers={'Authorization': f'Bearer {token}'}, follow_redirects=False)
    if response.status_code not in (302, 307):
        code = _status_code(response.status_code)
        raise HFAssetError(ERROR_MESSAGES[code if code != 'failed' else 'resolve'])
    url = response.headers.get('location', '')
    try:
        expiry = parse_qs(urlsplit(url).query, strict_parsing=True)['Expires']
        if len(expiry) != 1:
            raise ValueError()
        source = {'url': url, 'expires_at': int(expiry[0])}
    except Exception:
        raise HFAssetError('HF returned an invalid source capability') from None
    return _validate_source(source)


def _execute(request):
    config = configuration()
    if config is None:
        raise HFAssetError('HF asset configuration is missing or unsafe')
    # Avoid global user SDK tokens, endpoint/proxy overrides, telemetry and caches.
    token = _token(config.token_file)
    with tempfile.TemporaryDirectory(prefix='bms-hf-sdk-', dir=os.environ.get('BMS_HF_SDK_STATE')) as cache:
        for key in tuple(os.environ):
            if key.startswith(('HF_', 'HUGGINGFACE_', 'XET_')):
                os.environ.pop(key, None)
        os.environ.update(HF_HOME=cache, HF_XET_CACHE=cache + '/xet', HF_ENDPOINT=ENDPOINT,
                          HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_PROGRESS_BARS='1',
                          HF_HUB_DISABLE_IMPLICIT_TOKEN='1', HF_XET_LOG_DEST='/dev/null',
                          HF_XET_CHUNK_CACHE_SIZE_BYTES='0')
        import httpx
        from huggingface_hub import HfApi, set_client_factory
        set_client_factory(lambda: httpx.Client(timeout=60, follow_redirects=False, trust_env=False))
        api = HfApi(endpoint=ENDPOINT, token=token)
        with os.fdopen(_open_regular(config.token_file), 'rb') as lock:
            # Presence checks and signed reads do not mutate the bucket and
            # must not wait behind an unrelated upload. _prepare locks misses.
            if request.get('action') == 'check':
                _private_bucket(api, config)
                return {'available': True}
            if request.get('action') != 'prepare':
                raise HFAssetError('HF asset operation is invalid')
            with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as http:
                return _prepare(request, config, api, http, token, publisher_lock=lock.fileno())


def main():
    os.umask(0o077)
    # API death also kills a blocked SDK upload. Close the race around prctl.
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if parent == 1 or libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        return 1
    logging.disable(logging.CRITICAL)
    # SDK diagnostics, including native output, must not enter protocol output.
    output_fd = os.dup(1)
    with open(os.devnull, 'wb') as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    code = 0
    try:
        request = json.loads(sys.stdin.buffer.read(128 * 1024 + 1))
        result = _execute(request)
    except Exception as exc:
        result = {'error': _error_code(exc)}
        code = 1
    with os.fdopen(output_fd, 'w') as output:
        output.write(json.dumps(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
