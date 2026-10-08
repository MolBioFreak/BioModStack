from __future__ import annotations

import os
import re


_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _clean(value: str | None, default: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or any(character in cleaned for character in "\r\n\x00"):
        return default
    return cleaned


def current_build_identity() -> dict[str, str]:
    revision = _clean(os.getenv("BMS_BUILD_SHA"), "unknown").lower()
    if not _FULL_GIT_SHA.fullmatch(revision):
        revision = "unknown"
    return {
        "revision": revision,
        "build_id": _clean(os.getenv("BMS_BUILD_ID"), "development"),
        "build_time": _clean(os.getenv("BMS_BUILD_TIME"), "unknown"),
    }
