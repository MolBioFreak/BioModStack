"""Persistent three-strike bans for the public share server.

Every failed authentication (unknown link key or wrong passcode) is a strike
against the client network (IPv4 address, or IPv6 /64). The third strike within
``STRIKE_WINDOW_SECONDS`` bans that network from every route for
``IP_BAN_SECONDS``. Independently, the third wrong passcode for one grant (from
any network) locks that grant for the rest of its lifetime.

State lives in ``BMS_SHARE_STATE_DIR/bans.json`` (the unit's StateDirectory),
so a restart of the socket-activated service does not reset strikes or bans.
Loopback peers without a forwarded address are never banned: only Tailscale
Funnel/Serve, which always supplies ``X-Forwarded-For``, reaches this server.
"""
from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

STRIKE_LIMIT = 3
STRIKE_WINDOW_SECONDS = 24 * 60 * 60
IP_BAN_SECONDS = 7 * 24 * 60 * 60
GRANT_LOCK_RETAIN_SECONDS = 25 * 60 * 60  # grants live at most 24 h
MAX_TRACKED = 10_000
STATE_FILENAME = "bans.json"
STATE_SCHEMA = "bms.molbio.share-bans.v1"
MAX_STATE_BYTES = 16 * 1024 * 1024


def network_key(ip: str) -> str | None:
    """IPv4 address or IPv6 /64; None for loopback/unparseable (never banned)."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if address.is_loopback:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return network_key(str(address.ipv4_mapped))
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


class BanStore:
    def __init__(self, state_dir: Path | None) -> None:
        self.path = None if state_dir is None else state_dir / STATE_FILENAME
        self._lock = threading.Lock()
        # Writers preserve snapshot order; readers never wait for disk I/O.
        self._writer_lock = threading.Lock()
        self.strikes: dict[str, list[float]] = {}
        self.ip_bans: dict[str, dict[str, Any]] = {}
        self.grant_strikes: dict[str, list[float]] = {}
        self.grant_locks: dict[str, dict[str, Any]] = {}
        self.load_error: str | None = None
        self._load()

    # -- persistence -----------------------------------------------------------
    def _load(self) -> None:
        if self.path is None:
            return
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError("ban state is too large")
            document = json.loads(self.path.read_bytes())
            if not isinstance(document, dict) or document.get("schema") != STATE_SCHEMA:
                raise ValueError("unknown ban state schema")
            strikes = {str(k): [float(t) for t in v] for k, v in (document.get("strikes") or {}).items()}
            ip_bans = {str(k): dict(v) for k, v in (document.get("ip_bans") or {}).items()}
            grant_strikes = {str(k): [float(t) for t in v] for k, v in (document.get("grant_strikes") or {}).items()}
            grant_locks = {str(k): dict(v) for k, v in (document.get("grant_locks") or {}).items()}
            self.strikes, self.ip_bans = strikes, ip_bans
            self.grant_strikes, self.grant_locks = grant_strikes, grant_locks
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # Keep the unreadable file for inspection and start a fresh record.
            self.load_error = f"{exc.__class__.__name__}: {exc}"
            with contextlib.suppress(OSError):
                os.replace(self.path, self.path.with_name(self.path.name + ".unreadable"))

    def _save(self, data: bytes) -> str | None:
        if self.path is None:
            return
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=self.path.parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            return f"{exc.__class__.__name__}: {exc}"
        return None

    def _prune(self, now: float) -> None:
        cutoff = now - STRIKE_WINDOW_SECONDS
        for table in (self.strikes, self.grant_strikes):
            for key in [k for k, hits in table.items() if not any(t > cutoff for t in hits)]:
                del table[key]
            for key, hits in table.items():
                table[key] = [t for t in hits if t > cutoff]
        for key in [k for k, ban in self.ip_bans.items() if float(ban.get("until", 0)) <= now]:
            del self.ip_bans[key]
        for key in [k for k, lock in self.grant_locks.items() if float(lock.get("at", 0)) <= now - GRANT_LOCK_RETAIN_SECONDS]:
            del self.grant_locks[key]
        for table in (self.strikes, self.ip_bans, self.grant_strikes, self.grant_locks):
            while len(table) > MAX_TRACKED:
                del table[next(iter(table))]

    # -- queries ---------------------------------------------------------------
    def banned(self, ip: str, now: float) -> bool:
        key = network_key(ip)
        if key is None:
            return False
        with self._lock:
            ban = self.ip_bans.get(key)
            return ban is not None and float(ban.get("until", 0)) > now

    def grant_locked(self, grant_id: str) -> bool:
        with self._lock:
            return grant_id in self.grant_locks

    # -- strikes ---------------------------------------------------------------
    def strike(self, ip: str, grant_id: str | None, reason: str, now: float) -> dict[str, Any]:
        """Record one failed attempt; return which bans/locks it triggered."""
        key = network_key(ip)
        result: dict[str, Any] = {"network": key, "ip_strikes": 0, "ip_banned": False, "grant_locked": False}
        with self._writer_lock:
            with self._lock:
                self._prune(now)
                if key is not None:
                    hits = self.strikes.setdefault(key, [])
                    hits.append(now)
                    result["ip_strikes"] = len(hits)
                    if len(hits) >= STRIKE_LIMIT and key not in self.ip_bans:
                        self.ip_bans[key] = {"at": now, "until": now + IP_BAN_SECONDS, "reason": reason, "strikes": len(hits)}
                        result["ip_banned"] = True
                if grant_id is not None and reason == "wrong_passcode":
                    hits = self.grant_strikes.setdefault(grant_id, [])
                    hits.append(now)
                    if len(hits) >= STRIKE_LIMIT and grant_id not in self.grant_locks:
                        self.grant_locks[grant_id] = {"at": now, "strikes": len(hits)}
                        result["grant_locked"] = True
                snapshot = {
                    "schema": STATE_SCHEMA,
                    "strikes": {k: list(v) for k, v in self.strikes.items()},
                    "ip_bans": {k: dict(v) for k, v in self.ip_bans.items()},
                    "grant_strikes": {k: list(v) for k, v in self.grant_strikes.items()},
                    "grant_locks": {k: dict(v) for k, v in self.grant_locks.items()},
                }
            data = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode("utf-8")
            result["save_error"] = self._save(data)
        return result
