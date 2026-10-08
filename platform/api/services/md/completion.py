from __future__ import annotations

from typing import Any

from services.md.results import MDJobRecord, apply_completion_barrier, completion_barrier


def validate_md_completion(job: MDJobRecord) -> dict[str, Any]:
    """Validate the complete immutable MD generation without mutating job state."""

    return completion_barrier(job)


def validate_and_finalize_md_job(job: MDJobRecord) -> dict[str, Any]:
    """Apply the MD-specific terminal barrier to the caller's current DB transaction."""

    return apply_completion_barrier(job)
