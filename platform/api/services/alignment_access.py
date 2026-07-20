"""Opaque per-job capability credentials for sensitive alignment artifacts."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Request, Response

COOKIE_PREFIX = "bms_alignment_access_"
PROVENANCE_DIGEST_KEY = "alignment_access_token_sha256"
PROVENANCE_SCHEME_KEY = "alignment_access_scheme"
SCHEME = "opaque_job_capability_v1"


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
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    response.set_cookie(
        key=cookie_name(job_id),
        value=token,
        httponly=True,
        secure=forwarded_proto == "https" or request.url.scheme == "https",
        samesite="strict",
        path=f"/api/jobs/{job_id}",
    )


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_name(job_id: str) -> str:
    safe = "".join(character if character.isalnum() else "_" for character in job_id)
    return f"{COOKIE_PREFIX}{safe}"


def request_alignment_token(request: Request, job_id: str) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and credential.strip():
        return credential.strip()
    value = request.cookies.get(cookie_name(job_id))
    return value.strip() if value and value.strip() else None


def capability_matches(token: str | None, expected_sha256: str | None) -> bool:
    if not token or not expected_sha256 or len(expected_sha256) != 64:
        return False
    return hmac.compare_digest(token_sha256(token), expected_sha256.lower())


def request_is_authorized(request: Request, job_id: str, provenance: object) -> bool:
    expected = provenance.get(PROVENANCE_DIGEST_KEY) if isinstance(provenance, dict) else None
    return capability_matches(request_alignment_token(request, job_id), expected if isinstance(expected, str) else None)
