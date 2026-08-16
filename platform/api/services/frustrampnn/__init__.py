"""Neutral, CPU-testable FrustraMPNN contracts and scientific core."""

from .analysis import (
    LandscapeValidationError,
    THRESHOLD_POLICY,
    finalize_landscape,
    score_class,
    summarize_landscape,
)
from .contracts import (
    AA_ORDER,
    ContractValidationError,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
    validate_schema,
)
from .manifests import (
    CANONICAL_ARTIFACT_PATHS,
    ManifestValidationError,
    build_result_manifest,
    validate_result_manifest,
)
from .structure import (
    NORMALIZER_VERSION,
    StructureNormalizationError,
    normalize_structure,
    normalize_structure_bytes,
    read_structure_bytes,
)
from .runtime import (
    FRUSTRAMPNN_RUNTIME_IDENTITY,
    FRUSTRAMPNN_RUNTIME_REGISTRY,
    FrustraMPNNInvocation,
    RuntimeValidationError,
    build_frustrampnn_command,
    cm_analysis_runtime_registry_v1,
    open_regular_no_follow,
    open_verified_container,
    verify_container_assets,
)

__all__ = [
    "AA_ORDER", "CANONICAL_ARTIFACT_PATHS", "ContractValidationError",
    "FRUSTRAMPNN_RUNTIME_IDENTITY", "FRUSTRAMPNN_RUNTIME_REGISTRY",
    "FrustraMPNNInvocation",
    "LandscapeValidationError", "ManifestValidationError", "NORMALIZER_VERSION",
    "RuntimeValidationError", "StructureNormalizationError", "THRESHOLD_POLICY",
    "build_frustrampnn_command", "build_result_manifest",
    "canonical_json_bytes", "canonical_json_loads", "canonical_sha256",
    "finalize_landscape", "normalize_structure", "normalize_structure_bytes",
    "read_structure_bytes", "score_class",
    "cm_analysis_runtime_registry_v1", "open_regular_no_follow", "open_verified_container",
    "summarize_landscape", "validate_result_manifest", "validate_schema",
    "verify_container_assets",
]
