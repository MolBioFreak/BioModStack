from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from antibody_pipeline_contract import normalize_antibody_artifact_class


class ResultContract(BaseModel):
    analysis_contract_id: Optional[str] = None
    supported_analyzers: List[str] = Field(default_factory=list)


def _token(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def resolve_result_contract(
    *,
    result_set: Optional[str] = None,
    stage_family: Any = None,
    stage_mode: Any = None,
    artifact_class: Any = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> ResultContract:
    """Resolve the explicit analysis contract for a design row.

    This is intentionally conservative: unknown model/stage/artifact
    combinations return no analyzers so the viewer can fail closed instead of
    borrowing behavior from metric-shaped payloads.
    """
    family = _token(stage_family)
    mode = _token(stage_mode)
    artifact = normalize_antibody_artifact_class(artifact_class) or _token(artifact_class)
    model_id = _token((provenance or {}).get("model_id"))

    if result_set == "rfantibody_backbones" or family == "rfantibody" or artifact == "backbone_complex":
        return ResultContract(analysis_contract_id="antibody_backbone_v1", supported_analyzers=["antibody_backbone_v1"])
    if result_set == "sequence_designs" or artifact == "sequence_designed_complex" or family in {"boltzgen", "fampnn", "proteinmpnn", "antifold", "frustrampnn", "caliby"}:
        return ResultContract(analysis_contract_id="sequence_design_v1", supported_analyzers=["sequence_design_v1"])
    if result_set in {"ppiflow_candidates", "ppiflow_passed", "ppiflow_rejected"} or family == "ppiflow" or (mode and ("ppiflow" in mode or "maturation" in mode)):
        return ResultContract(analysis_contract_id="ppiflow_maturation_v1", supported_analyzers=["ppiflow_maturation_v1"])
    if artifact == "validated_complex" or family in {"validation", "boltz2", "protenix", "esmfold2"} or model_id in {"boltz2", "boltz_cp_experimental", "protenix", "esmfold2_experimental"}:
        return ResultContract(analysis_contract_id="structure_prediction_v1", supported_analyzers=["structure_prediction_v1"])
    if family == "confornets" or model_id == "confornets_experimental":
        return ResultContract(analysis_contract_id="confornets_monomer_v1", supported_analyzers=[])
    return ResultContract()
