"""Small text-only filter for explicitly published operational diagnostics.

Filter before hashing/copy publication, never over sealed scientific artifacts.
Recognizes credential URLs and secret-labelled assignments; explicit environment
values cover unlabelled occurrences of known secrets, not unknown secret formats.
"""
from __future__ import annotations

import re
from typing import Mapping

_URL_CREDENTIAL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)([^@\s]+)(@)")
_SECRET_LABEL = r"[A-Za-z0-9_-]*(?:password|passwd|token|secret|api[_-]?key|authorization)[A-Za-z0-9_-]*"
_SECRET_KEY_RE = re.compile(_SECRET_LABEL, re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b" + _SECRET_LABEL + r"\b[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;]+)"
)


def redact_text(text: str, *, environment: Mapping[str, str] | None = None) -> str:
    """Return filtered text without I/O or implicit environment access.

    Only nonempty values of secret-labelled environment keys are matched, as
    literal strings (longest first). Callers must supply the execution environment
    when those values could appear without labels. This is not a universal secret
    detector and must not be applied to scientific inputs, metrics or manifests.
    """
    if environment is not None:
        values = {value for key, value in environment.items()
                  if value and _SECRET_KEY_RE.fullmatch(key)}
        for value in sorted(values, key=lambda item: (-len(item), item)):
            text = text.replace(value, "[REDACTED]")
    text = _URL_CREDENTIAL_RE.sub(r"\1***\3", text)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
