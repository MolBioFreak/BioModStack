"""Public, read-only MolBio share server (raw ASGI, served by uvicorn).

Intended to run as its own hardened process behind ``tailscale funnel`` on
port 8443. It imports only ``share_server.*`` and the pure restriction
services; it never imports BMS database/session/router modules and has no
route that writes anything except its own append-only access log.

Every API route requires a valid, unexpired, unrevoked grant: the link key
(``X-BMS-Share-Key``) and passcode (``X-BMS-Share-Passcode``).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

from share_server import bans as B
from share_server import grants as G

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_HEADER_VALUE = 256
VERIFIED_CACHE_SECONDS = 300
IP_RATE_PER_MINUTE = 120
GRANT_RATE_PER_MINUTE = 600
COMPUTE_CONCURRENCY = 2
SCRYPT_CONCURRENCY = 2
TRACKED_KEYS = 4096

KEY_HEADER = b"x-bms-share-key"
PASSCODE_HEADER = b"x-bms-share-passcode"

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
    "worker-src 'self' blob:; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; object-src 'none'"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"strict-transport-security", b"max-age=31536000"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
    (b"x-robots-tag", b"noindex, nofollow"),
)

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff", ".json": "application/json",
    ".txt": "text/plain; charset=utf-8", ".map": "application/json", ".wasm": "application/wasm",
}


# ---------------------------------------------------------------------------
# Rate limiting / lockout (in-memory, bounded)
# ---------------------------------------------------------------------------

class _Window:
    def __init__(self, limit: int, seconds: float) -> None:
        self.limit = limit
        self.seconds = seconds
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str, now: float) -> bool:
        """Record a hit; return False when the key is over its limit."""
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
                while len(self._hits) > TRACKED_KEYS:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            while hits and hits[0] <= now - self.seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def count(self, key: str, now: float) -> int:
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            while hits and hits[0] <= now - self.seconds:
                hits.popleft()
            return len(hits)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


@dataclass
class _Limits:
    ip_rate: _Window = field(default_factory=lambda: _Window(IP_RATE_PER_MINUTE, 60))
    grant_rate: _Window = field(default_factory=lambda: _Window(GRANT_RATE_PER_MINUTE, 60))
    # At most one access-log line per banned network per minute.
    banned_log: _Window = field(default_factory=lambda: _Window(1, 60))


# ---------------------------------------------------------------------------
# Configuration and read-only grant index
# ---------------------------------------------------------------------------

@dataclass
class ShareSettings:
    publish_dir: Path
    access_log: Path | None
    static_dir: Path | None
    state_dir: Path | None = None

    @classmethod
    def from_env(cls) -> "ShareSettings":
        static = os.environ.get("BMS_SHARE_STATIC_DIR", "").strip()
        return cls(
            publish_dir=G.publish_dir(),
            access_log=G.access_log_path(),
            static_dir=Path(static) if static else None,
            state_dir=G._env_path("BMS_SHARE_STATE_DIR"),
        )


class GrantIndex:
    """Reload the BMS-written index when its identity changes; never writes it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._identity: tuple[int, int, int] | None = None
        self._grants: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def grants(self) -> list[dict[str, Any]]:
        path = self.root / G.INDEX_FILENAME
        try:
            info = os.stat(path)
        except FileNotFoundError:
            return []
        identity = (info.st_ino, info.st_mtime_ns, info.st_size)
        with self._lock:
            if identity != self._identity:
                try:
                    document = G.load_index(self.root)
                    self._grants = [g for g in document["grants"] if isinstance(g, dict) and G.well_formed_grant_id(g.get("grant_id"))]
                except (OSError, ValueError):
                    self._grants = []  # fail closed
                self._identity = identity
            return list(self._grants)

    def find_by_key(self, key: str) -> dict[str, Any] | None:
        """Constant-time compare against every grant's key hash."""
        match = None
        presented = G.key_sha256(key).encode("ascii") if G.well_formed_key(key) else b"0" * 64
        for grant in self.grants():
            stored = str(grant.get("key_sha256") or "").encode("ascii", "replace")
            if hmac.compare_digest(presented, stored) and G.well_formed_key(key):
                match = grant
        return match


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class HTTPError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: tuple[tuple[bytes, bytes], ...] = ()) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[bytes, bytes]
    client_ip: str
    body: bytes = b""
    grant: dict[str, Any] | None = None


Handler = Callable[["ShareApp", Request], Awaitable[tuple[int, bytes, str, tuple[tuple[bytes, bytes], ...]]]]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _loopback(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_ip(scope: dict[str, Any], headers: dict[bytes, bytes]) -> str:
    """Use X-Forwarded-For only when the TCP peer is the loopback Funnel proxy."""
    peer = scope.get("client")
    peer_host = peer[0] if isinstance(peer, (list, tuple)) and peer else None
    if _loopback(peer_host):
        forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1")
        candidate = forwarded.split(",")[-1].strip() if forwarded else ""
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return str(peer_host)
    return str(peer_host or "unknown")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class ShareApp:
    def __init__(self, settings: ShareSettings | None = None) -> None:
        self._settings = settings
        self._index: GrantIndex | None = None
        self._static: dict[str, tuple[bytes, str]] | None = None
        self._bans: B.BanStore | None = None
        self.limits = _Limits()
        self._verified: dict[bytes, float] = {}
        self._process_key = secrets.token_bytes(32)
        self._compute = asyncio.Semaphore(COMPUTE_CONCURRENCY)
        self._scrypt = asyncio.Semaphore(SCRYPT_CONCURRENCY)

    # -- lazy configuration -------------------------------------------------
    @property
    def settings(self) -> ShareSettings:
        if self._settings is None:
            self._settings = ShareSettings.from_env()
        return self._settings

    @property
    def index(self) -> GrantIndex:
        if self._index is None:
            self._index = GrantIndex(self.settings.publish_dir)
        return self._index

    @property
    def bans(self) -> B.BanStore:
        if self._bans is None:
            self._bans = B.BanStore(self.settings.state_dir)
        return self._bans

    def static_files(self) -> dict[str, tuple[bytes, str]]:
        """Snapshot the guest build into memory once; only these paths are served."""
        if self._static is None:
            files: dict[str, tuple[bytes, str]] = {}
            root = self.settings.static_dir
            if root is not None and root.is_dir():
                for path in root.rglob("*"):
                    if path.is_symlink() or not path.is_file():
                        continue
                    relative = path.relative_to(root).as_posix()
                    suffix = path.suffix.lower()
                    if suffix not in _STATIC_TYPES or path.stat().st_size > 16 * 1024 * 1024:
                        continue
                    files["/" + relative] = (path.read_bytes(), _STATIC_TYPES[suffix])
            self._static = files
        return self._static

    # -- logging --------------------------------------------------------------
    def log(self, request: Request, grant_id: str | None, outcome: str, **extra: Any) -> None:
        G.append_access_log(self.settings.access_log, {
            "time": G.iso(time.time()), "ip": request.client_ip, "grant_id": grant_id,
            "route": request.path[:64], "outcome": outcome, **extra,
        })

    def reject_banned(self, request: Request, now: float) -> None:
        """Hard ban: a banned network gets 403 on every route, including static files."""
        if not self.bans.banned(request.client_ip, now):
            return
        network = B.network_key(request.client_ip) or request.client_ip
        if self.limits.banned_log.hit(network, now):
            self.log(request, None, "banned_request")
        raise HTTPError(403, "share_client_banned", "access from this network is blocked")

    def fail(self, request: Request, grant_id: str | None, reason: str, now: float) -> dict[str, Any]:
        """Record a failed authentication as a strike and log what it triggered."""
        result = self.bans.strike(request.client_ip, grant_id, reason, now)
        self.log(request, grant_id, reason, strike=result["ip_strikes"], limit=B.STRIKE_LIMIT)
        if result["ip_banned"]:
            self.log(request, grant_id, "ip_banned", network=result["network"],
                     until=G.iso(now + B.IP_BAN_SECONDS))
        if result["grant_locked"]:
            self.log(request, grant_id, "grant_locked")
        return result

    # -- authorization ----------------------------------------------------------
    async def authorize(self, request: Request) -> dict[str, Any]:
        now = time.time()
        if not self.limits.ip_rate.hit(request.client_ip, now):
            raise HTTPError(429, "rate_limited", "too many requests", ((b"retry-after", b"60"),))
        key = request.headers.get(KEY_HEADER, b"").decode("ascii", "replace")[:MAX_HEADER_VALUE]
        passcode = request.headers.get(PASSCODE_HEADER, b"").decode("ascii", "replace")[:MAX_HEADER_VALUE]
        grant = self.index.find_by_key(key)
        if grant is None:
            self.fail(request, None, "unknown_key", now)
            raise HTTPError(404, "share_not_found", "this share link is not valid")
        grant_id = str(grant["grant_id"])
        state = G.grant_state(grant, now=int(now))
        if not state.active:
            self.log(request, grant_id, state.reason)
            raise HTTPError(410, f"share_{state.reason}", "this share link is no longer available")
        if self.bans.grant_locked(grant_id):
            self.log(request, grant_id, "locked")
            raise HTTPError(423, "share_locked", "this share is locked after too many incorrect passcodes")
        if not self.limits.grant_rate.hit(grant_id, now):
            raise HTTPError(429, "rate_limited", "too many requests for this share", ((b"retry-after", b"60"),))
        token = hmac.new(self._process_key, f"{grant_id}\0{key}\0{passcode}".encode("utf-8", "replace"), hashlib.sha256).digest()
        cached = self._verified.get(token)
        if cached is not None and cached > now:
            return grant
        async with self._scrypt:
            ok = await asyncio.to_thread(G.verify_passcode, passcode, grant.get("passcode") or {})
        if not ok:
            result = self.fail(request, grant_id, "wrong_passcode", now)
            if result["ip_banned"]:
                raise HTTPError(403, "share_client_banned", "access from this network is blocked")
            if result["grant_locked"]:
                raise HTTPError(423, "share_locked", "this share is locked after too many incorrect passcodes")
            remaining = max(0, B.STRIKE_LIMIT - int(result["ip_strikes"]))
            raise HTTPError(401, "share_passcode_incorrect",
                            f"the passcode is incorrect ({remaining} attempt{'s' if remaining != 1 else ''} left)")
        if len(self._verified) > TRACKED_KEYS:
            self._verified = {k: v for k, v in self._verified.items() if v > now}
            if len(self._verified) > TRACKED_KEYS:
                self._verified.clear()
        self._verified[token] = min(now + VERIFIED_CACHE_SECONDS, float(grant["expires_at"]))
        return grant

    # -- ASGI -------------------------------------------------------------------
    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        # Cookies are never read: drop them before anything else sees the headers.
        headers = {name.lower(): value for name, value in scope.get("headers", []) if name.lower() != b"cookie"}
        request = Request(
            method=scope["method"].upper(), path=scope.get("path", "/"),
            query=parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True),
            headers=headers, client_ip=client_ip(scope, headers),
        )
        try:
            self.reject_banned(request, time.time())
            status, body, content_type, extra = await self.dispatch(request, receive)
        except HTTPError as exc:
            status, body, content_type, extra = exc.status, _json_bytes({"detail": {"code": exc.code, "message": exc.message}}), "application/json", exc.headers
        except Exception:  # noqa: BLE001 - never leak internals to guests
            status, body, content_type, extra = 500, _json_bytes({"detail": {"code": "internal_error", "message": "request failed"}}), "application/json", ()
        response_headers = [
            (b"content-type", content_type.encode()),
            (b"content-length", str(len(body)).encode()),
            *SECURITY_HEADERS, *extra,
        ]
        if not any(name == b"cache-control" for name, _ in extra):
            response_headers.append((b"cache-control", b"no-store"))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": b"" if request.method == "HEAD" else body})

    async def read_body(self, request: Request, receive: Callable) -> bytes:
        declared = request.headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > MAX_BODY_BYTES:
                    raise HTTPError(413, "request_too_large", "request body is too large")
            except ValueError as exc:
                raise HTTPError(400, "bad_request", "invalid content length") from exc
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise HTTPError(400, "bad_request", "client disconnected")
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise HTTPError(413, "request_too_large", "request body is too large")
            chunks.append(chunk)
            if not message.get("more_body"):
                return b"".join(chunks)

    async def dispatch(self, request: Request, receive: Callable):
        method = "GET" if request.method == "HEAD" else request.method
        route = ROUTES.get((method, request.path))
        if route is not None:
            if route is not route_health:
                # Authenticate before buffering any request body.
                request.grant = await self.authorize(request)
            if method == "POST":
                content_type = request.headers.get(b"content-type", b"").split(b";")[0].strip().lower()
                if content_type != b"application/json":
                    raise HTTPError(415, "unsupported_media_type", "JSON body required")
                request.body = await self.read_body(request, receive)
            return await route(self, request)
        if method == "GET" and not request.path.startswith("/api/"):
            return self.serve_static(request)
        allowed = sorted({m for (m, p) in ROUTES if p == request.path})
        if allowed:
            raise HTTPError(405, "method_not_allowed", "method not allowed", ((b"allow", ", ".join(allowed).encode()),))
        raise HTTPError(404, "not_found", "not found")

    def serve_static(self, request: Request):
        files = self.static_files()
        path = request.path
        if path in {"/", "/s", "/s/"}:
            path = "/share.html" if "/share.html" in files else "/index.html"
        entry = files.get(path)
        if entry is None:
            raise HTTPError(404, "not_found", "not found")
        body, content_type = entry
        cache = b"no-store" if content_type.startswith("text/html") else b"public, max-age=3600, immutable"
        return 200, body, content_type, ((b"cache-control", cache),)


# ---------------------------------------------------------------------------
# Routes. This table is the complete API surface; there are no write routes.
# The two POST routes are pure computations on client-supplied sequences.
# ---------------------------------------------------------------------------

async def route_health(app: ShareApp, request: Request):
    return 200, _json_bytes({"status": "ok"}), "application/json", ()


async def route_bundle(app: ShareApp, request: Request):
    grant = request.grant
    assert grant is not None
    try:
        data = await asyncio.to_thread(G.read_bundle, app.settings.publish_dir, grant)
    except (OSError, ValueError) as exc:
        app.log(request, grant["grant_id"], "bundle_unavailable")
        raise HTTPError(410, "share_unavailable", "this share is no longer available") from exc
    app.log(request, grant["grant_id"], "bundle_ok")
    envelope = (
        b'{"grant":' + _json_bytes({
            "grant_id": grant["grant_id"], "label": grant.get("label") or "",
            "expires_at": G.iso(grant["expires_at"]), "created_at": G.iso(grant["created_at"]),
        }) + b',"bundle":' + data + b"}"
    )
    return 200, envelope, "application/json", ()


def _single(request: Request, name: str, default: str | None = None) -> str | None:
    values = request.query.get(name)
    if values is None:
        return default
    if len(values) != 1:
        raise HTTPError(422, "invalid_catalog_query", "catalog query parameters are invalid")
    return values[0]


async def route_catalog(app: ShareApp, request: Request):
    from share_server import compute

    allowed = {"response_view", "enzyme_ids", "limit", "cursor"}
    if set(request.query) - allowed:
        raise HTTPError(422, "invalid_catalog_query", "unknown catalog query parameter")
    try:
        limit = int(_single(request, "limit", str(compute.SHARE_CATALOG_PAGE_LIMIT)) or "")
        cursor = _single(request, "cursor")
        offset = 0
        if cursor is not None:
            if not cursor.startswith("o") or not cursor[1:].isdigit() or len(cursor) > 12:
                raise ValueError
            offset = int(cursor[1:])
    except ValueError as exc:
        raise HTTPError(422, "cursor_invalid", "catalog cursor is invalid for this request") from exc
    try:
        page = compute.catalog_page(
            response_view=_single(request, "response_view", "full") or "full",
            enzyme_ids=request.query.get("enzyme_ids"), limit=limit, offset=offset,
        )
    except compute.ComputeError as exc:
        raise HTTPError(exc.status, exc.code, exc.message) from exc
    return 200, _json_bytes(page), "application/json", ()


async def _compute_route(app: ShareApp, request: Request, kind: str):
    grant = request.grant
    assert grant is not None
    from share_server import compute

    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPError(422, "invalid_request", "request body is not valid JSON") from exc
    if app._compute.locked():
        raise HTTPError(503, "analysis_busy", "restriction analysis capacity is busy")
    async with app._compute:
        try:
            function = compute.analyze if kind == "analyze" else compute.simulate_digest
            output = await asyncio.wait_for(asyncio.to_thread(function, payload), timeout=60)
        except compute.ComputeError as exc:
            raise HTTPError(exc.status, exc.code, exc.message) from exc
        except compute.DigestGeometryError as exc:
            raise HTTPError(409, exc.code, str(exc)) from exc
        except (compute.DigestLimitError, compute.AnalysisLimitError) as exc:
            raise HTTPError(413, "request_too_large", "restriction request is too large") from exc
        except compute.InvalidDNAError as exc:
            raise HTTPError(422, "invalid_dna", "DNA input is invalid") from exc
        except TimeoutError as exc:
            raise HTTPError(504, "analysis_timeout", "restriction computation timed out") from exc
    app.log(request, grant["grant_id"], f"{kind}_ok")
    return 200, output, "application/json", ()


async def route_analyze(app: ShareApp, request: Request):
    return await _compute_route(app, request, "analyze")


async def route_simulate(app: ShareApp, request: Request):
    return await _compute_route(app, request, "simulate")


ROUTES: dict[tuple[str, str], Handler] = {
    ("GET", "/api/share/health"): route_health,
    ("GET", "/api/share/bundle"): route_bundle,
    ("GET", "/api/molbio/restriction/catalog"): route_catalog,
    ("POST", "/api/molbio/restriction/analyze"): route_analyze,
    ("POST", "/api/molbio/restriction/digests/simulate"): route_simulate,
}
PURE_COMPUTE_POST_ROUTES = frozenset({"/api/molbio/restriction/analyze", "/api/molbio/restriction/digests/simulate"})

mimetypes.init()
app = ShareApp()
