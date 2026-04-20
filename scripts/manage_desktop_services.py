#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biomodstack_services import (  # noqa: E402
    API_LOG,
    CORE_RUNTIME_LOG,
    FRONTEND_LOG,
    ServiceManagerError,
    resolve_runtime_mode,
    restart_all,
    restart_api,
    start_all,
    status_lines,
    stop_all,
)


NOTIFY_ICON = "applications-science"


def notify(message: str, icon: str = NOTIFY_ICON) -> None:
    import subprocess

    subprocess.run(
        ["notify-send", "BioModStack", message, "-i", icon],
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage BioModStack desktop services")
    parser.add_argument("action", choices=["start", "stop", "restart", "restart-api", "status"])
    parser.add_argument(
        "--runtime",
        choices=["dev", "container"],
        help="runtime mode to manage (defaults to BMS_RUNTIME_MODE or dev)",
    )
    parser.add_argument("--notify", action="store_true", help="send desktop notifications")
    args = parser.parse_args()

    runtime_mode = resolve_runtime_mode(args.runtime)

    try:
        if args.action == "start":
            if args.notify:
                notify("🚀 Starting BioModStack services…")
            start_all(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack services are running")
            print("\n".join(status_lines(runtime_mode=runtime_mode)))
            return 0

        if args.action == "stop":
            if args.notify:
                notify("🛑 Stopping BioModStack services…", icon="dialog-warning")
            stop_all(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack services stopped", icon="dialog-information")
            print("Stopped BioModStack services")
            return 0

        if args.action == "restart":
            if args.notify:
                notify("♻️ Restarting BioModStack services…", icon="view-refresh")
            restart_all(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack services restarted", icon="dialog-information")
            print("\n".join(status_lines(runtime_mode=runtime_mode)))
            return 0

        if args.action == "restart-api":
            if args.notify:
                notify("🔄 Restarting BioModStack API…", icon="view-refresh")
            restart_api(runtime_mode=runtime_mode)
            if args.notify:
                notify("✅ BioModStack API restarted", icon="dialog-information")
            if runtime_mode == "container":
                print(f"Core runtime log: {CORE_RUNTIME_LOG}")
            else:
                print(f"API log: {API_LOG}")
            return 0

        if args.action == "status":
            print("\n".join(status_lines(runtime_mode=runtime_mode)))
            if runtime_mode == "container":
                print(f"Core runtime log: {CORE_RUNTIME_LOG}")
            else:
                print(f"Frontend log: {FRONTEND_LOG}")
            return 0
    except ServiceManagerError as exc:
        if args.notify:
            notify(f"❌ {exc}", icon="dialog-error")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
