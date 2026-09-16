"""Real loopback HTTP/file IO only; no scientific assets or registry fixtures."""
import hashlib
import json
import os
import socket
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.pinned_acquisition import AcquisitionError, Artifact, acquire
from lib.shared_runtime_images import SharedRuntimeImageError

PAYLOAD = b'Explicit BMS acquisition transport test bytes; NOT A MODEL.\n' * 4000


@pytest.fixture(autouse=True)
def cleanup_readonly_objects(tmp_path):
    yield
    for parent, directories, _ in os.walk(tmp_path):
        os.chmod(parent, 0o700)
        for name in directories:
            child = Path(parent) / name
            if not child.is_symlink():
                child.chmod(0o700)


@pytest.fixture
def source():
    class Handler(BaseHTTPRequestHandler):
        requests = []
        interrupt = False
        corrupt = False
        oversized = False
        split_oversized = False
        redirect = False
        bad_range = False
        unavailable = False
        delayed = False
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            cls = type(self)
            cls.requests.append(self.headers.get('Range'))
            if cls.delayed:
                time.sleep(0.1)
                return
            if cls.unavailable:
                self.send_error(503)
                return
            if cls.redirect:
                self.send_response(302)
                self.send_header('Location', '/unapproved')
                self.end_headers()
                return
            offset = int(self.headers.get('Range', 'bytes=0-')[6:-1])
            payload = b'x' * len(PAYLOAD) if cls.corrupt else PAYLOAD
            if cls.oversized:
                payload += b'x'
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header('Content-Range', 'invalid' if cls.bad_range else
                                 f'bytes {offset}-{len(payload)-1}/{len(payload)}')
            self.send_header('Content-Length', str(len(payload)-offset))
            self.end_headers()
            if cls.interrupt:
                cls.interrupt = False
                self.wfile.write(payload[offset:offset+10000])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
            elif cls.split_oversized:
                self.wfile.write(payload[offset:-1])
                self.wfile.flush()
                time.sleep(0.15)
                self.wfile.write(payload[-1:])
            else:
                self.wfile.write(payload[offset:])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    authority = f'127.0.0.1:{server.server_port}'
    artifact = Artifact('fixture', 'image', f'http://{authority}/bytes',
                        hashlib.sha256(PAYLOAD).hexdigest(), len(PAYLOAD),
                        authority, 'TEST-ONLY-NOT-RELEASE-APPROVAL')
    try:
        yield artifact, Handler
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_oversize_rejection_cannot_be_laundered_by_resume(source, tmp_path):
    artifact, handler = source
    handler.oversized = handler.split_oversized = True
    with pytest.raises(AcquisitionError, match='exceeds pinned'):
        acquire(artifact, tmp_path, test_only=True)
    state = json.loads(next(tmp_path.rglob('state.json')).read_text())
    assert state['bytes'] == artifact.size_bytes
    handler.oversized = handler.split_oversized = False
    with pytest.raises(AcquisitionError, match='previous acquisition rejected'):
        acquire(artifact, tmp_path, test_only=True)
    assert len(handler.requests) == 1
    assert not list(tmp_path.rglob('runtime.sif'))


def test_real_download_revalidation_and_corrupt_cache(source, tmp_path):
    artifact, handler = source
    receipt = acquire(artifact, tmp_path, test_only=True)
    result = Path(receipt['path'])
    assert result.read_bytes() == PAYLOAD
    assert receipt['qualification'] == 'not_checked'
    assert receipt['test_only'] is True
    assert 'test-fixtures-not-scientific-assets' in result.parts
    assert acquire(artifact, tmp_path, test_only=True)['verification'] == receipt['verification']
    assert len(handler.requests) == 1
    result.chmod(0o600)
    result.write_bytes(b'x' * len(PAYLOAD))
    result.chmod(0o400)
    with pytest.raises(SharedRuntimeImageError, match='SHA-256'):
        acquire(artifact, tmp_path, test_only=True)
    assert len(handler.requests) == 1


def test_interrupted_resume_across_invocations(source, tmp_path):
    artifact, handler = source
    handler.interrupt = True
    with pytest.raises(AcquisitionError, match='download failed'):
        acquire(artifact, tmp_path, attempts=1, test_only=True)
    state = next(tmp_path.rglob('state.json'))
    assert json.loads(state.read_text())['bytes'] == 10000
    result = acquire(artifact, tmp_path, test_only=True)
    assert Path(result['path']).read_bytes() == PAYLOAD
    assert handler.requests == [None, 'bytes=10000-']


def test_bounded_retry_finishes_interrupted_download(source, tmp_path):
    artifact, handler = source
    handler.interrupt = True
    assert acquire(artifact, tmp_path, attempts=2, test_only=True)['verification']['size'] == len(PAYLOAD)
    assert len(handler.requests) == 2


def test_changed_manifest_and_corrupt_checkpoint_rejected(source, tmp_path):
    artifact, handler = source
    handler.interrupt = True
    with pytest.raises(AcquisitionError):
        acquire(artifact, tmp_path, attempts=1, test_only=True)
    with pytest.raises(AcquisitionError, match='manifest changed'):
        acquire(replace(artifact, approval_ref='different approval'), tmp_path, test_only=True)
    next(tmp_path.rglob('payload.part')).write_bytes(b'corrupt')
    with pytest.raises(AcquisitionError, match='corrupt or uncheckpointed'):
        acquire(artifact, tmp_path, test_only=True)
    assert len(handler.requests) == 1


@pytest.mark.parametrize('mode,message', [('corrupt', 'SHA-256'), ('oversized', 'exceeds'), ('redirect', 'redirect')])
def test_invalid_network_bytes_never_published(source, tmp_path, mode, message):
    artifact, handler = source
    setattr(handler, mode, True)
    with pytest.raises(AcquisitionError, match=message):
        acquire(artifact, tmp_path, test_only=True)
    assert not list(tmp_path.rglob('runtime.sif'))


def test_invalid_range_rejected(source, tmp_path):
    artifact, handler = source
    handler.interrupt = True
    with pytest.raises(AcquisitionError):
        acquire(artifact, tmp_path, attempts=1, test_only=True)
    handler.bad_range = True
    with pytest.raises(AcquisitionError, match='Content-Range'):
        acquire(artifact, tmp_path, test_only=True)


def test_license_and_production_source_gates_before_io(source, tmp_path):
    artifact, handler = source
    with pytest.raises(AcquisitionError, match='HTTPS'):
        acquire(artifact, tmp_path)
    weights = replace(artifact, kind='weights', license_id='fixture-license')
    with pytest.raises(AcquisitionError, match='license acceptance'):
        acquire(weights, tmp_path, test_only=True)
    result = acquire(weights, tmp_path, test_only=True, accepted_licenses=['fixture-license'])
    assert 'weights' in Path(result['path']).parts
    assert len(handler.requests) == 1


@pytest.mark.parametrize('mode', ['unavailable', 'delayed'])
def test_network_retry_and_timeout_are_bounded(source, tmp_path, mode):
    artifact, handler = source
    setattr(handler, mode, True)
    started = time.monotonic()
    with pytest.raises(AcquisitionError, match='after 2 attempt'):
        acquire(artifact, tmp_path, attempts=2, timeout=0.02, total_timeout=1, test_only=True)
    assert len(handler.requests) == 2
    assert time.monotonic() - started < 1
    assert not list(tmp_path.rglob('runtime.sif'))


def test_symlink_staging_rejected(source, tmp_path):
    artifact, _ = source
    stage = tmp_path / 'test-fixtures-not-scientific-assets' / '.acquisition' / 'fixture'
    stage.mkdir(parents=True)
    victim = tmp_path / 'unrelated'
    victim.write_bytes(b'untouched')
    (stage / 'payload.part').symlink_to(victim)
    with pytest.raises(OSError):
        acquire(artifact, tmp_path, test_only=True)
    assert victim.read_bytes() == b'untouched'
