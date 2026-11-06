"""Toggle Tailscale Funnel on :8443 to match the existence of active share grants.

Run periodically (see ``config/share-funnel/bms-share-funnel.timer.template``).
The only Tailscale commands this module can issue are the two below; both are
scoped to ``--https=8443``. It never touches 443 or ``tailscale serve``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Sequence

from share_server import grants as G

FUNNEL_PORT = G.PUBLIC_FUNNEL_PORT
assert FUNNEL_PORT == 8443


def funnel_on_command(tailscale: str, local_port: int) -> list[str]:
    return [tailscale, "funnel", "--bg", f"--https={FUNNEL_PORT}", f"http://127.0.0.1:{local_port}"]


def funnel_off_command(tailscale: str) -> list[str]:
    return [tailscale, "funnel", f"--https={FUNNEL_PORT}", "off"]


def any_active(grants: Sequence[dict[str, Any]], now: int) -> bool:
    return any(isinstance(g, dict) and G.grant_state(g, now=now).active for g in grants)


def funnel_state(status: dict[str, Any], local_port: int) -> str:
    """Return ``on`` (8443 funnelled to our target), ``off`` or ``other``."""
    allow = status.get("AllowFunnel") or {}
    web = status.get("Web") or {}
    funnelled = [host for host, enabled in allow.items() if enabled and host.endswith(f":{FUNNEL_PORT}")]
    served = [host for host in web if host.endswith(f":{FUNNEL_PORT}")]
    if not funnelled and not served and str(FUNNEL_PORT) not in (status.get("TCP") or {}):
        return "off"
    expected = f"http://127.0.0.1:{local_port}"
    for host in served:
        handlers = (web.get(host) or {}).get("Handlers") or {}
        if set(handlers) == {"/"} and handlers["/"].get("Proxy") == expected and host in funnelled:
            return "on"
    return "other"


def decide(active: bool, state: str) -> str:
    if active and state != "on":
        return "on"
    if not active and state != "off":
        return "off"
    return "noop"


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    port_raw = os.environ.get("BMS_SHARE_PORT", "").strip()
    if not port_raw.isdigit() or not 1024 <= int(port_raw) <= 65535:
        print("BMS_SHARE_PORT must be the local share listener port", file=sys.stderr)
        return 2
    port = int(port_raw)
    tailscale = os.environ.get("BMS_TAILSCALE_BIN") or shutil.which("tailscale")
    if not tailscale:
        print("tailscale binary not found", file=sys.stderr)
        return 2
    try:
        document = G.load_index(G.publish_dir())
        active = any_active(document["grants"], G.utc_now())
    except FileNotFoundError:
        active = False
    except (OSError, ValueError, G.ShareConfigurationError) as exc:
        # Unreadable index: fail closed (no public exposure).
        print(f"share index unreadable ({exc}); funnel will be turned off", file=sys.stderr)
        active = False
    status_run = subprocess.run(
        [tailscale, "funnel", "status", "--json"], capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        status = json.loads(status_run.stdout or "{}") if status_run.returncode == 0 else {}
    except ValueError:
        status = {}
    state = funnel_state(status, port) if status_run.returncode == 0 else "other"
    action = decide(active, state)
    if action == "noop":
        return 0
    command = funnel_on_command(tailscale, port) if action == "on" else funnel_off_command(tailscale)
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    print(f"funnel {action}: exit {result.returncode}", file=sys.stderr)
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
