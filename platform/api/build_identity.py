from __future__ import annotations

import os
import re
import subprocess

from component_runtime import SourceIdentity
from paths import get_code_root


def deployed_source_identity() -> tuple[str, str]:
    """Observe deployment metadata, never attest working bytes or gate work.

    Packaged builds may have no Git metadata. Preserve the deployed revision
    and report an unavailable tree as unknown rather than inventing a digest.
    """
    revision = current_build_identity()["revision"]
    try:
        if revision == "unknown":
            source = SourceIdentity.from_checkout(get_code_root())
        else:
            tree = subprocess.run(
                ["git", "rev-parse", f"{revision}^{{tree}}"],
                cwd=get_code_root(), check=True, capture_output=True, text=True,
                timeout=5,
            ).stdout.strip()
            source = SourceIdentity(revision, tree)
        return source.revision, source.tree
    except (OSError, ValueError, subprocess.SubprocessError):
        return revision, "unknown"


def source_build_revision() -> str:
    revision = current_build_identity()["revision"]
    return revision if revision != "unknown" else deployed_source_identity()[0]


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
