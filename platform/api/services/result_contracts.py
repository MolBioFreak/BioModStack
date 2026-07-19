from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from antibody_pipeline_contract import normalize_antibody_artifact_class


REVIEW_ARTIFACT_SCHEMA = "bms.review-artifacts.v1"
REVIEW_CONTRACT_VERSION = 1


class ResultContract(BaseModel):
    analysis_contract_id: Optional[str] = None
    supported_analyzers: List[str] = Field(default_factory=list)
    viewer_capabilities: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    required_artifacts: List[str] = Field(default_factory=list)
    schema_version: Optional[int] = None
    contract_source: str = "unsupported"


class ResultContractDefinition(BaseModel):
    contract_id: str
    schema_version: int = 1
    model_ids: List[str] = Field(default_factory=list)
    stage_families: List[str] = Field(default_factory=list)
    stage_modes: List[str] = Field(default_factory=list)
    artifact_classes: List[str] = Field(default_factory=list)
    result_sets: List[str] = Field(default_factory=list)
    supported_analyzers: List[str] = Field(default_factory=list)
    viewer_capabilities: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    required_artifacts: List[str] = Field(default_factory=list)
    notes: str = ""


_RESULT_CONTRACT_DEFINITIONS: List[ResultContractDefinition] = [
    ResultContractDefinition(
        contract_id="antibody_backbone_v1",
        model_ids=["rfantibody"],
        stage_families=["rfantibody"],
        artifact_classes=["backbone_complex"],
        result_sets=["rfantibody_backbones"],
        supported_analyzers=["antibody_backbone_v1"],
        viewer_capabilities=["result_filter", "structure_viewer", "antibody_backbone_metrics"],
        required_fields=["artifact_class", "result_set"],
        required_artifacts=["structure"],
        notes="RFantibody/backbone generation outputs.",
    ),
    ResultContractDefinition(
        contract_id="sequence_design_v1",
        model_ids=["fampnn", "proteinmpnn", "antifold", "frustrampnn", "caliby", "boltzgen"],
        stage_families=["fampnn", "proteinmpnn", "antifold", "frustrampnn", "caliby", "boltzgen"],
        artifact_classes=["sequence_designed_complex"],
        result_sets=["sequence_designs"],
        supported_analyzers=["sequence_design_v1"],
        viewer_capabilities=["result_filter", "structure_viewer", "sequence_design_metrics"],
        required_fields=["artifact_class", "result_set"],
        required_artifacts=["structure"],
        notes="Sequence design outputs; individual models opt in by family/model id.",
    ),
    ResultContractDefinition(
        contract_id="ppiflow_maturation_v1",
        model_ids=["ppiflow"],
        stage_families=["ppiflow"],
        stage_modes=["maturation"],
        artifact_classes=["sequence_designed_complex", "post_validation_refined_complex"],
        result_sets=["ppiflow_candidates", "ppiflow_passed", "ppiflow_rejected"],
        supported_analyzers=["ppiflow_maturation_v1"],
        viewer_capabilities=["result_filter", "structure_viewer", "ppiflow_maturation_metrics"],
        required_fields=["artifact_class", "result_set", "ppiflow_objective_score", "rosetta_interface_score"],
        required_artifacts=["structure", "maturation_score_json"],
        notes="PPIFlow local maturation/refinement outputs; paper-rank fields are completeness-gated.",
    ),
    ResultContractDefinition(
        contract_id="structure_prediction_v1",
        model_ids=["boltz2", "boltz_cp_experimental", "protenix", "esmfold2_experimental", "esmfold2"],
        stage_families=["validation", "boltz2", "protenix", "esmfold2"],
        artifact_classes=["validated_complex", "imported_structure"],
        result_sets=["validated"],
        supported_analyzers=["structure_prediction_v1"],
        viewer_capabilities=["structure_viewer", "structure_confidence_metrics"],
        required_fields=["artifact_class"],
        required_artifacts=["structure"],
        notes="Structure prediction/validation-style outputs with explicitly compatible confidence metrics.",
    ),
    ResultContractDefinition(
        contract_id="confornets_monomer_v1",
        model_ids=["confornets_experimental", "confornets"],
        stage_families=["confornets"],
        artifact_classes=["monomer_conformation"],
        result_sets=[],
        supported_analyzers=[],
        viewer_capabilities=["generic_metadata"],
        required_fields=["artifact_class"],
        required_artifacts=[],
        notes="Experimental conformational mapping contract; ConforNets is the first implemented monomer backend and publishes normalized conformer artifacts plus backend-native metrics.",
    ),
]


def get_result_contract_definitions() -> List[ResultContractDefinition]:
    return list(_RESULT_CONTRACT_DEFINITIONS)


def _token(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _tokens(values: List[str]) -> set[str]:
    return {_token(value) or "" for value in values}


def _contract_from_definition(definition: ResultContractDefinition) -> ResultContract:
    return ResultContract(
        analysis_contract_id=definition.contract_id,
        supported_analyzers=list(definition.supported_analyzers),
        viewer_capabilities=list(definition.viewer_capabilities),
        required_fields=list(definition.required_fields),
        required_artifacts=list(definition.required_artifacts),
        schema_version=definition.schema_version,
        contract_source="registry",
    )


def _matches_definition(
    definition: ResultContractDefinition,
    *,
    result_set: Optional[str],
    family: Optional[str],
    mode: Optional[str],
    artifact: Optional[str],
    model_id: Optional[str],
) -> bool:
    if result_set and result_set in definition.result_sets:
        return True
    if model_id and model_id in _tokens(definition.model_ids):
        return True
    if family and family in _tokens(definition.stage_families):
        return True
    if mode and mode in _tokens(definition.stage_modes):
        return True
    if artifact and artifact in _tokens(definition.artifact_classes):
        return True
    return False


def resolve_result_contract(
    *,
    result_set: Optional[str] = None,
    stage_family: Any = None,
    stage_mode: Any = None,
    artifact_class: Any = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> ResultContract:
    """Resolve the explicit analysis contract for a design row.

    Unknown model/stage/artifact combinations intentionally return no analyzers
    so the viewer fails closed instead of borrowing behavior from metric-shaped
    payloads.  Existing stage/result-set inference may supply inputs here for
    old rows, but this resolver itself only returns registered contracts.
    """
    family = _token(stage_family)
    mode = _token(stage_mode)
    artifact = normalize_antibody_artifact_class(artifact_class) or _token(artifact_class)
    model_id = _token((provenance or {}).get("model_id"))

    # Result sets are the most specific durable selector and must win over
    # broader artifact classes (for example PPIFlow matured designs are still
    # sequence-designed complexes, but their result_set is PPIFlow-specific).
    if result_set:
        for definition in _RESULT_CONTRACT_DEFINITIONS:
            if result_set in definition.result_sets:
                return _contract_from_definition(definition)

    for definition in _RESULT_CONTRACT_DEFINITIONS:
        if _matches_definition(
            definition,
            result_set=None,
            family=family,
            mode=mode,
            artifact=artifact,
            model_id=model_id,
        ):
            return _contract_from_definition(definition)
    return ResultContract()
