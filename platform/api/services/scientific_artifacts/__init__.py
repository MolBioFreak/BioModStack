"""Shared scientific artifact package."""
from .contracts import (
    ARTIFACT_REFERENCE_SCHEMA,
    ARTIFACT_ROW_REFERENCE_SCHEMA,
    artifact_reference,
    artifact_row_reference,
    canonical_json_bytes,
    canonical_sha256,
    envelope_rows,
    is_artifact_reference,
    reconstruct_envelope,
    require_row_reference,
)
from .persistence import publish_json_payload, publish_table_rows
from .query import query_rows
from .resolve import resolve_json_value
from .writer import (
    InstalledArtifact,
    ScientificArtifactError,
    artifact_root,
    install_parquet_rows,
    read_rows,
    verify_artifact,
)

__all__ = [
    "ARTIFACT_REFERENCE_SCHEMA",
    "ARTIFACT_ROW_REFERENCE_SCHEMA",
    "InstalledArtifact",
    "ScientificArtifactError",
    "artifact_reference",
    "artifact_row_reference",
    "artifact_root",
    "canonical_json_bytes",
    "canonical_sha256",
    "envelope_rows",
    "install_parquet_rows",
    "is_artifact_reference",
    "publish_json_payload",
    "publish_table_rows",
    "query_rows",
    "read_rows",
    "require_row_reference",
    "resolve_json_value",
    "reconstruct_envelope",
    "verify_artifact",
]
