"""Share grant primitives shared by the BMS API (writer) and share server (reader).

Pure stdlib only. The BMS API writes the grant index and frozen bundles into a
configured publish directory; the share server only reads them and appends to
its own access log. Secrets are never stored: the link key is kept as SHA-256
(256-bit random input), the passcode as scrypt with a per-grant salt.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

INDEX_SCHEMA = "bms.molbio.share-grant-index.v1"
BUNDLE_SCHEMA = "bms.molbio.share-bundle.v1"
INDEX_FILENAME = "grants.json"
BUNDLE_DIRNAME = "bundles"
LOCK_FILENAME = ".grants.lock"

# Hard server-side bounds. Not configurable by design.
MIN_EXPIRY_SECONDS = 60
MAX_EXPIRY_SECONDS = 24 * 60 * 60
PUBLIC_FUNNEL_PORT = 8443

KEY_BYTES = 32
PASSCODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no I/L/O/0/1
PASSCODE_LENGTH = 8
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024

MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_LABEL_LENGTH = 120
ACCESS_LOG_FILENAME = "access.jsonl"
ACCESS_LOG_ROTATE_BYTES = 10 * 1024 * 1024
ACCESS_LOG_READ_TAIL_BYTES = 2 * 1024 * 1024

_KEY_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
_GRANT_ID_CHARS = frozenset("0123456789abcdef")


class ShareConfigurationError(RuntimeError):
    """Share publishing is not configured or its storage is unsafe."""


def utc_now() -> int:
    return int(time.time())


def iso(epoch: int | float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def clamp_check_expiry(expires_in_seconds: int) -> int:
    """Validate a requested lifetime against the non-configurable 24 h cap."""
    if isinstance(expires_in_seconds, bool) or not isinstance(expires_in_seconds, int):
        raise ValueError("expiry must be an integer number of seconds")
    if expires_in_seconds < MIN_EXPIRY_SECONDS or expires_in_seconds > MAX_EXPIRY_SECONDS:
        raise ValueError(
            f"expiry must be between {MIN_EXPIRY_SECONDS} and {MAX_EXPIRY_SECONDS} seconds"
        )
    return expires_in_seconds


def new_grant_id() -> str:
    return secrets.token_hex(16)


def new_link_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).rstrip(b"=").decode("ascii")


def new_passcode() -> str:
    return "".join(secrets.choice(PASSCODE_ALPHABET) for _ in range(PASSCODE_LENGTH))


def format_passcode(passcode: str) -> str:
    return f"{passcode[:4]}-{passcode[4:]}"


def normalize_passcode(value: str) -> str | None:
    if not isinstance(value, str) or len(value) > 32:
        return None
    cleaned = "".join(ch for ch in value.upper() if ch not in " -")
    if len(cleaned) != PASSCODE_LENGTH or any(ch not in PASSCODE_ALPHABET for ch in cleaned):
        return None
    return cleaned


def well_formed_key(value: str) -> bool:
    return isinstance(value, str) and len(value) == 43 and all(ch in _KEY_CHARS for ch in value)


def well_formed_grant_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(ch in _GRANT_ID_CHARS for ch in value)


def key_sha256(key: str) -> str:
    return hashlib.sha256(key.encode("ascii")).hexdigest()


def hash_passcode(passcode: str, *, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        passcode.encode("ascii"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM,
    )
    return {
        "alg": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def verify_key(presented_key: str, stored_sha256: str) -> bool:
    """Constant-time comparison of a presented link key against its stored hash."""
    if not well_formed_key(presented_key) or not isinstance(stored_sha256, str):
        return False
    return hmac.compare_digest(key_sha256(presented_key).encode("ascii"), stored_sha256.encode("ascii"))


def verify_passcode(presented: str, record: dict[str, Any]) -> bool:
    """Recompute scrypt with the grant salt and compare in constant time."""
    normalized = normalize_passcode(presented)
    try:
        if record.get("alg") != "scrypt":
            return False
        n, r, p = int(record["n"]), int(record["r"]), int(record["p"])
        if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        salt = base64.b64decode(record["salt"], validate=True)
        expected = base64.b64decode(record["hash"], validate=True)
    except (KeyError, TypeError, ValueError):
        return False
    # Always spend the scrypt cost, even for malformed input, to keep timing flat.
    candidate = hashlib.scrypt(
        (normalized or "!" * PASSCODE_LENGTH).encode("ascii"), salt=salt, n=n, r=r, p=p,
        dklen=len(expected) or SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM,
    )
    return hmac.compare_digest(candidate, expected) and normalized is not None


@dataclass(frozen=True)
class GrantState:
    active: bool
    reason: str  # active | expired | revoked | invalid


def grant_state(grant: dict[str, Any], *, now: int | None = None) -> GrantState:
    now = utc_now() if now is None else now
    try:
        created = int(grant["created_at"])
        expires = int(grant["expires_at"])
    except (KeyError, TypeError, ValueError):
        return GrantState(False, "invalid")
    if expires <= created or expires - created > MAX_EXPIRY_SECONDS:
        return GrantState(False, "invalid")
    if grant.get("revoked_at") is not None:
        return GrantState(False, "revoked")
    if now >= expires:
        return GrantState(False, "expired")
    return GrantState(True, "active")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def publish_dir() -> Path:
    path = _env_path("BMS_SHARE_PUBLISH_DIR")
    if path is None:
        raise ShareConfigurationError("BMS_SHARE_PUBLISH_DIR is not configured")
    return path


def access_log_path() -> Path | None:
    path = _env_path("BMS_SHARE_ACCESS_LOG_DIR")
    return None if path is None else path / ACCESS_LOG_FILENAME


def public_host() -> str:
    value = os.environ.get("BMS_SHARE_PUBLIC_HOST", "").strip().lower()
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for ch in value):
        raise ShareConfigurationError("BMS_SHARE_PUBLIC_HOST is not configured")
    return value


def share_link(key: str) -> str:
    return f"https://{public_host()}:{PUBLIC_FUNNEL_PORT}/s#{key}"


# ---------------------------------------------------------------------------
# Index storage (writer side: BMS API)
# ---------------------------------------------------------------------------

def _ensure_safe_dir(path: Path) -> None:
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise ShareConfigurationError("share publish path is not a directory")
    if info.st_mode & 0o002:
        raise ShareConfigurationError("share publish directory is world-writable")


def prepare_publish_dir(root: Path) -> None:
    if not root.exists():
        raise ShareConfigurationError("share publish directory does not exist")
    _ensure_safe_dir(root)
    bundles = root / BUNDLE_DIRNAME
    if not bundles.exists():
        bundles.mkdir(mode=0o750)
    _ensure_safe_dir(bundles)


@contextlib.contextmanager
def index_lock(root: Path) -> Iterator[None]:
    fd = os.open(root / LOCK_FILENAME, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_bounded(path: Path, maximum: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("file is not a bounded regular file")
        chunks = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum:
            raise ValueError("file exceeds bound")
        return data
    finally:
        os.close(fd)


def load_index(root: Path) -> dict[str, Any]:
    path = root / INDEX_FILENAME
    if not path.exists():
        return {"schema": INDEX_SCHEMA, "grants": []}
    document = json.loads(_read_bounded(path, MAX_INDEX_BYTES))
    if not isinstance(document, dict) or document.get("schema") != INDEX_SCHEMA or not isinstance(document.get("grants"), list):
        raise ValueError("share grant index is malformed")
    return document


def atomic_write(path: Path, data: bytes, *, mode: int = 0o640) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def save_index(root: Path, document: dict[str, Any]) -> None:
    document = {**document, "schema": INDEX_SCHEMA, "written_at": utc_now()}
    data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_INDEX_BYTES:
        raise ValueError("share grant index exceeds bound")
    atomic_write(root / INDEX_FILENAME, data)


def bundle_path(root: Path, grant_id: str) -> Path:
    if not well_formed_grant_id(grant_id):
        raise ValueError("invalid grant id")
    return root / BUNDLE_DIRNAME / f"{grant_id}.json"


def read_bundle(root: Path, grant: dict[str, Any]) -> bytes:
    """Read a frozen bundle and verify it still matches its recorded digest."""
    data = _read_bounded(bundle_path(root, str(grant.get("grant_id"))), MAX_BUNDLE_BYTES)
    expected = str(grant.get("bundle_sha256") or "")
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest().encode(), expected.encode()):
        raise ValueError("bundle digest mismatch")
    return data


# ---------------------------------------------------------------------------
# Access log (writer side: share server; reader side: BMS API)
# ---------------------------------------------------------------------------

def append_access_log(path: Path | None, entry: dict[str, Any]) -> None:
    if path is None:
        return
    line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        with contextlib.suppress(FileNotFoundError):
            if os.stat(path).st_size > ACCESS_LOG_ROTATE_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o640)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        # Logging failure must not change the guest response.
        return


def read_access_log(path: Path | None, *, limit_per_grant: int = 50) -> dict[str, list[dict[str, Any]]]:
    if path is None or not path.exists():
        return {}
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - ACCESS_LOG_READ_TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for line in raw.splitlines()[-20000:]:
        try:
            entry = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(entry, dict):
            continue
        grant_id = entry.get("grant_id")
        if not well_formed_grant_id(grant_id):
            continue
        rows = grouped.setdefault(str(grant_id), [])
        rows.append({
            "time": str(entry.get("time", ""))[:40],
            "ip": str(entry.get("ip", ""))[:64],
            "route": str(entry.get("route", ""))[:64],
            "outcome": str(entry.get("outcome", ""))[:40],
        })
    return {key: rows[-limit_per_grant:] for key, rows in grouped.items()}
