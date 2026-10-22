"""Run the public share server.

Production: systemd socket activation (``LISTEN_FDS``) inside a unit with
``PrivateNetwork=yes``; the process inherits one already-bound loopback
listener and cannot open any network connection itself.

Development: ``BMS_SHARE_DEV_PORT`` binds 127.0.0.1 only.
"""
from __future__ import annotations

import os
import socket
import sys

SD_LISTEN_FDS_START = 3


def _inherited_socket() -> socket.socket | None:
    if os.environ.get("LISTEN_PID") != str(os.getpid()):
        return None
    try:
        count = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        return None
    if count != 1:
        raise SystemExit("bms-share expects exactly one socket-activated listener")
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)
    return socket.socket(fileno=SD_LISTEN_FDS_START)


def _dev_socket() -> socket.socket:
    raw = os.environ.get("BMS_SHARE_DEV_PORT", "").strip()
    if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise SystemExit("no socket-activated listener and BMS_SHARE_DEV_PORT is not set")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", int(raw)))
    sock.listen(128)
    return sock


def main() -> None:
    import uvicorn

    from share_server.app import app

    sock = _inherited_socket() or _dev_socket()
    config = uvicorn.Config(
        app,
        http="h11",
        ws="none",
        lifespan="on",
        proxy_headers=False,
        server_header=False,
        date_header=False,
        access_log=False,
        log_level="warning",
        limit_concurrency=64,
        timeout_keep_alive=5,
        h11_max_incomplete_event_size=16 * 1024,
    )
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    sys.exit(main())
