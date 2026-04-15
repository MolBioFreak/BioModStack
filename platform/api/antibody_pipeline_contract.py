from __future__ import annotations

from typing import Any, Optional


ANTIBODY_PIPELINE_CONTRACT_VERSION = 1

ANTIBODY_DENOVO_PIPELINE = "antibody_denovo_pipeline"
ANTIBODY_REFINEMENT_PIPELINE = "antibody_refinement_pipeline"
ANTIBODY_PIPELINE_MODES = frozenset(
    {
        ANTIBODY_DENOVO_PIPELINE,
        ANTIBODY_REFINEMENT_PIPELINE,
    }
)

BACKBONE_COMPLEX = "backbone_complex"
SEQUENCE_DESIGNED_COMPLEX = "sequence_designed_complex"
VALIDATED_COMPLEX = "validated_complex"
POST_VALIDATION_REFINED_COMPLEX = "post_validation_refined_complex"

ANTIBODY_ARTIFACT_CLASSES = frozenset(
    {
        BACKBONE_COMPLEX,
        SEQUENCE_DESIGNED_COMPLEX,
        VALIDATED_COMPLEX,
        POST_VALIDATION_REFINED_COMPLEX,
    }
)


def _normalize_token(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def normalize_antibody_artifact_class(value: Any) -> Optional[str]:
    normalized = _normalize_token(value)
    return normalized if normalized in ANTIBODY_ARTIFACT_CLASSES else None


def normalize_antibody_pipeline_mode(value: Any) -> Optional[str]:
    normalized = _normalize_token(value)
    return normalized if normalized in ANTIBODY_PIPELINE_MODES else None


def is_antibody_pipeline_mode(value: Any) -> bool:
    return normalize_antibody_pipeline_mode(value) is not None


def normalize_antibody_pipeline_contract_version(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def infer_antibody_artifact_class_from_stage(stage_family: Any, stage_mode: Any) -> Optional[str]:
    family = _normalize_token(stage_family)
    mode = _normalize_token(stage_mode)

    if family == "rfantibody":
        return BACKBONE_COMPLEX
    if family == "boltzgen":
        return SEQUENCE_DESIGNED_COMPLEX
    if family in {"fampnn", "antifold", "proteinmpnn", "frustrampnn", "caliby"}:
        return SEQUENCE_DESIGNED_COMPLEX
    if family == "validation":
        return VALIDATED_COMPLEX
    if family == "openmm":
        return POST_VALIDATION_REFINED_COMPLEX
    if family == "ppiflow":
        if mode in {"backbone_refine", "generator_backbone_refine", "post_rfantibody", "post_ppiflow"}:
            return BACKBONE_COMPLEX
        if mode in {"maturation", "post_fampnn"}:
            return SEQUENCE_DESIGNED_COMPLEX
        if mode in {"post_validation_maturation", "post_structure_validation"}:
            return POST_VALIDATION_REFINED_COMPLEX
    return None


def infer_selected_input_artifact_class(
    *,
    selected_input_artifact_class: Any = None,
    selected_input_stage_family: Any = None,
    selected_input_stage_mode: Any = None,
    rfantibody_input_pdbs: Any = None,
    fampnn_collected_pdbs: Any = None,
) -> Optional[str]:
    explicit = normalize_antibody_artifact_class(selected_input_artifact_class)
    if explicit:
        return explicit

    inferred = infer_antibody_artifact_class_from_stage(
        selected_input_stage_family,
        selected_input_stage_mode,
    )
    if inferred:
        return inferred

    if _normalize_token(fampnn_collected_pdbs):
        return SEQUENCE_DESIGNED_COMPLEX
    if _normalize_token(rfantibody_input_pdbs):
        return BACKBONE_COMPLEX
    return None
