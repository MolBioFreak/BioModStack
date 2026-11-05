#!/usr/bin/env python3
"""Host-side helpers for the upstream AF2/AF3 refolding-oracle launcher daemons.

``extract DEST`` runs inside the RFD4 image and copies rfproteina's own daemon
sources (af3/ and af2/, host-python-safe siblings) so the Nextflow task can start
them on the host. ``shutdown SOCKET`` runs on the host (stdlib only) and sends the
daemon its documented ``shutdown`` request, which also stops the warm worker.
"""
import socket
import sys


def extract(destination):
    import importlib.util
    import pathlib
    import shutil
    root = pathlib.Path(importlib.util.find_spec('rfproteina').submodule_search_locations[0])
    for name in ('af3', 'af2'):
        if (root / name).is_dir():
            shutil.copytree(root / name, pathlib.Path(destination) / name,
                            ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)


def shutdown(path, timeout=10.0):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(path)
            client.sendall(b'{"cmd":"shutdown"}\n')
            client.recv(1024)
    except OSError:
        pass


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'extract':
        extract(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == 'shutdown':
        shutdown(sys.argv[2])
    else:
        raise SystemExit('usage: oracle_daemons.py extract DEST | shutdown SOCKET')
