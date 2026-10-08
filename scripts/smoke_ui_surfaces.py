#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ELECTRON_DIR = REPO_ROOT / "platform/desktop-electron"

DEFAULT_VITE_CLIENT_URL = "http://127.0.0.1:5173/@vite/client"
DEFAULT_VITE_ROOT_URL = "http://127.0.0.1:5173/"
DEFAULT_STABLE_URL = "http://127.0.0.1:18080/bms/"
DEFAULT_API_HEALTH_URL = "http://127.0.0.1:8000/api/health"
EXPECTED_ELECTRON_STABLE_URL = "http://127.0.0.1:18080/bms/"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def fetch_text(url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "BioModStack-ui-surface-smoke/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(512_000).decode("utf-8", errors="replace")
        return int(response.status), body


def check_http(name: str, url: str, timeout: float, contains: str | None = None, absent: str | None = None) -> CheckResult:
    try:
        status, body = fetch_text(url, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return CheckResult(name, False, f"{url} unreachable: {exc}")

    if not 200 <= status < 400:
        return CheckResult(name, False, f"{url} returned HTTP {status}")
    if contains and contains not in body:
        return CheckResult(name, False, f"{url} did not contain expected marker {contains!r}")
    if absent and absent in body:
        return CheckResult(name, False, f"{url} unexpectedly contained dev marker {absent!r}")
    return CheckResult(name, True, f"{url} returned HTTP {status}")


def run_command(command: list[str], cwd: Path, timeout: float, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_electron_stable_default(timeout: float, skip_build: bool = False) -> CheckResult:
    if not (ELECTRON_DIR / "package.json").exists():
        return CheckResult("Electron stable default", False, f"missing {ELECTRON_DIR / 'package.json'}")

    if not skip_build:
        build = run_command(["pnpm", "run", "build"], cwd=ELECTRON_DIR, timeout=timeout)
        if build.returncode != 0:
            tail = "\n".join((build.stdout + build.stderr).splitlines()[-12:])
            return CheckResult("Electron stable default", False, f"electron build failed:\n{tail}")

    env = os.environ.copy()
    for key in ("BMS_RUNTIME_MODE", "BMS_FRONTEND_ORIGIN", "BMS_ROUTER_BASENAME", "BMS_WEB_HOST_PORT"):
        env.pop(key, None)
    node_code = """
const { resolveShellContext } = require('./platform/desktop-electron/dist/src/windowState.js');
const context = resolveShellContext({ runtimeMode: 'container' });
const expected = 'http://127.0.0.1:18080/bms/';
if (context.windowUrl !== expected || context.browserUrl !== expected || context.routerBasename !== '/bms/') {
  console.error(JSON.stringify(context));
  process.exit(1);
}
console.log(JSON.stringify(context));
""".strip()
    probe = run_command(["node", "-e", node_code], cwd=REPO_ROOT, timeout=timeout, env=env)
    if probe.returncode != 0:
        tail = "\n".join((probe.stdout + probe.stderr).splitlines()[-12:])
        return CheckResult("Electron stable default", False, f"electron default context check failed:\n{tail}")
    return CheckResult("Electron stable default", True, probe.stdout.strip() or EXPECTED_ELECTRON_STABLE_URL)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check BioModStack UI channel separation")
    parser.add_argument("--vite-client-url", default=DEFAULT_VITE_CLIENT_URL)
    parser.add_argument("--vite-root-url", default=DEFAULT_VITE_ROOT_URL)
    parser.add_argument("--stable-url", default=os.environ.get("BMS_STABLE_UI_URL", DEFAULT_STABLE_URL))
    parser.add_argument("--api-health-url", default=DEFAULT_API_HEALTH_URL)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--skip-electron-build", action="store_true", help="reuse existing Electron dist/ instead of running pnpm build")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    results = [
        check_http("Vite dev HMR client", args.vite_client_url, args.timeout, contains="vite"),
        check_http("Vite dev root", args.vite_root_url, args.timeout, contains="/src/"),
        check_http("Stable hosted /bms/", args.stable_url, args.timeout, absent="/@vite/client"),
        check_http("API health", args.api_health_url, args.timeout, contains="healthy"),
        check_electron_stable_default(args.timeout * 20, skip_build=args.skip_electron_build),
    ]

    if args.json_output:
        print(json.dumps([result.__dict__ for result in results], indent=2))
    else:
        for result in results:
            status = "PASS" if result.ok else "FAIL"
            print(f"[{status}] {result.name}: {result.detail}")

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
