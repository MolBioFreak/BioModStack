from __future__ import annotations

from concurrent.futures import Future
from typing import Callable

import pytest

from tools import telemetry_collector


class FakeExecutor:
    def __init__(self) -> None:
        self.submissions: list[Callable[[], None]] = []
        self.future: Future[None] = Future()
        self.shutdown_calls: list[bool] = []

    def submit(self, function: Callable[[], None]) -> Future[None]:
        self.submissions.append(function)
        return self.future

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_calls.append(wait)


class FakeStore:
    def __init__(self) -> None:
        self.calls = 0

    def verify_integrity(self) -> None:
        self.calls += 1


def test_integrity_verifier_runs_once_per_interval_without_overlap() -> None:
    store = FakeStore()
    executor = FakeExecutor()
    verifier = telemetry_collector.ScheduledIntegrityVerifier(
        store,
        interval_seconds=3_600.0,
        started_at=100.0,
        executor=executor,
    )

    verifier.poll(3_699.0)
    assert executor.submissions == []

    verifier.poll(3_700.0)
    verifier.poll(3_701.0)
    assert executor.submissions == [store.verify_integrity]

    executor.future.set_result(None)
    verifier.poll(3_702.0)
    assert executor.submissions == [store.verify_integrity]

    verifier.close()
    assert executor.shutdown_calls == [True]


def test_integrity_verifier_propagates_failed_scan() -> None:
    executor = FakeExecutor()
    verifier = telemetry_collector.ScheduledIntegrityVerifier(
        FakeStore(),
        interval_seconds=3_600.0,
        started_at=0.0,
        executor=executor,
    )
    verifier.poll(3_600.0)
    executor.future.set_exception(RuntimeError("corrupt"))

    with pytest.raises(RuntimeError, match="corrupt"):
        verifier.poll(3_601.0)
