#!/usr/bin/env python3
"""Host-owned one-second telemetry collector for BioModStack."""
from __future__ import annotations

import logging
import signal
import time
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from datetime import datetime, timezone

from routers.gpu import get_cpu_stats, get_gpu_stats_with_error, get_ram_stats
from telemetry_store import TelemetryStore, telemetry_db_path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("biomodstack.telemetry")
_stop = False
INTEGRITY_INTERVAL_SECONDS = 3_600.0


class ScheduledIntegrityVerifier:
    def __init__(
        self,
        store: TelemetryStore,
        *,
        interval_seconds: float = INTEGRITY_INTERVAL_SECONDS,
        started_at: float | None = None,
        executor: Executor | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("integrity interval must be positive")
        start = time.monotonic() if started_at is None else started_at
        self._store = store
        self._interval_seconds = interval_seconds
        self._next_due = start + interval_seconds
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="telemetry-integrity",
        )
        self._future: Future[None] | None = None

    def poll(self, now: float) -> None:
        if self._future is not None:
            if not self._future.done():
                return
            self._future.result()
            self._future = None
        if now >= self._next_due:
            self._future = self._executor.submit(self._store.verify_integrity)
            self._next_due = now + self._interval_seconds

    def close(self) -> None:
        self._executor.shutdown(wait=True)


def _request_stop(_signum: int, _frame: object) -> None:
    global _stop
    _stop = True


def collect_sample(timestamp_ms: int | None = None) -> dict[str, object]:
    observed_ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    cpu = get_cpu_stats()
    ram = get_ram_stats()
    gpus, gpu_error = get_gpu_stats_with_error(force_refresh=True)
    return {
        "timestamp": datetime.fromtimestamp(observed_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
        "timestamp_ms": observed_ms,
        "cpu": cpu.model_dump(),
        "ram": ram.model_dump(),
        "gpus": [gpu.model_dump() for gpu in gpus],
        "gpu_error": gpu_error,
    }


def run() -> None:
    store = TelemetryStore(telemetry_db_path())
    store.initialize()
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    next_tick = time.monotonic()
    last_maintenance_minute: int | None = None
    integrity_verifier = ScheduledIntegrityVerifier(store, started_at=next_tick)
    logger.info("collector started database=%s", store.path)
    try:
        while not _stop:
            integrity_verifier.poll(time.monotonic())
            observed_ms = int(time.time() * 1000)
            try:
                store.append_sample(collect_sample(observed_ms))
                minute = observed_ms // 60_000
                if minute != last_maintenance_minute:
                    store.finalize_completed_minutes(observed_ms)
                    store.apply_retention(observed_ms)
                    last_maintenance_minute = minute
            except Exception:
                logger.exception("telemetry sample failed")
            next_tick += 1.0
            now = time.monotonic()
            if next_tick <= now:
                next_tick = now + 1.0
            time.sleep(next_tick - now)
    finally:
        integrity_verifier.close()


if __name__ == "__main__":
    run()
