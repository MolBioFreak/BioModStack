"""Authoritative resolution of persisted core-job result roots."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from paths import get_results_dir


class JobResultRootError(ValueError):
    """Raised when persisted result-root authority is missing or unsafe."""


def resolve_job_result_destination(job: Any) -> Path:
    """Project a confined destination identity without opening absent results."""
    return _resolve_job_result_root(job, must_exist=False)


def resolve_persisted_job_result_root(job: Any) -> Path:
    """Open only an existing confined persisted result directory."""
    return _resolve_job_result_root(job, must_exist=True)


def _resolve_job_result_root(job: Any, *, must_exist: bool) -> Path:
    """Resolve only ``child_output_dir`` or ``output_dir`` below results.

    A job ID is never interpreted as a directory name. Relative persisted
    values are rooted below the configured results directory; absolute values
    are accepted only when they are already contained there. Every existing
    path component is checked before resolution so symlink traversal cannot
    turn an apparently in-root declaration into outside authority.
    """

    raw = getattr(job, "child_output_dir", None) or getattr(job, "output_dir", None)
    if not isinstance(raw, str) or not raw.strip():
        raise JobResultRootError("job has no persisted result root")

    configured_root = get_results_dir().expanduser()
    if configured_root.is_symlink():
        raise JobResultRootError("configured results root is an unsafe symlink")
    try:
        root = configured_root.resolve(strict=True)
    except OSError as exc:
        raise JobResultRootError("configured results root is unavailable") from exc
    if not root.is_dir():
        raise JobResultRootError("configured results root is unavailable")

    declared = Path(raw.strip()).expanduser()
    lexical = declared.absolute() if declared.is_absolute() else root / declared
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise JobResultRootError("persisted job result root escapes configured results") from exc

    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise JobResultRootError("persisted job result root traverses a symlink")

    try:
        resolved = lexical.resolve(strict=must_exist)
        resolved.relative_to(root)
    except OSError as exc:
        raise JobResultRootError("persisted job result root is unavailable") from exc
    except ValueError as exc:
        raise JobResultRootError("persisted job result root escapes configured results") from exc
    if (must_exist or resolved.exists()) and not resolved.is_dir():
        raise JobResultRootError("persisted job result root is not a directory")
    return resolved


__all__ = ["JobResultRootError", "resolve_persisted_job_result_root", "resolve_job_result_destination"]
