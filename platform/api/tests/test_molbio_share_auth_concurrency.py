"""Isolated real-ASGI regressions for public-share concurrent authentication."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import threading
import time

import pytest

from share_server import app as A
from share_server import bans as B
from share_server import grants as G


@pytest.fixture
def setup_share(tmp_path):
    publish = tmp_path / "publish"
    publish.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    app = A.ShareApp(A.ShareSettings(publish, tmp_path / "access.jsonl", None, state))
    return app, publish


def grant(root):
    G.prepare_publish_dir(root)
    key, code, gid = G.new_link_key(), G.new_passcode(), G.new_grant_id()
    now = G.utc_now()
    bundle = b'{"schema":"test"}'
    G.atomic_write(G.bundle_path(root, gid), bundle)
    record = {"grant_id": gid, "key_sha256": G.key_sha256(key), "passcode": G.hash_passcode(code),
              "created_at": now, "expires_at": now + 3600, "revoked_at": None,
              "bundle_sha256": hashlib.sha256(bundle).hexdigest()}
    document = G.load_index(root)
    G.save_index(root, {"grants": [*document["grants"], record]})
    return gid, key, code


def update(root, gid, **changes):
    document = G.load_index(root)
    for record in document["grants"]:
        if record["grant_id"] == gid:
            record.update(changes)
    G.save_index(root, {"grants": document["grants"]})


async def request(app, key="", code="", ip="203.0.113.1", *, extra=(), path="/api/share/bundle", method="GET"):
    scope = {"type": "http", "method": method, "path": path, "query_string": b"", "scheme": "http",
             "client": ("127.0.0.1", 1234),
             "headers": [(b"x-forwarded-for", ip.encode()), (A.KEY_HEADER, key.encode()),
                         (A.PASSCODE_HEADER, code.encode()), (b"host", b"share.example:8443"), *extra]}
    sent = []

    async def receive():
        raise AssertionError("authentication must precede body reads")

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent[0]["status"]


def outcomes(app):
    return [json.loads(line) for line in app.settings.access_log.read_text().splitlines()]


def counted_verifier(monkeypatch):
    real = G.verify_passcode
    calls = []

    def verify(code, record):
        calls.append(code)
        time.sleep(0.015)
        return real(code, record)

    monkeypatch.setattr(G, "verify_passcode", verify)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("distributed", [False, True])
@pytest.mark.parametrize("warm_cache", [False, True])
async def test_queued_guesses_stop_at_three_and_correct_is_denied(setup_share, monkeypatch, distributed, warm_cache):
    app, root = setup_share
    gid, key, code = grant(root)
    if warm_cache:
        assert await request(app, key, code) == 200
    calls = counted_verifier(monkeypatch)
    ips = [f"198.51.100.{i + 1}" if distributed else "203.0.113.1" for i in range(12)]
    jobs = [request(app, key, "WRONG", ip) for ip in ips]
    jobs.append(request(app, key, code, "192.0.2.9" if distributed else ips[0]))
    statuses = await asyncio.gather(*jobs)
    assert len(calls) == 3
    assert statuses[:2] == [401, 401]
    assert all(s == (423 if distributed else 403) for s in statuses[2:])
    assert len(app.bans.grant_strikes[gid]) == 3
    assert not app._network_auth.entries and not app._grant_auth.entries
    restarted = A.ShareApp(app.settings)
    assert await request(restarted, key, code, "192.0.2.10") == 423


@pytest.mark.asyncio
async def test_same_ipv6_network_serializes_across_different_grants(setup_share, monkeypatch):
    app, root = setup_share
    records = [grant(root) for _ in range(5)]
    calls = counted_verifier(monkeypatch)
    statuses = await asyncio.gather(*(request(app, key, "WRONG", f"2001:db8:12:34::{i + 1}")
                                     for i, (_, key, _) in enumerate(records * 2)))
    assert len(calls) == 3 and statuses[:3] == [401, 401, 403]
    assert statuses[3:] == [403] * 7
    assert await request(app, records[4][1], records[4][2], "2001:db8:12:35::1") == 200
    assert await request(app, records[4][1], records[4][2], "2001:db8:12:34::ffff") == 403


@pytest.mark.asyncio
async def test_unknown_key_concurrency_does_not_add_queued_strikes(setup_share):
    app, root = setup_share
    _, key, code = grant(root)
    statuses = await asyncio.gather(*(request(app, G.new_link_key(), code) for _ in range(16)), request(app, key, code))
    assert statuses[:3] == [404] * 3 and statuses[3:] == [403] * 14
    assert len(app.bans.strikes["203.0.113.1"]) == 3
    assert sum(row["outcome"] == "unknown_key" for row in outcomes(app)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked", "expired", "verifier"])
async def test_state_changes_during_scrypt_are_rechecked(setup_share, monkeypatch, change):
    app, root = setup_share
    gid, key, code = grant(root)
    entered, release = threading.Event(), threading.Event()
    real = G.verify_passcode

    def slow(code, record):
        entered.set()
        assert release.wait(3)
        return real(code, record)

    monkeypatch.setattr(G, "verify_passcode", slow)
    job = asyncio.create_task(request(app, key, code))
    try:
        for _ in range(300):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        if change == "revoked":
            update(root, gid, revoked_at=G.utc_now())
        elif change == "expired":
            update(root, gid, expires_at=G.utc_now() - 1)
        else:
            update(root, gid, passcode=G.hash_passcode("ABCDEFGH" if code != "ABCDEFGH" else "BCDEFGHJ"))
    finally:
        release.set()
    assert await job == (401 if change == "verifier" else 410)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["network", "grant"])
async def test_ban_or_lock_applied_during_hash_denies_correct_code(setup_share, monkeypatch, kind):
    app, root = setup_share
    gid, key, code = grant(root)
    entered, release = threading.Event(), threading.Event()
    real = G.verify_passcode

    def slow(code, record):
        entered.set()
        assert release.wait(3)
        return real(code, record)

    monkeypatch.setattr(G, "verify_passcode", slow)
    job = asyncio.create_task(request(app, key, code))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.005)
        for i in range(3):
            await asyncio.to_thread(app.bans.strike,
                                    "203.0.113.1" if kind == "network" else f"192.0.2.{i + 1}",
                                    gid if kind == "grant" else None,
                                    "wrong_passcode" if kind == "grant" else "unknown_key", time.time())
    finally:
        release.set()
    assert await job == (403 if kind == "network" else 423)


@pytest.mark.asyncio
async def test_revocation_while_waiting_for_scrypt_skips_hash(setup_share, monkeypatch):
    app, root = setup_share
    gid, key, code = grant(root)
    calls = counted_verifier(monkeypatch)
    for _ in range(A.SCRYPT_CONCURRENCY):
        await app._scrypt.acquire()
    job = asyncio.create_task(request(app, key, code))
    try:
        for _ in range(100):
            if app._grant_auth.entries:
                break
            await asyncio.sleep(0)
        assert app._grant_auth.entries
        update(root, gid, revoked_at=G.utc_now())
    finally:
        for _ in range(A.SCRYPT_CONCURRENCY):
            app._scrypt.release()
    assert await job == 410 and not calls


@pytest.mark.asyncio
async def test_cache_binds_current_verifier_and_revoke(setup_share):
    app, root = setup_share
    gid, key, code = grant(root)
    assert await request(app, key, code) == 200
    new_code = "ABCDEFGH" if code != "ABCDEFGH" else "BCDEFGHJ"
    update(root, gid, passcode=G.hash_passcode(new_code))
    assert await request(app, key, code) == 401
    assert await request(app, key, new_code) == 200
    update(root, gid, revoked_at=G.utc_now())
    assert await request(app, key, new_code) == 410


@pytest.mark.asyncio
async def test_unrelated_valid_clients_are_not_globally_serialized(setup_share, monkeypatch):
    app, root = setup_share
    records = [grant(root), grant(root)]
    barrier = threading.Barrier(2)
    real = G.verify_passcode

    def simultaneous(code, record):
        barrier.wait(timeout=3)
        return real(code, record)

    monkeypatch.setattr(G, "verify_passcode", simultaneous)
    assert await asyncio.gather(*(request(app, key, code, f"192.0.2.{i + 1}")
                                  for i, (_, key, code) in enumerate(records))) == [200, 200]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["mkstemp", "fsync", "replace", "directory_fsync"])
async def test_save_errors_are_logged_without_resetting_memory(setup_share, monkeypatch, failure):
    app, root = setup_share
    gid, key, code = grant(root)
    real_fsync = B.os.fsync
    if failure == "mkstemp":
        def broken_mkstemp(*args, **kwargs):
            raise OSError(f"test mkstemp failure {key} {code}")
        monkeypatch.setattr(B.tempfile, "mkstemp", broken_mkstemp)
    elif failure == "replace":
        def broken_replace(*args, **kwargs):
            raise OSError(f"test replace failure {key} {code}")
        monkeypatch.setattr(B.os, "replace", broken_replace)
    else:
        def broken_fsync(fd):
            if failure == "fsync" or stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(f"test fsync failure {key} {code}")
            return real_fsync(fd)
        monkeypatch.setattr(B.os, "fsync", broken_fsync)
    statuses = [await request(app, key, "WRONG") for _ in range(3)]
    assert statuses == [401, 401, 403]
    assert await request(app, key, code) == 403
    assert await request(app, key, code, "192.0.2.10") == 423
    assert len(app.bans.grant_strikes[gid]) == 3
    errors = [row for row in outcomes(app) if row["outcome"] == "ban_state_error"]
    assert len(errors) == 3 and all(row["error_class"] == "OSError" and row["operation"] == "save" for row in errors)
    log = app.settings.access_log.read_text()
    assert key not in log and code not in log


@pytest.mark.asyncio
async def test_bad_load_is_reported_once_without_new_refusal_gate(setup_share):
    app, root = setup_share
    _, key, code = grant(root)
    (app.settings.state_dir / B.STATE_FILENAME).write_text("not json")
    assert await request(app, key, code) == 200
    assert await request(app, key, code) == 200
    assert sum(row["outcome"] == "ban_state_error" and row["operation"] == "load" for row in outcomes(app)) == 1
    assert (app.settings.state_dir / (B.STATE_FILENAME + ".unreadable")).exists()


@pytest.mark.asyncio
async def test_slow_fsync_does_not_block_queries_or_event_loop(setup_share, monkeypatch):
    app, root = setup_share
    _, key, code = grant(root)
    entered, release = threading.Event(), threading.Event()
    real = B.os.fsync

    def slow(fd):
        entered.set()
        release.wait(0.8)  # bounded watchdog also makes the old blocking implementation fail visibly
        return real(fd)

    monkeypatch.setattr(B.os, "fsync", slow)
    job = asyncio.create_task(request(app, G.new_link_key(), code))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.005)
        start = time.monotonic()
        assert await request(app, key, code, "192.0.2.7") == 200
        ticks = 0
        for _ in range(5):
            assert await request(app, path="/api/share/health", ip="192.0.2.8") == 200
            await asyncio.sleep(0.005)
            ticks += 1
        assert ticks == 5 and time.monotonic() - start < 0.5
    finally:
        release.set()
    assert await job == 404


@pytest.mark.asyncio
async def test_persisted_writer_order_and_directory_fsync(setup_share, monkeypatch):
    app, _ = setup_share
    real = B.os.fsync
    directories = []

    def fsync(fd):
        directories.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return real(fd)

    monkeypatch.setattr(B.os, "fsync", fsync)
    await asyncio.gather(*(request(app, G.new_link_key(), "wrong", f"192.0.2.{i + 1}") for i in range(20)))
    stored = json.loads(app.bans.path.read_text())
    assert stored["strikes"] == app.bans.strikes
    assert len(stored["strikes"]) == 20
    assert directories == [False, True] * 20


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    ((b"sec-fetch-site", b"cross-site"),),
    ((b"sec-fetch-site", b"same-site"),),
    ((b"origin", b"https://evil.example"),),
    ((b"origin", b"null"),),
    ((b"origin", b"http://share.example:8443"),),
    ((b"origin", b"https://share.example:8443.evil.example"),),
])
async def test_cross_site_requests_never_consume_strikes(setup_share, headers):
    app, root = setup_share
    _, key, code = grant(root)
    for _ in range(5):
        assert await request(app, key, "WRONG", extra=headers, method="POST", path="/api/molbio/restriction/analyze") == 403
        assert await request(app, G.new_link_key(), "WRONG", extra=headers) == 403
    assert not app.bans.strikes and not app.bans.grant_strikes
    assert not app._auth_tasks
    assert await request(app, key, code) == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [(), ((b"sec-fetch-site", b"none"),),
    ((b"sec-fetch-site", b"same-origin"),),
    ((b"origin", b"https://share.example:8443"), (b"sec-fetch-site", b"same-origin")),
])
async def test_same_origin_and_agents_remain_allowed(setup_share, headers):
    app, root = setup_share
    _, key, code = grant(root)
    assert await request(app, key, code, extra=headers) == 200


@pytest.mark.asyncio
async def test_cancelled_http_keeps_auth_locks_until_strike_finishes(setup_share, monkeypatch):
    app, root = setup_share
    _, key, code = grant(root)
    entered, release = threading.Event(), threading.Event()
    real = G.verify_passcode

    def slow(code, record):
        entered.set()
        assert release.wait(3)
        return real(code, record)

    monkeypatch.setattr(G, "verify_passcode", slow)
    first = asyncio.create_task(request(app, key, "WRONG"))
    while not entered.is_set():
        await asyncio.sleep(0.005)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    queued = asyncio.create_task(request(app, key, code))
    await asyncio.sleep(0.02)
    assert app._network_auth.entries and not queued.done()
    release.set()
    assert await queued == 200
    assert len(app.bans.strikes["203.0.113.1"]) == 1
    assert not app._network_auth.entries and not app._grant_auth.entries


@pytest.mark.asyncio
async def test_active_key_capacity_waits_and_cleans_on_cancellation(monkeypatch):
    monkeypatch.setattr(A, "TRACKED_KEYS", 1)
    pool = A._KeyedLocks()
    entered = asyncio.Event()

    async def use(key):
        async with pool.hold(key):
            entered.set()

    async with pool.hold("first"):
        queued = asyncio.create_task(use("second"))
        await asyncio.sleep(0)
        assert len(pool.entries) == 1 and not entered.is_set()
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
    assert not pool.entries
    await use("second")
    assert not pool.entries
