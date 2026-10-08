"""Strict, versioned contracts for conformational-mapping artifacts."""

from .contracts import (
    AA_ORDER,
    SCHEMA_FILENAMES,
    ContractValidationError,
    ResumeDescriptor,
    candidate_id,
    canonical_json_bytes,
    canonical_sha256,
    load_schema,
    validate_schema,
)

__all__ = [
    "AA_ORDER",
    "SCHEMA_FILENAMES",
    "ContractValidationError",
    "ResumeDescriptor",
    "candidate_id",
    "canonical_json_bytes",
    "canonical_sha256",
    "load_schema",
    "validate_schema",
]
