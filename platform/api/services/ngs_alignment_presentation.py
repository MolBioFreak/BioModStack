"""Durable lifecycle operations for NGS alignment-presentation requests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import NgsAlignmentPresentationJob


class PresentationClaimLost(RuntimeError):
    """Raised when a presentation worker no longer owns its claim."""


class PresentationAlreadyReady(RuntimeError):
    """Raised when manual retry targets an already-ready presentation."""


class PresentationSourceStale(RuntimeError):
    """Raised when retry authority no longer matches the stored source."""


def _now() -> datetime:
    return datetime.utcnow()


def presentation_request_id(
    *, job_id: str, session_id: str, source_authority_sha256: str, policy_version: int
) -> str:
    payload = json.dumps(
        {
            "job_id": job_id,
            "policy_version": policy_version,
            "session_id": session_id,
            "source_authority_sha256": source_authority_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ngs-presentation-" + hashlib.sha256(payload).hexdigest()


async def request_presentation(
    session: AsyncSession,
    *,
    job_id: str,
    session_id: str,
    mode: str,
    source_authority_sha256: str,
    source_manifest_sha256: str,
    source_artifact_set_sha256: str,
    policy_version: int,
) -> NgsAlignmentPresentationJob:
    request_id = presentation_request_id(
        job_id=job_id,
        session_id=session_id,
        source_authority_sha256=source_authority_sha256,
        policy_version=policy_version,
    )
    existing = await session.get(NgsAlignmentPresentationJob, request_id)
    if existing is not None:
        expected = (
            job_id,
            session_id,
            mode,
            source_authority_sha256,
            source_manifest_sha256,
            source_artifact_set_sha256,
            policy_version,
        )
        observed = (
            existing.job_id,
            existing.session_id,
            existing.mode,
            existing.source_authority_sha256,
            existing.source_manifest_sha256,
            existing.source_artifact_set_sha256,
            existing.policy_version,
        )
        if observed != expected:
            raise ValueError("presentation request identity conflicts with stored source authority")
        return existing
    row = NgsAlignmentPresentationJob(
        id=request_id,
        job_id=job_id,
        session_id=session_id,
        mode=mode,
        source_authority_sha256=source_authority_sha256,
        source_manifest_sha256=source_manifest_sha256,
        source_artifact_set_sha256=source_artifact_set_sha256,
        policy_version=policy_version,
        state="requested",
        attempt_count=0,
        manual_retry_count=0,
    )
    session.add(row)
    await session.flush()
    return row


async def get_session_presentation(
    session: AsyncSession, *, job_id: str, session_id: str,
    authority_sha256: str | None = None,
) -> NgsAlignmentPresentationJob | None:
    """Historical only: never select a newer generation as implicit authority."""
    query = select(NgsAlignmentPresentationJob).where(
        NgsAlignmentPresentationJob.job_id == job_id,
        NgsAlignmentPresentationJob.session_id == session_id)
    if authority_sha256 is not None:
        query = query.where(NgsAlignmentPresentationJob.authority_sha256 == authority_sha256,
                            NgsAlignmentPresentationJob.state == "ready")
    rows = (await session.scalars(query.limit(2))).all()
    if len(rows) > 1:
        raise PresentationSourceStale("historical presentation generation is ambiguous; use an exact authority")
    return rows[0] if rows else None

async def claim_next_presentation(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    lease_seconds: int = 300,
    claim_token: str,
) -> NgsAlignmentPresentationJob | None:
    claimed_at = now or _now()
    candidate = (
        await session.execute(
            select(NgsAlignmentPresentationJob)
            .where(
                NgsAlignmentPresentationJob.state == "requested",
                NgsAlignmentPresentationJob.claim_token.is_(None),
                NgsAlignmentPresentationJob.lease_expires_at.is_(None),
            )
            .order_by(
                NgsAlignmentPresentationJob.created_at.asc(),
                NgsAlignmentPresentationJob.id.asc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if candidate is None:
        return None
    result = await session.execute(
        update(NgsAlignmentPresentationJob)
        .where(
            NgsAlignmentPresentationJob.id == candidate.id,
            NgsAlignmentPresentationJob.state == "requested",
            NgsAlignmentPresentationJob.source_authority_sha256
            == candidate.source_authority_sha256,
            NgsAlignmentPresentationJob.claim_token.is_(None),
            NgsAlignmentPresentationJob.lease_expires_at.is_(None),
        )
        .values(
            state="running",
            attempt_count=NgsAlignmentPresentationJob.attempt_count + 1,
            claim_token=claim_token,
            lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
            error_code=None,
            updated_at=claimed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    await session.refresh(candidate)
    return candidate


async def renew_presentation_lease(
    session: AsyncSession,
    request_id: str,
    claim_token: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> datetime:
    renewed_at = now or _now()
    renewed_until = renewed_at + timedelta(seconds=lease_seconds)
    result = await session.execute(
        update(NgsAlignmentPresentationJob)
        .where(
            NgsAlignmentPresentationJob.id == request_id,
            NgsAlignmentPresentationJob.state == "running",
            NgsAlignmentPresentationJob.claim_token == claim_token,
            NgsAlignmentPresentationJob.lease_expires_at > renewed_at,
        )
        .values(lease_expires_at=renewed_until, updated_at=renewed_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationClaimLost("presentation lease ownership lost or expired")
    await session.commit()
    return renewed_until


async def mark_presentation_ready(
    session: AsyncSession,
    request_id: str,
    claim_token: str,
    *,
    source_authority_sha256: str,
    authority_sha256: str,
    manifest_sha256: str,
    now: datetime | None = None,
) -> NgsAlignmentPresentationJob:
    completed_at = now or _now()
    result = await session.execute(
        update(NgsAlignmentPresentationJob)
        .where(
            NgsAlignmentPresentationJob.id == request_id,
            NgsAlignmentPresentationJob.state == "running",
            NgsAlignmentPresentationJob.claim_token == claim_token,
            NgsAlignmentPresentationJob.lease_expires_at > completed_at,
            NgsAlignmentPresentationJob.source_authority_sha256
            == source_authority_sha256,
        )
        .values(
            state="ready",
            claim_token=None,
            lease_expires_at=None,
            authority_sha256=authority_sha256,
            manifest_sha256=manifest_sha256,
            error_code=None,
            updated_at=completed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationClaimLost("presentation ready publication claim was lost")
    await session.commit()
    row = await session.get(NgsAlignmentPresentationJob, request_id)
    if row is None:
        raise PresentationClaimLost("presentation request disappeared after ready publication")
    await session.refresh(row)
    return row


async def recover_expired_presentations(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    recovered_at = now or _now()
    rows = list(
        (
            await session.execute(
                select(NgsAlignmentPresentationJob).where(
                    NgsAlignmentPresentationJob.state == "running",
                    NgsAlignmentPresentationJob.lease_expires_at <= recovered_at,
                )
            )
        ).scalars()
    )
    recovered = 0
    for row in rows:
        result = await session.execute(
            update(NgsAlignmentPresentationJob)
            .where(
                NgsAlignmentPresentationJob.id == row.id,
                NgsAlignmentPresentationJob.state == "running",
                NgsAlignmentPresentationJob.claim_token == row.claim_token,
                NgsAlignmentPresentationJob.lease_expires_at == row.lease_expires_at,
                NgsAlignmentPresentationJob.lease_expires_at <= recovered_at,
                NgsAlignmentPresentationJob.source_authority_sha256
                == row.source_authority_sha256,
            )
            .values(
                state="failed",
                claim_token=None,
                lease_expires_at=None,
                authority_sha256=None,
                manifest_sha256=None,
                error_code="infrastructure_failed",
                updated_at=recovered_at,
            )
            .execution_options(synchronize_session=False)
        )
        recovered += int(result.rowcount == 1)
    if recovered:
        await session.commit()
    return recovered


async def record_presentation_failure(
    session: AsyncSession,
    request_id: str,
    claim_token: str,
    *,
    error_code: str,
    now: datetime | None = None,
) -> NgsAlignmentPresentationJob:
    failed_at = now or _now()
    row = await session.get(NgsAlignmentPresentationJob, request_id)
    if (
        row is None
        or row.state != "running"
        or row.claim_token != claim_token
        or row.lease_expires_at is None
        or row.lease_expires_at <= failed_at
    ):
        await session.rollback()
        raise PresentationClaimLost("presentation failure claim was lost")
    values = {
        "state": "failed",
        "claim_token": None,
        "lease_expires_at": None,
        "authority_sha256": None,
        "manifest_sha256": None,
        "error_code": error_code,
        "updated_at": failed_at,
    }
    result = await session.execute(
        update(NgsAlignmentPresentationJob)
        .where(
            NgsAlignmentPresentationJob.id == request_id,
            NgsAlignmentPresentationJob.state == "running",
            NgsAlignmentPresentationJob.claim_token == claim_token,
            NgsAlignmentPresentationJob.lease_expires_at == row.lease_expires_at,
            NgsAlignmentPresentationJob.lease_expires_at > failed_at,
            NgsAlignmentPresentationJob.source_authority_sha256
            == row.source_authority_sha256,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise PresentationClaimLost("presentation failure claim was lost")
    await session.commit()
    await session.refresh(row)
    return row


async def retry_failed_presentation(
    session: AsyncSession,
    request_id: str,
    *,
    current_source_authority_sha256: str,
    now: datetime | None = None,
) -> NgsAlignmentPresentationJob:
    retried_at = now or _now()
    row = await session.get(NgsAlignmentPresentationJob, request_id)
    if row is None:
        raise ValueError("presentation request does not exist")
    if row.state == "ready":
        raise PresentationAlreadyReady("presentation is already ready")
    if row.state in {"requested", "running"}:
        return row
    if row.source_authority_sha256 != current_source_authority_sha256:
        raise PresentationSourceStale("presentation source authority is stale")
    result = await session.execute(
        update(NgsAlignmentPresentationJob)
        .where(
            NgsAlignmentPresentationJob.id == request_id,
            NgsAlignmentPresentationJob.state == "failed",
            NgsAlignmentPresentationJob.source_authority_sha256
            == current_source_authority_sha256,
            NgsAlignmentPresentationJob.claim_token.is_(None),
            NgsAlignmentPresentationJob.lease_expires_at.is_(None),
        )
        .values(
            state="requested",
            attempt_count=0,
            manual_retry_count=NgsAlignmentPresentationJob.manual_retry_count + 1,
            claim_token=None,
            lease_expires_at=None,
            authority_sha256=None,
            manifest_sha256=None,
            error_code=None,
            updated_at=retried_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        winner = (
            await session.execute(
                select(NgsAlignmentPresentationJob)
                .where(NgsAlignmentPresentationJob.id == request_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if winner is None:
            raise PresentationClaimLost("presentation retry target disappeared")
        if winner.state in {"requested", "running"}:
            return winner
        if winner.state == "ready":
            raise PresentationAlreadyReady("presentation is already ready")
        if winner.source_authority_sha256 != current_source_authority_sha256:
            raise PresentationSourceStale("presentation source authority is stale")
        raise PresentationClaimLost("presentation retry CAS lost to a conflicting failed state")
    await session.commit()
    await session.refresh(row)
    return row


__all__ = [
    "PresentationAlreadyReady",
    "PresentationClaimLost",
    "PresentationSourceStale",
    "claim_next_presentation",
    "get_session_presentation",
    "mark_presentation_ready",
    "presentation_request_id",
    "recover_expired_presentations",
    "record_presentation_failure",
    "renew_presentation_lease",
    "request_presentation",
    "retry_failed_presentation",
]
