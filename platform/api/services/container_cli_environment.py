"""Host-only environment for the scientific container runtime clients."""
from __future__ import annotations

import os
from pathlib import Path


def container_cli_environment() -> dict[str, str]:
    """Minimal host-client context; container mounts/network remain command-owned.

    Rootless Podman needs the invoking user's real home and session runtime
    directory to find its existing storage and user namespace. These variables
    configure the host CLI; they are not forwarded into the scientific image.
    """
    context = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home())}
    for key in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "DBUS_SESSION_BUS_ADDRESS"):
        if os.environ.get(key):
            context[key] = os.environ[key]
    return context

