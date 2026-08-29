"""Opaque per-job capability credentials for sensitive alignment artifacts."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
from typing import Any

from fastapi import Request, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job

COOKIE_PREFIX = "bms-ngs-"
COOKIE_MAX_AGE_SECONDS = 1800
PROVENANCE_DIGEST_KEY = "alignment_access_token_sha256"
PROVENANCE_SCHEME_KEY = "alignment_access_scheme"
SCHEME = "opaque_job_capability_v1"


def secure_alignment_transport(request: Request) -> bool:
    return request.scope.get("scheme") == "https"


def _insecure_loopback_development(request: Request) -> bool:
    if os.environ.get("BMS_RUNTIME_MODE") != "dev" or request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return request.client.host == "localhost"


def issue_alignment_access_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_sha256(token)


def grant_alignment_access(
    job_id: str,
    provenance: dict | None,
    response: Response,
    request: Request,
) -> dict:
    """Return provenance with a fresh digest and deliver the plaintext only as a cookie."""
    token, token_digest = issue_alignment_access_token()
    updated = dict(provenance) if isinstance(provenance, dict) else {}
    updated[PROVENANCE_DIGEST_KEY] = token_digest
    updated[PROVENANCE_SCHEME_KEY] = SCHEME
    set_alignment_access_cookie(job_id, token, response, request)
    return updated


def set_alignment_access_cookie(job_id: str, token: str, response: Response, request: Request) -> None:
    """Deliver a pre-issued plaintext capability without persisting or returning it."""
    secure = secure_alignment_transport(request)
    if not secure and not _insecure_loopback_development(request):
        raise RuntimeError("insecure alignment capability cookies require loopback Development")
    response.set_cookie(
        key=cookie_name(job_id, secure=secure),
        value=token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=COOKIE_MAX_AGE_SECONDS,
    )


def expire_alignment_access_cookie(job_id: str, response: Response, request: Request) -> None:
    secure = secure_alignment_transport(request)
    if not secure and not _insecure_loopback_development(request):
        raise RuntimeError("insecure alignment capability cookies require loopback Development")
    response.delete_cookie(
        key=cookie_name(job_id, secure=secure),
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def alignment_access_cookie_expiration_header(job_id: str, request: Request) -> str:
    response = Response()
    expire_alignment_access_cookie(job_id, response, request)
    return response.headers["set-cookie"]


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_name(job_id: str, *, secure: bool = False) -> str:
    job_digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]
    host_prefix = "__Host-" if secure else ""
    return f"{host_prefix}{COOKIE_PREFIX}{job_digest}"


def request_alignment_token(request: Request, job_id: str) -> str | None:
    secure = secure_alignment_transport(request)
    if not secure and not _insecure_loopback_development(request):
        return None
    value = request.cookies.get(cookie_name(job_id, secure=secure))
    return value.strip() if value and value.strip() else None


def capability_matches(token: str | None, expected_sha256: str | None) -> bool:
    if not token or not expected_sha256 or len(expected_sha256) != 64:
        return False
    return hmac.compare_digest(token_sha256(token), expected_sha256.lower())


def request_is_authorized(request: Request, job_id: str, provenance: object) -> bool:
    expected = provenance.get(PROVENANCE_DIGEST_KEY) if isinstance(provenance, dict) else None
    return capability_matches(request_alignment_token(request, job_id), expected if isinstance(expected, str) else None)


async def rotate_alignment_authority_cas(
    session: AsyncSession,
    *,
    job_id: str,
    previous: dict[str, Any],
    updated: dict[str, Any],
) -> bool:
    result = await session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "completed",
            Job.model_id == "nanopore",
            Job.provenance == previous,
        )
        .values(provenance=updated)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
