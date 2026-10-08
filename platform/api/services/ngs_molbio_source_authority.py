"""Execution provenance from the deployed build, not release-audit source files."""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from build_identity import current_build_identity


class SourceBuildRevisionError(RuntimeError):
    pass


def source_build_revision() -> str:
    revision = current_build_identity()["revision"]
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SourceBuildRevisionError("deployed build revision is unavailable")
    return revision


@lru_cache(maxsize=4)
def _checkout_tree(revision: str) -> str:
    # Development checkouts predating BMS_BUILD_TREE still have this Git object.
    try:
        return subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parents[3]), "rev-parse", "--verify", f"{revision}^{{tree}}"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceBuildRevisionError("deployed build tree is unavailable") from exc


def source_build_identity() -> tuple[str, str]:
    revision = source_build_revision()
    tree = os.environ.get("BMS_BUILD_TREE") or _checkout_tree(revision)
    if re.fullmatch(r"[0-9a-f]{40}", tree) is None:
        raise SourceBuildRevisionError("deployed build tree is invalid")
    return revision, tree
