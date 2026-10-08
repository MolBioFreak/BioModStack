from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExternalImportPreview:
    provider: str
    resource_type: str
    provider_job_id: str
    model: str | None
    model_version: str | None
    status: str
    sample_count: int
    entities: list[dict[str, Any]]
    source_fingerprint: str
    run_metadata_sha256: str
    archive_sha256: str | None
    importable: bool
    error_code: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "resource_type": self.resource_type,
            "provider_job_id": self.provider_job_id,
            "model": self.model,
            "model_version": self.model_version,
            "status": self.status,
            "sample_count": self.sample_count,
            "entities": self.entities,
            "source_fingerprint": self.source_fingerprint,
            "run_metadata_sha256": self.run_metadata_sha256,
            "archive_sha256": self.archive_sha256,
            "importable": self.importable,
            "error_code": self.error_code,
            "errors": self.errors,
            "warnings": self.warnings,
            "provider_metadata": self.provider_metadata,
        }
