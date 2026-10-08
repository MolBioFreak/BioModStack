"""Process-wide SIGIO authority for Linux retained-file read leases."""
from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from types import FrameType

_BREAK_GENERATION = 0
_LISTENERS: list[Callable[[], None]] = []


def _dispatch_lease_break(_signum: int, _frame: FrameType | None) -> None:
    global _BREAK_GENERATION
    _BREAK_GENERATION += 1
    for listener in tuple(_LISTENERS):
        listener()


def install_lease_break_handler() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("retained-file read leases require the service main thread")
    if signal.getsignal(signal.SIGIO) is not _dispatch_lease_break:
        signal.signal(signal.SIGIO, _dispatch_lease_break)


def register_lease_break_listener(listener: Callable[[], None]) -> None:
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)
    install_lease_break_handler()


def lease_break_generation() -> int:
    install_lease_break_handler()
    return _BREAK_GENERATION
