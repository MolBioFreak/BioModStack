"""Real ephemeral loopback servers/files; no scientific approval or model assets."""
import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.pinned_acquisition import AcquisitionError, Artifact, acquire
from lib import pinned_weight_layout as layout
from lib.reviewed_transport import TransportError, _destination, validate_policy
from test_pinned_acquisition import source, cleanup_readonly_objects, PAYLOAD


@pytest.fixture
def redirects():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        target = None
        corrupt = False
        requests = []
        def log_message(self, *args):
            pass
        def do_GET(self):
            type(self).requests.append(dict(self.headers))
            if self.path.startswith('/start') or self.path.startswith('/hop'):
                self.send_response(302)
                self.send_header('Location', self.target or (
                    '/hop?Signature=SECRET' if self.path == '/start' else '/bytes?Signature=SECRET'))
                self.send_header('Set-Cookie', 'token=SECRET')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            body = b'x' * len(PAYLOAD) if self.corrupt else PAYLOAD
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    authority = f'127.0.0.1:{server.server_port}'
    url = f'http://{authority}/start'
    artifact = Artifact('redirect-fixture', 'image', url, hashlib.sha256(PAYLOAD).hexdigest(),
                        len(PAYLOAD), authority, 'TEST-ONLY', redirect_policy={
                            'source_url': url, 'approval_ref': 'TEST-ONLY',
                            'allowed_authorities': [authority], 'max_hops': 2})
    try:
        yield artifact, Handler
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_redirect_chain_and_no_secret_persistence(redirects, tmp_path):
    artifact, handler = redirects
    receipt = acquire(artifact, tmp_path, test_only=True)
    assert Path(receipt['path']).read_bytes() == PAYLOAD
    assert len(handler.requests) == 3
    for headers in handler.requests:
        assert not {'Authorization', 'Cookie', 'Referer', 'Proxy-Authorization'} & headers.keys()
    assert 'SECRET' not in json.dumps(receipt)
    assert all('SECRET' not in p.read_text() for p in tmp_path.rglob('state.json'))


@pytest.mark.parametrize('target', [
    'http://localhost:1/private?Signature=SECRET',
    'http://127.0.0.1.evil.invalid/file?Signature=SECRET',
    'http://user:SECRET@127.0.0.1/file',
    'file:///etc/passwd', '//169.254.169.254/latest/meta-data',
])
def test_redirect_rejections_are_redacted(redirects, tmp_path, target):
    artifact, handler = redirects
    handler.target = target
    with pytest.raises(AcquisitionError) as exc:
        acquire(artifact, tmp_path, test_only=True)
    assert 'SECRET' not in str(exc.value)
    assert len(handler.requests) == 1
    assert not list(tmp_path.rglob('runtime.sif'))


def test_redirect_bounds_default_deny_and_changed_policy(redirects, tmp_path):
    artifact, handler = redirects
    with pytest.raises(AcquisitionError, match='redirect'):
        acquire(replace(artifact, redirect_policy=None), tmp_path / 'deny', test_only=True)
    with pytest.raises(AcquisitionError, match='hop limit'):
        acquire(replace(artifact, redirect_policy={**artifact.redirect_policy, 'max_hops': 1}),
                tmp_path / 'limit', test_only=True)
    with pytest.raises(AcquisitionError, match='policy'):
        acquire(replace(artifact, redirect_policy={**artifact.redirect_policy, 'source_url': 'other'}),
                tmp_path / 'binding', test_only=True)
    acquire(artifact, tmp_path / 'good', test_only=True)
    with pytest.raises(AcquisitionError, match='manifest changed'):
        acquire(replace(artifact, redirect_policy={**artifact.redirect_policy, 'approval_ref': 'changed'}),
                tmp_path / 'good', test_only=True)


def test_cross_authority_redirect_and_interrupted_resume(redirects, source, tmp_path):
    artifact, handler = redirects
    final, final_handler = source
    handler.target = final.url + '?Signature=SECRET'
    artifact = replace(artifact, redirect_policy={**artifact.redirect_policy,
        'allowed_authorities': [final.source_authority]})
    final_handler.interrupt = True
    with pytest.raises(AcquisitionError, match='download failed'):
        acquire(artifact, tmp_path, test_only=True, attempts=1)
    receipt = acquire(artifact, tmp_path, test_only=True)
    assert Path(receipt['path']).read_bytes() == PAYLOAD
    assert final_handler.requests == [None, 'bytes=10000-']
    assert len(handler.requests) == 2  # Resume begins at original immutable URL.


def test_redirect_final_hash_is_required(redirects, tmp_path):
    artifact, handler = redirects
    handler.corrupt = True
    with pytest.raises(AcquisitionError, match='SHA-256'):
        acquire(artifact, tmp_path, test_only=True)
    assert not list(tmp_path.rglob('runtime.sif'))


@pytest.mark.parametrize('url', ['http://huggingface.co/a', 'https://127.0.0.1/a',
    'https://169.254.169.254/a', 'https://[::1]/a', 'https://example.org:8443/a',
    'https://user:SECRET@example.org/a', 'https://example.org/a#SECRET'])
def test_production_destination_safety(url):
    with pytest.raises(TransportError):
        _destination(url, test_only=False)


def members(artifact):
    weight = replace(artifact, kind='weights', license_id='fixture-license')
    return [{'member_path': name, 'manifest': asdict(replace(weight, artifact_id=f'member-{n}'))}
            for n, name in enumerate(['config.json', 'nested/model.bin'])]


DEP = {'kind': 'weights', 'relative_path': 'esmfold2'}


def materialize(entries, root, **kwargs):
    return layout.materialize_weights(DEP, entries, root, test_only=True,
                                     accepted_licenses=['fixture-license'], **kwargs)


def test_layout_exact_files_reuse_and_corruption(source, tmp_path):
    artifact, handler = source
    entries = members(artifact)
    receipt = materialize(entries, tmp_path)
    root = Path(receipt['path'])
    assert set(receipt['members']) == {'config.json', 'nested/model.bin'}
    assert (root / 'nested/model.bin').read_bytes() == PAYLOAD
    assert materialize(entries, tmp_path) == receipt
    assert len(handler.requests) == 1  # identical content-addressed bytes reused
    (root / 'config.json').chmod(0o600)
    (root / 'config.json').write_bytes(b'broken')
    (root / 'config.json').chmod(0o400)
    with pytest.raises(AcquisitionError, match='SHA-256/size'):
        materialize(entries, tmp_path)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a/../b', 'a//b', 'a\\b', '.', 'a/.'])
def test_member_path_rejected_before_io(source, tmp_path, name):
    artifact, handler = source
    entries = members(artifact)
    entries[0]['member_path'] = name
    with pytest.raises(AcquisitionError, match='unsafe'):
        materialize(entries, tmp_path)
    assert handler.requests == []
    assert not list(tmp_path.iterdir())


def test_all_licenses_duplicates_and_collisions_preflight(source, tmp_path):
    artifact, handler = source
    entries = members(artifact)
    with pytest.raises(AcquisitionError, match='license acceptance'):
        layout.materialize_weights(DEP, entries, tmp_path, test_only=True)
    with pytest.raises(AcquisitionError, match='duplicate'):
        materialize(entries + entries[:1], tmp_path)
    entries[0]['member_path'] = 'nested'
    with pytest.raises(AcquisitionError, match='collision'):
        materialize(entries, tmp_path)
    assert not handler.requests


def test_interrupted_copy_resume_and_no_partial_publication(source, tmp_path, monkeypatch):
    artifact, handler = source
    entries = members(artifact)
    copy = layout._copy
    def interrupt(src, dst):
        os.write(dst, b'uncommitted copy')
        raise OSError('fixture interruption')
    monkeypatch.setattr(layout, '_copy', interrupt)
    with pytest.raises(OSError, match='interruption'):
        materialize(entries, tmp_path)
    parent = tmp_path / 'test-fixtures-not-scientific-assets/weights/layouts/esmfold2'
    assert not [p for p in parent.iterdir() if not p.name.startswith('.')]
    monkeypatch.setattr(layout, '_copy', copy)
    result = materialize(entries, tmp_path)
    assert (Path(result['path']) / 'config.json').read_bytes() == PAYLOAD
    assert len(handler.requests) == 1


@pytest.mark.parametrize('attack', ['extra', 'symlink', 'hardlink', 'missing'])
def test_published_member_set_and_links_rejected(source, tmp_path, attack):
    artifact, _ = source
    entries = members(artifact)
    root = Path(materialize(entries, tmp_path)['path'])
    root.chmod(0o700)
    if attack == 'extra':
        (root / 'unexpected').write_bytes(b'x')
    elif attack == 'hardlink':
        os.link(root / 'config.json', tmp_path / 'link')
    else:
        (root / 'config.json').unlink()
        if attack == 'symlink':
            (root / 'config.json').symlink_to(root / 'nested/model.bin')
    root.chmod(0o500)
    with pytest.raises(AcquisitionError):
        materialize(entries, tmp_path)


def test_interruption_at_atomic_publication_resumes(source, tmp_path, monkeypatch):
    artifact, _ = source
    entries = members(artifact)
    rename = layout.os.rename
    def interrupt(src, dst, **kwargs):
        if str(src).startswith('.staging-'):
            raise OSError('publication interruption')
        return rename(src, dst, **kwargs)
    monkeypatch.setattr(layout.os, 'rename', interrupt)
    with pytest.raises(OSError, match='publication interruption'):
        materialize(entries, tmp_path)
    stage = next(tmp_path.rglob('.staging-*'))
    assert (stage / 'nested/model.bin').read_bytes() == PAYLOAD
    monkeypatch.setattr(layout.os, 'rename', rename)
    assert Path(materialize(entries, tmp_path)['path']).is_dir()
    assert not stage.exists()


def test_stage_symlink_never_writes_victim(source, tmp_path, monkeypatch):
    artifact, _ = source
    entries = members(artifact)
    original = layout.acquire
    def fail(*args, **kwargs):
        raise OSError('stop before copy')
    monkeypatch.setattr(layout, 'acquire', fail)
    with pytest.raises(OSError):
        materialize(entries, tmp_path)
    stage = next(tmp_path.rglob('.staging-*'))
    victim = tmp_path / 'victim'
    victim.mkdir()
    (stage / 'nested').symlink_to(victim, target_is_directory=True)
    monkeypatch.setattr(layout, 'acquire', original)
    with pytest.raises(OSError):
        materialize(entries, tmp_path)
    assert list(victim.iterdir()) == []
