"""Server-owned context for the selected blind-pose Job submission boundary."""
from contextlib import contextmanager
from contextvars import ContextVar

_selected = ContextVar('bms_blind_pose_selected_submission', default=False)


@contextmanager
def selected_submission():
    token = _selected.set(True)
    try:
        yield
    finally:
        _selected.reset(token)


def is_selected_submission() -> bool:
    return _selected.get() is True
