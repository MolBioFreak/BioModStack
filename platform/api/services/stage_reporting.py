"""Launch-scoped authentication for internal workflow stage callbacks."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any, Mapping

PROVENANCE_DIGEST_KEY = "workflow_stage_report_token_sha256"
ENV_TOKEN_KEY = "BMS_STAGE_REPORT_TOKEN"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def issue_stage_report_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode("ascii")).hexdigest()


def token_is_authorized(provenance: Mapping[str, Any] | None, token: str) -> bool:
    candidate = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(candidate):
        return False
    expected = str((provenance or {}).get(PROVENANCE_DIGEST_KEY) or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    observed = hashlib.sha256(candidate.encode("ascii")).hexdigest()
    return hmac.compare_digest(expected, observed)
