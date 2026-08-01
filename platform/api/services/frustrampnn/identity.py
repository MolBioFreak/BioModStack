"""Deterministic parent candidate identity for canonical FrustraMPNN."""
from __future__ import annotations

import hashlib


CANDIDATE_IDENTITY_DOMAIN = "bms.frustrampnn.parent-candidate.v1"


def _identity_bytes(*values: str) -> bytes:
    normalized: list[str] = [CANDIDATE_IDENTITY_DOMAIN]
    for value in values:
        text = str(value).strip()
        if not text or "\0" in text:
            raise ValueError("candidate identity fields must be non-empty and NUL-free")
        normalized.append(text)
    return "\0".join(normalized).encode("utf-8")


def deterministic_candidate_id(
    *,
    parent_job_id: str,
    parent_workflow_id: str,
    producer_stage: str,
    producer_candidate_key: str,
) -> str:
    """Return the 36-character ID shared by candidate, source artifact, and Design.

    The producer candidate key is an explicit job-relative source-artifact path,
    never an inferred basename. The NUL-delimited domain is reproduced in the
    structure_prediction Nextflow parent before any FrustraMPNN task runs.
    """

    digest = hashlib.sha256(
        _identity_bytes(
            parent_job_id,
            parent_workflow_id,
            producer_stage,
            producer_candidate_key,
        )
    ).hexdigest()[:32]
    return "-".join(
        (digest[:8], digest[8:12], digest[12:16], digest[16:20], digest[20:])
    )
