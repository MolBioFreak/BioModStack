from __future__ import annotations

from typing import Any

from database import MdRun
from services.md.results import MDJobRecord, MDResultError, apply_completion_barrier, completion_barrier


def validate_md_completion(job: MDJobRecord) -> dict[str, Any]:
    """Validate the complete immutable MD generation without mutating job state."""

    return completion_barrier(job)


def validate_and_finalize_md_job(job: MDJobRecord, session: Any) -> dict[str, Any]:
    """Apply the MD-specific terminal barrier to the caller's current DB transaction."""

    snapshot = apply_completion_barrier(job)
    run = session.get(MdRun, job.id)
    if run is None:
        raise MDResultError("MD_COMPLETION_CONFLICT", "Authoritative MD run state is missing", 409)
    run.phase = "completed"
    run.verification_status = "verified"
    run.state_version += 1
    run.controls_blocked = False
    return snapshot
