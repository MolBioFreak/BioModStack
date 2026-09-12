"""Fail-closed package source-revision authority shared by governed receipts."""
from __future__ import annotations

class SourceBuildRevisionError(RuntimeError):
    pass


def source_build_revision() -> str:
    try:
        from services.ngs_molbio_runtime_status import runtime_source_commit

        return runtime_source_commit()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise SourceBuildRevisionError(
            "package-local runtime source authority is unavailable"
        ) from exc
