from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from antibody_pipeline_contract import normalize_antibody_artifact_class
from paths import resolve_runtime_data_path


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


_STRUCTURE_ANALYZERS = ["structure_summary", "contact_map", "chain_metrics"]
_CONFIDENCE_ANALYZERS = ["pae_matrix"]
_ANTIBODY_ANALYZERS = ["ipsae_interface", "antibody_annotation_pack"]
_CM_ANALYZERS = ["conformational_mapping_analysis", "frustration_landscape"]
_CM_VIEWER_CAPABILITIES = [
    "conformational_mapping_viewer", "candidate_overlay", "residue_mapping",
    "frustration_landscape", "analysis_ranking", "content_addressed_download",
]


_RESULT_CONTRACT_DEFINITIONS: List[ResultContractDefinition] = [
    ResultContractDefinition(
        contract_id="shape_blueprint",
        model_ids=["protein_modification_experimental"],
        stage_families=["shape_blueprint"],
        stage_modes=["shape_blueprint"],
        artifact_classes=["shape_candidate"],
        result_sets=["shape_candidates"],
        supported_analyzers=[*_STRUCTURE_ANALYZERS, *_CONFIDENCE_ANALYZERS],
        viewer_capabilities=[
            "result_filter", "structure_viewer", "structure_confidence_metrics",
            "shape_geometry_overlay", "shape_candidate_metrics", "content_addressed_download",
        ],
        required_fields=["artifact_class"],
        required_artifacts=["structure", "metrics"],
        notes="Canonical Shape-guided, sequence-designed, ESMFold2-refolded structural candidates.",
    ),
    ResultContractDefinition(
        contract_id="antibody_backbone_v1",
        model_ids=["rfantibody"],
        stage_families=["rfantibody"],
        artifact_classes=["backbone_complex"],
        result_sets=["rfantibody_backbones"],
        supported_analyzers=[*_STRUCTURE_ANALYZERS, *_CONFIDENCE_ANALYZERS, *_ANTIBODY_ANALYZERS],
        viewer_capabilities=[
            "result_filter",
            "structure_viewer",
            "structure_confidence_metrics",
            "complex_interface_metrics",
            "antibody_backbone_metrics",
        ],
        required_fields=["artifact_class", "result_set"],
        required_artifacts=["structure"],
        notes="RFantibody/backbone generation outputs.",
    ),
    ResultContractDefinition(
        contract_id="ppiflow_maturation_v1",
        model_ids=["ppiflow"],
        stage_families=["ppiflow"],
        stage_modes=["maturation"],
        artifact_classes=["post_validation_refined_complex"],
        result_sets=["ppiflow_candidates", "ppiflow_passed", "ppiflow_rejected"],
        supported_analyzers=[*_STRUCTURE_ANALYZERS, *_CONFIDENCE_ANALYZERS, *_ANTIBODY_ANALYZERS],
        viewer_capabilities=[
            "result_filter",
            "structure_viewer",
            "structure_confidence_metrics",
            "complex_interface_metrics",
            "antibody_backbone_metrics",
            "ppiflow_maturation_metrics",
        ],
        required_fields=["artifact_class", "result_set", "ppiflow_objective_score", "rosetta_interface_score"],
        required_artifacts=["structure", "maturation_score_json"],
        notes="PPIFlow local maturation/refinement outputs; paper-rank fields are completeness-gated.",
    ),
    ResultContractDefinition(
        contract_id="de_novo_generation_v1",
        model_ids=["boltzgen", "rfd3", "rf3", "rfdiffusion3", "rfdiffusionaa"],
        stage_families=["boltzgen", "rfd3", "rf3", "rfdiffusion3", "rfdiffusionaa"],
        stage_modes=["backbone_generation", "de_novo", "generation"],
        artifact_classes=["generated_backbone", "generated_complex"],
        result_sets=["de_novo_backbones"],
        supported_analyzers=list(_STRUCTURE_ANALYZERS),
        viewer_capabilities=["result_filter", "structure_viewer", "de_novo_generation_metrics"],
        required_fields=["artifact_class"],
        required_artifacts=["structure"],
        notes="De-novo/backbone generation outputs; sequence-design metrics are downstream, not intrinsic.",
    ),
    ResultContractDefinition(
        contract_id="sequence_design_v1",
        model_ids=["fampnn", "proteinmpnn", "antifold", "frustrampnn", "caliby"],
        stage_families=["fampnn", "proteinmpnn", "antifold", "frustrampnn", "caliby"],
        artifact_classes=["sequence_designed_complex"],
        result_sets=["sequence_designs"],
        supported_analyzers=[*_STRUCTURE_ANALYZERS, "fampnn_psce_profile"],
        viewer_capabilities=["result_filter", "structure_viewer", "sequence_design_metrics"],
        required_fields=["artifact_class", "result_set"],
        required_artifacts=["structure"],
        notes="Sequence design outputs; individual models opt in by family/model id.",
    ),
    ResultContractDefinition(
        contract_id="binder_design_v1",
        model_ids=["binder_design"],
        stage_families=["binder_design"],
        stage_modes=["rfd3_caliby", "protein_hunter"],
        artifact_classes=["validated_binder_complex"],
        result_sets=["binder_candidates"],
        supported_analyzers=[*_STRUCTURE_ANALYZERS, *_CONFIDENCE_ANALYZERS, "ipsae_interface"],
        viewer_capabilities=[
            "result_filter",
            "structure_viewer",
            "structure_confidence_metrics",
            "complex_interface_metrics",
            "sequence_design_metrics",
            "provenance_audit",
        ],
        required_fields=["artifact_class", "result_set", "ipsae"],
        required_artifacts=["structure", "aligned_error"],
        notes="General de novo binder candidates independently validated from full PAE and accepted by numeric ipSAE.",
    ),
    ResultContractDefinition(
        contract_id="structure_prediction_v1",
        model_ids=["boltz2", "boltz_cp_experimental", "protenix", "esmfold2_experimental", "esmfold2"],
        stage_families=["validation", "boltz2", "protenix", "esmfold2"],
        artifact_classes=["validated_complex", "imported_structure"],
        result_sets=["validated"],
        supported_analyzers=[*_STRUCTURE_ANALYZERS, *_CONFIDENCE_ANALYZERS],
        viewer_capabilities=["structure_viewer", "structure_confidence_metrics"],
        required_fields=["artifact_class"],
        required_artifacts=["structure"],
        notes="Structure prediction/validation outputs. Binder semantics require a separate explicit role contract.",
    ),
    ResultContractDefinition(
        contract_id="conformational_mapping_protenix_v1",
        model_ids=["conformational_mapping"],
        artifact_classes=["monomer_conformation"],
        result_sets=["cm_protenix_ensemble"],
        supported_analyzers=list(_CM_ANALYZERS),
        viewer_capabilities=list(_CM_VIEWER_CAPABILITIES),
        required_fields=["artifact_class", "candidate_id", "backend_coordinates", "manifest_sha256"],
        required_artifacts=["structure", "confidence", "full_data", "native_manifest", "ensemble_manifest"],
        notes="Complete-complex Protenix conformational hypotheses with seed/sample authority.",
    ),
    ResultContractDefinition(
        contract_id="conformational_mapping_confornets_v1",
        model_ids=["conformational_mapping"],
        artifact_classes=["monomer_conformation"],
        result_sets=["cm_confornets_ensemble"],
        supported_analyzers=list(_CM_ANALYZERS),
        viewer_capabilities=list(_CM_VIEWER_CAPABILITIES),
        required_fields=["artifact_class", "candidate_id", "backend_coordinates", "manifest_sha256"],
        required_artifacts=["structure", "native_manifest", "ensemble_manifest"],
        notes="Instrumented ConforNets hypotheses with full backend coordinate authority.",
    ),
    ResultContractDefinition(
        contract_id="conformational_mapping_import_v1",
        model_ids=["conformational_mapping"],
        artifact_classes=["monomer_conformation"],
        result_sets=["cm_import_ensemble"],
        supported_analyzers=list(_CM_ANALYZERS),
        viewer_capabilities=list(_CM_VIEWER_CAPABILITIES),
        required_fields=["artifact_class", "candidate_id", "backend_coordinates", "manifest_sha256"],
        required_artifacts=["structure", "receipt", "native_manifest", "ensemble_manifest"],
        notes="Authenticated immutable external conformational hypotheses.",
    ),
    ResultContractDefinition(
        contract_id="conformational_mapping_analysis_v1",
        model_ids=["conformational_mapping"],
        result_sets=["cm_analysis"],
        supported_analyzers=[],
        viewer_capabilities=["analysis_ranking", "frustration_landscape", "residue_mapping"],
        required_fields=["analysis_id", "formula_version", "source_ensemble_sha256"],
        required_artifacts=["analysis_manifest"],
        notes="Server-computed hierarchical support and ranking components.",
    ),
    ResultContractDefinition(
        contract_id="conformational_mapping_resampling_v1",
        model_ids=["conformational_mapping"],
        result_sets=["cm_resampling"],
        supported_analyzers=list(_CM_ANALYZERS),
        viewer_capabilities=list(_CM_VIEWER_CAPABILITIES),
        required_fields=["pair_id", "feature_policy", "manifest_sha256"],
        required_artifacts=["wt_ensemble", "mutant_ensemble", "resampling_manifest"],
        notes="Matched complete-complex WT/mutant Protenix resampling.",
    ),
    ResultContractDefinition(
        contract_id="confornets_monomer_v1",
        model_ids=["confornets_experimental", "confornets"],
        stage_families=["confornets"],
        artifact_classes=["monomer_conformation"],
        result_sets=[],
        supported_analyzers=list(_STRUCTURE_ANALYZERS),
        viewer_capabilities=["structure_viewer", "generic_metadata"],
        required_fields=["artifact_class"],
        required_artifacts=["structure"],
        notes="Conformational mapping monomer outputs with normalized conformer artifacts.",
    ),
]


def get_result_contract_definitions() -> List[ResultContractDefinition]:
    return list(_RESULT_CONTRACT_DEFINITIONS)


def _token(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _tokens(values: List[str]) -> set[str]:
    return {_token(value) or "" for value in values}


def normalize_conformational_mapping_artifact_class(value: Any) -> Optional[str]:
    """Normalize exactly one historical spelling without changing stored rows."""

    if isinstance(value, str) and value in {"monomer_conformation", "conformer"}:
        return "monomer_conformation"
    return None


def _definition_by_id(contract_id: Any) -> Optional[ResultContractDefinition]:
    wanted = _token(contract_id)
    if not wanted:
        return None
    return next((definition for definition in _RESULT_CONTRACT_DEFINITIONS if definition.contract_id == wanted), None)


def _contract_from_definition(definition: ResultContractDefinition, *, source: str = "registry") -> ResultContract:
    return ResultContract(
        analysis_contract_id=definition.contract_id,
        supported_analyzers=list(definition.supported_analyzers),
        viewer_capabilities=list(definition.viewer_capabilities),
        required_fields=list(definition.required_fields),
        required_artifacts=list(definition.required_artifacts),
        schema_version=definition.schema_version,
        contract_source=source,
    )


def _unique_definition_for(selector: Optional[str], field_name: str) -> Optional[ResultContractDefinition]:
    if not selector:
        return None
    matches = [
        definition
        for definition in _RESULT_CONTRACT_DEFINITIONS
        if selector in _tokens(getattr(definition, field_name))
    ]
    return matches[0] if len(matches) == 1 else None


def resolve_result_contract(
    *,
    review_profile_id: Any = None,
    result_set: Optional[str] = None,
    model_type: Any = None,
    stage_family: Any = None,
    stage_mode: Any = None,
    artifact_class: Any = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> ResultContract:
    """Resolve one authoritative review profile and otherwise fail closed.

    A persisted producer/ingester profile wins. Legacy selectors are used only
    when no persisted profile exists. A non-empty unknown result set is treated
    as an explicit unsupported identity and never falls through to broad hints.
    """
    persisted_profile = _token(review_profile_id)
    if persisted_profile:
        definition = _definition_by_id(persisted_profile)
        return _contract_from_definition(definition, source="persisted") if definition else ResultContract(
            contract_source="unsupported_persisted"
        )

    normalized_result_set = _token(result_set)
    if normalized_result_set:
        definition = next(
            (item for item in _RESULT_CONTRACT_DEFINITIONS if normalized_result_set in _tokens(item.result_sets)),
            None,
        )
        return _contract_from_definition(definition, source="legacy_result_set") if definition else ResultContract()

    family = _token(stage_family)
    model_id = _token(model_type) or _token((provenance or {}).get("model_id"))
    family_matches = [
        definition
        for definition in _RESULT_CONTRACT_DEFINITIONS
        if (family and family in _tokens(definition.stage_families))
        or (model_id and model_id in _tokens(definition.model_ids))
    ]
    if len(family_matches) == 1:
        source = "registry" if model_id and model_id in _tokens(family_matches[0].model_ids) else "legacy_identity"
        return _contract_from_definition(family_matches[0], source=source)
    if len(family_matches) > 1:
        return ResultContract()

    artifact = (
        normalize_antibody_artifact_class(artifact_class)
        or normalize_conformational_mapping_artifact_class(artifact_class)
        or _token(artifact_class)
    )
    artifact_definition = _unique_definition_for(artifact, "artifact_classes")
    if artifact_definition:
        return _contract_from_definition(artifact_definition, source="legacy_artifact")

    mode_definition = _unique_definition_for(_token(stage_mode), "stage_modes")
    if mode_definition:
        return _contract_from_definition(mode_definition, source="legacy_mode")
    return ResultContract()


def _role_values(role_map: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for key, value in role_map.items():
        values.append(str(key).strip().lower())
        if isinstance(value, dict):
            values.extend(_role_values(value))
        elif isinstance(value, (list, tuple, set)):
            values.extend(str(item).strip().lower() for item in value)
        elif value is not None:
            values.append(str(value).strip().lower())
    return values


def build_review_artifact_manifest(design: Any) -> Dict[str, Any]:
    """Build the typed, fail-closed artifact/role envelope consumed by review UI."""
    role_map = getattr(design, "review_role_map", None)
    if not isinstance(role_map, dict):
        role_map = {}
    role_tokens = set(_role_values(role_map))
    profile_id = _token(getattr(design, "review_profile_id", None))
    has_binder = bool(
        role_tokens & {"binder", "antibody", "antibody_heavy", "antibody_light", "heavy", "light"}
        or profile_id in {"antibody_backbone_v1", "ppiflow_maturation_v1"}
    )

    def artifact(path: Any, *, kind: str, metadata_ready: bool = True) -> Dict[str, Any]:
        text = str(path or "").strip()
        resolved: Path | None = None
        if text:
            candidate = Path(text).expanduser()
            try:
                resolved = resolve_runtime_data_path(candidate) if candidate.is_absolute() else candidate.resolve()
            except Exception:
                resolved = None
        ready = bool(resolved and resolved.is_file() and metadata_ready)
        if ready:
            reason = None
        elif not text:
            reason = "not declared by producer/ingester"
        elif not metadata_ready:
            reason = "required artifact metadata is missing"
        else:
            reason = "declared artifact path does not resolve to a file"
        return {
            "kind": kind,
            "state": "ready" if ready else "missing",
            "path": text or None,
            "reason": reason,
        }

    if profile_id == "shape_blueprint":
        supplied = getattr(design, "review_artifact_manifest", None)
        supplied = supplied if isinstance(supplied, dict) else {}
        structure_raw = supplied.get("structure")
        metrics_raw = supplied.get("metrics")
        structure_descriptor: Dict[str, Any] = structure_raw if isinstance(structure_raw, dict) else {}
        metrics_descriptor: Dict[str, Any] = metrics_raw if isinstance(metrics_raw, dict) else {}
        structure_artifact = artifact(getattr(design, "pdb_path", None), kind="structure")
        metrics_artifact = artifact(getattr(design, "json_path", None), kind="shape_metrics")
        for target, source in ((structure_artifact, structure_descriptor), (metrics_artifact, metrics_descriptor)):
            for key in ("sha256", "bytes", "format", "relative_path"):
                if key in source:
                    target[key] = source[key]
        return {
            "schema": REVIEW_ARTIFACT_SCHEMA,
            "artifacts": {"structure": structure_artifact, "metrics": metrics_artifact},
            "roles": {**role_map, "has_binder": False},
        }

    return {
        "schema": REVIEW_ARTIFACT_SCHEMA,
        "artifacts": {
            "structure": artifact(getattr(design, "pdb_path", None), kind="structure"),
            "aligned_error": artifact(
                getattr(design, "aligned_error_path", None),
                kind="aligned_error",
                metadata_ready=bool(_token(getattr(design, "aligned_error_format", None))),
            ),
        },
        "roles": {**role_map, "has_binder": has_binder},
    }


def result_contract_for_design(design: Any) -> ResultContract:
    """Resolve live applicability from the persisted server-owned profile only."""
    return resolve_result_contract(
        review_profile_id=getattr(design, "review_profile_id", None),
    )


def apply_review_contract_to_design(design: Any) -> None:
    """Finalize the durable review identity and artifact envelope at ingestion.

    Trusted review identity is derived from persisted server-side lineage. Artifact
    readiness is always rebuilt from currently resolvable runtime files.
    """
    # Modern review authority must already be present on server-owned Design
    # fields. Legacy selector inference belongs exclusively to deterministic
    # migration/backfill; it must not turn arbitrary import or response metadata
    # into newly persisted producer authority.
    persisted_profile = getattr(design, "review_profile_id", None)
    contract = resolve_result_contract(review_profile_id=persisted_profile)
    if contract.analysis_contract_id:
        design.review_profile_id = contract.analysis_contract_id
        design.review_contract_version = contract.schema_version
        if not getattr(design, "review_contract_source", None):
            design.review_contract_source = "job_identity"
    else:
        design.review_profile_id = "unsupported_legacy"
        design.review_contract_version = getattr(design, "review_contract_version", None) or REVIEW_CONTRACT_VERSION
        design.review_contract_source = "unsupported_legacy"

    design.review_artifact_manifest = build_review_artifact_manifest(design)


_ANALYSIS_REQUIRED_ARTIFACTS: Dict[str, List[str]] = {
    "structure_summary": ["structure"],
    "contact_map": ["structure"],
    "chain_metrics": ["structure"],
    "fampnn_psce_profile": ["structure"],
    "antibody_annotation_pack": ["structure"],
    "pae_matrix": ["aligned_error"],
    "ipsae_interface": ["structure", "aligned_error"],
}


def validate_design_analysis_request(design: Any, analysis_type: str) -> Optional[str]:
    contract = result_contract_for_design(design)
    normalized_analysis = _token(analysis_type)
    if normalized_analysis not in {_token(item) for item in contract.supported_analyzers}:
        profile = contract.analysis_contract_id or "unsupported"
        return f"analysis is not allowed by review profile '{profile}'"

    manifest = build_review_artifact_manifest(design)
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    for artifact_name in _ANALYSIS_REQUIRED_ARTIFACTS.get(normalized_analysis or "", []):
        descriptor = artifacts.get(artifact_name) if isinstance(artifacts, dict) else None
        if not isinstance(descriptor, dict) or descriptor.get("state") != "ready":
            return f"required artifact '{artifact_name}' is not ready"
    return None


def is_design_analysis_allowed(design: Any, analysis_type: str) -> bool:
    contract = result_contract_for_design(design)
    return _token(analysis_type) in {_token(item) for item in contract.supported_analyzers}
