#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
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
    runtime_descriptor,
    start_all,
    status_lines,
    stop_all,
)


NOTIFY_ICON = "applications-science"


def notify(message: str, icon: str = NOTIFY_ICON) -> None:
    import subprocess

    try:
        subprocess.run(
            ["notify-send", "BioModStack", message, "-i", icon],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage BioModStack desktop services")
    parser.add_argument("action", choices=["start", "stop", "restart", "restart-api", "status"])
    parser.add_argument(
        "--runtime",
        choices=["dev", "container"],
        help="runtime mode to manage (defaults to BMS_RUNTIME_MODE or container)",
    )
    parser.add_argument("--notify", action="store_true", help="send desktop notifications")
    parser.add_argument("--json", action="store_true", dest="json_output", help="emit structured JSON for supported actions")
    args = parser.parse_args()

    if args.json_output and args.action != "status":
        parser.error("--json is only supported with the status action")

    try:
        runtime_mode = resolve_runtime_mode(args.runtime)

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
            if args.json_output:
                print(json.dumps(runtime_descriptor(runtime_mode=runtime_mode), indent=2, sort_keys=True))
                return 0
            lines = status_lines(runtime_mode=runtime_mode)
            print("\n".join(lines))
            if runtime_mode == "container":
                fallback_log_line = f"Core runtime log: {CORE_RUNTIME_LOG}"
                fallback_log_path = str(CORE_RUNTIME_LOG)
            else:
                fallback_log_line = f"Frontend log: {FRONTEND_LOG}"
                fallback_log_path = str(FRONTEND_LOG)
            if not any(line.rstrip().endswith(fallback_log_path) for line in lines):
                print(fallback_log_line)
            return 0
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        if args.notify:
            notify(f"❌ {exc}", icon="dialog-error")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
