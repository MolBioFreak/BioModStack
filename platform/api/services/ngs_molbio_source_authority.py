"""Fail-closed package source-revision authority shared by governed receipts."""
from __future__ import annotations

import re


class SourceBuildRevisionError(RuntimeError):
    pass


def source_build_revision() -> str:
    try:
        from services.ngs_molbio_runtime_status import runtime_implementation_record

        value = runtime_implementation_record().get("successor_source_commit")
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise SourceBuildRevisionError(
            "package-local runtime source authority is unavailable"
        ) from exc
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise SourceBuildRevisionError(
            "package-local runtime source revision is invalid"
        )
    return value
