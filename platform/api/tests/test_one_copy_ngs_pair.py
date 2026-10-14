"""Byte-custody/socket controls, not a BLOW5 scientific-format qualification."""
import array
import importlib.util
import os
from pathlib import Path
import socket
import threading

from services import ont_raw_signal as raw


def _lookup():
    path = Path(__file__).resolve().parents[3] / 'scripts/ont_raw_signal_lookup.py'
    spec = importlib.util.spec_from_file_location('one_copy_lookup', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_external_pair_retained_once_and_original_replacement_is_independent(tmp_path, monkeypatch):
    monkeypatch.setenv(raw.BLOW5_STAGING_ROOT_ENV, str(tmp_path / 'staging'))
    source = tmp_path / 'upload.blow5'
    index = tmp_path / 'upload.blow5.idx'
    # Deliberately transport-only bytes; these are not invented native records.
    source.write_bytes(b'transport-body')
    index.write_bytes(b'transport-index')
    artifacts = [raw._file_artifact(source, 'body', kind='blow5'),
                 raw._file_artifact(index, 'index', kind='blow5_index')]
    first = raw._retain_external_indexed_pair(artifacts, run_id='run', observed_generation=1)
    second = raw._retain_external_indexed_pair(artifacts, run_id='run', observed_generation=1)
    assert first == second
    assert first[0]['inode'] != source.stat().st_ino
    assert first[1]['path'] == first[0]['path'] + '.idx'
    source.write_bytes(b'replaced original')
    index.unlink()
    for record, payload in zip(first, (b'transport-body', b'transport-index')):
        path = Path(record['path'])
        assert path.read_bytes() == payload
        assert not path.stat().st_mode & 0o222
    assert raw._retain_external_indexed_pair(artifacts, run_id='run', observed_generation=1) == first


def test_socket_descriptors_expose_adjacent_pair_and_own_cancel_lifetime(tmp_path):
    lookup = _lookup()
    source = tmp_path / 'retained.blow5'
    index = tmp_path / 'retained.blow5.idx'
    source.write_bytes(b'body')
    index.write_bytes(b'index')
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    body_fd, index_fd = os.open(source, os.O_RDONLY), os.open(index, os.O_RDONLY)
    # Linux AF_UNIX abstract names avoid pathname limits in private test roots.
    address = '\0bms-ngs-pair-' + str(os.getpid())
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen(1)
    def serve():
        connection, _ = server.accept()
        with connection:
            connection.sendmsg([b'F'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                array.array('i', [root, body_fd, root, index_fd]))])
    thread = threading.Thread(target=serve)
    thread.start()
    received = lookup._receive_source_descriptors(address)
    thread.join()
    server.close()
    os.close(root)
    os.close(body_fd)
    os.close(index_fd)
    view = tmp_path / 'container-visible-view'
    view.mkdir()
    try:
        body_view, index_view = lookup._expose_retained_pair(view, received[1], received[3])
        assert str(index_view) == str(body_view) + '.idx'
        assert body_view.stat().st_ino == source.stat().st_ino
        assert index_view.stat().st_ino == index.stat().st_ino
        source.unlink()
        index.unlink()
        assert body_view.read_bytes() == b'body'
        assert index_view.read_bytes() == b'index'
        assert all(p.is_symlink() for p in view.iterdir())
    finally:
        for fd in received:
            os.close(fd)
    assert not body_view.exists()
    assert not index_view.exists()


def test_received_pair_visible_when_host_source_path_is_hidden(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys

    monkeypatch.chdir(tmp_path)
    donor = tmp_path / 'donor'
    donor.mkdir()
    body = donor / 'source.blow5'
    index = donor / 'source.blow5.idx'
    body.write_bytes(b'body transport')
    index.write_bytes(b'index transport')
    root_fd = os.open(donor, os.O_RDONLY | os.O_DIRECTORY)
    body_fd = os.open(body, os.O_RDONLY)
    index_fd = os.open(index, os.O_RDONLY)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind('fd.sock')
    server.listen(1)
    server.settimeout(10)
    lookup_path = Path(__file__).resolve().parents[3] / 'scripts/ont_raw_signal_lookup.py'
    child_code = '''
import importlib.util, json, os, pathlib, subprocess, sys
spec=importlib.util.spec_from_file_location('lookup',sys.argv[1])
lookup=importlib.util.module_from_spec(spec); spec.loader.exec_module(lookup)
fds=lookup._receive_source_descriptors('fd.sock')
try:
    subprocess.run(['mount','-t','tmpfs','tmpfs',sys.argv[2]],check=True)
    assert not (pathlib.Path(sys.argv[2])/'source.blow5').exists()
    view=pathlib.Path('view'); view.mkdir()
    body,index=lookup._expose_retained_pair(view,fds[1],fds[3])
    print(json.dumps({'body':body.read_text(),'index':index.read_text(),
       'body_inode':body.stat().st_ino,'index_inode':index.stat().st_ino}),flush=True)
finally:
    for fd in fds: os.close(fd)
'''
    child = subprocess.Popen(['unshare', '--user', '--map-root-user', '--mount', '--net',
        sys.executable, '-c', child_code, str(lookup_path), str(donor)],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        connection, _ = server.accept()
        with connection:
            connection.sendmsg([b'F'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                array.array('i', [root_fd, body_fd, root_fd, index_fd]))])
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, stderr
        observed = json.loads(stdout)
        assert observed == {'body': 'body transport', 'index': 'index transport',
            'body_inode': body.stat().st_ino, 'index_inode': index.stat().st_ino}
        assert body.read_bytes() == b'body transport'
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        server.close()
        for fd in (root_fd, body_fd, index_fd):
            os.close(fd)
