"""Real local HTTP bodies, interruptions and resume; no external HF access.

Only TLS connection creation is redirected to a local server. Production source
validation, HTTP parsing, range checks, file ownership and digest verification run.
"""
import http.client
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn, UnixStreamServer
import tempfile
from pathlib import Path
import socket
import threading
import time
import uuid

import pytest

from test_hf_worker_acquisition import hf, identity, incoming, source


@pytest.fixture
def local_http(monkeypatch):
    state = {"data": bytes(range(60)), "calls": [], "delay": 0,
             "interrupt": None, "stall": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            start, end = map(int, self.headers["Range"][6:].split("-"))
            state["calls"].append((start, end))
            if state["stall"]:
                time.sleep(0.5)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(state['data'])}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            time.sleep(state["delay"])
            body = state["data"][start:end + 1]
            if start == state["interrupt"]:
                self.wfile.write(body[:1])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The kill/resume test deliberately disconnects mid-response.
                pass

    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True

    temporary = tempfile.TemporaryDirectory(prefix="hf-http-")
    address = temporary.name + "/http.sock"
    server = Server(address, Handler)
    state["address"] = address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LocalConnection(http.client.HTTPConnection):
        def __init__(self, host, port, timeout, context):
            assert host == hf.APPROVED_HOST and port == 443
            assert context.check_hostname
            super().__init__("localhost", timeout=timeout)

        def connect(self):
            # Real HTTP over AF_UNIX respects the repository's offline lane;
            # no INET/DNS allowlist or live-service opt-in is bypassed.
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect(address)

    monkeypatch.setattr(hf.http.client, "HTTPSConnection", LocalConnection)
    monkeypatch.setattr(hf, "RANGE_BYTES", 3)
    monkeypatch.setattr(hf, "PARALLEL_RANGES", 1)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        temporary.cleanup()


def test_real_http_progress_outlives_artifact_budget(incoming, local_http, monkeypatch):
    cache, operation, batch, path = incoming
    monkeypatch.setattr(hf, "TRANSFER_TIMEOUT", 0.3)
    local_http["delay"] = 0.05
    item = identity(local_http["data"])
    started = time.monotonic()
    result = cache.acquire_hf(item, operation, batch, source())
    assert time.monotonic() - started > hf.TRANSFER_TIMEOUT
    assert result["received_bytes"] == len(local_http["data"])
    assert (path / item["sha256"]).read_bytes() == local_http["data"]
    assert cache.probe(item)["state"] == "missing"
    assert cache.ingest(item, path / item["sha256"])["state"] == "ready"


def test_real_http_interruption_resumes_new_operation(incoming, local_http):
    cache, operation, batch, path = incoming
    item = identity(local_http["data"])
    local_http["interrupt"] = 6
    with pytest.raises(hf.TransferError, match="hf_range_length_mismatch"):
        cache.acquire_hf(item, operation, batch, source())
    partial = Path(cache.root) / "hf-partials" / item["sha256"]
    assert partial.read_bytes() == local_http["data"][:6]
    assert not (path / item["sha256"]).exists()
    assert cache.probe(item)["state"] == "missing"
    cache.remove_incoming(operation, batch)
    operation, batch = str(uuid.uuid4()), uuid.uuid4().hex
    path = Path(cache.incoming_batch(operation, batch, create=True))
    local_http["interrupt"] = None
    local_http["calls"].clear()
    result = cache.acquire_hf(item, operation, batch, source())
    assert result["received_bytes"] == len(local_http["data"]) - 6
    assert min(start for start, _ in local_http["calls"]) == 6
    assert not partial.exists()
    assert (path / item["sha256"]).read_bytes() == local_http["data"]
    assert cache.ingest(item, path / item["sha256"])["state"] == "ready"


def test_real_http_stall_remains_bounded(incoming, local_http, monkeypatch):
    cache, operation, batch, _ = incoming
    local_http["stall"] = True
    monkeypatch.setattr(hf, "RANGE_TIMEOUT", 0.15)
    started = time.monotonic()
    with pytest.raises(hf.TransferError, match="hf_transfer_timeout"):
        cache.acquire_hf(identity(local_http["data"]), operation, batch, source())
    assert time.monotonic() - started < 1


def test_killed_acquirer_resumes_new_operation(incoming, local_http):
    cache, operation, batch, _ = incoming
    item = identity(local_http["data"])
    local_http["delay"] = 0.08
    script = '''
import http.client, json, socket, sys
from tools import bms_artifact_cache as cache_module, bms_hf_transfer as hf
root, operation, batch, item, source, address = json.loads(sys.argv[1])
class LocalConnection(http.client.HTTPConnection):
    def __init__(self, host, port, timeout, context):
        super().__init__('localhost', timeout=timeout)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(address)
hf.http.client.HTTPSConnection = LocalConnection
hf.RANGE_BYTES, hf.PARALLEL_RANGES = 3, 1
cache_module.Cache(root).acquire_hf(item, operation, batch, source)
'''
    args = json.dumps([str(cache.root), operation, batch, item, source(), local_http["address"]])
    process = subprocess.Popen([sys.executable, "-c", script, args],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    partial = Path(cache.root) / "hf-partials" / item["sha256"]
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if partial.exists() and partial.stat().st_size >= 6:
                break
            assert process.poll() is None
            time.sleep(0.01)
        assert partial.exists() and 6 <= partial.stat().st_size < len(local_http["data"])
        process.kill()
        process.communicate(timeout=5)
        retained = partial.stat().st_size
        assert partial.read_bytes() == local_http["data"][:retained]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    cache.remove_incoming(operation, batch)
    operation, batch = str(uuid.uuid4()), uuid.uuid4().hex
    path = Path(cache.incoming_batch(operation, batch, create=True))
    local_http["delay"] = 0
    result = cache.acquire_hf(item, operation, batch, source())
    assert result["received_bytes"] == len(local_http["data"]) - retained
    assert (path / item["sha256"]).read_bytes() == local_http["data"]
    assert cache.ingest(item, path / item["sha256"])["state"] == "ready"


def test_diagnostics_are_closed_codes_only():
    assert hf.TransferError("hf_transfer_timeout").code == "hf_transfer_timeout"
    assert hf.TransferError("https://secret/?Signature=secret").code == "hf_acquisition_failed"
