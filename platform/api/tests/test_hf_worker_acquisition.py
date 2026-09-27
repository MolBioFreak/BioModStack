"""Offline HTTP-boundary doubles; real incoming/cache/hash/publication owners."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlencode

import pytest

from tools import bms_artifact_cache as cache_module
from tools import bms_hf_transfer as hf


def source(*, lifetime=600, host=hf.APPROVED_HOST):
    expiry = int(time.time()) + lifetime
    query = urlencode({"Signature": "private-signature", "Policy": "private-policy",
                       "Key-Pair-Id": "fixture-key", "Expires": str(expiry)})
    return {"url": f"https://{host}/fixture-object?{query}", "expires_at": expiry}


def identity(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


@pytest.fixture
def incoming(tmp_path):
    cache = cache_module.Cache(tmp_path / "cache/artifacts/v1")
    operation, batch = str(uuid.uuid4()), uuid.uuid4().hex
    path = Path(cache.incoming_batch(operation, batch, create=True))
    return cache, operation, batch, path


class SocketDouble:
    def __init__(self):
        self.interrupted = threading.Event()

    def settimeout(self, value):
        assert 0 < value <= hf.RANGE_TIMEOUT

    def shutdown(self, how):
        self.interrupted.set()


class HTTPDouble:
    """Only the direct TLS HTTP boundary is replaced; no cache/file mocks."""
    def __init__(self, monkeypatch, data, *, status=206, mutate=None, fail_start=None,
                 barrier=None, stall=False):
        self.data, self.calls, self.connections = data, [], []
        owner = self

        class Connection:
            def __init__(self, host, port, timeout, context):
                assert (host, port) == (hf.APPROVED_HOST, 443)
                assert context.check_hostname
                assert context.verify_mode == hf.ssl.CERT_REQUIRED
                assert 0 < timeout <= hf.REQUEST_TIMEOUT
                self.sock, self.closed = SocketDouble(), False
                owner.connections.append(self)

            def connect(self):
                pass

            def request(self, method, target, headers):
                assert method == "GET"
                assert set(headers) == {"Range", "Accept-Encoding"}
                assert headers["Accept-Encoding"] == "identity"
                start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
                self.start, self.end = start, end
                owner.calls.append((start, end))
                if fail_start is not None and start == fail_start:
                    raise OSError("never disclose " + target)
                if barrier is not None and start < hf.RANGE_BYTES * 8:
                    barrier.wait(timeout=5)

            def getresponse(self):
                if stall:
                    assert self.sock.interrupted.wait(3)
                    raise OSError("secret transport timeout")
                content = data[self.start:self.end + 1]
                headers = [("Content-Range", f"bytes {self.start}-{self.end}/{len(data)}"),
                           ("Content-Length", str(self.end - self.start + 1))]
                if mutate:
                    content, headers = mutate(content, headers)
                response = io.BytesIO(content)
                response.status = status
                response.getheaders = lambda: headers
                return response

            def close(self):
                self.closed = True

        monkeypatch.setattr(hf.http.client, "HTTPSConnection", Connection)


def test_source_interface_and_safe_expiry():
    value = source()
    assert hf.validate_source(value) == (value["url"], value["expires_at"])
    assert hf.validate_source(source(host=hf.APPROVED_HOST + ":443"))
    with pytest.raises(hf.SourceExpired, match="^hf_source_expired$") as error:
        hf.validate_source(source(lifetime=-1))
    assert "private" not in repr(error.value)


@pytest.mark.parametrize("mutation", [
    lambda s: {**s, "url": s["url"].replace("https:", "http:")},
    lambda s: {**s, "url": s["url"].replace(hf.APPROVED_HOST, "evil.example")},
    lambda s: {**s, "url": s["url"].replace(hf.APPROVED_HOST, "127.0.0.1")},
    lambda s: {**s, "url": s["url"].replace(hf.APPROVED_HOST, "x." + hf.APPROVED_HOST)},
    lambda s: {**s, "url": s["url"].replace(hf.APPROVED_HOST, hf.APPROVED_HOST + ":444")},
    lambda s: {**s, "url": s["url"].replace("https://", "https://user:secret@")},
    lambda s: {**s, "url": s["url"] + "#secret"},
    lambda s: {**s, "url": s["url"] + "#"},
    lambda s: {**s, "url": "\n" + s["url"]},
    lambda s: {**s, "url": s["url"] + "%0a"},
    lambda s: {**s, "url": s["url"] + "&Signature=second"},
    lambda s: {**s, "url": s["url"] + "&%50olicy=second"},
    lambda s: {**s, "url": s["url"] + "&Expires=1"},
    lambda s: {**s, "url": s["url"] + "&Key-Pair-Id=second"},
    lambda s: {**s, "url": s["url"].replace("Signature=private-signature", "Signature=")},
    lambda s: {**s, "url": s["url"].replace("Policy=", "other=")},
    lambda s: {**s, "expires_at": s["expires_at"] + 1},
    lambda s: {**s, "expires_at": True},
    lambda s: {**s, "headers": {"Authorization": "secret"}},
    lambda s: source(lifetime=hf.MAX_SOURCE_LIFETIME + 60),
    lambda s: None,
])
def test_invalid_capabilities(mutation):
    with pytest.raises(hf.TransferError) as error:
        hf.validate_source(mutation(source()))
    assert "private" not in repr(error.value)
    assert "https" not in str(error.value)


def test_eight_ranges_and_separate_publication(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    data = b"x" * (hf.RANGE_BYTES * 8 + 13)
    item = identity(data)
    boundary = HTTPDouble(monkeypatch, data, barrier=threading.Barrier(8))
    events = []
    cache.events = events.append
    result = cache.acquire_hf(item, operation, batch, source())
    assert result["state"] == "downloaded"
    assert result["received_bytes"] == len(data)
    assert result["transfer_Mbps"] > 0 and result["transfer_seconds"] > 0
    assert sorted(boundary.calls) == [(start, min(len(data), start + hf.RANGE_BYTES) - 1)
                                    for start in range(0, len(data), hf.RANGE_BYTES)]
    assert all(c.closed for c in boundary.connections)
    assert (path / item["sha256"]).read_bytes() == data
    assert cache.probe(item)["state"] == "missing"
    assert cache.ingest(item, path / item["sha256"])["state"] == "ready"
    assert cache.probe(item)["state"] == "cache_hit"
    assert cache.remove_incoming(operation, batch)["state"] == "ready"
    assert not path.exists()
    assert "private" not in json.dumps([result, events])


def test_rolling_window_starts_ninth_before_eighth_finishes(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    monkeypatch.setattr(hf, "RANGE_BYTES", 3)
    assert hf.PARALLEL_RANGES == 8
    data = bytes(range(73))  # Generic bytes, including a short final range.
    item = identity(data)
    leaf = path / item["sha256"]
    boundary = HTTPDouble(monkeypatch, data)
    first_window = threading.Barrier(hf.PARALLEL_RANGES)
    ninth_started, eighth_finished = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = 0
    submitted = []
    fetch = hf._fetch_range

    class WindowExecutor(hf.ThreadPoolExecutor):
        def __init__(self, *, max_workers):
            assert max_workers == hf.PARALLEL_RANGES
            super().__init__(max_workers=max_workers)

        def submit(self, fn, /, *args, **kwargs):
            url, expiry, start, end, size, deadline = args
            # Includes running, queued AND completed-but-unwritten ranges.
            prefix = leaf.read_bytes()
            assert prefix == data[:len(prefix)]
            assert end + 1 - len(prefix) <= hf.PARALLEL_RANGES * hf.RANGE_BYTES
            submitted.append(start)
            return super().submit(fn, url, expiry, start, end, size, deadline)

    def controlled_fetch(url, expiry, start, end, size, deadline):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if start < 8 * hf.RANGE_BYTES:
                first_window.wait(timeout=5)
            if start == 7 * hf.RANGE_BYTES:
                # A fixed-batch scheduler cannot satisfy this dependency.
                assert ninth_started.wait(5)
            elif start == 8 * hf.RANGE_BYTES:
                assert not eighth_finished.is_set()
                ninth_started.set()
            return fetch(url, expiry, start, end, size, deadline)
        finally:
            if start == 7 * hf.RANGE_BYTES:
                eighth_finished.set()
            with lock:
                active -= 1

    monkeypatch.setattr(hf, "ThreadPoolExecutor", WindowExecutor)
    monkeypatch.setattr(hf, "_fetch_range", controlled_fetch)
    result = cache.acquire_hf(item, operation, batch, source())
    assert ninth_started.is_set() and eighth_finished.is_set()
    assert active == 0 and peak == hf.PARALLEL_RANGES
    assert submitted == list(range(0, len(data), hf.RANGE_BYTES))
    assert len(boundary.calls) == len(submitted)
    assert all(connection.closed for connection in boundary.connections)
    assert result["received_bytes"] == len(data)
    assert leaf.read_bytes() == data
    assert cache.probe(item)["state"] == "missing"


@pytest.mark.parametrize("status", [200, 301, 302, 307, 308, 401, 403, 404, 500])
def test_http_errors_never_publish(incoming, monkeypatch, status):
    cache, operation, batch, path = incoming
    item = identity(b"abc")
    boundary = HTTPDouble(monkeypatch, b"abc", status=status)
    with pytest.raises(hf.TransferError):
        cache.acquire_hf(item, operation, batch, source())
    assert len(boundary.calls) == 1  # no follow, fallback or automatic retry
    assert cache.probe(item)["state"] == "missing"
    assert (path / item["sha256"]).stat().st_size == 0


@pytest.mark.parametrize("mutate", [
    lambda b, h: (b[:-1], h),
    lambda b, h: (b, [(k, "bytes 1-3/3" if k == "Content-Range" else v) for k, v in h]),
    lambda b, h: (b, [(k, "999" if k == "Content-Length" else v) for k, v in h]),
    lambda b, h: (b, h + [("content-length", "3")]),
    lambda b, h: (b, h + [("Content-Encoding", "gzip")]),
    lambda b, h: (b, h + [("Transfer-Encoding", "chunked")]),
])
def test_range_headers_and_length_exact(incoming, monkeypatch, mutate):
    cache, operation, batch, _ = incoming
    item = identity(b"abc")
    HTTPDouble(monkeypatch, b"abc", mutate=mutate)
    with pytest.raises(hf.TransferError):
        cache.acquire_hf(item, operation, batch, source())
    assert cache.probe(item)["state"] == "missing"


def test_partial_failure_retry_rehashes_all_bytes(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    monkeypatch.setattr(hf, "RANGE_BYTES", 3)
    data, item = b"abcdefghi", identity(b"abcdefghi")
    HTTPDouble(monkeypatch, data, fail_start=3)
    with pytest.raises(hf.TransferError, match="hf_transport_failed") as error:
        cache.acquire_hf(item, operation, batch, source())
    assert "private" not in repr(error.value)
    assert (path / item["sha256"]).read_bytes() == b"abc"
    with pytest.raises(ValueError):
        cache.ingest(item, path / item["sha256"])
    expired = cache.acquire_hf(item, operation, batch, source(lifetime=-1))
    assert expired["state"] == "source_expired"
    boundary = HTTPDouble(monkeypatch, data)
    result = cache.acquire_hf(item, operation, batch, source())
    assert result["received_bytes"] == 6
    assert sorted(boundary.calls) == [(3, 5), (6, 8)]
    assert cache.ingest(item, path / item["sha256"])["state"] == "ready"


def test_corrupt_prefix_and_full_digest_rejected(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    item = identity(b"abcdef")
    leaf = path / item["sha256"]
    leaf.write_bytes(b"xxx")
    leaf.chmod(0o600)
    HTTPDouble(monkeypatch, b"abcdef")
    with pytest.raises(hf.TransferError, match="hf_identity_mismatch"):
        cache.acquire_hf(item, operation, batch, source())
    assert leaf.read_bytes() == b""
    assert cache.probe(item)["state"] == "missing"
    HTTPDouble(monkeypatch, b"xxxxxx")
    with pytest.raises(hf.TransferError, match="hf_identity_mismatch"):
        cache.acquire_hf(item, operation, batch, source())
    HTTPDouble(monkeypatch, b"abcdef")
    assert cache.acquire_hf(item, operation, batch, source())["state"] == "downloaded"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "permissions"])
def test_unsafe_incoming_leaf(incoming, tmp_path, kind):
    cache, operation, batch, path = incoming
    item = identity(b"abc")
    outside = tmp_path / "outside"
    outside.write_bytes(b"abc")
    outside.chmod(0o600)
    leaf = path / item["sha256"]
    if kind == "symlink":
        leaf.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, leaf)
    elif kind == "fifo":
        os.mkfifo(leaf, 0o600)
    elif kind == "directory":
        leaf.mkdir()
    else:
        leaf.write_bytes(b"abc")
        leaf.chmod(0o644)
    with pytest.raises(hf.TransferError):
        cache.acquire_hf(item, operation, batch, source())
    assert outside.read_bytes() == b"abc"
    assert cache.probe(item)["state"] == "missing"


@pytest.mark.parametrize("mutation", ["operation", "batch", "missing", "parent_symlink"])
def test_incoming_identity_required(incoming, tmp_path, mutation):
    cache, operation, batch, path = incoming
    if mutation == "operation":
        operation = "../../escape"
    elif mutation == "batch":
        batch = str(uuid.uuid4())
    elif mutation == "missing":
        batch = uuid.uuid4().hex
    else:
        path.rmdir()
        path.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(hf.TransferError):
        cache.acquire_hf(identity(b"abc"), operation, batch, source())


def test_concurrent_writer_rejected(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    item = identity(b"abc")
    leaf = path / item["sha256"]
    fd = os.open(leaf, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(hf.TransferError, match="hf_acquisition_busy"):
            cache.acquire_hf(item, operation, batch, source())
        assert leaf.read_bytes() == b""
    finally:
        os.close(fd)
    HTTPDouble(monkeypatch, b"abc")
    assert cache.acquire_hf(item, operation, batch, source())["state"] == "downloaded"


def test_slow_header_hard_deadline(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    monkeypatch.setattr(hf, "RANGE_TIMEOUT", 0.15)
    boundary = HTTPDouble(monkeypatch, b"abc", stall=True)
    started = time.monotonic()
    with pytest.raises(hf.TransferError, match="hf_transfer_timeout"):
        cache.acquire_hf(identity(b"abc"), operation, batch, source())
    assert time.monotonic() - started < 2
    assert boundary.connections[0].closed
    assert (path / identity(b"abc")["sha256"]).stat().st_size == 0


def test_dns_deadline_without_live_resolution(monkeypatch):
    release = threading.Event()
    def resolve(*args, **kwargs):
        release.wait(2)
        return []
    monkeypatch.setattr(hf.socket, "getaddrinfo", resolve)
    try:
        started = time.monotonic()
        with pytest.raises(hf.TransferError, match="hf_transport_timeout"):
            hf._connect_socket((hf.APPROVED_HOST, 443), 0.05)
        assert time.monotonic() - started < 1
    finally:
        release.set()


def test_json_action_sanitizes_invalid_capability(incoming):
    cache, operation, batch, _ = incoming
    request = {"action": "acquire_hf", "artifact": identity(b"abc"),
               "operation_id": operation, "batch_id": batch,
               "source": source(host="invalid.example")}
    result = subprocess.run([sys.executable, cache_module.__file__, "--root", str(cache.root)],
                            input=json.dumps(request), text=True, capture_output=True)
    assert result.returncode == 1
    assert json.loads(result.stderr) == {"state": "failed", "error": "TransferError"}
    assert "private" not in result.stdout + result.stderr
    assert "https" not in result.stdout + result.stderr


def test_json_action_dispatch_success(incoming, monkeypatch, capsys):
    cache, operation, batch, path = incoming
    item = identity(b"abc")
    HTTPDouble(monkeypatch, b"abc")
    request = {"action": "acquire_hf", "artifact": item, "operation_id": operation,
               "batch_id": batch, "source": source()}
    monkeypatch.setattr(sys, "argv", [cache_module.__file__, "--root", str(cache.root)])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(request).encode())))
    cache_module.main()
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "downloaded"
    assert (path / item["sha256"]).read_bytes() == b"abc"
    assert cache.probe(item)["state"] == "missing"


def test_runtime_image_uses_existing_leased_store(incoming, monkeypatch):
    # Deliberately not executable SIF bytes: tests storage/lifecycle, not science.
    cache, operation, batch, path = incoming
    data = b"explicit non-scientific runtime-image fixture"
    item = {**identity(data), "kind": "runtime_image"}
    HTTPDouble(monkeypatch, data)
    assert cache.acquire_hf(item, operation, batch, source())["state"] == "downloaded"
    assert cache.probe(item)["state"] == "missing"
    assert cache.ingest(item, path / item["sha256"])["state"] == "ready"
    published = cache.image_path(item)
    assert published.read_bytes() == data
    assert published.stat().st_mode & 0o777 == 0o400
    assert published.parent.stat().st_mode & 0o777 == 0o500
    assert published.stat().st_nlink == 1
    assert not list(Path(cache.root / "objects/sha256").rglob(item["sha256"]))
    state = cache_module.runtime_lifecycle().load_state(cache.image_store)
    assert any(lease["owner"] == "cache-artifact:" + item["sha256"]
               for lease in state["leases"].values())
    cache.remove_incoming(operation, batch)
    assert cache.probe(item)["state"] == "cache_hit"


def test_expiry_during_transfer_retains_only_completed_prefix(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    monkeypatch.setattr(hf, "RANGE_BYTES", 3)
    monkeypatch.setattr(hf, "PARALLEL_RANGES", 1)
    capability = source()
    clock = [time.time()]
    monkeypatch.setattr(hf.time, "time", lambda: clock[0])
    def expire_second(data, headers):
        if data == b"def":
            clock[0] = capability["expires_at"] + 1
        return data, headers
    HTTPDouble(monkeypatch, b"abcdef", mutate=expire_second)
    item = identity(b"abcdef")
    assert cache.acquire_hf(item, operation, batch, capability)["state"] == "source_expired"
    assert (path / item["sha256"]).read_bytes() == b"abc"
    assert cache.probe(item)["state"] == "missing"
    boundary = HTTPDouble(monkeypatch, b"abcdef")
    assert cache.acquire_hf(item, operation, batch, source())["state"] == "downloaded"
    assert boundary.calls == [(3, 5)]


def test_active_acquisition_excludes_second_writer(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    entered, release = threading.Event(), threading.Event()
    def blocked(data, headers):
        entered.set()
        assert release.wait(3)
        return data, headers
    boundary = HTTPDouble(monkeypatch, b"abc", mutate=blocked)
    item, results = identity(b"abc"), []
    def acquire():
        try:
            results.append(cache.acquire_hf(item, operation, batch, source()))
        except Exception as error:
            results.append(error)
    thread = threading.Thread(target=acquire)
    thread.start()
    try:
        assert entered.wait(3)
        with pytest.raises(hf.TransferError, match="hf_acquisition_busy"):
            cache.acquire_hf(item, operation, batch, source())
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert len(results) == 1 and results[0]["state"] == "downloaded"
    assert len(boundary.calls) == 1
    assert (path / item["sha256"]).read_bytes() == b"abc"


def test_overall_deadline_and_byte_limit(incoming, monkeypatch):
    cache, operation, batch, path = incoming
    item = identity(b"abc")
    monkeypatch.setattr(hf, "TRANSFER_TIMEOUT", 0)
    with pytest.raises(hf.TransferError, match="hf_transfer_timeout"):
        cache.acquire_hf(item, operation, batch, source())
    assert (path / item["sha256"]).read_bytes() == b""
    with pytest.raises(hf.TransferError, match="hf_artifact_too_large"):
        cache.acquire_hf({**item, "size_bytes": hf.MAX_ARTIFACT_BYTES + 1}, operation, batch, source())
    assert cache.probe(item)["state"] == "missing"
