#!/usr/bin/env python3
"""Tiny localhost-only RAPL CPU package power collector for BioModStack.

The main BioModStack services normally run as an unprivileged user, while modern
Linux exposes RAPL package energy counters as root-readable sysfs files. This
collector is intended to run in the core-runtime compose stack as root with
/sys bind-mounted read-only, expose only localhost HTTP, and return measured
watts from energy deltas without fabricating CPU/TDP constants.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

POWER_SETUP_HINT = (
    "Run the BioModStack CPU power collector with /sys mounted read-only so it "
    "can read /sys/class/powercap/*/energy_uj as root."
)

_state_lock = threading.Lock()
_sample_state: dict[str, dict[str, float]] = {}


def _status(
    *,
    available: bool,
    status: str,
    message: str,
    discovered_sources: int,
    readable_sources: int,
    setup_hint: str | None = None,
    power_watts: float | None = None,
) -> dict[str, Any]:
    return {
        "source": "rapl_collector",
        "available": available,
        "status": status,
        "message": message,
        "discovered_sources": int(discovered_sources),
        "readable_sources": int(readable_sources),
        "setup_hint": setup_hint,
        "power_watts": None if power_watts is None else round(float(power_watts), 1),
    }


def _powercap_roots() -> list[Path]:
    configured = os.environ.get("BMS_POWER_CAP_ROOT", "").strip()
    roots = [Path(configured)] if configured else []
    roots.extend([Path("/host_sys/class/powercap"), Path("/sys/class/powercap")])
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _discover_sources() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for root in _powercap_roots():
        if not root.exists():
            continue
        for domain_path in sorted(root.glob("*-rapl:*")):
            name_path = domain_path / "name"
            energy_path = domain_path / "energy_uj"
            if not name_path.exists() or not energy_path.exists():
                continue
            try:
                domain_name = name_path.read_text().strip().lower()
            except OSError:
                continue
            if not domain_name.startswith("package-"):
                continue
            max_energy_uj = float(2**32)
            try:
                max_range_path = domain_path / "max_energy_range_uj"
                if max_range_path.exists():
                    max_energy_uj = float(max_range_path.read_text().strip())
            except (OSError, ValueError):
                pass
            sources.append({
                "domain_name": domain_name,
                "energy_path": energy_path,
                "max_energy_uj": max_energy_uj,
            })
        if sources:
            return sources
    return sources


def _read_energy_uj(path: Path) -> float:
    return float(path.read_text().strip())


def _sample_once(sources: list[dict[str, Any]]) -> tuple[float | None, int, list[str]]:
    total_power_watts = 0.0
    valid_samples = 0
    successful_reads = 0
    read_errors: list[str] = []

    with _state_lock:
        for source in sources:
            energy_path: Path = source["energy_path"]
            max_energy_uj = float(source.get("max_energy_uj") or float(2**32))
            try:
                current_energy = _read_energy_uj(energy_path)
            except Exception as exc:  # noqa: BLE001 - diagnostics endpoint should report all read failures.
                read_errors.append(f"{energy_path}: {type(exc).__name__}: {exc}")
                continue

            successful_reads += 1
            current_time = time.monotonic()
            cache_key = str(energy_path)
            previous = _sample_state.get(cache_key)
            _sample_state[cache_key] = {"energy_uj": current_energy, "time_s": current_time}
            if not previous:
                continue

            time_delta_s = current_time - previous["time_s"]
            if time_delta_s <= 0.01:
                continue
            energy_delta_uj = current_energy - previous["energy_uj"]
            if energy_delta_uj < 0:
                energy_delta_uj += max_energy_uj
            power_watts = energy_delta_uj / (time_delta_s * 1_000_000)
            if power_watts >= 0:
                total_power_watts += power_watts
                valid_samples += 1

    if valid_samples > 0:
        return total_power_watts, successful_reads, read_errors
    return None, successful_reads, read_errors


def sample_power() -> dict[str, Any]:
    sources = _discover_sources()
    discovered_sources = len(sources)
    if not sources:
        return _status(
            available=False,
            status="no_sources",
            message="No RAPL package energy counters were discovered by the CPU power collector.",
            discovered_sources=0,
            readable_sources=0,
            setup_hint=POWER_SETUP_HINT,
        )

    power_watts, successful_reads, read_errors = _sample_once(sources)
    if power_watts is not None:
        return _status(
            available=True,
            status="ok",
            message="CPU package power sampled by host RAPL collector.",
            discovered_sources=discovered_sources,
            readable_sources=successful_reads,
            power_watts=power_watts,
        )

    # First request after process start primes the delta cache. Sleep briefly and
    # retry so the live dashboard can show watts immediately after deployment.
    if successful_reads > 0:
        time.sleep(float(os.environ.get("BMS_CPU_POWER_PRIME_INTERVAL", "0.15")))
        power_watts, successful_reads, read_errors = _sample_once(sources)
        if power_watts is not None:
            return _status(
                available=True,
                status="ok",
                message="CPU package power sampled by host RAPL collector.",
                discovered_sources=discovered_sources,
                readable_sources=successful_reads,
                power_watts=power_watts,
            )
        return _status(
            available=False,
            status="priming",
            message="CPU power collector read RAPL energy; waiting for a second sample to compute watts.",
            discovered_sources=discovered_sources,
            readable_sources=successful_reads,
        )

    return _status(
        available=False,
        status="read_error",
        message="CPU power collector could not read RAPL energy counters: " + " | ".join(read_errors[:3]),
        discovered_sources=discovered_sources,
        readable_sources=0,
        setup_hint=POWER_SETUP_HINT,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        if path in {"/health", "/ready"}:
            self._send_json({"ok": True})
            return
        if path == "/power":
            self._send_json(sample_power())
            return
        self.send_error(404, "not found")

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = os.environ.get("BMS_CPU_POWER_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("BMS_CPU_POWER_PORT", "18797"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"BioModStack CPU power collector listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
