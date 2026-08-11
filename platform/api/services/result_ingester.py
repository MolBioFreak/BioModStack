"""
Result Ingester Service - Parse pipeline outputs into database.

Reads all_designs.csv and success_metrics.json from completed jobs
and populates the Design table in SQLite.
"""

import csv
import copy
import hashlib
import json
import math
import os
import re
import stat
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from antibody_pipeline_contract import (
    ANTIBODY_PIPELINE_CONTRACT_VERSION,
    infer_antibody_artifact_class_from_stage,
    is_antibody_pipeline_mode,
    normalize_antibody_artifact_class,
    normalize_antibody_pipeline_contract_version,
)
from database import (
    Design,
    FrustraMPNNResult,
    Job,
    ShapeDesignGeometry,
    ShapeDesignRequest,
    RFD3LocalRedesignRequest,
    RFD3LocalRedesignCandidate,
    RFD3LocalRedesignArtifact,
)
from paths import get_data_root, resolve_runtime_data_path
from services.rfantibody_metadata import load_rfantibody_trb_summary
from services.cdr_annotator import extract_sequence_from_pdb, identify_binder_chains
from .aligned_error_utils import detect_aligned_error_artifact, load_aligned_error_artifact
from .ipsae import compute_ipsae_interface
from .result_contracts import REVIEW_ARTIFACT_SCHEMA, REVIEW_CONTRACT_VERSION, resolve_result_contract
from .conformational_mapping.persistence import (
    ConformationalPersistenceError,
    get_request as get_cm_request,
    ingest_result_bundle as ingest_cm_result_bundle,
)
from .frustrampnn.contracts import canonical_json_bytes, canonical_json_loads
from .frustrampnn.identity import deterministic_candidate_id
from .frustrampnn.manifests import MANIFEST_PATH, V2_MANIFEST_PATH
from .frustrampnn.structure import StructureNormalizationError, read_structure_bytes
from .frustrampnn.persistence import (
    FrustraMPNNPersistenceError,
    ingest_result_bundle as ingest_frustrampnn_result_bundle,
    load_and_validate_result_bundle as validate_frustrampnn_result_bundle,
)
from .structure_utils import calculate_epitope_contacts, compute_contact_geometry_metrics, compute_gyration_radius, get_per_chain_fampnn_psce



def _normalize_boltzgen_design_name(design_name: str) -> str:
    """Normalize upstream ``rankN_name`` artifacts to the UI/API ``name_N`` contract."""
    import re

    normalized = str(design_name or "").strip()
    ranked = re.fullmatch(r"rank(\d+)_(.+)", normalized, flags=re.IGNORECASE)
    if not ranked:
        return normalized
    rank, base_name = ranked.groups()
    return f"{base_name}_{int(rank)}"


def _boltzgen_filtered_output_dir(output_path: Path) -> Optional[Path]:
    filtered = output_path / "collected" / "boltzgen_filtered"
    return filtered if filtered.is_dir() else None


def parse_backbone_id(design_name: str) -> Optional[int]:
    """
    Extract backbone ID from design name.
    
    Formats:
    - antibody_job_gpu0_99 -> 99
    - antibody_job_2_seq_15_model_0 -> 2
    - boltzgen_input_5 -> 5
    - rfd_design_3 -> 3
    """
    import re
    
    normalized = str(design_name or "").strip()
    while re.match(r'^\d+_', normalized):
        normalized = normalized.split('_', 1)[1]

    patterns = (
        r"(?:^|[_-])antibody[_-]?job(?:[_-]?gpu\d+)?[_-]?(\d+)(?=[_-]|$)",
        r"(?:^|[_-])rfantibody[_-]?child[_-]?(\d+)(?=[_-]|$)",
        r"(?:^|[_-])child[_-]?(\d+)(?=[_-]|$)",
        r"(?:^|[_-])(?:job|input|design)[_-]?(\d+)(?=[_-]|$)",
    )
    for pattern in patterns:
        matches = re.findall(pattern, normalized)
        if matches:
            return int(matches[-1])
    return None


def _parse_job_params(raw_params: Any) -> Dict[str, Any]:
    if isinstance(raw_params, dict):
        return raw_params
    if isinstance(raw_params, str) and raw_params:
        try:
            parsed = json.loads(raw_params)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _job_has_explicit_binder_target_roles(job: Optional[Job]) -> bool:
    if not job:
        return False
    params = _parse_job_params(job.params)
    model_id = str(job.model_id or "").strip().lower()
    mode = str(job.mode or "").strip().lower()
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()
    boltzgen_mode = str(params.get("boltzgen_mode") or mode or "").strip().lower()

    if is_antibody_pipeline_mode(rfd_mode):
        return True
    if "antibody" in model_id or "antibody" in mode:
        return True
    if model_id == "boltzgen" and boltzgen_mode in {"nanobody_binder", "antibody_binder"}:
        return True
    if params.get("antibody_chains"):
        return True
    if params.get("binder_chains") or params.get("target_chains"):
        return True
    complex_components = params.get("complex_components")
    if isinstance(complex_components, list):
        protein_like = [
            comp
            for comp in complex_components
            if isinstance(comp, dict) and str(comp.get("type") or "").strip().lower() in {"protein", "peptide"}
        ]
        if len(protein_like) >= 2:
            return True
    return False


def _infer_antibody_type_from_job_params(job_params: Dict[str, Any]) -> Optional[str]:
    framework_type = str(job_params.get("framework_type") or "").strip().lower()
    boltzgen_mode = str(job_params.get("boltzgen_mode") or job_params.get("mode") or "").strip().lower()

    if framework_type in {"nanobody", "vhh"} or boltzgen_mode == "nanobody_binder":
        return "vhh"
    if framework_type in {"fab", "scfv", "antibody"}:
        return framework_type
    if boltzgen_mode == "antibody_binder":
        return "fab"
    return None


def _job_has_reference_target_structure(job_params: Dict[str, Any]) -> bool:
    target_pdb_value = str(
        job_params.get("target_pdb")
        or job_params.get("fixed_target_source_path")
        or ""
    ).strip()
    return bool(target_pdb_value)


def _job_supports_inferred_validation_roles(job: Optional[Job], job_params: Dict[str, Any]) -> bool:
    return _job_has_explicit_binder_target_roles(job) or _job_has_reference_target_structure(job_params)


def _parse_epitope_residues(raw_value: Any) -> Optional[List[str]]:
    if not raw_value:
        return None
    if isinstance(raw_value, list):
        cleaned = [str(item).strip() for item in raw_value if str(item).strip()]
        return cleaned or None
    if isinstance(raw_value, str):
        cleaned = [item.strip() for item in raw_value.split(",") if item.strip()]
        return cleaned or None
    return None


def _parse_chain_ids(raw_value: Any) -> List[str]:
    import re

    if not raw_value:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        values = [str(item).strip() for item in raw_value if str(item).strip()]
    else:
        values = [token.strip() for token in re.split(r"[,;|\s]+", str(raw_value)) if token.strip()]

    ordered: List[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _infer_complex_role_chain_ids(job_params: Dict[str, Any]) -> tuple[List[str], List[str]]:
    components = job_params.get("complex_components")
    if not isinstance(components, list):
        return [], []

    protein_like_ids: List[str] = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        comp_type = str(comp.get("type") or "").strip().lower()
        if comp_type not in {"protein", "peptide"}:
            continue
        chain_id = str(comp.get("id") or "").strip()
        if chain_id:
            protein_like_ids.append(chain_id)

    if len(protein_like_ids) < 2:
        return [], []
    return [protein_like_ids[0]], protein_like_ids[1:]


def _validation_role_fields(job: Optional[Job], job_params: Dict[str, Any]) -> Dict[str, Optional[str]]:
    binder_chains = _parse_chain_ids(job_params.get("antibody_chains") or job_params.get("binder_chains"))
    target_chains = _parse_chain_ids(job_params.get("antigen_chains") or job_params.get("target_chains"))
    inferred_target_chains, inferred_binder_chains = _infer_complex_role_chain_ids(job_params)
    if not target_chains:
        target_chains = inferred_target_chains
    if not binder_chains:
        binder_chains = inferred_binder_chains
    return {
        "detected_antibody_chains": ",".join(binder_chains) or None,
        "detected_target_chain": ",".join(target_chains) or None,
    }


def _sequence_match_score(seq_a: str, seq_b: str) -> float:
    left = str(seq_a or "").strip().upper()
    right = str(seq_b or "").strip().upper()
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if shorter and shorter in longer:
        return len(shorter) / len(longer)
    overlap = sum(1 for aa, bb in zip(left, right) if aa == bb)
    return overlap / max(len(left), len(right))


def _resolve_validation_structure_role_fields(
    *,
    structure_path: Path,
    job_params: Dict[str, Any],
    detected_antibody_chains: Optional[str],
    detected_target_chain: Optional[str],
) -> Dict[str, Optional[str]]:
    resolved = {
        "detected_antibody_chains": detected_antibody_chains,
        "detected_target_chain": detected_target_chain,
    }

    target_pdb_value = str(
        job_params.get("target_pdb")
        or job_params.get("fixed_target_source_path")
        or ""
    ).strip()
    if not target_pdb_value:
        return resolved

    try:
        structure_sequences = extract_sequence_from_pdb(str(structure_path)) or {}
    except Exception:
        return resolved
    if len(structure_sequences) < 2:
        return resolved

    target_pdb = Path(target_pdb_value).expanduser()
    if not target_pdb.is_absolute():
        target_pdb = (get_data_root() / target_pdb).resolve()
    if not target_pdb.exists():
        return resolved

    try:
        target_sequences = extract_sequence_from_pdb(str(target_pdb)) or {}
    except Exception:
        return resolved
    # Source-PDB chain selectors and validated-output role selectors are distinct.
    # Binder workflows commonly read target chain A and publish it as output chain B.
    configured_target_chain_ids = _parse_chain_ids(
        job_params.get("target_source_chain")
        or job_params.get("target_source_chains")
        or job_params.get("antigen_source_chains")
        or job_params.get("antigen_chains")
    )
    if configured_target_chain_ids:
        target_sequences = {
            chain_id: seq for chain_id, seq in target_sequences.items()
            if chain_id in configured_target_chain_ids
        }
    if not target_sequences:
        return resolved

    scored_actual_chains: List[tuple[float, str]] = []
    for actual_chain_id, actual_sequence in structure_sequences.items():
        best_score = max(
            (_sequence_match_score(actual_sequence, target_sequence) for target_sequence in target_sequences.values()),
            default=0.0,
        )
        scored_actual_chains.append((best_score, actual_chain_id))
    scored_actual_chains.sort(reverse=True)

    matched_target_chains = [chain_id for score, chain_id in scored_actual_chains if score >= 0.85]
    if not matched_target_chains and scored_actual_chains and scored_actual_chains[0][0] > 0.0:
        matched_target_chains = [scored_actual_chains[0][1]]
    if not matched_target_chains:
        return resolved

    matched_target_set = set(matched_target_chains)
    matched_binder_chains = [
        chain_id for chain_id in structure_sequences.keys()
        if chain_id not in matched_target_set
    ]
    if not matched_binder_chains:
        return resolved

    return {
        "detected_antibody_chains": ",".join(matched_binder_chains),
        "detected_target_chain": ",".join(matched_target_chains),
    }


_GEOMETRY_METRIC_FIELDS = (
    "epitope_contact_count",
    "epitope_min_distance",
    "epitope_min_atom_distance",
    "epitope_nearest_antibody_residue",
    "epitope_nearest_target_residue",
    "epitope_nearest_antibody_atom",
    "epitope_nearest_target_atom",
    "epitope_mapping_mode",
    "epitope_centroid_distance",
    "target_contact_count",
    "target_min_distance",
    "target_min_atom_distance",
    "target_nearest_antibody_residue",
    "target_nearest_target_residue",
    "target_nearest_antibody_atom",
    "target_nearest_target_atom",
    "target_centroid_distance",
    "detected_antibody_chains",
    "detected_target_chain",
    "antibody_residue_count",
    "target_residue_count",
    "epitope_residue_count",
)


def _geometry_design_fields(metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        field_name: metrics.get(field_name)
        for field_name in _GEOMETRY_METRIC_FIELDS
        if field_name in metrics
    }


def _compute_validation_geometry_fields(
    *,
    structure_path: Path,
    job_params: Dict[str, Any],
    detected_antibody_chains: Optional[str],
    detected_target_chain: Optional[str],
    epitope_residues: Optional[List[str]],
) -> Dict[str, Any]:
    if not detected_antibody_chains or not detected_target_chain:
        return {}

    try:
        metrics = compute_contact_geometry_metrics(
            pdb_path=structure_path,
            epitope_residues=epitope_residues or [],
            antibody_chain=detected_antibody_chains,
            target_chain=detected_target_chain,
            reference_target_pdb=job_params.get("target_pdb") or job_params.get("fixed_target_source_path"),
        )
    except Exception as exc:
        print(f"[Ingester] Failed validation geometry scoring for {structure_path}: {exc}")
        return {}

    if not epitope_residues:
        for field_name in tuple(metrics.keys()):
            if field_name.startswith("epitope_"):
                metrics.pop(field_name, None)
    return metrics


def _strict_aligned_error_fields(
    *,
    structure_path: Path,
    summary_json_path: Optional[Path],
    detected_antibody_chains: Optional[str],
    detected_target_chain: Optional[str],
) -> Dict[str, Any]:
    detected = detect_aligned_error_artifact(
        structure_path=structure_path,
        summary_json_path=summary_json_path,
    )
    if not detected:
        return {}

    artifact_path, artifact_format, artifact_key = detected
    fields: Dict[str, Any] = {
        "aligned_error_path": str(artifact_path),
        "aligned_error_format": artifact_format,
        "aligned_error_key": artifact_key,
    }
    try:
        artifact = load_aligned_error_artifact(
            aligned_error_path=artifact_path,
            aligned_error_format=artifact_format,
            matrix_key=artifact_key,
            structure_path=structure_path,
        )
        ipsae_result = compute_ipsae_interface(
            artifact,
            binder_chains=_parse_chain_ids(detected_antibody_chains),
            target_chains=_parse_chain_ids(detected_target_chain),
        )
        fields.update(
            {
                "ipsae": safe_float(ipsae_result.get("ipsae")),
                "ipsae_binder_to_target": safe_float(ipsae_result.get("ipsae_binder_to_target")),
                "ipsae_target_to_binder": safe_float(ipsae_result.get("ipsae_target_to_binder")),
                "ipsae_d0chn": safe_float(ipsae_result.get("ipsae_d0chn")),
                "ipsae_d0dom": safe_float(ipsae_result.get("ipsae_d0dom")),
                "ipsae_chain_pair": str(ipsae_result.get("ipsae_chain_pair")).strip() if ipsae_result.get("ipsae_chain_pair") else None,
                "ipsae_pae_cutoff": safe_float(ipsae_result.get("pae_cutoff")),
                "ipsae_dist_cutoff": safe_float(ipsae_result.get("dist_cutoff")),
            }
        )
    except Exception as exc:
        print(f"[Ingester] Failed strict aligned-error processing for {structure_path}: {exc}")
    return fields


def _load_json_payload(json_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if not json_path or not json_path.exists():
        return None
    try:
        payload = json.loads(json_path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _trusted_producer_review_fields(job: Optional[Job], payload: Any) -> Dict[str, Any]:
    """Accept canonical producer intent only within server-owned job boundaries.

    Request parameters and arbitrary provenance never enter this path. The
    producer declaration must use the current schema and a profile allowed for
    the persisted job model. Artifact readiness is deliberately not copied;
    it is rebuilt from the material Design paths during contract finalization.
    """
    if job is None or not isinstance(payload, dict):
        return {}
    profile_id = str(payload.get("review_profile_id") or "").strip().lower()
    source = str(payload.get("review_contract_source") or "").strip().lower()
    role_map = payload.get("review_role_map")
    manifest = payload.get("review_artifact_manifest")
    raw_version = payload.get("review_contract_version")
    if raw_version is None:
        return {}
    try:
        version = int(str(raw_version))
    except (TypeError, ValueError):
        return {}
    if (
        not profile_id
        or source != "producer"
        or version != REVIEW_CONTRACT_VERSION
        or not isinstance(role_map, dict)
        or not isinstance(manifest, dict)
        or manifest.get("schema") != REVIEW_ARTIFACT_SCHEMA
    ):
        return {}

    result_role = role_map.get("result_role")
    if not isinstance(result_role, str) or not result_role.strip():
        return {}
    for role_key, role_value in role_map.items():
        if not isinstance(role_key, str) or not role_key.strip():
            return {}
        if role_key.endswith("_chains") and (
            not isinstance(role_value, list)
            or not all(isinstance(chain, str) and chain.strip() for chain in role_value)
        ):
            return {}

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return {}
    contract = resolve_result_contract(review_profile_id=profile_id)
    for artifact_name in contract.required_artifacts:
        descriptor = artifacts.get(artifact_name)
        if not isinstance(descriptor, dict):
            return {}
        if descriptor.get("kind") != artifact_name:
            return {}
        if descriptor.get("state") not in {"ready", "missing"}:
            return {}
        path = descriptor.get("path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            return {}

    model_id = str(getattr(job, "model_id", None) or "").strip().lower()
    job_mode = str(getattr(job, "mode", None) or "").strip().lower()
    job_params = getattr(job, "params", None) or {}
    modification_mode = str(job_params.get("modification_mode") or "").strip().lower()
    generator_family = str(payload.get("generator_family") or "").strip().lower()
    if generator_family == "caliby":
        if model_id == "binder_design" and job_mode == "rfd3_caliby":
            allowed_profiles = {"binder_design_v1"}

        else:
            return {}
    elif generator_family == "protein_hunter":
        if model_id != "binder_design" or job_mode != "protein_hunter":
            return {}
        allowed_profiles = {"binder_design_v1"}
    elif model_id == "protein_local_redesign" or (
        model_id == "protein_modification_experimental"
        and (job_mode == "region_redesign" or modification_mode == "region_redesign")
    ):
        allowed_profiles = {"de_novo_generation_v1"}
    else:
        server_contract = resolve_result_contract(model_type=model_id)
        allowed_profiles = {server_contract.analysis_contract_id} if server_contract.analysis_contract_id else set()
    if profile_id not in allowed_profiles:
        return {}
    if profile_id == "binder_design_v1":
        raw_ipsae = payload.get("ipsae")
        if not isinstance(raw_ipsae, (int, float, str)):
            return {}
        try:
            ipsae = float(raw_ipsae)
        except (TypeError, ValueError):
            return {}
        if not math.isfinite(ipsae) or not 0.0 <= ipsae <= 1.0:
            return {}
        if (
            str(payload.get("artifact_class") or "").strip() != "validated_binder_complex"
            or str(payload.get("result_set") or "").strip() != "binder_candidates"
            or str(payload.get("stage_family") or "").strip() != "binder_design"
            or str(payload.get("stage_mode") or "").strip() not in {"rfd3_caliby", "protein_hunter"}
        ):
            return {}
    fields = {
        "review_profile_id": profile_id,
        "review_contract_version": REVIEW_CONTRACT_VERSION,
        "review_contract_source": "producer",
        "review_role_map": role_map,
    }
    if profile_id == "binder_design_v1":
        fields.update(
            {
                "artifact_class": "validated_binder_complex",
                "artifact_schema_version": 1,
            }
        )
    return fields


def _default_fampnn_metrics() -> Dict[str, Any]:
    return {
        "avg_psce": None,
        "max_residue_psce": None,
        "min_residue_psce": None,
        "chain_avg_psce": None,
        "sequence": None,
        "binder_sequence": None,
        "binder_length": None,
        "mpnn_score": None,
        "seq_probs_available": False,
        "mean_sampled_prob": None,
        "min_sampled_prob": None,
        "mean_sampled_log_prob": None,
        "total_sampled_log_prob": None,
        "mean_entropy": None,
        "max_entropy": None,
        "low_confidence_positions": None,
        "mutation_scoring_available": False,
        "mutation_score_source": None,
        "mutation_score_scope": None,
        "mutation_opportunity_count": None,
        "top_model_favored_mutations": None,
    }


def _compute_binder_metrics_from_structure(structure_path: Optional[Path]) -> Dict[str, Any]:
    metrics = {
        "binder_sequence": None,
        "binder_length": None,
    }
    if not structure_path or not structure_path.exists():
        return metrics

    try:
        sequences = extract_sequence_from_pdb(str(structure_path))
        if not sequences:
            return metrics
        if len(sequences) < 2:
            return metrics

        binder_chains = identify_binder_chains(sequences, str(structure_path))
        ordered_chain_ids = [
            chain_id
            for chain_id in dict.fromkeys(binder_chains.values())
            if chain_id in sequences
        ]

        if ordered_chain_ids:
            binder_sequences = [
                sequences[chain_id]
                for chain_id in ordered_chain_ids
                if sequences.get(chain_id)
            ]
            if binder_sequences:
                metrics["binder_sequence"] = "|".join(binder_sequences)
                metrics["binder_length"] = sum(len(seq) for seq in binder_sequences)
                return metrics

        heavy_chain = sequences.get("H")
        if heavy_chain:
            metrics["binder_sequence"] = heavy_chain
            metrics["binder_length"] = len(heavy_chain)
            return metrics


    except Exception as exc:
        print(f"[Ingester] Failed binder metric extraction for {structure_path}: {exc}")

    return metrics


def _compute_fampnn_metrics_from_structure(structure_path: Optional[Path]) -> Dict[str, Any]:
    metrics = _default_fampnn_metrics()
    if not structure_path or not structure_path.exists():
        return metrics

    metrics.update(_compute_binder_metrics_from_structure(structure_path))

    try:
        chain_profiles = get_per_chain_fampnn_psce(structure_path)
    except Exception as exc:
        print(f"[Ingester] Failed FA-MPNN structure-side metric extraction for {structure_path}: {exc}")
        return metrics

    residue_psces: List[float] = []
    chain_avg_psce: Dict[str, float] = {}
    for chain_id, profile in chain_profiles.items():
        chain_scores = [
            value
            for value in (profile.get("psce") if isinstance(profile, dict) else []) or []
            if isinstance(value, (int, float))
        ]
        if not chain_scores:
            continue
        residue_psces.extend(float(value) for value in chain_scores)
        chain_avg = safe_float(profile.get("avg_psce") if isinstance(profile, dict) else None)
        if chain_avg is None:
            chain_avg = sum(chain_scores) / len(chain_scores)
        chain_avg_psce[str(chain_id)] = round(float(chain_avg), 2)

    if not residue_psces:
        return metrics

    metrics["avg_psce"] = round(sum(residue_psces) / len(residue_psces), 2)
    metrics["max_residue_psce"] = round(max(residue_psces), 2)
    metrics["min_residue_psce"] = round(min(residue_psces), 2)
    metrics["chain_avg_psce"] = chain_avg_psce or None
    return metrics


def _build_fampnn_payload(fam_payload: Optional[Dict[str, Any]], fam_metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = dict(fam_payload or {})
    if fam_metrics.get("chain_avg_psce") and not isinstance(payload.get("chain_avg_psce"), dict):
        payload["chain_avg_psce"] = fam_metrics["chain_avg_psce"]
    if fam_metrics.get("avg_psce") is not None and safe_float(payload.get("fampnn_avg_psce")) is None:
        payload["fampnn_avg_psce"] = fam_metrics["avg_psce"]
    if fam_metrics.get("max_residue_psce") is not None and safe_float(payload.get("fampnn_max_residue_psce")) is None:
        payload["fampnn_max_residue_psce"] = fam_metrics["max_residue_psce"]
    if fam_metrics.get("min_residue_psce") is not None and safe_float(payload.get("fampnn_min_residue_psce")) is None:
        payload["fampnn_min_residue_psce"] = fam_metrics["min_residue_psce"]
    return payload or None


def _extract_fampnn_metrics(
    fam_payload: Optional[Dict[str, Any]],
    structure_path: Optional[Path] = None,
) -> Dict[str, Any]:
    metrics = _default_fampnn_metrics()

    chain_avg_psce: Optional[Dict[str, float]] = None
    avg_psce: Optional[float] = None
    max_residue_psce: Optional[float] = None
    min_residue_psce: Optional[float] = None
    sequence: Optional[str] = None
    binder_sequence: Optional[str] = None
    binder_length: Optional[int] = None
    mpnn_score: Optional[float] = None
    seq_probs_available = False
    mean_sampled_prob: Optional[float] = None
    min_sampled_prob: Optional[float] = None
    mean_sampled_log_prob: Optional[float] = None
    total_sampled_log_prob: Optional[float] = None
    mean_entropy: Optional[float] = None
    max_entropy: Optional[float] = None
    low_confidence_positions: Optional[List[Dict[str, Any]]] = None
    mutation_scoring_available = False
    mutation_score_source: Optional[str] = None
    mutation_score_scope: Optional[str] = None
    mutation_opportunity_count: Optional[int] = None
    top_model_favored_mutations: Optional[List[Dict[str, Any]]] = None

    if isinstance(fam_payload, dict):
        chain_avg_raw = fam_payload.get("chain_avg_psce")
        if isinstance(chain_avg_raw, dict):
            normalized_chain_avg: Dict[str, float] = {}
            for chain_id, raw_value in chain_avg_raw.items():
                numeric = safe_float(raw_value)
                if numeric is None:
                    continue
                normalized_chain_avg[str(chain_id)] = numeric
            chain_avg_psce = normalized_chain_avg or None

        avg_psce = safe_float(fam_payload.get("fampnn_avg_psce"))
        if avg_psce is None and chain_avg_psce:
            avg_psce = sum(chain_avg_psce.values()) / len(chain_avg_psce)

        max_residue_psce = safe_float(fam_payload.get("fampnn_max_residue_psce"))
        min_residue_psce = safe_float(fam_payload.get("fampnn_min_residue_psce"))
        mpnn_score = safe_float(fam_payload.get("mpnn_score") or fam_payload.get("seq_mpnn_score"))
        seq_probs_available = bool(fam_payload.get("fampnn_seq_probs_available"))
        mean_sampled_prob = safe_float(fam_payload.get("fampnn_mean_sampled_prob"))
        min_sampled_prob = safe_float(fam_payload.get("fampnn_min_sampled_prob"))
        mean_sampled_log_prob = safe_float(fam_payload.get("fampnn_mean_sampled_log_prob"))
        total_sampled_log_prob = safe_float(fam_payload.get("fampnn_total_sampled_log_prob"))
        mean_entropy = safe_float(fam_payload.get("fampnn_mean_entropy"))
        max_entropy = safe_float(fam_payload.get("fampnn_max_entropy"))
        low_confidence_raw = fam_payload.get("fampnn_low_confidence_positions")
        if isinstance(low_confidence_raw, list):
            low_confidence_positions = [item for item in low_confidence_raw if isinstance(item, dict)] or None

        mutation_scoring_available = bool(fam_payload.get("fampnn_mutation_scoring_available"))
        mutation_source_raw = fam_payload.get("fampnn_mutation_score_source")
        if isinstance(mutation_source_raw, str) and mutation_source_raw.strip():
            mutation_score_source = mutation_source_raw.strip()
        mutation_scope_raw = fam_payload.get("fampnn_mutation_score_scope")
        if isinstance(mutation_scope_raw, str) and mutation_scope_raw.strip():
            mutation_score_scope = mutation_scope_raw.strip()
        try:
            raw_count = fam_payload.get("fampnn_mutation_opportunity_count")
            mutation_opportunity_count = int(raw_count) if raw_count is not None else None
        except (TypeError, ValueError):
            mutation_opportunity_count = None
        top_mutations_raw = fam_payload.get("fampnn_top_model_favored_mutations")
        if isinstance(top_mutations_raw, list):
            top_model_favored_mutations = [item for item in top_mutations_raw if isinstance(item, dict)] or None

        sequence_text = fam_payload.get("sequence")
        sequence = str(sequence_text).strip() if isinstance(sequence_text, str) and sequence_text.strip() else None
        if sequence:
            first_chain = sequence.split("|", 1)[0].strip()
            if ":" in first_chain:
                _, chain_sequence = first_chain.split(":", 1)
                first_chain = chain_sequence.strip()
            if first_chain:
                binder_sequence = first_chain
                binder_length = len(first_chain)

    structure_metrics = _compute_fampnn_metrics_from_structure(structure_path)
    if avg_psce is None:
        avg_psce = structure_metrics["avg_psce"]
    if max_residue_psce is None:
        max_residue_psce = structure_metrics["max_residue_psce"]
    if min_residue_psce is None:
        min_residue_psce = structure_metrics["min_residue_psce"]
    if chain_avg_psce is None:
        chain_avg_psce = structure_metrics["chain_avg_psce"]
    if binder_sequence is None:
        binder_sequence = structure_metrics["binder_sequence"]
    if binder_length is None:
        binder_length = structure_metrics["binder_length"]

    metrics.update({
        "avg_psce": avg_psce,
        "max_residue_psce": max_residue_psce,
        "min_residue_psce": min_residue_psce,
        "chain_avg_psce": chain_avg_psce,
        "sequence": sequence,
        "binder_sequence": binder_sequence,
        "binder_length": binder_length,
        "mpnn_score": mpnn_score,
        "seq_probs_available": seq_probs_available,
        "mean_sampled_prob": mean_sampled_prob,
        "min_sampled_prob": min_sampled_prob,
        "mean_sampled_log_prob": mean_sampled_log_prob,
        "total_sampled_log_prob": total_sampled_log_prob,
        "mean_entropy": mean_entropy,
        "max_entropy": max_entropy,
        "low_confidence_positions": low_confidence_positions,
        "mutation_scoring_available": mutation_scoring_available,
        "mutation_score_source": mutation_score_source,
        "mutation_score_scope": mutation_score_scope,
        "mutation_opportunity_count": mutation_opportunity_count,
        "top_model_favored_mutations": top_model_favored_mutations,
    })
    return metrics


def _ordered_unique(values: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _candidate_source_design_names(design_name: str) -> List[str]:
    stem = Path(str(design_name or "")).stem.strip()
    if not stem:
        return []

    candidates = [stem]

    if "_ppiflow_" in stem:
        upstream = stem.split("_ppiflow_", 1)[0].strip()
        if upstream:
            candidates.append(upstream)

    if "_seq_" in stem:
        prefix, suffix = stem.rsplit("_seq_", 1)
        candidates.append(prefix)
        if suffix.isdigit():
            candidates.append(f"{prefix}_seq_{suffix}")

    return _ordered_unique(candidates)


_UUID_TOKEN_RE = re.compile(
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}"
)


def _candidate_source_design_ids(design_name: str) -> List[str]:
    stem = Path(str(design_name or "")).stem.strip()
    if not stem:
        return []

    candidates: List[str] = []
    search_values = [stem]
    if "_seq_" in stem:
        prefix, _suffix = stem.rsplit("_seq_", 1)
        search_values.append(prefix)

    for value in search_values:
        stripped = re.sub(r"^job\d+_", "", value)
        for token in stripped.split("_"):
            token = token.strip()
            if token and _UUID_TOKEN_RE.fullmatch(token):
                candidates.append(token)
        for match in _UUID_TOKEN_RE.findall(stripped):
            candidates.append(match)

    return _ordered_unique(candidates)


_SOURCE_LINEAGE_LOAD_ONLY_COLUMNS = (
    Design.id,
    Design.job_id,
    Design.name,
    Design.pdb_path,
    Design.origin_design_id,
    Design.origin_backbone_design_id,
    Design.stage_family,
    Design.stage_mode,
    Design.source_stage_job_id,
    Design.source_stage_family,
    Design.source_stage_mode,
    Design.artifact_class,
    Design.artifact_schema_version,
    Design.source_pdb_path,
    Design.source_design_name,
    Design.binder_length,
    Design.antibody_type,
    Design.humanness_score,
    Design.cdr_h1_length,
    Design.cdr_h2_length,
    Design.cdr_h3_length,
    Design.cdr_l1_length,
    Design.cdr_l2_length,
    Design.cdr_l3_length,
    Design.fr2_contacts,
    Design.de_loop,
    Design.fr3_contacts,
    Design.fr4_contacts,
    Design.rog,
    Design.rfd_rog,
    Design.epitope_contact_count,
    Design.epitope_min_distance,
    Design.epitope_min_atom_distance,
    Design.epitope_nearest_antibody_residue,
    Design.epitope_nearest_target_residue,
    Design.epitope_nearest_antibody_atom,
    Design.epitope_nearest_target_atom,
    Design.epitope_mapping_mode,
    Design.epitope_centroid_distance,
    Design.target_contact_count,
    Design.target_min_distance,
    Design.target_min_atom_distance,
    Design.target_nearest_antibody_residue,
    Design.target_nearest_target_residue,
    Design.target_nearest_antibody_atom,
    Design.target_nearest_target_atom,
    Design.target_centroid_distance,
    Design.detected_antibody_chains,
    Design.detected_target_chain,
    Design.antibody_residue_count,
    Design.target_residue_count,
    Design.epitope_residue_count,
    Design.passed_screen,
    Design.rfa_hotspot_covered_count,
)


def _parse_source_pdb_paths(raw_value: Any) -> List[Path]:
    if raw_value in (None, "", [], (), {}):
        return []
    if isinstance(raw_value, (list, tuple, set)):
        values = list(raw_value)
    else:
        values = [part.strip() for part in str(raw_value).split(",") if part.strip()]
    paths: List[Path] = []
    for value in values:
        try:
            paths.append(Path(str(value)).expanduser())
        except Exception:
            continue
    return paths


def _build_source_design_index(params: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for pdb_path in _parse_source_pdb_paths(params.get("pdb_paths")):
        payload = {
            "source_pdb_path": str(pdb_path),
            "source_design_name": pdb_path.stem,
        }
        for key in _candidate_source_design_names(pdb_path.stem):
            index.setdefault(key, payload)
    return index


def _extract_stage_settings(params: Dict[str, Any], stage_family: Optional[str], stage_mode: Optional[str]) -> Optional[Dict[str, Any]]:
    family = str(stage_family or "").strip().lower()
    if family == "maturation":
        family = "ppiflow"

    key_groups = {
        "caliby": (
            "caliby_model_name",
            "caliby_packer_model_name",
            "caliby_num_seqs_per_pdb",
            "caliby_batch_size",
            "caliby_num_workers",
            "caliby_clean_num_workers",
            "caliby_temperature",
            "caliby_omit_aas",
            "caliby_run_self_consistency_eval",
            "caliby_self_consistency_num_models",
            "caliby_self_consistency_num_recycles",
            "caliby_self_consistency_use_multimer",
            "caliby_fixed_pos_override_seq",
            "caliby_pos_restrict_aatype",
            "caliby_symmetry_pos",
            "enable_caliby_filter",
            "caliby_max_potts_energy",
            "caliby_min_sc_plddt",
            "caliby_max_sc_rmsd",
            "caliby_sampling_overrides_json",
            "seqs_per_design",
            "antibody_design_mode",
            "antibody_design_loops",
        ),
        "fampnn": (
            "fampnn_checkpoint",
            "fampnn_checkpoint_path",
            "fampnn_constraint_mode",
            "fampnn_temperature",
            "fampnn_num_steps",
            "fampnn_psce_threshold",
            "fampnn_exclude_cys",
            "fampnn_repack_last",
            "fampnn_seq_only",
            "fampnn_extra_config",
            "seqs_per_design",
        ),
        "ppiflow": (
            "ppiflow_mode",
            "ppiflow_stage_mode",
            "ppiflow_tuning_profile",
            "ppiflow_start_t",
            "ppiflow_samples_per_target",
            "ppiflow_retry_limit",
            "ppiflow_config",
            "ppiflow_checkpoint",
            "ppiflow_checkpoint_path",
            "ppiflow_weights_dir",
            "ppiflow_antigen_chain",
            "ppiflow_heavy_chain",
            "ppiflow_light_chain",
            "ppiflow_selected_loops",
            "ppiflow_objective_mode",
            "ppiflow_objective_threshold",
            "maturation_design_mode",
            "maturation_redesign_enabled",
            "maturation_redesign_temp",
            "maturation_redesign_steps",
            "maturation_redesign_top_n",
            "maturation_anchor_threshold",
            "maturation_anchor_distance_cutoff",
            "maturation_min_improvement",
            "maturation_filter_percentile",
        ),
    }

    settings: Dict[str, Any] = {}
    for key in key_groups.get(family, ()):
        value = params.get(key)
        if value in (None, "", [], {}, ()):
            continue
        settings[key] = value

    if stage_mode and "stage_mode" not in settings:
        settings["stage_mode"] = stage_mode
    return settings or None


def _find_first_existing_path(candidates: List[Path]) -> Optional[Path]:
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def _find_fampnn_sidecar_path(structure_path: Path, output_path: Optional[Path] = None) -> Optional[Path]:
    candidates = [structure_path.with_suffix(".json")]
    if output_path is not None:
        candidates.extend([
            output_path / "run" / "ppiflow" / "redesign_debug" / f"{structure_path.stem}.json",
            output_path / "ppiflow" / "redesign_debug" / f"{structure_path.stem}.json",
            output_path / "run" / "fampnn" / "results" / f"{structure_path.stem}.json",
            output_path / "fampnn" / "results" / f"{structure_path.stem}.json",
        ])
    return _find_first_existing_path(candidates)


def _normalize_scope_value(raw_value: Any) -> Optional[Dict[str, Any]]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        cleaned = {key: value for key, value in raw_value.items() if value not in (None, "", [], {}, ())}
        return cleaned or None
    if isinstance(raw_value, (list, tuple, set)):
        cleaned = [str(item).strip().upper() for item in raw_value if str(item).strip()]
        return {"selected_loops": sorted(set(cleaned))} if cleaned else None
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return None
        tokens = [token.strip().upper() for token in text.replace(";", ",").replace("|", ",").split(",") if token.strip()]
        if len(tokens) > 1:
            return {"selected_loops": sorted(set(tokens))}
        return {"value": text}
    return {"value": raw_value}


def _job_stage_context(job: Optional[Job]) -> Dict[str, Any]:
    params = _parse_job_params(job.params if job else None)
    model_id = str(job.model_id or "").strip().lower() if job else ""
    mode = str(job.mode or "").strip().lower() if job else ""
    # Applicability selectors come only from persisted server-owned Job fields.
    # Request params may carry workflow inputs, but cannot mint review authority.
    stage_family = str(getattr(job, "stage_family", None) or "").strip().lower() or None
    stage_mode = str(getattr(job, "stage_mode", None) or "").strip().lower() or None
    if not stage_family:
        if mode == "maturation_child":
            stage_family = "ppiflow"
            stage_mode = stage_mode or "maturation"
        elif "confornets" in model_id or "confornets" in mode:
            stage_family = "confornets"
        elif "esmfold2" in model_id or "esmfold2" in mode:
            stage_family = "esmfold2"
            stage_mode = stage_mode or "predict"
        elif "caliby" in model_id:
            stage_family = "caliby"
        elif "protein_hunter" in model_id:
            stage_family = "protein_hunter"
        elif "fampnn" in model_id:
            stage_family = "fampnn"
        elif "antibody" in model_id or "antibody" in mode:
            stage_family = "antibody"
        elif mode == "shape_blueprint":
            stage_family = "shape_blueprint"
            stage_mode = stage_mode or "shape_blueprint"

    selected_loop_scope = _normalize_scope_value(params.get("selected_loop_scope"))
    if selected_loop_scope is None:
        for key in (
            "ppiflow_selected_loops",
            "selected_loops",
            "loop_ids",
            "selected_residues",
            "antibody_design_loops",
        ):
            selected_loop_scope = _normalize_scope_value(params.get(key))
            if selected_loop_scope:
                break

    lineage_root_job_id = (
        params.get("lineage_root_job_id")
        or params.get("iteration_source_root_job_id")
        or params.get("resume_root_job_id")
        or getattr(job, "parent_job_id", None)
        or getattr(job, "id", None)
    )
    origin_job_id = (
        params.get("selection_source_job_id")
        or params.get("iteration_source_job_id")
        or getattr(job, "parent_job_id", None)
        or getattr(job, "id", None)
    )

    selection_manifest = None
    selection_manifest_path = None
    selection_dir = params.get("selected_input_dir") or params.get("iteration_selection_dir")
    manifest_candidate = params.get("selected_input_manifest") or params.get("source_selection_manifest_path")
    if manifest_candidate:
        try:
            selection_manifest_path = Path(str(manifest_candidate)).expanduser()
        except Exception:
            selection_manifest_path = None
    elif selection_dir:
        try:
            selection_manifest_path = Path(str(selection_dir)).expanduser() / "selection_manifest.json"
        except Exception:
            selection_manifest_path = None
    if selection_manifest_path:
        try:
            selection_manifest = _load_json_payload(selection_manifest_path)
        except Exception:
            selection_manifest = None

    selection_index: Dict[str, Dict[str, Any]] = {}
    if selection_manifest and isinstance(selection_manifest.get("designs"), list):
        for item in selection_manifest["designs"]:
            if not isinstance(item, dict):
                continue
            for key in (
                str(item.get("selection_pdb_path") or "").strip(),
                Path(str(item.get("selection_pdb_path") or "")).stem if item.get("selection_pdb_path") else "",
                str(item.get("design_id") or "").strip(),
                str(item.get("design_name") or "").strip(),
            ):
                if key:
                    selection_index.setdefault(key, item)

    source_design_index = _build_source_design_index(params)
    stage_settings = _extract_stage_settings(params, stage_family, stage_mode)

    source_stage_job_id = (
        params.get("source_stage_job_id")
        or params.get("selected_input_source_job_id")
        or getattr(job, "source_stage_job_id", None)
    )
    source_stage_family = str(
        params.get("source_stage_family")
        or params.get("selected_input_stage_family")
        or getattr(job, "source_stage_family", None)
        or ""
    ).strip().lower() or None
    source_stage_mode = str(
        params.get("source_stage_mode")
        or params.get("selected_input_stage_mode")
        or getattr(job, "source_stage_mode", None)
        or ""
    ).strip().lower() or None
    source_selection_count = params.get("source_selection_count") or getattr(job, "source_selection_count", None)
    selected_input_artifact_class = normalize_antibody_artifact_class(
        params.get("selected_input_artifact_class")
        or (selection_manifest.get("selected_input_artifact_class") if isinstance(selection_manifest, dict) else None)
        or getattr(job, "selected_input_artifact_class", None)
    )
    selected_input_schema_version = normalize_antibody_pipeline_contract_version(
        params.get("selected_input_schema_version")
        or (selection_manifest.get("selected_input_schema_version") if isinstance(selection_manifest, dict) else None)
        or getattr(job, "selected_input_schema_version", None)
    )
    if selected_input_artifact_class and selected_input_schema_version is None:
        selected_input_schema_version = ANTIBODY_PIPELINE_CONTRACT_VERSION
    artifact_class = normalize_antibody_artifact_class(
        infer_antibody_artifact_class_from_stage(stage_family, stage_mode)
    )
    artifact_schema_version = ANTIBODY_PIPELINE_CONTRACT_VERSION if artifact_class else None

    inferred_review_contract = resolve_result_contract(
        model_type=getattr(job, "model_id", None),
        stage_family=stage_family,
        stage_mode=stage_mode,
        artifact_class=artifact_class,
        provenance={"model_id": getattr(job, "model_id", None)},
    )
    review_profile_id = inferred_review_contract.analysis_contract_id
    review_role_map = None
    if review_profile_id in {"antibody_backbone_v1", "ppiflow_maturation_v1"}:
        target_chains = params.get("antigen_chains") or params.get("target_chains")
        if isinstance(target_chains, str):
            target_chains = [chain.strip() for chain in target_chains.split(",") if chain.strip()]
        review_role_map = {
            "result_role": "antibody_binder",
            "target_chains": target_chains if isinstance(target_chains, list) else [],
        }
    review_contract_source = "job_identity" if review_profile_id else None

    provenance = {
        "job_id": getattr(job, "id", None),
        "job_name": getattr(job, "name", None),
        "model_id": getattr(job, "model_id", None),
        "mode": getattr(job, "mode", None),
        "stage_family": stage_family,
        "stage_mode": stage_mode,
        "lineage_root_job_id": lineage_root_job_id,
        "origin_job_id": origin_job_id,
        "selection_source_type": params.get("selection_source_type"),
        "selection_source_job_id": params.get("selection_source_job_id") or params.get("iteration_source_job_id"),
        "selection_dataset_name": params.get("selection_dataset_name"),
        "selected_loop_scope": selected_loop_scope,
        "source_stage_job_id": source_stage_job_id,
        "source_stage_family": source_stage_family,
        "source_stage_mode": source_stage_mode,
        "source_selection_manifest_path": str(selection_manifest_path) if selection_manifest_path else None,
        "source_selection_count": source_selection_count,
        "selected_input_dir": str(selection_dir) if selection_dir else None,
        "selected_input_manifest": str(selection_manifest_path) if selection_manifest_path else None,
        "selected_input_artifact_class": selected_input_artifact_class,
        "selected_input_schema_version": selected_input_schema_version,
        "artifact_class": artifact_class,
        "artifact_schema_version": artifact_schema_version,
        "review_profile_id": review_profile_id,
        "review_contract_source": review_contract_source,
        "review_role_map": review_role_map,
        "review_artifact_manifest": None,
        "iteration_action": params.get("iteration_action"),
        "ppiflow_stage_target": params.get("ppiflow_stage_target"),
        "ppiflow_stage_mode": params.get("ppiflow_stage_mode"),
        "stage_settings": stage_settings,
        "selection_manifest": selection_manifest,
    }
    return {
        "params": params,
        "stage_family": stage_family,
        "stage_mode": stage_mode,
        "lineage_root_job_id": lineage_root_job_id,
        "origin_job_id": origin_job_id,
        "selected_loop_scope": selected_loop_scope,
        "source_stage_job_id": source_stage_job_id,
        "source_stage_family": source_stage_family,
        "source_stage_mode": source_stage_mode,
        "source_selection_manifest_path": str(selection_manifest_path) if selection_manifest_path else None,
        "source_selection_count": source_selection_count,
        "selected_input_artifact_class": selected_input_artifact_class,
        "selected_input_schema_version": selected_input_schema_version,
        "artifact_class": artifact_class,
        "artifact_schema_version": artifact_schema_version,
        "review_profile_id": review_profile_id,
        "review_contract_source": review_contract_source,
        "review_role_map": review_role_map,
        "review_artifact_manifest": None,
        "provenance": provenance,
        "selection_index": selection_index,
        "source_design_index": source_design_index,
    }


async def _resolve_job_param_from_lineage(
    session: AsyncSession,
    job: Optional[Job],
    params: Dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = params.get(key)
        if value not in (None, "", [], {}):
            return value

    related_job_ids: List[str] = []
    if job:
        for candidate in (
            getattr(job, "parent_job_id", None),
            params.get("selection_source_job_id"),
            params.get("iteration_source_job_id"),
            params.get("iteration_source_root_job_id"),
            params.get("resume_root_job_id"),
            params.get("lineage_root_job_id"),
        ):
            candidate_id = str(candidate).strip() if candidate else ""
            if candidate_id and candidate_id not in related_job_ids and candidate_id != getattr(job, "id", None):
                related_job_ids.append(candidate_id)

    for job_id in related_job_ids:
        result = await session.execute(select(Job.params).where(Job.id == job_id))
        row = result.one_or_none()
        lineage_params = _parse_job_params(row[0] if row else None)
        for key in keys:
            value = lineage_params.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


async def _resolve_parent_design_lineage(
    session: AsyncSession,
    context: Dict[str, Any],
    design_name: str,
    *,
    cache: Optional[Dict[str, Optional[Design]]] = None,
) -> Dict[str, Optional[str]]:
    cache = cache or {}
    selection_index = context.get("selection_index") or {}
    candidate_source_ids = _candidate_source_design_ids(design_name)
    manifest_item = selection_index.get(design_name)
    if manifest_item is None:
        stem = Path(design_name).stem
        manifest_item = selection_index.get(stem)
    if manifest_item is None:
        for key in _candidate_source_design_names(design_name):
            match = (context.get("source_design_index") or {}).get(key)
            if match:
                manifest_item = match
                break

    parent_design_id = None
    if manifest_item:
        for key in ("design_id", "source_design_id"):
            value = manifest_item.get(key)
            if value:
                parent_design_id = str(value).strip()
                break
    source_pdb_path = None
    if manifest_item:
        for key in ("source_pdb_path", "pdb_path"):
            value = manifest_item.get(key)
            if value:
                source_pdb_path = str(value).strip()
                break
    source_design_name = None
    if manifest_item:
        for key in ("source_design_name", "design_name"):
            value = manifest_item.get(key)
            if value:
                source_design_name = str(value).strip()
                break
    if source_design_name:
        candidate_source_ids.extend(_candidate_source_design_ids(source_design_name))
    source_stage_job_id = str(manifest_item.get("design_job_id")).strip() if manifest_item and manifest_item.get("design_job_id") else context.get("source_stage_job_id")
    source_stage_family = str((manifest_item.get("design_stage_family") if manifest_item else None) or context.get("source_stage_family") or "").strip().lower() or None
    source_stage_mode = str((manifest_item.get("design_stage_mode") if manifest_item else None) or context.get("source_stage_mode") or "").strip().lower() or None
    parent_design = None
    if parent_design_id:
        parent_design = cache.get(parent_design_id)
        if parent_design is None and parent_design_id not in cache:
            result = await session.execute(
                select(Design).options(
                    load_only(*_SOURCE_LINEAGE_LOAD_ONLY_COLUMNS)
                ).where(Design.id == parent_design_id)
            )
            parent_design = result.scalar_one_or_none()
            cache[parent_design_id] = parent_design
    if parent_design is None and candidate_source_ids:
        for candidate_id in _ordered_unique(candidate_source_ids):
            parent_design = cache.get(candidate_id)
            if parent_design is None and candidate_id not in cache:
                result = await session.execute(
                    select(Design).options(
                        load_only(*_SOURCE_LINEAGE_LOAD_ONLY_COLUMNS)
                    ).where(Design.id == candidate_id)
                )
                parent_design = result.scalar_one_or_none()
                cache[candidate_id] = parent_design
            if parent_design is not None:
                parent_design_id = parent_design.id
                break
    if parent_design is None and source_pdb_path:
        cache_key = f"pdb::{source_pdb_path}"
        parent_design = cache.get(cache_key)
        if parent_design is None and cache_key not in cache:
            result = await session.execute(
                select(Design).options(
                    load_only(*_SOURCE_LINEAGE_LOAD_ONLY_COLUMNS)
                ).where(Design.pdb_path == source_pdb_path)
            )
            parent_design = result.scalars().first()
            cache[cache_key] = parent_design
        if parent_design is None and source_design_name:
            cache_key = f"name::{source_design_name}"
            parent_design = cache.get(cache_key)
            if parent_design is None and cache_key not in cache:
                result = await session.execute(
                    select(Design).options(
                        load_only(*_SOURCE_LINEAGE_LOAD_ONLY_COLUMNS)
                    ).where(Design.name == source_design_name)
                )
                parent_design = result.scalars().first()
                cache[cache_key] = parent_design
        if parent_design is not None:
            parent_design_id = parent_design.id

    origin_design_id = None
    origin_backbone_design_id = None
    origin_job_id = None
    if parent_design:
        source_design_name = getattr(parent_design, "name", None) or source_design_name
        source_pdb_path = getattr(parent_design, "pdb_path", None) or source_pdb_path
        source_stage_job_id = source_stage_job_id or getattr(parent_design, "job_id", None)
        source_stage_family = source_stage_family or getattr(parent_design, "stage_family", None)
        source_stage_mode = source_stage_mode or getattr(parent_design, "stage_mode", None)
        origin_design_id = parent_design.origin_design_id or parent_design.id
        origin_backbone_design_id = parent_design.origin_backbone_design_id or parent_design.id
        origin_job_id = parent_design.job_id
    elif parent_design_id:
        origin_design_id = parent_design_id
        origin_backbone_design_id = parent_design_id
        origin_job_id = str(manifest_item.get("design_job_id")).strip() if manifest_item and manifest_item.get("design_job_id") else None

    return {
        "parent_design_id": parent_design_id,
        "origin_design_id": origin_design_id,
        "origin_backbone_design_id": origin_backbone_design_id,
        "origin_job_id": origin_job_id,
        "source_stage_job_id": source_stage_job_id,
        "source_stage_family": source_stage_family,
        "source_stage_mode": source_stage_mode,
        "source_cdr_lengths": _extract_design_cdr_lengths(parent_design),
        "selection_manifest_item": manifest_item,
        "source_pdb_path": source_pdb_path,
        "source_design_name": source_design_name,
        "source_design": parent_design,
    }


def _design_lineage_fields(
    context: Dict[str, Any],
    lineage: Dict[str, Any],
    *,
    artifact_class_override: Optional[str] = None,
    producer_job: Optional[Job] = None,
    producer_payload: Any = None,
) -> Dict[str, Any]:
    fields = {
        "lineage_root_job_id": context.get("lineage_root_job_id"),
        "parent_design_id": lineage.get("parent_design_id"),
        "origin_design_id": lineage.get("origin_design_id"),
        "origin_job_id": lineage.get("origin_job_id") or context.get("origin_job_id"),
        "origin_backbone_design_id": lineage.get("origin_backbone_design_id"),
        "source_stage_job_id": lineage.get("source_stage_job_id") or context.get("source_stage_job_id"),
        "source_stage_family": lineage.get("source_stage_family") or context.get("source_stage_family"),
        "source_stage_mode": lineage.get("source_stage_mode") or context.get("source_stage_mode"),
        "source_pdb_path": lineage.get("source_pdb_path"),
        "source_design_name": lineage.get("source_design_name"),
        "artifact_class": artifact_class_override or context.get("artifact_class"),
        "artifact_schema_version": context.get("artifact_schema_version"),
        "review_profile_id": context.get("review_profile_id"),
        "review_contract_source": context.get("review_contract_source"),
        "review_role_map": context.get("review_role_map"),
        "review_artifact_manifest": context.get("review_artifact_manifest"),
    }
    fields.update(_trusted_producer_review_fields(producer_job, producer_payload))
    return fields


def _parse_hlt_cdr_lengths(structure_path: Optional[Path]) -> Dict[str, int]:
    """
    Parse RFantibody-style HLT REMARK labels from a PDB and return actual
    per-structure loop lengths.

    These values are design-specific. They should not be substituted with the
    job-level configured loop spans, which only describe the intended search
    space and can differ across RFantibody outputs.
    """
    if not structure_path or not structure_path.exists() or structure_path.suffix.lower() != ".pdb":
        return {}

    counts: Dict[str, int] = {}
    try:
        with open(structure_path, "r") as handle:
            for line in handle:
                if not line.startswith("REMARK PDBinfo-LABEL:"):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                loop_id = parts[3].upper()
                if loop_id in {"H1", "H2", "H3", "L1", "L2", "L3"}:
                    counts[loop_id] = counts.get(loop_id, 0) + 1
    except Exception as e:
        print(f"[Ingester] Failed to parse HLT CDR labels from {structure_path}: {e}")
        return {}

    return counts


def _extract_design_cdr_lengths(design: Optional[Design]) -> Dict[str, int]:
    if design is None:
        return {}
    return {
        "H1": getattr(design, "cdr_h1_length", None),
        "H2": getattr(design, "cdr_h2_length", None),
        "H3": getattr(design, "cdr_h3_length", None),
        "L1": getattr(design, "cdr_l1_length", None),
        "L2": getattr(design, "cdr_l2_length", None),
        "L3": getattr(design, "cdr_l3_length", None),
    }


def _coalesce_cdr_lengths(*sources: Optional[Dict[str, Any]]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for loop_id in ("H1", "H2", "H3", "L1", "L2", "L3"):
        for source in sources:
            if not source:
                continue
            value = source.get(loop_id)
            if value in (None, "", 0):
                continue
            try:
                merged[loop_id] = int(value)
                break
            except (TypeError, ValueError):
                continue
    return merged


def _parse_source_cdr_lengths(source_pdb_path: Optional[str]) -> Dict[str, int]:
    raw_path = str(source_pdb_path or "").strip()
    if not raw_path:
        return {}
    try:
        return _parse_hlt_cdr_lengths(Path(raw_path).expanduser())
    except Exception:
        return {}


def _parse_ppiflow_sample_index(name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    match = re.search(r"_ppiflow(?:_seq_\d+)?_sample(\d+)$", str(name), re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _ppiflow_design_prefix(name: Optional[str]) -> str:
    raw_name = str(name or "").strip()
    if not raw_name:
        return raw_name
    match = re.match(r"^(.*)_ppiflow(?:_seq_\d+)?_sample\d+$", raw_name, re.IGNORECASE)
    return match.group(1) if match else raw_name


def _is_final_ppiflow_structure_path(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.is_file()
        and path.suffix.lower() in {".pdb", ".cif", ".mmcif"}
        and "_ppiflow" in name
        and not name.endswith("_enriched_complex.pdb")
    )


def _is_confornets_job(job: Optional[Job]) -> bool:
    if not job:
        return False
    params = _parse_job_params(job.params)
    model_id = str(job.model_id or "").strip().lower()
    mode = str(job.mode or "").strip().lower()
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()
    return "confornets" in {model_id, mode, rfd_mode} or model_id == "confornets_experimental"


def _is_esmfold2_job(job: Optional[Job]) -> bool:
    if not job:
        return False
    params = _parse_job_params(job.params)
    model_id = str(job.model_id or "").strip().lower()
    mode = str(job.mode or "").strip().lower()
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()
    launch_variant = str(params.get("structure_launch_variant") or "").strip().lower()
    template_model_id = str(params.get("template_model_id") or "").strip().lower()
    tokens = {model_id, mode, rfd_mode, launch_variant, template_model_id}
    return any("esmfold2" in token for token in tokens)


def _load_json_any(path: Optional[Path]) -> Any:
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _normalize_confidence_percent(value: Any) -> Optional[float]:
    numeric = safe_float(value)
    if numeric is None:
        return None
    if 0 <= numeric <= 1:
        return numeric * 100.0
    return numeric


def _resolve_existing_child_path(root: Path, raw_path: Any) -> Optional[Path]:
    text = str(raw_path or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate if candidate.exists() else None


def _resolve_esmfold2_final_root(output_path: Path) -> Optional[Path]:
    esmfold2_root = output_path / "final" / "esmfold2"
    # The workflow publishes single-sequence jobs under
    # final/esmfold2/<sequence_name>/esmfold2_results. Keep the older aggregate
    # directory candidates for backward compatibility, but discover only the
    # bounded one-level sequence namespace rather than recursively scanning the
    # job root.
    sequence_roots = (
        sorted(esmfold2_root.glob("*/esmfold2_results"))
        if esmfold2_root.is_dir()
        else []
    )
    candidates = [
        esmfold2_root / "esmfold2_results",
        *sequence_roots,
        esmfold2_root,
        output_path / "esmfold2_results",
        output_path / "pdb_files" / "esmfold2_results",
        output_path,
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        manifest = _load_json_any(candidate / "manifest.json")
        if isinstance(manifest, dict) and str(manifest.get("workflow") or "").strip().lower() == "esmfold2_experimental":
            return candidate
        if any(candidate.glob("*.metrics.json")) and (
            any(candidate.glob("*.cif")) or any(candidate.glob("*.mmcif")) or any(candidate.glob("*.pdb"))
        ):
            return candidate
    return None


def _esmfold2_sample_entries(final_root: Path, manifest_payload: Any) -> List[Dict[str, Any]]:
    if isinstance(manifest_payload, dict) and isinstance(manifest_payload.get("samples"), list):
        return [entry for entry in manifest_payload["samples"] if isinstance(entry, dict)]

    entries: List[Dict[str, Any]] = []
    for metrics_path in sorted(final_root.glob("*.metrics.json")):
        metrics = _load_json_payload(metrics_path) or {}
        sample_id = str(metrics.get("sample_id") or metrics_path.name.removesuffix(".metrics.json")).strip()
        entry = {"sample_id": sample_id, "metrics": metrics_path.name}
        if metrics.get("cif"):
            entry["cif"] = metrics["cif"]
        elif (final_root / f"{sample_id}.cif").exists():
            entry["cif"] = f"{sample_id}.cif"
        elif (final_root / f"{sample_id}.mmcif").exists():
            entry["cif"] = f"{sample_id}.mmcif"
        elif (final_root / f"{sample_id}.pdb").exists():
            entry["cif"] = f"{sample_id}.pdb"
        entries.append(entry)
    return entries


def _filtered_esmfold2_record(payload: Dict[str, Any], *, manifest_path: Path, metrics_path: Optional[Path], structure_path: Path) -> Dict[str, Any]:
    record_keys = {
        "sample_id",
        "sequence_name",
        "sequence_length",
        "total_polymer_residues",
        "component_count",
        "components",
        "model_variant",
        "model_id_or_path",
        "local_files_only",
        "num_loops",
        "num_sampling_steps",
        "num_diffusion_samples",
        "seed",
        "device",
        "plddt_mean",
        "ptm",
        "iptm",
        "cif",
    }
    record = {key: payload[key] for key in record_keys if key in payload}
    record.update(
        {
            "schema": "esmfold2.result.v1",
            "workflow": "esmfold2_experimental",
            "manifest_json": str(manifest_path),
            "metrics_json": str(metrics_path) if metrics_path else None,
            "structure_path": str(structure_path),
        }
    )
    return record


async def ingest_esmfold2_results(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
    current_job: Optional[Job] = None,
    *,
    commit: bool = True,
) -> int:
    final_root = _resolve_esmfold2_final_root(output_path)
    if final_root is None:
        return 0

    manifest_path = final_root / "manifest.json"
    manifest_payload = _load_json_any(manifest_path)
    if not isinstance(manifest_payload, dict):
        manifest_payload = {}

    existing_designs = (
        await session.execute(
            select(Design).where(
                Design.job_id == job_id,
                Design.source_stage.is_(None),
            )
        )
    ).scalars().all()
    existing_by_name = {design.name: design for design in existing_designs}

    job_context = _job_stage_context(current_job)
    created = 0
    seen_names: set[str] = set()
    for entry in _esmfold2_sample_entries(final_root, manifest_payload):
        sample_id = str(entry.get("sample_id") or entry.get("id") or entry.get("name") or "").strip()
        metrics_path = _resolve_existing_child_path(final_root, entry.get("metrics"))
        metrics_payload = _load_json_payload(metrics_path) if metrics_path else None
        combined_payload: Dict[str, Any] = {}
        combined_payload.update(manifest_payload)
        combined_payload.update(entry)
        if isinstance(metrics_payload, dict):
            combined_payload.update(metrics_payload)
        if not sample_id:
            sample_id = str(combined_payload.get("sample_id") or "").strip()

        structure_path = (
            _resolve_existing_child_path(final_root, combined_payload.get("cif"))
            or _resolve_existing_child_path(final_root, combined_payload.get("structure_path"))
            or (final_root / f"{sample_id}.cif" if sample_id and (final_root / f"{sample_id}.cif").exists() else None)
            or (final_root / f"{sample_id}.mmcif" if sample_id and (final_root / f"{sample_id}.mmcif").exists() else None)
            or (final_root / f"{sample_id}.pdb" if sample_id and (final_root / f"{sample_id}.pdb").exists() else None)
        )
        if structure_path is None:
            print(f"[Ingester] No ESMFold2 structure found for sample {sample_id or '<unknown>'}")
            continue

        design_name = sample_id or structure_path.stem
        if design_name in seen_names:
            continue
        seen_names.add(design_name)

        structure_plddt, residue_plddt = extract_plddt_from_pdb(structure_path)
        plddt_candidates = [
            _normalize_confidence_percent(combined_payload.get("plddt_mean")),
            _normalize_confidence_percent(combined_payload.get("mean_plddt")),
            _normalize_confidence_percent(combined_payload.get("plddt")),
            _normalize_confidence_percent(structure_plddt),
        ]
        plddt_overall = next((value for value in plddt_candidates if value is not None), None)
        esmfold2_record = _filtered_esmfold2_record(
            combined_payload,
            manifest_path=manifest_path,
            metrics_path=metrics_path,
            structure_path=structure_path,
        )
        # Historical artifacts may retain the retired standalone workflow ID.
        # Canonicalize capability provenance without mutating source files.
        esmfold2_record["workflow"] = "esmfold2"
        esmfold2_record["engine"] = "esmfold2"
        confidence_metrics = {
            "esmfold2": esmfold2_record,
            "esmfold2_components": combined_payload.get("components"),
        }
        model_variant = str(combined_payload.get("model_variant") or manifest_payload.get("model_variant") or "").strip() or None
        model_id_or_path = str(combined_payload.get("model_id_or_path") or manifest_payload.get("model_id_or_path") or "").strip() or None
        provenance = {
            **job_context.get("provenance", {}),
            "artifact_group": "esmfold2",
            "stage_family": "esmfold2",
            "stage_mode": job_context.get("stage_mode") or "predict",
            "model_variant": model_variant,
            "model_id_or_path": model_id_or_path,
            "local_files_only": combined_payload.get("local_files_only", manifest_payload.get("local_files_only")),
            "sample_id": design_name,
            "structure_path": str(structure_path),
            "manifest_json": str(manifest_path),
            "metrics_json": str(metrics_path) if metrics_path else None,
            "esmfold2": esmfold2_record,
        }

        design_fields = {
            "pdb_path": str(structure_path),
            "json_path": str(metrics_path) if metrics_path else (str(manifest_path) if manifest_path.exists() else None),
            "stage_family": "esmfold2",
            "stage_mode": job_context.get("stage_mode") or "predict",
            "artifact_group": "esmfold2",
            "artifact_class": "structure_prediction",
            "review_profile_id": job_context.get("review_profile_id"),
            "review_contract_source": job_context.get("review_contract_source"),
            "review_role_map": job_context.get("review_role_map"),
            "provenance": provenance,
            "confidence_metrics": confidence_metrics,
            "plddt_overall": plddt_overall,
            "residue_plddt": residue_plddt,
            "ptm": safe_float(combined_payload.get("ptm")),
            "iptm": safe_float(combined_payload.get("iptm")),
            "mpnn_score": None,
            "fampnn_psce": None,
        }
        existing_design = existing_by_name.get(design_name)
        if existing_design is not None:
            for field_name, field_value in design_fields.items():
                setattr(existing_design, field_name, field_value)
            flag_modified(existing_design, "provenance")
            flag_modified(existing_design, "confidence_metrics")
        else:
            session.add(Design(
                id=str(uuid.uuid4()),
                job_id=job_id,
                name=design_name,
                **design_fields,
                is_favorite=False,
                created_at=datetime.utcnow(),
            ))
        created += 1

    if created > 0 and commit:
        await session.commit()
        print(f"[Ingester] Ingested {created} ESMFold2 designs for job {job_id}")
    return created


def _resolve_confornets_final_root(output_path: Path) -> Optional[Path]:
    candidates = [
        output_path / "final" / "confornets",
        output_path,
        output_path / "final_confornets_results",
        output_path / "confornets_results",
    ]
    for candidate in candidates:
        if (candidate / "conformers").exists() and (
            (candidate / "samples.json").exists()
            or (candidate / "ensemble_manifest.json").exists()
            or any((candidate / "conformers").glob("*.cif"))
            or any((candidate / "conformers").glob("*.pdb"))
            or any((candidate / "conformers").glob("*.mmcif"))
        ):
            return candidate
    return None


def _confornets_samples_by_id(samples_payload: Any) -> Dict[str, Dict[str, Any]]:
    if isinstance(samples_payload, dict):
        raw_samples = samples_payload.get("samples") or samples_payload.get("conformers") or []
    elif isinstance(samples_payload, list):
        raw_samples = samples_payload
    else:
        raw_samples = []

    samples_by_id: Dict[str, Dict[str, Any]] = {}
    for idx, entry in enumerate(raw_samples):
        if not isinstance(entry, dict):
            continue
        sample_id = str(
            entry.get("sample_id")
            or entry.get("id")
            or entry.get("name")
            or f"sample_{idx}"
        ).strip()
        if sample_id:
            samples_by_id[sample_id] = entry
    return samples_by_id


def _confornets_manifest_entries(ensemble_payload: Any) -> List[Dict[str, Any]]:
    if isinstance(ensemble_payload, dict):
        entries = ensemble_payload.get("conformers") or ensemble_payload.get("samples") or []
    elif isinstance(ensemble_payload, list):
        entries = ensemble_payload
    else:
        entries = []
    return [entry for entry in entries if isinstance(entry, dict)]


def _confornets_entry_path(final_root: Path, entry: Dict[str, Any]) -> Optional[Path]:
    raw_path = str(
        entry.get("relative_path")
        or entry.get("path")
        or entry.get("conformer_path")
        or entry.get("structure_path")
        or entry.get("cif_path")
        or entry.get("pdb_path")
        or ""
    ).strip()
    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = final_root / candidate
    try:
        resolved = candidate.resolve()
        conformers_root = (final_root / "conformers").resolve()
        resolved.relative_to(conformers_root)
    except Exception:
        return None
    return resolved if resolved.exists() and resolved.suffix.lower() in {".pdb", ".cif", ".mmcif"} else None


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


_CONFORNETS_CONFIDENCE_SCHEMA = "confornets.confidence_metrics.v1"

_CONFORNETS_REPORTING_SEMANTICS: Dict[str, Any] = {
    "schema_version": 1,
    "sample_semantics": "independent_generated_conformer_sample",
    "sample_semantics_note": "Each row is an independent generated monomer conformer sample; frame/sample indices are stable selectors, not a time-resolved trajectory.",
    "reference_evaluation_semantics": "exact_identity_matched_ca_proper_rotation_kabsch_rmsd_to_staged_references",
    "reference_evaluation_note": "Reference RMSD metrics are BMS post-hoc Cα RMSD after proper-rotation Kabsch alignment. Correspondence is the exact set of (model, auth_asym_id, auth_seq_id, insertion_code, auth_comp_id, auth_atom_id) identities; row order, ambiguous atoms, missing identities, and imputation are not accepted.",
    "pairwise_diversity_semantics": "exact_identity_matched_pairwise_ca_proper_rotation_kabsch_rmsd",
    "pairwise_diversity_note": "Pairwise diversity is computed after generation from final samples with the same exact Cα identity and Kabsch method; it is not an upstream training objective trace or a thermodynamic ensemble probability.",
    "landscape_semantics": "classical_metric_mds_on_exact_identity_matched_pairwise_ca_kabsch_rmsd",
    "landscape_note": "Landscape coordinates use classical metric MDS on the complete pairwise exact-identity Cα Kabsch RMSD matrix. They are not calibrated free energy, thermodynamics, or trajectory time.",
    "ca_identity": "(model, auth_asym_id, auth_seq_id, insertion_code, auth_comp_id, auth_atom_id)",
    "ca_rmsd_method": "proper_rotation_kabsch_svd_in_angstroms",
    "landscape_embedding_method": "classical_metric_mds_double_centered_squared_distances_with_nonpositive_eigencomponents_discarded",
    "confidence_semantics": "sample_scalar_confidence_with_optional_full_tensor",
    "confidence_note": "Scalar pLDDT/gPDE/pTM values come from the optional ConforNets/OpenFold3 confidence path. Per-residue confidence requires a saved full confidence tensor artifact.",
}


def _summarize_confornets_training_loss(csv_path: Path) -> Optional[Dict[str, Any]]:
    if not csv_path.exists():
        return None

    rows: List[tuple[Optional[float], float]] = []
    try:
        with open(csv_path, "r", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                loss = None
                for loss_key in ("loss", "training_loss", "mse_loss", "val_loss"):
                    loss = _coerce_float(row.get(loss_key))
                    if loss is not None:
                        break
                if loss is None:
                    continue

                step = None
                for step_key in ("step", "iteration", "epoch"):
                    step = _coerce_float(row.get(step_key))
                    if step is not None:
                        break
                rows.append((step, loss))
    except Exception as exc:
        print(f"[Ingester] Error parsing ConforNets training loss from {csv_path}: {exc}")
        return None

    if not rows:
        return None

    steps = [step for step, _loss in rows if step is not None]
    losses = [loss for _step, loss in rows]
    summary: Dict[str, Any] = {
        "csv_path": str(csv_path),
        "row_count": len(rows),
        "first_loss": losses[0],
        "final_loss": losses[-1],
        "min_loss": min(losses),
        "max_loss": max(losses),
    }
    if steps:
        summary["first_step"] = steps[0]
        summary["last_step"] = steps[-1]
    return summary


def _confornets_artifact_schema_version(artifact_manifest: Any) -> int:
    if isinstance(artifact_manifest, dict):
        try:
            version = int(artifact_manifest.get("schema_version"))
            if version > 0:
                return version
        except (TypeError, ValueError):
            pass
    return 1


def _confornets_sidecar_path(
    final_root: Path,
    artifact_manifest: Any,
    manifest_key: str,
    default_relative_path: str,
) -> Path:
    raw_path = ""
    if isinstance(artifact_manifest, dict):
        raw_path = str(artifact_manifest.get(manifest_key) or "").strip()
    candidate = Path(raw_path).expanduser() if raw_path else final_root / default_relative_path
    if not candidate.is_absolute():
        candidate = final_root / candidate
    return candidate


def _merge_confornets_metric_payload(existing: Any, incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _confornets_summary_samples_by_id(summary_payload: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(summary_payload, dict):
        return {}
    raw_samples = summary_payload.get("samples") or []
    if not isinstance(raw_samples, list):
        return {}
    samples_by_id: Dict[str, Dict[str, Any]] = {}
    for idx, entry in enumerate(raw_samples):
        if not isinstance(entry, dict):
            continue
        sample_id = str(entry.get("sample_id") or entry.get("id") or entry.get("name") or "").strip()
        frame_index = entry.get("frame_index", entry.get("sample_index"))
        keys = {sample_id, Path(sample_id).stem if sample_id else "", str(idx), f"sample_{idx}"}
        if frame_index is not None:
            keys.add(str(frame_index))
            keys.add(f"sample_{frame_index}")
            try:
                keys.add(f"cn_{int(frame_index):05d}_sample_{int(frame_index)}")
            except (TypeError, ValueError):
                pass
        for key in keys:
            if key:
                samples_by_id[key] = entry
    return samples_by_id


def _confornets_entry_lookup_keys(
    structure_path: Path,
    manifest_entry: Optional[Dict[str, Any]],
    sample_entry: Optional[Dict[str, Any]],
) -> List[str]:
    keys: List[str] = [structure_path.stem]
    for entry in (sample_entry, manifest_entry):
        if not isinstance(entry, dict):
            continue
        for scalar_key in ("sample_id", "id", "name", "frame_index", "sample_index"):
            value = entry.get(scalar_key)
            if value not in (None, ""):
                keys.append(str(value))
                if scalar_key in {"frame_index", "sample_index"}:
                    keys.append(f"sample_{value}")
                    try:
                        keys.append(f"cn_{int(value):05d}_sample_{int(value)}")
                    except (TypeError, ValueError):
                        pass
        for path_key in ("relative_path", "conformer_path", "path", "file", "filename", "structure_path", "cif_path", "pdb_path"):
            value = entry.get(path_key)
            if value:
                keys.append(Path(str(value)).stem)
    seen: set[str] = set()
    ordered: List[str] = []
    for key in keys:
        normalized = str(key).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _lookup_confornets_summary_entry(
    summary_by_id: Dict[str, Dict[str, Any]],
    structure_path: Path,
    manifest_entry: Optional[Dict[str, Any]],
    sample_entry: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for key in _confornets_entry_lookup_keys(structure_path, manifest_entry, sample_entry):
        if key in summary_by_id:
            return summary_by_id[key]
    return None


def _confornets_sample_index(
    structure_path: Path,
    manifest_entry: Optional[Dict[str, Any]],
    sample_entry: Optional[Dict[str, Any]],
) -> Optional[int]:
    for entry in (sample_entry, manifest_entry):
        if not isinstance(entry, dict):
            continue
        for key in ("sample_index", "frame_index", "index"):
            value = entry.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    for candidate in (structure_path.stem,):
        match = re.search(r"(?:sample|frame|cn)[_-]?(\d+)", candidate, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _strip_confornets_sample_list(summary_payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(summary_payload, dict):
        return None
    return {key: value for key, value in summary_payload.items() if key != "samples"}


def _build_confornets_design_payload(
    *,
    final_root: Path,
    structure_path: Path,
    manifest_entry: Optional[Dict[str, Any]],
    sample_entry: Optional[Dict[str, Any]],
    samples_json: Path,
    landscape_json: Path,
    provenance_json: Path,
    ensemble_manifest_json: Path,
    artifact_manifest_json: Path,
    request_json: Path,
    training_loss_csv: Path,
    landscape_payload: Any,
    provenance_payload: Any,
    artifact_manifest_payload: Any,
    request_payload: Any,
    training_loss_summary: Optional[Dict[str, Any]],
    confidence_entry: Optional[Dict[str, Any]],
    evaluation_entry: Optional[Dict[str, Any]],
    evaluation_summary: Optional[Dict[str, Any]],
    artifact_schema_version: int,
    job_context: Dict[str, Any],
    job_params: Dict[str, Any],
) -> Dict[str, Any]:
    design_name = structure_path.stem
    plddt, residue_plddt = extract_plddt_from_pdb(structure_path)
    if isinstance(confidence_entry, dict):
        computed_plddt = _coerce_float(confidence_entry.get("plddt"))
        if computed_plddt is not None:
            plddt = computed_plddt
    confornets_provenance: Dict[str, Any] = {
        **job_context.get("provenance", {}),
        "artifact_group": "confornets",
        "model_id": "confornets_experimental",
        "structure_path": str(structure_path),
        "final_root": str(final_root),
    }
    for key, path in {
        "samples_json": samples_json,
        "landscape_json": landscape_json,
        "provenance_json": provenance_json,
        "ensemble_manifest_json": ensemble_manifest_json,
        "artifact_manifest_json": artifact_manifest_json,
        "request_json": request_json,
        "training_loss_csv": training_loss_csv,
    }.items():
        if path.exists():
            confornets_provenance[key] = str(path)
    if isinstance(provenance_payload, dict):
        confornets_provenance["confornets_provenance"] = provenance_payload

    confidence_metrics: Dict[str, Any] = {
        "confornets_schema": _CONFORNETS_CONFIDENCE_SCHEMA,
        "confornets_reporting": dict(_CONFORNETS_REPORTING_SEMANTICS),
    }
    normalized_sample_entry: Dict[str, Any] = dict(sample_entry) if isinstance(sample_entry, dict) else {}
    sample_index = _confornets_sample_index(structure_path, manifest_entry, sample_entry)
    if sample_index is not None:
        normalized_sample_entry.setdefault("sample_index", sample_index)
        normalized_sample_entry.setdefault("frame_index", sample_index)
    if normalized_sample_entry:
        confidence_metrics["confornets_sample"] = normalized_sample_entry
    if isinstance(landscape_payload, dict):
        confidence_metrics["confornets_landscape"] = landscape_payload
    if isinstance(manifest_entry, dict):
        confidence_metrics["confornets_ensemble"] = manifest_entry
    if isinstance(artifact_manifest_payload, dict):
        confidence_metrics["confornets_artifact_manifest"] = artifact_manifest_payload
    if isinstance(request_payload, dict):
        confidence_metrics["confornets_request"] = request_payload
    if isinstance(training_loss_summary, dict):
        confidence_metrics["confornets_training_loss_summary"] = training_loss_summary
    if isinstance(confidence_entry, dict):
        confidence_metrics["confornets_confidence"] = confidence_entry
    if isinstance(evaluation_entry, dict):
        reference_evaluation = {
            key: value
            for key, value in evaluation_entry.items()
            if key not in {"pairwise_diversity", "landscape"}
        }
        if reference_evaluation:
            confidence_metrics["confornets_reference_evaluation"] = reference_evaluation
        if isinstance(evaluation_entry.get("pairwise_diversity"), dict):
            confidence_metrics["confornets_pairwise_diversity"] = evaluation_entry["pairwise_diversity"]
        if isinstance(evaluation_entry.get("landscape"), dict):
            confidence_metrics["confornets_landscape_point"] = evaluation_entry["landscape"]
    if isinstance(evaluation_summary, dict):
        confidence_metrics["confornets_evaluation_summary"] = evaluation_summary
    if job_params:
        confidence_metrics["confornets_params"] = {
            key: value for key, value in job_params.items() if str(key).startswith("cn_")
        }

    return {
        "name": design_name,
        "pdb_path": str(structure_path),
        "json_path": str(samples_json) if samples_json.exists() else (str(ensemble_manifest_json) if ensemble_manifest_json.exists() else None),
        "backbone_id": parse_backbone_id(design_name),
        "artifact_group": "confornets",
        "artifact_class": "conformer",
        "artifact_schema_version": artifact_schema_version,
        "stage_family": job_context.get("stage_family") or "confornets",
        "stage_mode": job_context.get("stage_mode"),
        "selected_loop_scope": job_context.get("selected_loop_scope"),
        "provenance": confornets_provenance,
        "plddt_overall": plddt,
        "residue_plddt": residue_plddt,
        "confidence_metrics": confidence_metrics or None,
    }


def _match_existing_confornets_design(
    existing_designs: List[Design],
    structure_path: Path,
    manifest_entry: Optional[Dict[str, Any]],
    sample_entry: Optional[Dict[str, Any]],
) -> Optional[Design]:
    candidate_names = {structure_path.stem}
    for entry in (manifest_entry, sample_entry):
        if not isinstance(entry, dict):
            continue
        for key in ("relative_path", "conformer_path", "path", "file", "filename"):
            raw_path = entry.get(key)
            if raw_path:
                candidate_names.add(Path(str(raw_path)).stem)

    sample_id = ""
    frame_index: Any = None
    for entry in (sample_entry, manifest_entry):
        if not isinstance(entry, dict):
            continue
        sample_id = sample_id or str(entry.get("sample_id") or entry.get("id") or "").strip()
        if frame_index is None:
            frame_index = entry.get("frame_index", entry.get("sample_index"))

    for design in existing_designs:
        if design.name in candidate_names or Path(str(design.pdb_path)).stem in candidate_names:
            return design
        raw_conf = design.confidence_metrics if isinstance(design.confidence_metrics, dict) else {}
        existing_sample = raw_conf.get("confornets_sample") if isinstance(raw_conf.get("confornets_sample"), dict) else {}
        if sample_id and str(existing_sample.get("sample_id") or existing_sample.get("id") or "").strip() == sample_id:
            return design
        if frame_index is not None and str(existing_sample.get("frame_index", existing_sample.get("sample_index"))) == str(frame_index):
            return design
    return None


async def ingest_confornets_results(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
    current_job: Optional[Job],
    *,
    commit: bool = True,
) -> int:
    """Ingest only final ConforNets conformers, not raw/work duplicate structures."""
    final_root = _resolve_confornets_final_root(output_path)
    if final_root is None:
        print(f"[Ingester] No final/confornets conformer directory found under {output_path}")
        return 0

    samples_json = final_root / "samples.json"
    landscape_json = final_root / "landscape.json"
    provenance_json = final_root / "provenance.json"
    ensemble_manifest_json = final_root / "ensemble_manifest.json"
    artifact_manifest_json = final_root / "artifact_manifest.json"
    request_json = final_root / "request.json"
    training_loss_csv = final_root / "confidence" / "training_loss.csv"
    samples_payload = _load_json_any(samples_json)
    landscape_payload = _load_json_any(landscape_json)
    provenance_payload = _load_json_any(provenance_json)
    ensemble_payload = _load_json_any(ensemble_manifest_json)
    artifact_manifest_payload = _load_json_any(artifact_manifest_json)
    request_payload = _load_json_any(request_json)
    confidence_summary_json = _confornets_sidecar_path(
        final_root,
        artifact_manifest_payload,
        "confidence_summary_json",
        "confidence/confidence_summary.json",
    )
    evaluation_summary_json = _confornets_sidecar_path(
        final_root,
        artifact_manifest_payload,
        "evaluation_summary_json",
        "evaluation/evaluation_summary.json",
    )
    confidence_summary_payload = _load_json_any(confidence_summary_json)
    evaluation_summary_payload = _load_json_any(evaluation_summary_json)
    training_loss_summary = _summarize_confornets_training_loss(training_loss_csv)
    artifact_schema_version = _confornets_artifact_schema_version(artifact_manifest_payload)
    samples_by_id = _confornets_samples_by_id(samples_payload)
    confidence_by_id = _confornets_summary_samples_by_id(confidence_summary_payload)
    evaluation_by_id = _confornets_summary_samples_by_id(evaluation_summary_payload)
    evaluation_summary = _strip_confornets_sample_list(evaluation_summary_payload)
    manifest_entries = _confornets_manifest_entries(ensemble_payload)

    candidate_records: List[tuple[Path, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]] = []
    seen_paths: set[Path] = set()
    for entry in manifest_entries:
        structure_path = _confornets_entry_path(final_root, entry)
        if structure_path is None or structure_path in seen_paths:
            continue
        sample_id = str(entry.get("sample_id") or entry.get("id") or structure_path.stem).strip()
        candidate_records.append((structure_path, entry, samples_by_id.get(sample_id)))
        seen_paths.add(structure_path)

    if not candidate_records:
        for structure_path in sorted(final_root.glob("conformers/*.pdb")) + sorted(final_root.glob("conformers/*.cif")) + sorted(final_root.glob("conformers/*.mmcif")):
            resolved = structure_path.resolve()
            if resolved in seen_paths:
                continue
            sample_entry = None
            for sample_id, entry in samples_by_id.items():
                raw_sample_path = str(entry.get("relative_path") or entry.get("conformer_path") or entry.get("path") or "")
                if raw_sample_path and Path(raw_sample_path).name == structure_path.name:
                    sample_entry = entry
                    break
                if sample_id and sample_id in structure_path.stem:
                    sample_entry = entry
                    break
            candidate_records.append((resolved, None, sample_entry))
            seen_paths.add(resolved)

    if not candidate_records:
        print(f"[Ingester] No final ConforNets conformer structures found in {final_root / 'conformers'}")
        return 0

    job_context = _job_stage_context(current_job)
    job_params = _parse_job_params(current_job.params) if current_job else {}
    existing_result = await session.execute(
        select(Design).where(
            Design.job_id == job_id,
            Design.source_stage.is_(None),
        )
    )
    existing_designs = list(existing_result.scalars().all())
    had_existing_designs = bool(existing_designs)
    processed_count = 0
    for structure_path, manifest_entry, sample_entry in candidate_records:
        confidence_entry = _lookup_confornets_summary_entry(
            confidence_by_id,
            structure_path,
            manifest_entry,
            sample_entry,
        )
        evaluation_entry = _lookup_confornets_summary_entry(
            evaluation_by_id,
            structure_path,
            manifest_entry,
            sample_entry,
        )
        payload = _build_confornets_design_payload(
            final_root=final_root,
            structure_path=structure_path,
            manifest_entry=manifest_entry,
            sample_entry=sample_entry,
            samples_json=samples_json,
            landscape_json=landscape_json,
            provenance_json=provenance_json,
            ensemble_manifest_json=ensemble_manifest_json,
            artifact_manifest_json=artifact_manifest_json,
            request_json=request_json,
            training_loss_csv=training_loss_csv,
            landscape_payload=landscape_payload,
            provenance_payload=provenance_payload,
            artifact_manifest_payload=artifact_manifest_payload,
            request_payload=request_payload,
            training_loss_summary=training_loss_summary,
            confidence_entry=confidence_entry,
            evaluation_entry=evaluation_entry,
            evaluation_summary=evaluation_summary,
            artifact_schema_version=artifact_schema_version,
            job_context=job_context,
            job_params=job_params,
        )
        existing_design = _match_existing_confornets_design(existing_designs, structure_path, manifest_entry, sample_entry)
        if existing_design is not None:
            incoming_conf = payload.get("confidence_metrics")
            existing_design.name = payload["name"]
            existing_design.pdb_path = payload["pdb_path"]
            existing_design.json_path = payload["json_path"]
            existing_design.backbone_id = payload["backbone_id"]
            existing_design.artifact_group = payload["artifact_group"]
            existing_design.artifact_class = payload["artifact_class"]
            existing_design.artifact_schema_version = payload["artifact_schema_version"]
            existing_design.stage_family = payload["stage_family"]
            existing_design.stage_mode = payload["stage_mode"]
            existing_design.selected_loop_scope = payload["selected_loop_scope"]
            existing_design.provenance = _merge_confornets_metric_payload(existing_design.provenance, payload["provenance"])
            if payload.get("plddt_overall") is not None:
                existing_design.plddt_overall = payload["plddt_overall"]
            if payload.get("residue_plddt"):
                existing_design.residue_plddt = payload["residue_plddt"]
            existing_design.confidence_metrics = _merge_confornets_metric_payload(
                existing_design.confidence_metrics,
                incoming_conf if isinstance(incoming_conf, dict) else {},
            )
            processed_count += 1
            continue

        design = Design(
            id=str(uuid.uuid4()),
            job_id=job_id,
            **payload,
            is_favorite=False,
            created_at=datetime.utcnow(),
        )
        session.add(design)
        existing_designs.append(design)
        processed_count += 1

    try:
        if commit:
            await session.commit()
        action = "Updated" if had_existing_designs else "Ingested"
        print(f"[Ingester] {action} {processed_count} final ConforNets conformers for job {job_id}")
    except Exception as exc:
        print(f"[Ingester] Error committing ConforNets designs: {exc}")
        await session.rollback()
        return 0
    return processed_count


class ShapeNoCandidates(RuntimeError):
    """A scientifically successful Shape run with declared zero candidate yield."""

    integrity_state = "no_candidates"

    def __init__(self, reason: Dict[str, Any]):
        self.reason = reason
        super().__init__(str(reason.get("message") or reason.get("code") or "Shape produced no candidates"))


def _shape_contained_artifact(output_root: Path, descriptor: Dict[str, Any], label: str) -> Path:
    relative = descriptor.get("relative_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise RuntimeError(f"Shape {label} path must be a non-empty relative path")
    lexical = output_root / relative
    current = output_root
    for part in Path(relative).parts:
        if part in {"", ".", ".."}:
            raise RuntimeError(f"Shape {label} path is unsafe")
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"Shape {label} path contains a symlink")
    resolved = lexical.resolve()
    if not resolved.is_relative_to(output_root.resolve()) or not resolved.is_file():
        raise RuntimeError(f"Shape {label} is missing or outside the job output root")
    stat = resolved.stat()
    if stat.st_nlink != 1:
        raise RuntimeError(f"Shape {label} must not be hard-linked")
    expected_bytes = descriptor.get("bytes")
    if not isinstance(expected_bytes, int) or expected_bytes != stat.st_size:
        raise RuntimeError(f"Shape {label} byte count mismatch")
    expected_sha = descriptor.get("sha256")
    actual_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if not isinstance(expected_sha, str) or expected_sha != actual_sha:
        raise RuntimeError(f"Shape {label} SHA-256 mismatch")
    return resolved


async def _ingest_shape_result_manifest(
    job: Job,
    output_root: Path,
    session: AsyncSession,
    *,
    commit: bool,
) -> int:
    manifest_path = output_root / "results" / "shape_result_manifest.json"
    current = output_root
    for part in ("results", "shape_result_manifest.json"):
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Shape result manifest path contains a symlink")
    resolved_manifest = manifest_path.resolve()
    if (
        not resolved_manifest.is_relative_to(output_root.resolve())
        or not resolved_manifest.is_file()
        or resolved_manifest.stat().st_nlink != 1
    ):
        raise RuntimeError("Shape result manifest is absent or unsafe")
    try:
        manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Shape result manifest is malformed: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "bms_shape_result_v1":
        raise RuntimeError("Shape result manifest schema is invalid")

    request = (
        await session.execute(select(ShapeDesignRequest).where(ShapeDesignRequest.job_id == str(job.id)))
    ).scalar_one_or_none()
    if request is None:
        raise RuntimeError("Shape job has no immutable ShapeDesignRequest")
    request_spec = dict(request.request_spec or {})
    declared_request_sha = request_spec.pop("request_sha256", None)
    computed_request_sha = hashlib.sha256(
        json.dumps(request_spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    if declared_request_sha != request.request_sha256 or computed_request_sha != request.request_sha256:
        raise RuntimeError("canonical Shape request SHA-256 is invalid")
    geometry = await session.get(ShapeDesignGeometry, request.geometry_id)
    if geometry is None:
        raise RuntimeError("Shape request geometry is absent")
    geometry_manifest = dict(geometry.manifest or {})
    bindings = {
        "job_id": str(job.id),
        "request_id": request.request_id,
        "request_sha256": request.request_sha256,
        "geometry_id": geometry.geometry_id,
        "geometry_sha256": geometry.geometry_sha256,
        "point_pool_sha256": geometry_manifest.get("point_pool_sha256"),
        "sdf_sha256": geometry_manifest.get("sdf_sha256"),
        "sdf_sign": geometry_manifest.get("sdf_sign"),
    }
    for key in ("geometry_id", "geometry_sha256", "point_pool_sha256", "sdf_sha256", "sdf_sign"):
        if request_spec.get(key) != bindings[key]:
            raise RuntimeError(f"canonical Shape request {key} binding mismatch")
    if bindings["sdf_sign"] != "positive_inside":
        raise RuntimeError("canonical Shape SDF convention is invalid")
    for key, expected in bindings.items():
        if not expected or manifest.get(key) != expected:
            raise RuntimeError(f"Shape result manifest {key} binding mismatch")
    aggregate_descriptor = manifest.get("rfd3_aggregate")
    aggregate_payload: dict[str, Any] | None = None
    if aggregate_descriptor is not None:
        aggregate_path = _shape_contained_artifact(output_root, aggregate_descriptor, "RFD3 aggregate manifest")
        try:
            aggregate_payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("RFD3 aggregate manifest is malformed") from exc
        if not isinstance(aggregate_payload, dict):
            raise RuntimeError("RFD3 aggregate manifest is not an object")
        aggregate_unsigned = dict(aggregate_payload)
        claimed_aggregate_sha = aggregate_unsigned.pop("aggregate_sha256", None)
        aggregate_hash = hashlib.sha256(
            json.dumps(aggregate_unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        if (
            aggregate_payload.get("schema") != "bms_rfd3_aggregate_manifest_v1"
            or aggregate_payload.get("request_sha256") != request.request_sha256
            or not isinstance(claimed_aggregate_sha, str)
            or aggregate_hash != claimed_aggregate_sha
        ):
            raise RuntimeError("RFD3 aggregate manifest binding is invalid")

    candidates = manifest.get("candidates")
    outcome = manifest.get("outcome")
    if not isinstance(candidates, list) or manifest.get("candidate_count") != len(candidates):
        raise RuntimeError("Shape result manifest candidate count mismatch")
    if outcome == "no_candidates":
        reason = manifest.get("reason")
        if candidates or not isinstance(reason, dict) or not reason.get("code"):
            raise RuntimeError("Shape no_candidates result lacks an explicit reason")
        raise ShapeNoCandidates(reason)
    if outcome != "candidates" or not candidates:
        raise RuntimeError("Shape candidate result must contain at least one candidate")

    created = 0
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise RuntimeError("Shape candidate entry must be an object")
        candidate_id = item.get("candidate_id")
        name = item.get("name")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen_ids:
            raise RuntimeError("Shape candidate IDs must be unique non-empty strings")
        if not isinstance(name, str) or not name or name in seen_names or name != candidate_id:
            raise RuntimeError("Shape candidate names must uniquely equal candidate IDs")
        seen_ids.add(candidate_id)
        seen_names.add(name)
        structure = _shape_contained_artifact(output_root, item.get("structure") or {}, f"{candidate_id} structure")
        source_backbone = _shape_contained_artifact(output_root, item.get("source_backbone") or {}, f"{candidate_id} source backbone")
        metrics_path = _shape_contained_artifact(output_root, item.get("metrics") or {}, f"{candidate_id} metrics")
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Shape candidate metrics are malformed: {candidate_id}") from exc
        if not isinstance(metrics, dict) or metrics.get("schema") != "bms_shape_candidate_metrics_v1":
            raise RuntimeError(f"Shape candidate metrics schema is invalid: {candidate_id}")
        metric_bindings = {
            "candidate_id": candidate_id,
            "geometry_sha256": bindings["geometry_sha256"],
            "point_pool_sha256": bindings["point_pool_sha256"],
            "sdf_sha256": bindings["sdf_sha256"],
            "source_backbone_sha256": item["source_backbone"]["sha256"],
        }
        for key, expected in metric_bindings.items():
            if metrics.get(key) != expected:
                raise RuntimeError(f"Shape candidate metrics {key} binding mismatch: {candidate_id}")
        existing = (
            await session.execute(select(Design).where(Design.job_id == str(job.id), Design.name == name))
        ).scalar_one_or_none()
        if existing is not None:
            expected_manifest = {
                "structure": dict(item["structure"]),
                "source_backbone": dict(item["source_backbone"]),
                "metrics": dict(item["metrics"]),
            }
            stored_manifest = existing.review_artifact_manifest
            role_map = existing.review_role_map if isinstance(existing.review_role_map, dict) else {}

            def normalized_artifact(
                descriptor: dict[str, Any], *, kind: str, path: str
            ) -> dict[str, Any]:
                return {
                    "kind": kind,
                    "state": "ready",
                    "path": path,
                    "reason": None,
                    **{
                        key: descriptor[key]
                        for key in ("sha256", "bytes", "format", "relative_path")
                        if key in descriptor
                    },
                }

            expected_stored_manifest = {
                "schema": REVIEW_ARTIFACT_SCHEMA,
                "artifacts": {
                    "structure": normalized_artifact(
                        expected_manifest["structure"], kind="structure", path=str(existing.pdb_path)
                    ),
                    "source_backbone": normalized_artifact(
                        expected_manifest["source_backbone"],
                        kind="source_backbone",
                        path=str(existing.source_pdb_path),
                    ),
                    "metrics": normalized_artifact(
                        expected_manifest["metrics"], kind="shape_metrics", path=str(existing.json_path)
                    ),
                },
                "roles": {**role_map, "has_binder": False},
            }
            if (
                Path(existing.pdb_path).resolve() != structure
                or Path(existing.source_pdb_path or "").resolve() != source_backbone
                or Path(existing.json_path or "").resolve() != metrics_path
                or stored_manifest != expected_stored_manifest
            ):
                raise RuntimeError(f"Shape candidate conflicts with an existing Design: {name}")
            continue
        producer_provenance = item.get("provenance") or {}
        if not isinstance(producer_provenance, dict):
            raise RuntimeError(f"Shape candidate provenance is invalid: {candidate_id}")
        if aggregate_descriptor is not None:
            producer_provenance = {
                **producer_provenance,
                "rfd3_aggregate": dict(aggregate_descriptor),
                "rfd3_aggregate_status": aggregate_payload.get("status") if aggregate_payload else None,
            }
        design = Design(
            id=str(uuid.uuid4()),
            job_id=str(job.id),
            name=name,
            pdb_path=str(structure),
            source_pdb_path=str(source_backbone),
            json_path=str(metrics_path),
            lineage_root_job_id=str(job.id),
            stage_family="shape_blueprint",
            stage_mode="shape_blueprint",
            source_stage_job_id=str(job.id),
            source_stage_family="shape_blueprint",
            source_stage_mode="shape_blueprint",
            artifact_class="shape_candidate",
            artifact_schema_version=1,
            review_profile_id="shape_blueprint",
            review_contract_version=1,
            review_contract_source="producer",
            review_artifact_manifest={
                "structure": dict(item["structure"]),
                "source_backbone": dict(item["source_backbone"]),
                "metrics": dict(item["metrics"]),
            },
            review_role_map={"designed_structure": item["structure"]["relative_path"]},
            provenance={
                **producer_provenance,
                **bindings,
                "candidate_id": candidate_id,
                "shape_result_manifest": str(resolved_manifest),
            },
            plddt_overall=metrics.get("plddt_overall"),
            confidence_metrics=metrics,
        )
        session.add(design)
        created += 1
    await session.flush()
    persisted = (
        await session.execute(
            select(Design).where(
                Design.job_id == str(job.id),
                Design.stage_family == "shape_blueprint",
            )
        )
    ).scalars().all()
    persisted_ids = [str((row.provenance or {}).get("candidate_id") or "") for row in persisted]
    if len(persisted_ids) != len(seen_ids) or set(persisted_ids) != seen_ids:
        raise RuntimeError("persisted Shape Designs do not exactly match the terminal manifest")
    if commit:
        await session.commit()
    return created


def _local_redesign_canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _local_redesign_safe_job_artifact(output_root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in relative_path
    ):
        raise RuntimeError("RFD3 local-redesign artifact path is unsafe")
    current = output_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("RFD3 local-redesign artifact path contains a symlink")
    resolved = current.resolve()
    if not resolved.is_relative_to(output_root.resolve()) or not resolved.is_file():
        raise RuntimeError("RFD3 local-redesign artifact is missing or outside the job output root")
    if resolved.stat().st_nlink != 1:
        raise RuntimeError("RFD3 local-redesign artifact must not be hard-linked")
    return resolved


def _local_redesign_external_source(path_value: str) -> Path:
    source = resolve_runtime_data_path(path_value)
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("RFD3 local-redesign source structure is missing or unsafe")
    return source.resolve()


def _local_redesign_validate_artifact(
    descriptor: Mapping[str, Any],
    *,
    output_root: Path,
    source_path: Path,
) -> Path:
    role = str(descriptor.get("role") or "").strip()
    relative = descriptor.get("relative_path")
    storage = descriptor.get("storage_path")
    expected_sha = descriptor.get("sha256")
    expected_bytes = descriptor.get("bytes")
    if not role or not isinstance(relative, str) or not isinstance(storage, str):
        raise RuntimeError("RFD3 local-redesign artifact descriptor is incomplete")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise RuntimeError(f"RFD3 local-redesign artifact has an invalid SHA-256: {role}")
    if not isinstance(expected_bytes, int) or expected_bytes < 0:
        raise RuntimeError(f"RFD3 local-redesign artifact has an invalid byte count: {role}")
    if role == "source_structure":
        resolved = _local_redesign_external_source(storage)
        if resolved != source_path:
            raise RuntimeError("RFD3 local-redesign source artifact path does not match the request")
    else:
        resolved = _local_redesign_safe_job_artifact(output_root, relative)
        if resolved != Path(storage).expanduser().resolve():
            raise RuntimeError(f"RFD3 local-redesign artifact storage path mismatch: {role}")
    actual_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if actual_sha != expected_sha or resolved.stat().st_size != expected_bytes:
        raise RuntimeError(f"RFD3 local-redesign artifact identity mismatch: {role}")
    return resolved


async def _ingest_rfd3_local_redesign_manifest(
    job: Job,
    output_root: Path,
    session: AsyncSession,
    *,
    commit: bool,
) -> int:
    manifest_path = output_root / "collected" / "protein_local_redesign" / "rfd3_result_manifest.json"
    manifest = None
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RuntimeError("RFD3 local-redesign result manifest is absent or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"RFD3 local-redesign result manifest is malformed: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "bms.rfd3.local-redesign.result.v1":
        raise RuntimeError("RFD3 local-redesign result manifest schema is invalid")
    unsigned = dict(manifest)
    claimed_manifest_sha = unsigned.pop("manifest_sha256", None)
    if not isinstance(claimed_manifest_sha, str) or _local_redesign_canonical_sha(unsigned) != claimed_manifest_sha:
        raise RuntimeError("RFD3 local-redesign result manifest SHA-256 is invalid")

    request = (
        await session.execute(select(RFD3LocalRedesignRequest).where(RFD3LocalRedesignRequest.job_id == str(job.id)))
    ).scalar_one_or_none()
    if request is None:
        raise RuntimeError("RFD3 local-redesign job has no immutable request row")
    request_payload = request.request_json
    if not isinstance(request_payload, dict):
        raise RuntimeError("RFD3 local-redesign request JSON is invalid")
    if request.request_sha256 != _local_redesign_canonical_sha(request_payload):
        raise RuntimeError("RFD3 local-redesign request SHA-256 is invalid")
    if request.profile_registry_sha256 != request_payload.get("profile_registry_sha256"):
        raise RuntimeError("RFD3 local-redesign profile registry hash is invalid")
    if manifest.get("request_sha256") != request.request_sha256:
        raise RuntimeError("RFD3 local-redesign result does not bind to the immutable request")
    if manifest.get("result_contract_id") != "rfd3_local_redesign_v1":
        raise RuntimeError("RFD3 local-redesign result contract is unsupported")
    if (
        manifest.get("profile_id") != request_payload.get("profile_id")
        or manifest.get("profile_registry_sha256") != request_payload.get("profile_registry_sha256")
        or manifest.get("profile") != request_payload.get("profile")
    ):
        raise RuntimeError("RFD3 local-redesign result profile binding is invalid")
    semantic_bindings = {
        "request_schema": "schema",
        "redesign_mode": "redesign_mode",
        "contig_dialect": "contig_dialect",
        "sequence_policy": "sequence_policy",
        "input": "input",
        "rfd3": "rfd3",
        "execution": "execution",
        "evaluation": "evaluation",
    }
    for manifest_key, request_key in semantic_bindings.items():
        if manifest.get(manifest_key) != request_payload.get(request_key):
            raise RuntimeError(f"RFD3 local-redesign manifest {manifest_key} binding is invalid")
    source_binding = request_payload.get("input")
    if not isinstance(source_binding, dict) or not isinstance(source_binding.get("path"), str):
        raise RuntimeError("RFD3 local-redesign request has no source input")
    source_request = source_binding["path"]
    source_path = _local_redesign_external_source(source_request)
    if source_binding.get("sha256") != hashlib.sha256(source_path.read_bytes()).hexdigest():
        raise RuntimeError("RFD3 local-redesign source hash is invalid")

    manifest_artifacts = manifest.get("artifacts")
    candidates = manifest.get("candidates")
    if not isinstance(manifest_artifacts, list) or not isinstance(candidates, list) or not candidates:
        raise RuntimeError("RFD3 local-redesign result lacks declared artifacts or candidates")
    descriptor_by_path: dict[str, dict[str, Any]] = {}
    for descriptor in manifest_artifacts:
        if not isinstance(descriptor, dict):
            raise RuntimeError("RFD3 local-redesign artifact entry is malformed")
        relative_path = descriptor.get("relative_path")
        if not isinstance(relative_path, str) or relative_path in descriptor_by_path:
            raise RuntimeError("RFD3 local-redesign artifact paths must be unique")
        _local_redesign_validate_artifact(descriptor, output_root=output_root, source_path=source_path)
        descriptor_by_path[relative_path] = descriptor

    role_counts: dict[str, int] = {}
    for descriptor in descriptor_by_path.values():
        role = str(descriptor.get("role") or "")
        role_counts[role] = role_counts.get(role, 0) + 1
    required_roles = {
        "source_structure",
        "native_request",
        "preparation_receipt",
        "native_producer_input",
        "producer_log",
        "producer_metadata_index",
    }
    if any(role_counts.get(role) != 1 for role in required_roles):
        raise RuntimeError("RFD3 local-redesign result lacks required source or runtime evidence")

    execution = request_payload.get("execution")
    execution_evidence = manifest.get("execution_evidence")
    if not isinstance(execution, dict) or not isinstance(execution_evidence, dict):
        raise RuntimeError("RFD3 local-redesign result lacks execution evidence")
    requested_num_designs = execution.get("num_designs")
    if (
        not isinstance(requested_num_designs, int)
        or execution_evidence.get("requested_num_designs") != requested_num_designs
        or execution_evidence.get("observed_num_designs") != len(candidates)
        or execution_evidence.get("candidate_count_integrity") != "exact"
        or len(candidates) != requested_num_designs
    ):
        raise RuntimeError("RFD3 local-redesign candidate count integrity is invalid")
    trajectories_requested = execution.get("dump_trajectories") is True
    expected_trajectory_state = "produced" if trajectories_requested else "not_requested"
    if execution_evidence.get("trajectories") != expected_trajectory_state:
        raise RuntimeError("RFD3 local-redesign trajectory evidence is invalid")
    if request_payload.get("sequence_policy") == "skip" and execution_evidence.get("sequence_design") != "not_requested":
        raise RuntimeError("RFD3 local-redesign sequence skip evidence is invalid")
    expected_role_counts = {role: 1 for role in required_roles}
    expected_role_counts.update(
        {
            "structure": requested_num_designs,
            "native_prediction_metadata": requested_num_designs,
            "denoised_trajectory": requested_num_designs if trajectories_requested else 0,
            "noisy_trajectory": requested_num_designs if trajectories_requested else 0,
        }
    )
    if role_counts != {role: count for role, count in expected_role_counts.items() if count}:
        raise RuntimeError("RFD3 local-redesign artifact role cardinality is invalid")

    seen_candidates: set[str] = set()
    seen_artifacts: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise RuntimeError("RFD3 local-redesign candidate entry is malformed")
        candidate_id = candidate.get("candidate_id")
        candidate_artifacts = candidate.get("artifacts")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen_candidates:
            raise RuntimeError("RFD3 local-redesign candidate IDs must be unique")
        if not isinstance(candidate_artifacts, list) or not candidate_artifacts:
            raise RuntimeError(f"RFD3 local-redesign candidate lacks artifacts: {candidate_id}")
        if candidate.get("status") != "generated" or candidate.get("result_set") != "rfd3_local_redesign_candidates":
            raise RuntimeError(f"RFD3 local-redesign candidate semantics are invalid: {candidate_id}")
        candidate_artifact_sha = _local_redesign_canonical_sha(candidate_artifacts)
        if candidate.get("artifact_manifest_sha256") != candidate_artifact_sha:
            raise RuntimeError(f"RFD3 local-redesign candidate artifact hash is invalid: {candidate_id}")
        candidate_roles = {str(item.get("role") or "") for item in candidate_artifacts if isinstance(item, dict)}
        trajectory_roles = {"denoised_trajectory", "noisy_trajectory"}
        expected_candidate_roles = {"structure", "native_prediction_metadata"}
        if trajectories_requested:
            expected_candidate_roles.update(trajectory_roles)
        if candidate_roles != expected_candidate_roles or len(candidate_artifacts) != len(expected_candidate_roles):
            raise RuntimeError(f"RFD3 local-redesign candidate artifact roles are invalid: {candidate_id}")
        for descriptor in candidate_artifacts:
            if not isinstance(descriptor, dict):
                raise RuntimeError(f"RFD3 local-redesign candidate artifact is malformed: {candidate_id}")
            relative_path = descriptor.get("relative_path")
            if relative_path not in descriptor_by_path or descriptor_by_path[relative_path] != descriptor:
                raise RuntimeError(f"RFD3 local-redesign candidate artifact is undeclared: {candidate_id}")
            if str(relative_path) in seen_artifacts:
                raise RuntimeError(f"RFD3 local-redesign candidate artifact is reused: {candidate_id}")
            seen_artifacts.add(str(relative_path))
        native_metadata_descriptor = next(
            item for item in candidate_artifacts if item.get("role") == "native_prediction_metadata"
        )
        native_metadata_path = _local_redesign_safe_job_artifact(
            output_root, str(native_metadata_descriptor["relative_path"])
        )
        try:
            native_metadata = json.loads(native_metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"RFD3 local-redesign native metadata is malformed: {candidate_id}") from exc
        if not isinstance(native_metadata, dict):
            raise RuntimeError(f"RFD3 local-redesign native metadata is not an object: {candidate_id}")
        metric_keys = {
            "summary_confidences",
            "ca_rmsd_to_input",
            "backbone_rmsd",
            "insertion_rmsd",
            "diffused_index_map",
            "hbond_metrics",
            "inference_metadata",
        }
        expected_metrics = {key: native_metadata[key] for key in metric_keys if key in native_metadata}
        if candidate.get("metrics") != expected_metrics:
            raise RuntimeError(f"RFD3 local-redesign candidate metrics do not match native metadata: {candidate_id}")
        seen_candidates.add(candidate_id)

    candidate_roles_all = {
        "structure",
        "native_prediction_metadata",
        "denoised_trajectory",
        "noisy_trajectory",
    }
    declared_candidate_artifacts = {
        relative_path
        for relative_path, descriptor in descriptor_by_path.items()
        if descriptor.get("role") in candidate_roles_all
    }
    if seen_artifacts != declared_candidate_artifacts:
        raise RuntimeError("RFD3 local-redesign candidate artifact assignment is incomplete")

    runtime_records = [
        candidate.get("metrics", {}).get("inference_metadata")
        for candidate in candidates
        if isinstance(candidate.get("metrics"), dict) and candidate.get("metrics", {}).get("inference_metadata") is not None
    ]
    if runtime_records:
        request.runtime_identity_json = {
            "source": "native_rfd3_prediction_metadata",
            "records": runtime_records,
        }

    receipt_descriptors = [
        descriptor for descriptor in descriptor_by_path.values() if descriptor.get("role") == "preparation_receipt"
    ]
    if len(receipt_descriptors) != 1:
        raise RuntimeError("RFD3 local-redesign preparation receipt descriptor is invalid")
    receipt_descriptor = receipt_descriptors[0]
    receipt_relative_path = str(receipt_descriptor.get("relative_path") or "")
    if receipt_relative_path != "collected/protein_local_redesign/rfd3_preparation_receipt.json":
        raise RuntimeError("RFD3 local-redesign preparation receipt path is invalid")
    receipt_path = _local_redesign_safe_job_artifact(output_root, receipt_relative_path)
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise RuntimeError("RFD3 local-redesign preparation receipt is unavailable")
    else:
        try:
            preparation_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("RFD3 local-redesign preparation receipt is malformed") from exc
        if (
            not isinstance(preparation_receipt, dict)
            or preparation_receipt.get("schema") != "bms.rfd3.local-redesign.preparation-receipt.v1"
            or preparation_receipt.get("request_sha256") != request.request_sha256
        ):
            raise RuntimeError("RFD3 local-redesign preparation receipt binding is invalid")
        design_id = preparation_receipt.get("design_id")
        native_input_descriptors = [
            descriptor
            for descriptor in descriptor_by_path.values()
            if descriptor.get("role") == "native_producer_input"
        ]
        if len(native_input_descriptors) != 1:
            raise RuntimeError("RFD3 local-redesign native producer input descriptor is invalid")
        native_input_relative_path = str(native_input_descriptors[0].get("relative_path") or "")
        if native_input_relative_path != "collected/protein_local_redesign/rfd3_input_protein_local_redesign_0.json":
            raise RuntimeError("RFD3 local-redesign native producer input path is invalid")
        native_input_path = _local_redesign_safe_job_artifact(output_root, native_input_relative_path)
        try:
            native_input_payload = json.loads(native_input_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("RFD3 local-redesign native producer input is malformed") from exc
        receipt_native = preparation_receipt.get("native_rfd3")
        native_runtime_input = receipt_native.get("input") if isinstance(receipt_native, dict) else None
        expected_native = dict(request_payload.get("rfd3") or {})
        expected_native["input"] = native_runtime_input
        runtime_input = preparation_receipt.get("runtime_input")
        sequence_design = preparation_receipt.get("sequence_design")
        if (
            not isinstance(design_id, str)
            or not design_id
            or not isinstance(native_runtime_input, str)
            or Path(native_runtime_input).name != source_path.name
            or receipt_native != expected_native
            or native_input_payload != {design_id: expected_native}
            or preparation_receipt.get("native_input_sha256")
            != _local_redesign_canonical_sha(native_input_payload)
            or not isinstance(runtime_input, dict)
            or runtime_input.get("path") != source_path.name
            or runtime_input.get("sha256") != source_binding.get("sha256")
            or preparation_receipt.get("redesign_mode") != request_payload.get("redesign_mode")
            or preparation_receipt.get("sequence_policy") != request_payload.get("sequence_policy")
            or not isinstance(sequence_design, dict)
            or sequence_design.get("state")
            != ("not_requested" if request_payload.get("sequence_policy") == "skip" else "requested")
        ):
            raise RuntimeError("RFD3 local-redesign preparation receipt semantics are invalid")
        request.preparation_receipt_json = preparation_receipt

    request.result_manifest_sha256 = claimed_manifest_sha
    request.status = "completed"
    request.updated_at = datetime.utcnow()
    request.terminal_at = datetime.utcnow()

    manifest_descriptor = {
        "role": "rfd3_result_manifest",
        "relative_path": "collected/protein_local_redesign/rfd3_result_manifest.json",
        "storage_path": str(manifest_path.resolve()),
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "bytes": manifest_path.stat().st_size,
        "media_type": "application/json",
    }
    all_descriptors = [*descriptor_by_path.values(), manifest_descriptor]
    for descriptor in all_descriptors:
        relative_path = str(descriptor["relative_path"])
        artifact_id = hashlib.sha256(f"{request.request_id}:{relative_path}".encode("utf-8")).hexdigest()
        candidate_id = None
        for candidate in candidates:
            if any(item.get("relative_path") == relative_path for item in candidate.get("artifacts", [])):
                candidate_id = candidate.get("candidate_id")
                break
        existing = (
            await session.execute(
                select(RFD3LocalRedesignArtifact).where(
                    RFD3LocalRedesignArtifact.request_id == request.request_id,
                    RFD3LocalRedesignArtifact.relative_path == relative_path,
                )
            )
        ).scalar_one_or_none()
        values = {
            "artifact_id": artifact_id,
            "request_id": request.request_id,
            "candidate_id": candidate_id,
            "role": descriptor["role"],
            "relative_path": relative_path,
            "storage_path": descriptor["storage_path"],
            "content_sha256": descriptor["sha256"],
            "size_bytes": descriptor["bytes"],
            "media_type": descriptor.get("media_type") or "application/octet-stream",
            "metadata_json": {"request_sha256": request.request_sha256},
        }
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items() if key not in {"artifact_id", "metadata_json"}):
                raise RuntimeError(f"RFD3 local-redesign artifact replay conflicts: {relative_path}")
        else:
            session.add(RFD3LocalRedesignArtifact(**values))

    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        row_id = hashlib.sha256(f"{request.request_id}:{candidate_id}".encode("utf-8")).hexdigest()
        values = {
            "id": row_id,
            "request_id": request.request_id,
            "candidate_id": candidate_id,
            "result_set": candidate.get("result_set") or "rfd3_local_redesign_candidates",
            "stage": "backbone",
            "status": candidate.get("status") or "generated",
            "artifact_manifest_sha256": candidate["artifact_manifest_sha256"],
            "metrics_json": candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {},
            "metadata_json": {
                "request_sha256": request.request_sha256,
                "profile_id": manifest.get("profile_id"),
                "redesign_mode": manifest.get("redesign_mode"),
            },
        }
        existing = (
            await session.execute(
                select(RFD3LocalRedesignCandidate).where(
                    RFD3LocalRedesignCandidate.request_id == request.request_id,
                    RFD3LocalRedesignCandidate.candidate_id == candidate_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items() if key not in {"id", "created_at"}):
                raise RuntimeError(f"RFD3 local-redesign candidate replay conflicts: {candidate_id}")
        else:
            session.add(RFD3LocalRedesignCandidate(**values))

    await session.flush()
    if commit:
        await session.commit()
    return len(candidates)


_FRUSTRAMPNN_TERMINAL_STAGES = frozenset({"frustrampnn", "canonical_frustrampnn"})
_FRUSTRAMPNN_TERMINAL_RESULT = "workflow_component_result_v1.json"
_FRUSTRAMPNN_TERMINAL_RESULTS = frozenset(
    {_FRUSTRAMPNN_TERMINAL_RESULT, "workflow_component_result_v2.json"}
)
_FRUSTRAMPNN_RESULT_MANIFESTS = frozenset({MANIFEST_PATH, V2_MANIFEST_PATH})
_FRUSTRAMPNN_RESULT_PAIRS = {
    MANIFEST_PATH: _FRUSTRAMPNN_TERMINAL_RESULT,
    V2_MANIFEST_PATH: "workflow_component_result_v2.json",
}


def _explicit_stage_paths(value: Any) -> list[str]:
    """Flatten only values persisted under an explicit FrustraMPNN stage key."""

    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, (list, tuple)):
        paths: list[str] = []
        for item in value:
            paths.extend(_explicit_stage_paths(item))
        return paths
    if isinstance(value, dict):
        paths = []
        for item in value.values():
            paths.extend(_explicit_stage_paths(item))
        return paths
    return []


def _open_absolute_no_symlinks(path: Path, *, directory: bool | None) -> int:
    """Open an absolute path component-by-component without following symlinks."""

    absolute = Path(os.path.abspath(path))
    parts = absolute.parts[1:]
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(parts):
            is_leaf = index == len(parts) - 1
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            if not is_leaf or directory is True:
                flags |= os.O_DIRECTORY
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        expected = (
            stat.S_ISDIR(metadata.st_mode)
            if directory is True
            else stat.S_ISREG(metadata.st_mode)
            if directory is False
            else stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        )
        if not expected:
            raise OSError("stage authority path has the wrong physical type")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _stage_path(path: str, output_path: Path) -> Path:
    job_root = output_path.absolute()
    candidate = Path(path)
    candidate = candidate.absolute() if candidate.is_absolute() else (job_root / candidate).absolute()
    try:
        candidate.relative_to(job_root)
    except ValueError as exc:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN terminal stage output escapes the exact job root"
        ) from exc
    descriptor = -1
    try:
        descriptor = _open_absolute_no_symlinks(candidate, directory=None)
    except OSError as exc:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN terminal stage output is missing, unsafe, or traverses a symlink"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return candidate


def _read_explicit_terminal_envelope(
    bundle_root: Path, terminal_result_name: str = _FRUSTRAMPNN_TERMINAL_RESULT
) -> dict[str, Any]:
    """Read the exact terminal child without following root or leaf symlinks."""

    if terminal_result_name not in _FRUSTRAMPNN_TERMINAL_RESULTS:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN explicit terminal result generation is unsupported"
        )

    leaf_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    root_fd = -1
    terminal_fd = -1
    try:
        root_fd = _open_absolute_no_symlinks(bundle_root, directory=True)
        terminal_fd = os.open(terminal_result_name, leaf_flags, dir_fd=root_fd)
        metadata = os.fstat(terminal_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN explicit terminal result is not a regular file"
            )
        with os.fdopen(terminal_fd, "rb", closefd=True) as handle:
            terminal_fd = -1
            payload = handle.read()
    except OSError as exc:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN explicit terminal result is missing or unsafe"
        ) from exc
    finally:
        if terminal_fd >= 0:
            os.close(terminal_fd)
        if root_fd >= 0:
            os.close(root_fd)
    try:
        terminal = canonical_json_loads(payload)
    except Exception as exc:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN explicit terminal result is invalid JSON"
        ) from exc
    if not isinstance(terminal, dict) or canonical_json_bytes(terminal) != payload:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN explicit terminal result is not canonical JSON"
        )
    return terminal


_PROTEIN_DESIGN_INTEGER_PROJECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("num_helices", ("pr_helices",)),
    ("num_strands", ("pr_strands",)),
)
_PROTEIN_DESIGN_FLOAT_PROJECTIONS: tuple[
    tuple[str, tuple[str, ...], bool], ...
] = (
    ("rog", ("pr_RoG",), False),
    ("rfd_rog", ("rfd_RoG",), False),
    ("mpnn_score", ("seq_mpnn_score",), True),
    ("fampnn_psce", ("seq_fampnn_psce",), False),
    ("plddt_overall", ("pr_plddt", "plddt"), False),
    ("plddt_binder", ("pr_plddt_binder",), False),
    ("plddt_target", ("pr_plddt_target",), False),
    ("pae_interaction", ("pr_pae_interaction",), False),
    ("pae_overall", ("pr_pae", "pae"), False),
    ("rmsd_overall", ("pr_rmsd",), False),
    ("rmsd_binder", ("pr_rmsd_binder",), False),
    ("conf_score", ("conf_score",), False),
    ("ptm", ("ptm",), False),
)
_CANONICAL_NONNEGATIVE_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_CANONICAL_FINITE_NUMBER = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)
_AUTHORITATIVE_CSV_HEADER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SQLITE_INTEGER_MAX = (1 << 63) - 1


def _protein_design_numeric_error(
    candidate_id: str,
    field: str,
    detail: str,
) -> FrustraMPNNPersistenceError:
    return FrustraMPNNPersistenceError(
        f"canonical protein_design metadata candidate {candidate_id!r} "
        f"field {field!r} {detail}"
    )


def _strict_optional_count(value: Any, *, candidate_id: str, field: str) -> int | None:
    if value in (None, ""):
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, str)
        or _CANONICAL_NONNEGATIVE_INTEGER.fullmatch(value) is None
    ):
        raise _protein_design_numeric_error(
            candidate_id,
            field,
            "must be a canonical nonnegative integer",
        )
    parsed = int(value)
    if parsed > _SQLITE_INTEGER_MAX:
        raise _protein_design_numeric_error(
            candidate_id,
            field,
            "overflows the persisted integer domain",
        )
    return parsed


def _strict_optional_float(
    value: Any,
    *,
    candidate_id: str,
    field: str,
    allow_negative: bool,
) -> float | None:
    if value in (None, ""):
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, str)
        or _CANONICAL_FINITE_NUMBER.fullmatch(value) is None
    ):
        raise _protein_design_numeric_error(
            candidate_id,
            field,
            "must be a canonical finite number",
        )
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _protein_design_numeric_error(
            candidate_id,
            field,
            "must be finite and within the persisted float domain",
        )
    if not allow_negative and parsed < 0:
        raise _protein_design_numeric_error(
            candidate_id,
            field,
            "must be nonnegative",
        )
    return parsed


def _typed_protein_design_metadata_row(
    row: dict[str, Any],
    *,
    candidate_id: str,
) -> dict[str, Any]:
    typed = dict(row)
    for _design_field, csv_fields in _PROTEIN_DESIGN_INTEGER_PROJECTIONS:
        for field in csv_fields:
            if field in typed:
                typed[field] = _strict_optional_count(
                    typed[field], candidate_id=candidate_id, field=field
                )
    for _design_field, csv_fields, allow_negative in _PROTEIN_DESIGN_FLOAT_PROJECTIONS:
        for field in csv_fields:
            if field in typed:
                typed[field] = _strict_optional_float(
                    typed[field],
                    candidate_id=candidate_id,
                    field=field,
                    allow_negative=allow_negative,
                )
    if "producer_rank" in typed:
        typed["producer_rank"] = _strict_optional_count(
            typed["producer_rank"], candidate_id=candidate_id, field="producer_rank"
        )
    if typed.get("producer_sample") == "":
        typed["producer_sample"] = None
    try:
        canonical_json_bytes(typed)
    except (TypeError, ValueError) as exc:
        raise FrustraMPNNPersistenceError(
            f"canonical protein_design metadata candidate {candidate_id!r} "
            "is not strict JSON serializable"
        ) from exc
    return typed


def _read_strict_authoritative_csv_rows(
    csv_path: Path,
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Read CSV authority only after proving an exact, unambiguous ASCII header."""

    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            records = list(csv.reader(handle, strict=True))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata is unreadable or malformed"
        ) from exc
    if not records or not records[0]:
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata has no header"
        )
    header = records[0]
    if any(not field for field in header):
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata header contains an empty field"
        )
    normalized = [field.strip().casefold() for field in header]
    if len(set(normalized)) != len(normalized):
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata header contains duplicate or whitespace-colliding fields"
        )
    if any(
        field != field.strip()
        or not field.isascii()
        or _AUTHORITATIVE_CSV_HEADER.fullmatch(field) is None
        for field in header
    ):
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata header contains invalid, confusable, or noncanonical fields"
        )
    rows: list[dict[str, str]] = []
    for record in records[1:]:
        if len(record) != len(header):
            raise FrustraMPNNPersistenceError(
                "canonical protein_design metadata row does not match its authoritative header"
            )
        rows.append(dict(zip(header, record, strict=True)))
    return tuple(header), rows


def _prevalidate_protein_design_metadata(
    output_path: Path,
    candidate_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Read and type the complete parent metadata set before any ORM write."""

    csv_path = output_path / "results" / "all_designs.csv"
    if not csv_path.exists():
        raise FrustraMPNNPersistenceError(
            "canonical protein_design all_designs.csv is required"
        )
    fieldnames, raw_rows = _read_strict_authoritative_csv_rows(csv_path)
    if "candidate_id" not in fieldnames:
        raise FrustraMPNNPersistenceError(
            "canonical protein_design all_designs.csv header requires candidate_id authority"
        )
    rows: dict[str, dict[str, Any]] = {}
    for raw_row in raw_rows:
        row: dict[str, Any] = dict(raw_row)
        candidate_id = row["candidate_id"]
        if not candidate_id:
            raise FrustraMPNNPersistenceError(
                "canonical protein_design metadata has a missing candidate_id"
            )
        if candidate_id in rows:
            raise FrustraMPNNPersistenceError(
                f"canonical protein_design metadata candidate {candidate_id!r} "
                "has a duplicate candidate_id"
            )
        if candidate_id not in candidate_ids:
            raise FrustraMPNNPersistenceError(
                f"canonical protein_design metadata candidate {candidate_id!r} "
                "is an unmatched candidate_id"
            )
        rows[candidate_id] = _typed_protein_design_metadata_row(
            row,
            candidate_id=candidate_id,
        )
    if set(rows) != candidate_ids:
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata candidate set is incomplete"
        )
    return rows


_PROTEIN_DESIGN_CANONICAL_IDENTITY_FIELDS = (
    "candidate_id",
    "parent_job_id",
    "parent_workflow_id",
    "producer_stage",
    "producer_candidate_key",
    "producer_method",
    "producer_output_key",
    "producer_identity_sha256",
    "producer_artifact_sha256",
    "source_format",
)
_PROTEIN_DESIGN_HASH = re.compile(r"[0-9a-f]{64}")
_PROTEIN_DESIGN_METHOD = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _canonical_protein_design_claim(row: Mapping[str, Any]) -> bool:
    """Recognize workflow ownership without letting a partial row evade validation."""

    return (
        str(row.get("parent_workflow_id") or "").strip() == "protein_design"
        or str(row.get("producer_stage") or "").strip().startswith("protein_design:")
        or str(row.get("producer_candidate_key") or "").strip().startswith(
            "frustrampnn/sources/"
        )
    )


def _validate_canonical_protein_design_identity(
    row: dict[str, Any], *, parent_job_id: str
) -> None:
    candidate_id = str(row.get("candidate_id") or "").strip()
    for field in _PROTEIN_DESIGN_CANONICAL_IDENTITY_FIELDS:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            raise FrustraMPNNPersistenceError(
                f"canonical protein_design metadata candidate {candidate_id!r} "
                f"has an incomplete {field} identity field"
            )
    if row["parent_workflow_id"] != "protein_design" or row["parent_job_id"] != parent_job_id:
        raise FrustraMPNNPersistenceError(
            f"canonical protein_design metadata candidate {candidate_id!r} "
            "does not belong to the persisted protein_design job"
        )
    if _PROTEIN_DESIGN_METHOD.fullmatch(row["producer_method"]) is None:
        raise FrustraMPNNPersistenceError(
            f"canonical protein_design metadata candidate {candidate_id!r} "
            "has an invalid producer_method"
        )
    for field in ("producer_identity_sha256", "producer_artifact_sha256"):
        if _PROTEIN_DESIGN_HASH.fullmatch(row[field]) is None:
            raise FrustraMPNNPersistenceError(
                f"canonical protein_design metadata candidate {candidate_id!r} "
                f"has an invalid {field}"
            )
    if row["source_format"] not in {"pdb", "mmcif"}:
        raise FrustraMPNNPersistenceError(
            f"canonical protein_design metadata candidate {candidate_id!r} "
            "has an invalid source_format"
        )
    for field in ("producer_candidate_key", "producer_output_key"):
        value = row[field]
        path = Path(value)
        if (
            "\\" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise FrustraMPNNPersistenceError(
                f"canonical protein_design metadata candidate {candidate_id!r} "
                f"has an unsafe {field}"
            )
    expected = deterministic_candidate_id(
        parent_job_id=parent_job_id,
        parent_workflow_id="protein_design",
        producer_stage=row["producer_stage"],
        producer_candidate_key=row["producer_candidate_key"],
    )
    if candidate_id != expected:
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata candidate_id is not deterministic"
        )


def _prevalidate_published_protein_design_metadata(
    output_path: Path, *, parent_job_id: str
) -> tuple[dict[str, dict[str, Any]], dict[str, str]] | None:
    """Preflight a complete disabled-path canonical CSV before any ORM mutation."""

    csv_path = output_path / "results" / "all_designs.csv"
    _fieldnames, raw_rows = _read_strict_authoritative_csv_rows(csv_path)
    claims = [_canonical_protein_design_claim(row) for row in raw_rows]
    if not any(claims):
        return None
    if not raw_rows or not all(claims):
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata cannot mix canonical and historical rows"
        )
    candidate_ids = {
        str(row.get("candidate_id") or "").strip() for row in raw_rows
    }
    if "" in candidate_ids or len(candidate_ids) != len(raw_rows):
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata candidate set is incomplete or duplicated"
        )
    typed_rows = _prevalidate_protein_design_metadata(output_path, candidate_ids)
    physical_paths: dict[str, str] = {}
    for candidate_id, row in typed_rows.items():
        _validate_canonical_protein_design_identity(row, parent_job_id=parent_job_id)
        physical_paths[candidate_id] = find_pdb_path(
            output_path,
            str(row.get("description") or ""),
            producer_output_key=row["producer_output_key"],
            producer_artifact_sha256=row["producer_artifact_sha256"],
        )
    return typed_rows, physical_paths


def _first_present_metadata_value(
    row: Mapping[str, Any], fields: tuple[str, ...]
) -> Any:
    for field in fields:
        value = row.get(field)
        if value is not None:
            return value
    return None


def _protein_design_projection_values(
    design: Design,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    description = row.get("description", "")
    values: dict[str, Any] = {
        "name": description if description != "" else design.name,
        "backbone_id": (
            parse_backbone_id(description)
            if isinstance(description, str) and description != ""
            else design.backbone_id
        ),
    }
    for design_field, csv_fields in _PROTEIN_DESIGN_INTEGER_PROJECTIONS:
        values[design_field] = _first_present_metadata_value(row, csv_fields)
    for design_field, csv_fields, _allow_negative in _PROTEIN_DESIGN_FLOAT_PROJECTIONS:
        values[design_field] = _first_present_metadata_value(row, csv_fields)
    return values


def _strict_canonical_json_equal(left: Any, right: Any) -> bool:
    """Compare JSON authority by canonical bytes, preserving signed-zero identity."""

    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError):
        return False


def _enrich_protein_design_from_metadata(
    design: Design, row: dict[str, Any]
) -> None:
    """Apply prevalidated typed protein-design metrics to one Design."""

    values = _protein_design_projection_values(design, row)
    for field, value in values.items():
        setattr(design, field, value)
    provenance = dict(design.provenance or {})
    provenance["all_designs_metadata"] = dict(row)
    design.provenance = provenance


def _assert_protein_design_metadata_replay(
    design: Design,
    row: dict[str, Any],
) -> None:
    """Require the persisted ordinary projection to equal its immutable snapshot."""

    expected = _protein_design_projection_values(design, row)
    observed = {field: getattr(design, field) for field in expected}
    provenance = design.provenance if isinstance(design.provenance, dict) else {}
    if observed != expected or not _strict_canonical_json_equal(
        provenance.get("all_designs_metadata"), row
    ):
        raise FrustraMPNNPersistenceError(
            "canonical protein_design metadata replay contradicts persisted Design fields"
        )


async def _ingest_explicit_frustrampnn_results(
    current_job: Job | None,
    output_path: Path,
    session: AsyncSession,
    *,
    commit: bool,
) -> int | None:
    """Ingest exact terminal stage products, or return None when none exist."""

    if current_job is None:
        return None

    provenance = (
        current_job.provenance if isinstance(current_job.provenance, dict) else {}
    )
    terminal_states = provenance.get("stage_terminal_states")
    frustrampnn_terminal_entries = (
        [
            (stage, state)
            for stage, state in terminal_states.items()
            if str(stage).strip().lower() in _FRUSTRAMPNN_TERMINAL_STAGES
        ]
        if isinstance(terminal_states, dict)
        else []
    )
    not_requested_entries = [
        (stage, state)
        for stage, state in frustrampnn_terminal_entries
        if isinstance(state, dict) and state.get("status") == "not_requested"
    ]
    if not_requested_entries:
        if (
            len(frustrampnn_terminal_entries) != 1
            or frustrampnn_terminal_entries[0]
            != ("frustrampnn", {"status": "not_requested", "outputs": []})
        ):
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN not-requested terminal state must be exact"
            )
        frustrampnn_output_entries = (
            [
                (stage, outputs)
                for stage, outputs in current_job.stage_outputs.items()
                if str(stage).strip().lower() in _FRUSTRAMPNN_TERMINAL_STAGES
            ]
            if isinstance(current_job.stage_outputs, dict)
            else []
        )
        if (
            len(frustrampnn_output_entries) != 1
            or frustrampnn_output_entries[0] != ("frustrampnn", [])
        ):
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN not-requested persisted stage outputs must be exactly empty"
            )
        # This terminal component intentionally has no candidate bundle to ingest.
        # Continue with ordinary parent-result ingestion rather than returning a
        # canonical candidate count and bypassing the parent workflow's Designs.
        return None

    if not isinstance(current_job.stage_outputs, dict):
        return None
    explicit: list[str] = []
    discovered_stage = False
    for stage, outputs in current_job.stage_outputs.items():
        if str(stage).strip().lower() not in _FRUSTRAMPNN_TERMINAL_STAGES:
            continue
        discovered_stage = True
        explicit.extend(_explicit_stage_paths(outputs))
    if not discovered_stage:
        return None

    paths = [_stage_path(path, output_path) for path in explicit]
    manifests = [path for path in paths if path.name in _FRUSTRAMPNN_RESULT_MANIFESTS]
    terminal_paths = [
        path for path in paths if path.name in _FRUSTRAMPNN_TERMINAL_RESULTS
    ]
    if not manifests:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN terminal stage output has no explicit canonical manifest"
        )
    manifest_roots = [os.fspath(path.parent.absolute()) for path in manifests]
    terminal_roots_list = [
        os.fspath(path.parent.absolute()) for path in terminal_paths
    ]
    if (
        len(manifest_roots) != len(set(manifest_roots))
        or len(terminal_roots_list) != len(set(terminal_roots_list))
    ):
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN terminal stage output contains duplicate bundle roots"
        )
    if set(manifest_roots) != set(terminal_roots_list):
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN canonical manifest and terminal envelope roots are not exact pairs"
        )

    roots: list[Path] = []
    seen_roots: set[str] = set()
    for manifest_path in manifests:
        root = manifest_path.parent.absolute()
        root_key = os.fspath(root)
        if root_key not in seen_roots:
            roots.append(root)
            seen_roots.add(root_key)
    terminal_roots = {os.fspath(path.parent.absolute()) for path in terminal_paths}
    if any(os.fspath(root) not in terminal_roots for root in roots):
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN canonical manifest lacks its explicit terminal envelope output"
        )

    validated_candidates = []
    parent_designs: list[tuple[str, str, Path, Any]] = []
    invocation_roots: dict[str, Path] = {}
    candidate_roots: dict[str, Path] = {}
    terminal_paths_by_root = {
        os.fspath(path.parent.absolute()): path for path in terminal_paths
    }
    for root in roots:
        terminal_path = terminal_paths_by_root[os.fspath(root)]
        manifest_path = next(path for path in manifests if path.parent.absolute() == root)
        if _FRUSTRAMPNN_RESULT_PAIRS[manifest_path.name] != terminal_path.name:
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN canonical manifest and terminal result generations are mixed"
            )
        terminal = _read_explicit_terminal_envelope(root, terminal_path.name)
        bundle = validate_frustrampnn_result_bundle(
            root,
            expected_parent_job_id=str(current_job.id),
            terminal_envelope=terminal,
        )
        invocation_id = bundle.manifest["invocation_id"]
        prior_root = invocation_roots.get(invocation_id)
        if prior_root is not None:
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN terminal stage output contains duplicate invocation IDs "
                "across distinct bundle roots"
            )
        invocation_roots[invocation_id] = root
        validated_candidates.append((root, terminal, bundle))

        request = bundle.request
        if request["parent_workflow_id"] not in {
            "structure_prediction",
            "protein_design",
            "complex_prediction",
        }:
            continue
        source_artifact = request["source_artifact"]
        candidate_id = str(bundle.manifest["candidate_id"])
        producer_stage = str(source_artifact.get("producer_stage") or "").strip()
        producer_candidate_key = str(source_artifact.get("relative_path") or "").strip()
        if ":" not in producer_stage or not all(
            part.strip() for part in producer_stage.split(":", 1)
        ):
            # Pre-Phase-5 canonical bundles already attach to a Design seeded by
            # the parent ingester. Only typed parent-candidate authority opts in
            # to deterministic precreation; Phase 7 removes the compatibility path.
            continue
        expected_candidate_id = deterministic_candidate_id(
            parent_job_id=str(current_job.id),
            parent_workflow_id=request["parent_workflow_id"],
            producer_stage=producer_stage,
            producer_candidate_key=producer_candidate_key,
        )
        if (
            candidate_id != expected_candidate_id
            or source_artifact.get("artifact_id") != candidate_id
        ):
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN parent manifest violates deterministic candidate identity"
            )
        prior_candidate_root = candidate_roots.get(candidate_id)
        if prior_candidate_root is not None:
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN terminal stage output contains duplicate candidate IDs"
            )
        candidate_roots[candidate_id] = root

        source_path = _stage_path(producer_candidate_key, output_path)
        try:
            source_sha256 = hashlib.sha256(read_structure_bytes(source_path)).hexdigest()
        except (OSError, StructureNormalizationError) as exc:
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN parent source structure is missing or unsafe"
            ) from exc
        if source_sha256 != source_artifact["sha256"]:
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN parent source structure SHA-256 does not match request authority"
            )
        parent_designs.append(
            (candidate_id, producer_candidate_key, source_path, bundle)
        )

    # Finish the complete physical/identity preflight and all read-only replay
    # checks before the first ORM write. Protein-design metadata is keyed only by
    # the canonical candidate identity; basename/path heuristics are forbidden.
    protein_candidate_ids = {
        candidate_id
        for candidate_id, _candidate_key, _source_path, candidate_bundle in parent_designs
        if candidate_bundle.request["parent_workflow_id"] == "protein_design"
    }
    protein_metadata = (
        _prevalidate_protein_design_metadata(output_path, protein_candidate_ids)
        if protein_candidate_ids
        else {}
    )

    # The deterministic Design rows then become visible to canonical
    # persistence through one explicit flush.
    existing_results: dict[str, FrustraMPNNResult | None] = {}
    for _root, _terminal, bundle in validated_candidates:
        invocation_id = bundle.manifest["invocation_id"]
        existing_results[invocation_id] = await session.get(
            FrustraMPNNResult, (str(current_job.id), invocation_id)
        )
    designs_to_add: list[Design] = []
    for candidate_id, candidate_key, source_path, _bundle in parent_designs:
        design = await session.get(Design, candidate_id)
        if design is not None:
            if design.job_id != str(current_job.id) or design.pdb_path != os.fspath(source_path):
                raise FrustraMPNNPersistenceError(
                    "FrustraMPNN deterministic Design identity conflicts with persisted authority"
                )
            existing_result = existing_results[_bundle.manifest["invocation_id"]]
            if existing_result is not None and _bundle.request["parent_workflow_id"] == "protein_design":
                metadata_row = protein_metadata[candidate_id]
                if not _strict_canonical_json_equal(
                    existing_result.parent_metadata_json, metadata_row
                ):
                    raise FrustraMPNNPersistenceError(
                        "canonical protein_design metadata replay conflicts with immutable metadata snapshot"
                    )
                _assert_protein_design_metadata_replay(design, metadata_row)
            continue
        designs_to_add.append(
            Design(
                id=candidate_id,
                job_id=str(current_job.id),
                name=candidate_key,
                pdb_path=os.fspath(source_path),
                source_stage="frustrampnn_candidate",
                source_stage_family=str(_bundle.request["parent_workflow_id"]),
                source_stage_mode=str(
                    _bundle.request["source_artifact"]["producer_stage"]
                ),
                artifact_class=(
                    "designed_structure"
                    if _bundle.request["parent_workflow_id"] == "protein_design"
                    else "predicted_structure"
                ),
                created_at=datetime.utcnow(),
            )
        )

    created = 0
    try:
        session.add_all(designs_to_add)
        if designs_to_add:
            await session.flush()
        for candidate_id, row in protein_metadata.items():
            design = await session.get(Design, candidate_id)
            if design is None:
                raise FrustraMPNNPersistenceError(
                    "canonical protein_design metadata references a missing Design"
                )
            _enrich_protein_design_from_metadata(design, row)
        for root, terminal, bundle in validated_candidates:
            invocation_id = bundle.manifest["invocation_id"]
            await ingest_frustrampnn_result_bundle(
                session,
                root,
                parent_job_id=str(current_job.id),
                terminal_envelope=terminal,
                commit=False,
                validated_bundle=bundle,
                parent_metadata_snapshot=(
                    protein_metadata[str(bundle.manifest["candidate_id"])]
                    if bundle.request["parent_workflow_id"] == "protein_design"
                    else None
                ),
            )
            if existing_results[invocation_id] is None:
                created += 1
        if commit:
            await session.commit()
    except Exception:
        await session.rollback()
        raise
    return created


def _canonical_protein_design_row_id(
    row: Mapping[str, Any], *, parent_job_id: str
) -> Optional[str]:
    """Return a validated workflow-owned Design ID, when the row declares one."""
    if str(row.get("parent_workflow_id") or "").strip() != "protein_design":
        return None
    from .frustrampnn.identity import deterministic_candidate_id

    fields = {
        "candidate_id": str(row.get("candidate_id") or "").strip(),
        "parent_job_id": str(row.get("parent_job_id") or "").strip(),
        "producer_stage": str(row.get("producer_stage") or "").strip(),
        "producer_candidate_key": str(row.get("producer_candidate_key") or "").strip(),
    }
    if any(not value for value in fields.values()):
        raise FrustraMPNNPersistenceError(
            "protein_design metadata declares canonical ownership without complete identity fields"
        )
    if fields["parent_job_id"] != parent_job_id:
        raise FrustraMPNNPersistenceError(
            "protein_design metadata parent_job_id does not match the persisted job"
        )
    expected = deterministic_candidate_id(
        parent_job_id=parent_job_id,
        parent_workflow_id="protein_design",
        producer_stage=fields["producer_stage"],
        producer_candidate_key=fields["producer_candidate_key"],
    )
    if fields["candidate_id"] != expected:
        raise FrustraMPNNPersistenceError(
            "protein_design metadata candidate_id is not deterministic"
        )
    return expected


async def _persist_cm_bundle_atomically(
    session: AsyncSession,
    cm_request: Any,
    *,
    bundle: Mapping[str, Any],
    result_root: Path,
    commit: bool,
) -> None:
    """Keep canonical FrustraMPNN rows and CM records in one transaction."""

    try:
        await ingest_cm_result_bundle(
            session, cm_request, bundle=bundle, result_root=result_root
        )
        if commit:
            await session.commit()
    except Exception:
        await session.rollback()
        raise


async def ingest_job_results(
    job_id: str, 
    output_dir: str, 
    session: AsyncSession,
    epitope_residues: Optional[list] = None,
    *,
    commit: bool = True,
) -> int:
    """
    Parse pipeline outputs and populate Design table.
    
    Args:
        job_id: The job ID to associate designs with
        output_dir: Path to the job's output directory (e.g., legacy bms_results/job_xxx)
        session: Async database session
        epitope_residues: Optional list of epitope residues (e.g., ["A111", "A112"])
            for calculating contact metrics
        
    Returns:
        Number of designs ingested
    """
    # Resolve relative paths to absolute using data root
    output_path = Path(output_dir)
    if output_path.is_absolute():
        output_path = resolve_runtime_data_path(output_path)
    else:
        output_path = get_data_root() / output_dir

    job_result = await session.execute(select(Job).where(Job.id == job_id))
    current_job = job_result.scalar_one_or_none()
    is_conformational_mapping = bool(
        current_job and str(current_job.model_id or "").strip().lower() == "conformational_mapping"
    )
    canonical_count = await _ingest_explicit_frustrampnn_results(
        current_job,
        output_path,
        session,
        commit=commit and not is_conformational_mapping,
    )
    if canonical_count is not None and not is_conformational_mapping:
        return canonical_count

    if not output_path.exists():
        print(f"[Ingester] Output dir not found: {output_path}")
        return 0

    job_result = await session.execute(select(Job).where(Job.id == job_id))
    current_job = job_result.scalar_one_or_none()
    if (
        current_job
        and str(current_job.model_id or "").strip().lower() == "protein_modification_experimental"
        and str(current_job.mode or "").strip().lower() == "shape_blueprint"
    ):
        return await _ingest_shape_result_manifest(current_job, output_path, session, commit=commit)
    if current_job and str(current_job.model_id or "").strip().lower() == "protein_local_redesign":
        return await _ingest_rfd3_local_redesign_manifest(current_job, output_path, session, commit=commit)
    if current_job and str(current_job.model_id or "") == "conformational_mapping":
        cm_request = await get_cm_request(session, job_id)
        if cm_request is None:
            raise ConformationalPersistenceError("canonical job has no typed request record")
        backend_directory = {
            "protenix_v2_ensemble": "canonical_protenix",
            "confornets": "canonical_confornets",
            "external_import": "canonical_import",
        }.get(cm_request.backend)
        if backend_directory is None:
            raise ConformationalPersistenceError("canonical request backend is unknown")
        exact_roots = (
            output_path / "final" / "conformational_mapping" / backend_directory / "canonical_result",
            output_path / "final" / "conformational_mapping" / backend_directory,
            output_path / backend_directory / "canonical_result",
            output_path / backend_directory,
        )
        result_root = next((path for path in exact_roots if path.is_dir()), None)
        if result_root is None:
            raise ConformationalPersistenceError("canonical result root is absent")
        try:
            ensemble = json.loads((result_root / "cm_ensemble_v1.json").read_text(encoding="utf-8"))
            native = json.loads((result_root / "cm_native_artifacts_v1.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConformationalPersistenceError(f"canonical manifest is missing or malformed: {exc}") from exc
        bundle: Dict[str, Any] = {
            "cm_ensemble_v1": ensemble,
            "cm_native_artifacts_v1": native,
        }
        derived_path = result_root / "cm_derived_index_v1.json"
        if not derived_path.is_file() or derived_path.is_symlink():
            raise ConformationalPersistenceError("canonical derived index is absent or unsafe")
        if derived_path.is_file() and not derived_path.is_symlink():
            try:
                loaded_index = json.loads(derived_path.read_text(encoding="utf-8"))
                if not isinstance(loaded_index, dict):
                    raise ConformationalPersistenceError("canonical derived index must be an object")
                derived = dict(loaded_index)
                supplied = derived.pop("index_sha256")
                from .conformational_mapping.contracts import canonical_sha256

                if supplied != canonical_sha256(derived):
                    raise ConformationalPersistenceError("canonical derived index hash mismatch")
                if derived.get("request_id") != cm_request.request_id:
                    raise ConformationalPersistenceError("canonical derived index request mismatch")
                required_index_fields = {
                    "schema_name", "schema_version", "request_id", "source_ensemble_sha256",
                    "records", "analysis", "lineage", "support", "missingness", "resampling",
                }
                legacy_result_fields = {"structure_maps", "landscapes"}
                global_result_fields = {"frustrampnn_result_references"}
                allowed_index_fields = (
                    required_index_fields | legacy_result_fields | global_result_fields
                    | {"state_landscape_analyses"}
                )
                has_legacy_results = legacy_result_fields.issubset(derived)
                has_global_results = global_result_fields.issubset(derived)
                if (
                    not required_index_fields.issubset(derived)
                    or set(derived) - allowed_index_fields
                    or has_legacy_results == has_global_results
                    or (set(derived) & legacy_result_fields and not has_legacy_results)
                ):
                    raise ConformationalPersistenceError("canonical derived index fields are incomplete or unknown")
                if derived["schema_name"] != "cm_derived_index" or derived["schema_version"] != 1:
                    raise ConformationalPersistenceError("canonical derived index schema is unsupported")
                if derived["source_ensemble_sha256"] != canonical_sha256(ensemble):
                    raise ConformationalPersistenceError("canonical derived index ensemble hash mismatch")
                records = derived["records"]
                if not isinstance(records, list):
                    raise ConformationalPersistenceError("canonical derived file records must be an array")
                seen_derived_paths: set[str] = set()
                for item in records:
                    if not isinstance(item, dict) or set(item) != {
                        "relative_path", "sha256", "bytes", "semantic_role", "candidate_id"
                    }:
                        raise ConformationalPersistenceError("canonical derived file record is malformed")
                    relative = Path(str(item["relative_path"]))
                    relative_text = relative.as_posix()
                    if (
                        relative.is_absolute() or relative_text != str(item["relative_path"])
                        or any(part in {"", ".", ".."} for part in relative.parts)
                        or "\\" in str(item["relative_path"])
                        or relative_text in seen_derived_paths
                    ):
                        raise ConformationalPersistenceError("canonical derived path is unsafe")
                    seen_derived_paths.add(relative_text)
                    artifact = (result_root / relative).resolve(strict=True)
                    artifact.relative_to(result_root.resolve())
                    if artifact.is_symlink() or not artifact.is_file():
                        raise ConformationalPersistenceError("canonical derived artifact is unsafe")
                    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                    if digest != item["sha256"] or artifact.stat().st_size != item["bytes"]:
                        raise ConformationalPersistenceError("canonical derived artifact identity mismatch")
                if has_global_results:
                    snapshots_path = result_root / "cm_complex_snapshots_v1.json"
                    if snapshots_path.is_symlink() or not snapshots_path.is_file():
                        raise ConformationalPersistenceError(
                            "canonical CM snapshot authority is absent or unsafe"
                        )
                    snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))
                    if not isinstance(snapshots, list) or not snapshots:
                        raise ConformationalPersistenceError(
                            "canonical CM snapshot authority is malformed"
                        )
                    references = derived["frustrampnn_result_references"]
                    if (
                        not isinstance(references, dict)
                        or references.get("schema_name") != "cm_frustrampnn_result_references"
                        or references.get("schema_version") != 1
                        or references.get("parent_job_id") != str(current_job.id)
                        or references.get("parent_workflow_id") != "conformational_mapping"
                        or references.get("expected_cardinality") != len(ensemble["candidates"])
                        or not isinstance(references.get("results"), list)
                        or len(references["results"]) != len(ensemble["candidates"])
                    ):
                        raise ConformationalPersistenceError(
                            "canonical FrustraMPNN result references are incomplete or unbound"
                        )
                    global_maps: list[dict[str, Any]] = []
                    global_landscapes: list[dict[str, Any]] = []
                    for reference in references["results"]:
                        if not isinstance(reference, dict):
                            raise ConformationalPersistenceError(
                                "canonical FrustraMPNN result reference is malformed"
                            )
                        relative = Path(str(reference.get("bundle_relative_path") or ""))
                        relative_text = relative.as_posix()
                        if (
                            relative.is_absolute()
                            or relative_text != str(reference.get("bundle_relative_path") or "")
                            or any(part in {"", ".", ".."} for part in relative.parts)
                            or "\\" in str(reference.get("bundle_relative_path") or "")
                        ):
                            raise ConformationalPersistenceError(
                                "canonical FrustraMPNN result reference path is unsafe"
                            )
                        unresolved_bundle_root = result_root / relative
                        if unresolved_bundle_root.is_symlink():
                            raise ConformationalPersistenceError(
                                "canonical FrustraMPNN result bundle is unsafe"
                            )
                        bundle_root = unresolved_bundle_root.resolve(strict=True)
                        bundle_root.relative_to(result_root.resolve())
                        if not bundle_root.is_dir():
                            raise ConformationalPersistenceError(
                                "canonical FrustraMPNN result bundle is unsafe"
                            )
                        manifest_path = bundle_root / "frustrampnn_result_manifest_v2.json"
                        landscape_path = bundle_root / "frustrampnn_landscape_v2.json"
                        structure_map_path = bundle_root / "frustrampnn_structure_map_v1.json"
                        for artifact_path in (manifest_path, landscape_path, structure_map_path):
                            if artifact_path.is_symlink() or not artifact_path.is_file():
                                raise ConformationalPersistenceError(
                                    "canonical FrustraMPNN result artifact is unsafe"
                                )
                        if (
                            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                            != reference.get("result_manifest_sha256")
                            or hashlib.sha256(landscape_path.read_bytes()).hexdigest()
                            != reference.get("landscape_sha256")
                            or hashlib.sha256(structure_map_path.read_bytes()).hexdigest()
                            != reference.get("structure_map_sha256")
                        ):
                            raise ConformationalPersistenceError(
                                "canonical FrustraMPNN result reference hash mismatch"
                            )
                        landscape = json.loads(landscape_path.read_text(encoding="utf-8"))
                        structure_map = json.loads(structure_map_path.read_text(encoding="utf-8"))
                        candidate_id = str(reference.get("candidate_id") or "")
                        if (
                            landscape.get("candidate_id") != candidate_id
                            or structure_map.get("candidate_id") != candidate_id
                        ):
                            raise ConformationalPersistenceError(
                                "canonical FrustraMPNN result candidate binding mismatch"
                            )
                        global_landscapes.append(landscape)
                        global_maps.append(structure_map)
                    bundle["cm_frustrampnn_result_references"] = references
                    bundle["cm_complex_snapshots"] = snapshots
                    bundle["frustrampnn_structure_maps"] = global_maps
                    bundle["frustrampnn_landscapes"] = global_landscapes
                else:
                    bundle["cm_structure_maps"] = derived.get("structure_maps", [])
                    bundle["cm_frustration_landscapes"] = derived.get("landscapes", [])
                bundle["cm_analysis_v1"] = derived.get("analysis")
                bundle["cm_state_landscape_analyses"] = derived.get("state_landscape_analyses")
                bundle["cm_lineage"] = derived.get("lineage")
                bundle["cm_support"] = derived.get("support")
                bundle["cm_missingness"] = derived.get("missingness")
                bundle["cm_resampling_v1"] = derived.get("resampling")
                bundle["cm_derived_files"] = records
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ConformationalPersistenceError(
                    f"canonical derived index is malformed: {exc}"
                ) from exc
        await _persist_cm_bundle_atomically(
            session,
            cm_request,
            bundle=bundle,
            result_root=result_root,
            commit=commit,
        )
        return len(ensemble["candidates"])
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    
    csv_path = output_path / "results" / "all_designs.csv"
    
    if not csv_path.exists():
        print(f"[Ingester] No all_designs.csv found at {csv_path}")
        # Proceed to try other methods if CSV missing
    else:
        # Check if designs already ingested for this job (only if we have a CSV to potentially dupe against? 
        # Actually logic for dupe check is before extraction.
        pass
    
    # Extract PDB files from tar.gz archives
    extract_pdb_files(output_path)
    
    designs_created = 0

    canonical_metadata: dict[str, dict[str, Any]] = {}
    canonical_physical_paths: dict[str, str] = {}
    if csv_path.exists():
        try:
            canonical_preflight = _prevalidate_published_protein_design_metadata(
                output_path,
                parent_job_id=str(current_job.id),
            )
        except FrustraMPNNPersistenceError:
            await session.rollback()
            raise
        if canonical_preflight is not None:
            canonical_metadata, canonical_physical_paths = canonical_preflight

    # Stage-review rows are ephemeral parent-review artifacts. Remove them before
    # real final-stage ingestion so completed jobs don't double-count review rows.
    await session.execute(
        delete(Design).where(
            Design.job_id == job_id,
            Design.source_stage.is_not(None),
        )
    )

    if _is_confornets_job(current_job):
        return await ingest_confornets_results(job_id, output_path, session, current_job, commit=commit)

    if _is_esmfold2_job(current_job):
        return await ingest_esmfold2_results(job_id, output_path, session, current_job, commit=commit)
    
    # Only try to process CSV if it exists
    if csv_path.exists():
        # Check if designs already ingested for this job
        existing = await session.execute(
            select(Design).where(
                Design.job_id == job_id,
                Design.source_stage.is_(None),
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            print(f"[Ingester] Designs already ingested for job {job_id}")
            return 0
        
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                rows_to_ingest = (
                    list(canonical_metadata.values())
                    if canonical_metadata
                    else reader
                )

                for row in rows_to_ingest:
                    # Map CSV columns to Design fields
                    design_name = row.get('description', f'design_{designs_created}')
                    canonical_design_id = (
                        str(row.get("candidate_id") or "") if canonical_metadata else ""
                    )
                    structure_path_str = canonical_physical_paths.get(canonical_design_id)
                    if structure_path_str is None:
                        structure_path_str = find_pdb_path(
                            output_path,
                            design_name,
                            producer_output_key=row.get("producer_output_key"),
                            producer_artifact_sha256=row.get("producer_artifact_sha256"),
                        )
                    structure_path = Path(structure_path_str) if structure_path_str else None
                    structure_cdr_lengths = _parse_hlt_cdr_lengths(structure_path)
                    fam_json_path = _find_fampnn_sidecar_path(structure_path, output_path) if structure_path else None
                    fam_payload = _load_json_payload(fam_json_path) if fam_json_path else None
                    fam_metrics = _extract_fampnn_metrics(fam_payload, structure_path)
                    fampnn_record = _build_fampnn_payload(fam_payload, fam_metrics)
                    canonical_row = canonical_metadata.get(canonical_design_id)
                    strict_projection: dict[str, Any] = {}
                    if canonical_row is not None:
                        for design_field, csv_fields in _PROTEIN_DESIGN_INTEGER_PROJECTIONS:
                            strict_projection[design_field] = _first_present_metadata_value(
                                canonical_row, csv_fields
                            )
                        for design_field, csv_fields, _allow_negative in _PROTEIN_DESIGN_FLOAT_PROJECTIONS:
                            strict_projection[design_field] = _first_present_metadata_value(
                                canonical_row, csv_fields
                            )
                    row_mpnn_score = (
                        strict_projection["mpnn_score"]
                        if canonical_row is not None
                        else safe_float(row.get('seq_mpnn_score'))
                    )
                    row_fampnn_psce = (
                        strict_projection["fampnn_psce"]
                        if canonical_row is not None
                        else safe_float(row.get('seq_fampnn_psce'))
                    )
                    lineage = await _resolve_parent_design_lineage(
                        session,
                        job_context,
                        design_name,
                        cache=lineage_cache,
                    )
                    design_provenance = {
                        **job_context.get("provenance", {}),
                        "artifact_group": "rfantibody",
                        "structure_path": str(structure_path) if structure_path else None,
                    }
                    if lineage.get("source_design_name"):
                        design_provenance["source_design_name"] = lineage["source_design_name"]
                    if lineage.get("source_pdb_path"):
                        design_provenance["source_pdb_path"] = lineage["source_pdb_path"]
                    if fampnn_record:
                        design_provenance["fampnn"] = fampnn_record

                    design_id = _canonical_protein_design_row_id(
                        row,
                        parent_job_id=str(current_job.id),
                    ) or str(uuid.uuid4())
                    design = Design(
                        id=design_id,
                        job_id=job_id,
                        name=design_name,
                        pdb_path=str(structure_path) if structure_path else None,
                        json_path=str(fam_json_path) if fam_json_path and fam_json_path.exists() else None,
                        
                        # Backbone grouping
                        backbone_id=parse_backbone_id(design_name),
                        
                        # Structural metrics (predicted structures)
                        num_helices=(strict_projection["num_helices"] if canonical_row is not None else safe_int(row.get('pr_helices'))),
                        num_strands=(strict_projection["num_strands"] if canonical_row is not None else safe_int(row.get('pr_strands'))),
                        rog=(strict_projection["rog"] if canonical_row is not None else safe_float(row.get('pr_RoG'))),
                        # RFdiffusion backbone metrics
                        rfd_rog=(strict_projection["rfd_rog"] if canonical_row is not None else safe_float(row.get('rfd_RoG'))),
                        
                        # Sequence design metrics
                        mpnn_score=(row_mpnn_score if canonical_row is not None or row_mpnn_score is not None else fam_metrics.get("mpnn_score")),
                        fampnn_psce=(row_fampnn_psce if canonical_row is not None or row_fampnn_psce is not None else fam_metrics.get("avg_psce")),
                        binder_length=fam_metrics.get("binder_length"),
                        
                        # Structure prediction metrics (AF2/Boltz)
                        plddt_overall=(strict_projection["plddt_overall"] if canonical_row is not None else safe_float(row.get('pr_plddt') or row.get('plddt'))),
                        plddt_binder=(strict_projection["plddt_binder"] if canonical_row is not None else safe_float(row.get('pr_plddt_binder'))),
                        plddt_target=(strict_projection["plddt_target"] if canonical_row is not None else safe_float(row.get('pr_plddt_target'))),
                        pae_interaction=(strict_projection["pae_interaction"] if canonical_row is not None else safe_float(row.get('pr_pae_interaction'))),
                        pae_overall=(strict_projection["pae_overall"] if canonical_row is not None else safe_float(row.get('pr_pae') or row.get('pae'))),
                        rmsd_overall=(strict_projection["rmsd_overall"] if canonical_row is not None else safe_float(row.get('pr_rmsd'))),
                        rmsd_binder=(strict_projection["rmsd_binder"] if canonical_row is not None else safe_float(row.get('pr_rmsd_binder'))),
                        cdr_h1_length=structure_cdr_lengths.get("H1"),
                        cdr_h2_length=structure_cdr_lengths.get("H2"),
                        cdr_h3_length=structure_cdr_lengths.get("H3"),
                        cdr_l1_length=structure_cdr_lengths.get("L1"),
                        cdr_l2_length=structure_cdr_lengths.get("L2"),
                        cdr_l3_length=structure_cdr_lengths.get("L3"),
                        
                        # Boltz-2 specific
                        conf_score=(strict_projection["conf_score"] if canonical_row is not None else safe_float(row.get('conf_score'))),
                        ptm=(strict_projection["ptm"] if canonical_row is not None else safe_float(row.get('ptm'))),
                        
                        # User annotations (defaults)
                        is_favorite=False,
                        notes=None,
                        
                        created_at=datetime.utcnow()
                    )

                    rfa_trb = load_rfantibody_trb_summary(structure_path) if structure_path else {}
                    if rfa_trb:
                        design.plddt_overall = safe_float(rfa_trb.get("plddt_overall"))
                        design.residue_plddt = rfa_trb.get("residue_plddt")
                        design.rfa_hotspot_min_distance = safe_float(rfa_trb.get("rfa_hotspot_min_distance"))
                        design.rfa_hotspot_avg_min_distance = safe_float(rfa_trb.get("rfa_hotspot_avg_min_distance"))
                        design.rfa_runtime_seconds = safe_float(rfa_trb.get("rfa_runtime_seconds"))
                        design.rfa_device = rfa_trb.get("rfa_device")
                        design.rfa_diffusion_steps = safe_int(rfa_trb.get("rfa_diffusion_steps"))
                        design.rfa_noise_scale_ca = safe_float(rfa_trb.get("rfa_noise_scale_ca"))
                        design.rfa_noise_scale_frame = safe_float(rfa_trb.get("rfa_noise_scale_frame"))
                        design.rfa_guide_scale = safe_float(rfa_trb.get("rfa_guide_scale"))
                        design.rfa_plddt_initial = safe_float(rfa_trb.get("rfa_plddt_initial"))
                        design.rfa_plddt_final = safe_float(rfa_trb.get("rfa_plddt_final"))
                        design.rfa_plddt_delta = safe_float(rfa_trb.get("rfa_plddt_delta"))
                        design.rfa_plddt_selected = safe_float(rfa_trb.get("rfa_plddt_selected"))
                        design.rfa_plddt_nonselected = safe_float(rfa_trb.get("rfa_plddt_nonselected"))
                        design.rfa_design_loops = rfa_trb.get("rfa_design_loops")
                        design.rfa_hotspots = rfa_trb.get("rfa_hotspots")
                        design_provenance["rfantibody"] = rfa_trb
                    combined_confidence: Dict[str, Any] = {}
                    if fampnn_record:
                        combined_confidence["fampnn"] = fampnn_record
                    if rfa_trb:
                        combined_confidence["rfantibody"] = rfa_trb.get("rfa_metadata") or rfa_trb
                    if combined_confidence:
                        design.confidence_metrics = combined_confidence
                    lineage = await _resolve_parent_design_lineage(
                        session,
                        job_context,
                        design.name,
                        cache=lineage_cache,
                    )
                    for field_name, field_value in _design_lineage_fields(
                        job_context,
                        lineage,
                        producer_job=current_job,
                        producer_payload=fam_payload,
                    ).items():
                        setattr(design, field_name, field_value)
                    design.stage_family = job_context.get("stage_family")
                    design.stage_mode = job_context.get("stage_mode")
                    design.selected_loop_scope = job_context.get("selected_loop_scope")
                    design.provenance = design_provenance
                    _inherit_source_design_metrics(
                        design,
                        lineage.get("source_design"),
                        structure_path=structure_path,
                    )
                    if canonical_row is not None:
                        _enrich_protein_design_from_metadata(design, canonical_row)
                        _assert_protein_design_metadata_replay(design, canonical_row)
                    
                    session.add(design)
                    designs_created += 1
            
            if commit:
                await session.commit()
            print(f"[Ingester] Ingested {designs_created} designs for job {job_id}")
            
        except FrustraMPNNPersistenceError:
            await session.rollback()
            raise
        except Exception as e:
            print(f"[Ingester] Error ingesting results: {e}")
            await session.rollback()
            # Don't return 0 here, let it fall through to valid loose file check if CSV failed partial?
            # Or return 0? Standard flow usually returns if error.
            # But let's allow fallback if designs_created is still 0
            pass
    
    if designs_created == 0 and str(current_job.mode or "").strip().lower() == "maturation_child":
        print(f"[Ingester] No CSV designs for maturation child {job_id}. Trying published PPIFlow results...")
        designs_created = await ingest_published_maturation_structures(job_id, output_path, session, current_job=current_job)

    if designs_created == 0:
        print(f"[Ingester] No CSV designs for job {job_id}. Trying collected PPIFlow parent outputs...")
        designs_created = await ingest_collected_ppiflow_structures(job_id, output_path, session, current_job=current_job)

    if designs_created == 0:
        print("[Ingester] No designs found in CSV or CSV missing. Trying loose files...")
        designs_created = await ingest_loose_files(job_id, output_path, session, current_job=current_job)

    # Post-ingestion: Attach supplementary metrics from pipeline stages
    if designs_created > 0:
        await ingest_screening_data(job_id, output_path, session)
        await ingest_maturation_data(job_id, output_path, session)

    return designs_created


async def ingest_screening_data(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
) -> int:
    """
    Backfill contact-distance metrics onto existing Design rows.

    Reads the ``rfantibody_screening_summary.csv`` produced by
    ``screen_rfantibody_backbones.py``.  Because the CSV design names
    correspond to the *original* RFA output filenames (e.g.
    ``001_<uuid>``) while the ingester stores designs under their
    *collected* names (e.g. ``job0_rfantibody_child_0``), exact name
    matching is tried first, then ordinal matching (sorted CSV rows →
    sorted designs that have no contact data yet).
    """
    from database import Job

    # ── Build lineage-aware job-ID set ───────────────────────────────────
    job_info = await session.execute(select(Job).where(Job.id == job_id))
    current_job = job_info.scalar_one_or_none()

    design_job_ids = [job_id]
    if current_job:
        if current_job.parent_job_id:
            design_job_ids.append(current_job.parent_job_id)
        if current_job.batch_id:
            batch_res = await session.execute(
                select(Job.id).where(Job.batch_id == current_job.batch_id)
            )
            design_job_ids.extend([row[0] for row in batch_res.all()])
        child_result = await session.execute(
            select(Job.id).where(Job.parent_job_id == job_id)
        )
        design_job_ids.extend([row[0] for row in child_result.all()])
        params_dict = _parse_job_params(current_job.params)
        if params_dict.get("iteration_source_job_id"):
            design_job_ids.append(params_dict["iteration_source_job_id"])
        if params_dict.get("iteration_source_root_job_id"):
            design_job_ids.append(params_dict["iteration_source_root_job_id"])
    design_job_ids = list(set(design_job_ids))

    # ── Locate screening CSV ─────────────────────────────────────────────
    search_dirs = [
        output_path / "collected" / "rfantibody_filtered",
        output_path / "run" / "rfantibody_screen",
        output_path,
    ]
    csv_path: Optional[Path] = None
    for d in search_dirs:
        candidate = d / "rfantibody_screening_summary.csv"
        if candidate.exists():
            csv_path = candidate
            break

    if csv_path is None:
        return 0

    print(f"[Ingester] Found RFA screening CSV at {csv_path}")

    # ── Load CSV rows ────────────────────────────────────────────────────
    csv_rows: list[dict] = []
    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ecc = safe_int(row.get("epitope_contact_count"))
                emd = safe_float(row.get("epitope_min_distance"))
                if ecc is not None or emd is not None:
                    csv_rows.append(row)
    except Exception as e:
        print(f"[Ingester] Error reading screening CSV {csv_path}: {e}")
        return 0

    if not csv_rows:
        return 0

    # ── Phase 1: exact name match ────────────────────────────────────────
    updated_count = 0
    unmatched_rows: list[dict] = []
    for row in csv_rows:
        design_name = row.get("design_name", "").strip()
        if not design_name:
            unmatched_rows.append(row)
            continue
        result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.name == design_name,
            )
        )
        design = result.scalars().first()
        if design:
            changed = _apply_screening_row(design, row)
            if changed:
                updated_count += 1
        else:
            unmatched_rows.append(row)

    # ── Phase 2: ordinal fallback for unmatched rows ─────────────────────
    if unmatched_rows:
        # Fetch ALL designs without contact data, sorted by name for
        # deterministic ordinal alignment with the CSV row order.
        result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.epitope_min_distance.is_(None),
            ).order_by(Design.name)
        )
        orphan_designs = result.scalars().all()

        if orphan_designs:
            print(f"[Ingester] Phase-2 ordinal match: {len(unmatched_rows)} CSV rows → {len(orphan_designs)} designs without contacts")
            for idx, row in enumerate(unmatched_rows):
                if idx >= len(orphan_designs):
                    break
                design = orphan_designs[idx]
                changed = _apply_screening_row(design, row)
                if changed:
                    updated_count += 1
                    print(f"[Ingester]   ordinal [{idx}] CSV '{row.get('design_name','')}' → DB '{design.name}': "
                          f"contacts={safe_int(row.get('epitope_contact_count'))}, "
                          f"min_dist={safe_float(row.get('epitope_min_distance'))}")

    if updated_count > 0:
        await session.commit()
        print(f"[Ingester] Updated {updated_count} designs with screening contact metrics")

    return updated_count


def _apply_screening_row(design: "Design", row: dict) -> bool:
    """Apply screening CSV fields to a Design. Returns True if anything changed."""
    changed = False
    ecc = safe_int(row.get("epitope_contact_count"))
    emd = safe_float(row.get("epitope_min_distance"))
    emad = safe_float(row.get("epitope_min_atom_distance"))
    tcc = safe_int(row.get("target_contact_count"))
    tmd = safe_float(row.get("target_min_distance"))
    tmad = safe_float(row.get("target_min_atom_distance"))
    screening_reason = row.get("screening_reason")
    if ecc is not None and design.epitope_contact_count is None:
        design.epitope_contact_count = ecc
        changed = True
    if emd is not None and design.epitope_min_distance is None:
        design.epitope_min_distance = emd
        changed = True
    if emad is not None and getattr(design, "epitope_min_atom_distance", None) is None:
        design.epitope_min_atom_distance = emad
        changed = True
    if row.get("epitope_nearest_antibody_residue") and getattr(design, "epitope_nearest_antibody_residue", None) is None:
        design.epitope_nearest_antibody_residue = str(row.get("epitope_nearest_antibody_residue"))
        changed = True
    if row.get("epitope_nearest_target_residue") and getattr(design, "epitope_nearest_target_residue", None) is None:
        design.epitope_nearest_target_residue = str(row.get("epitope_nearest_target_residue"))
        changed = True
    if row.get("epitope_nearest_antibody_atom") and getattr(design, "epitope_nearest_antibody_atom", None) is None:
        design.epitope_nearest_antibody_atom = str(row.get("epitope_nearest_antibody_atom"))
        changed = True
    if row.get("epitope_nearest_target_atom") and getattr(design, "epitope_nearest_target_atom", None) is None:
        design.epitope_nearest_target_atom = str(row.get("epitope_nearest_target_atom"))
        changed = True
    if row.get("epitope_mapping_mode") and getattr(design, "epitope_mapping_mode", None) is None:
        design.epitope_mapping_mode = str(row.get("epitope_mapping_mode"))
        changed = True
    ecd = safe_float(row.get("epitope_centroid_distance"))
    if ecd is not None and getattr(design, "epitope_centroid_distance", None) is None:
        design.epitope_centroid_distance = ecd
        changed = True
    if tcc is not None and design.target_contact_count is None:
        design.target_contact_count = tcc
        changed = True
    if tmd is not None and getattr(design, "target_min_distance", None) is None:
        design.target_min_distance = tmd
        changed = True
    if tmad is not None and getattr(design, "target_min_atom_distance", None) is None:
        design.target_min_atom_distance = tmad
        changed = True
    if row.get("target_nearest_antibody_residue") and getattr(design, "target_nearest_antibody_residue", None) is None:
        design.target_nearest_antibody_residue = str(row.get("target_nearest_antibody_residue"))
        changed = True
    if row.get("target_nearest_target_residue") and getattr(design, "target_nearest_target_residue", None) is None:
        design.target_nearest_target_residue = str(row.get("target_nearest_target_residue"))
        changed = True
    if row.get("target_nearest_antibody_atom") and getattr(design, "target_nearest_antibody_atom", None) is None:
        design.target_nearest_antibody_atom = str(row.get("target_nearest_antibody_atom"))
        changed = True
    if row.get("target_nearest_target_atom") and getattr(design, "target_nearest_target_atom", None) is None:
        design.target_nearest_target_atom = str(row.get("target_nearest_target_atom"))
        changed = True
    tcd = safe_float(row.get("target_centroid_distance"))
    if tcd is not None and getattr(design, "target_centroid_distance", None) is None:
        design.target_centroid_distance = tcd
        changed = True
    if row.get("detected_antibody_chains") and getattr(design, "detected_antibody_chains", None) is None:
        design.detected_antibody_chains = str(row.get("detected_antibody_chains"))
        changed = True
    if row.get("detected_target_chain") and getattr(design, "detected_target_chain", None) is None:
        design.detected_target_chain = str(row.get("detected_target_chain"))
        changed = True
    arc = safe_int(row.get("antibody_residue_count"))
    if arc is not None and getattr(design, "antibody_residue_count", None) is None:
        design.antibody_residue_count = arc
        changed = True
    trc = safe_int(row.get("target_residue_count"))
    if trc is not None and getattr(design, "target_residue_count", None) is None:
        design.target_residue_count = trc
        changed = True
    erc = safe_int(row.get("epitope_residue_count"))
    if erc is not None and getattr(design, "epitope_residue_count", None) is None:
        design.epitope_residue_count = erc
        changed = True
    passed_screen = row.get("passed_screen")
    if passed_screen not in (None, "") and getattr(design, "passed_screen", None) is None:
        design.passed_screen = str(passed_screen).strip().lower() == "true"
        changed = True
    hotspot_covered = safe_int(row.get("rfa_hotspot_covered_count"))
    if hotspot_covered is not None and getattr(design, "rfa_hotspot_covered_count", None) is None:
        design.rfa_hotspot_covered_count = hotspot_covered
        changed = True
    for json_key in ("rfa_loop_metrics", "rfa_hotspot_metrics"):
        value = row.get(json_key)
        if value and getattr(design, json_key, None) is None:
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    pass
            setattr(design, json_key, value)
            changed = True
    if screening_reason and not design.screening_reason:
        design.screening_reason = str(screening_reason)
        changed = True
    return changed


def _apply_geometry_metrics(design: "Design", metrics: Dict[str, Any], *, overwrite: bool = True) -> bool:
    changed = False
    for field_name in _GEOMETRY_METRIC_FIELDS:
        if field_name not in metrics:
            continue
        new_value = metrics.get(field_name)
        if not overwrite and getattr(design, field_name, None) is not None:
            continue
        if getattr(design, field_name, None) != new_value:
            setattr(design, field_name, new_value)
            changed = True
    return changed


def _coerce_optional_float(value: Any) -> Optional[float]:
    if value in (None, "", [], {}, ()):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value in (None, "", [], {}, ()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if value in (None, "", [], {}, ()):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "passed"}:
            return True
        if lowered in {"false", "0", "no", "n", "failed"}:
            return False
    return None


def _apply_ppiflow_score_fields(design: "Design", score_data: Dict[str, Any]) -> bool:
    changed = False
    scalar_fields = {
        "maturation_delta_interface": _coerce_optional_float(score_data.get("delta_interface_score")),
        "maturation_interface_score": _coerce_optional_float(score_data.get("interface_score_matured") or score_data.get("interface_score_refined")),
        "maturation_rmsd": _coerce_optional_float(score_data.get("rmsd_backbone")),
        "maturation_selected_delta_interface": _coerce_optional_float(score_data.get("selected_delta_interface_score")),
        "maturation_selected_interface_score": _coerce_optional_float(score_data.get("selected_interface_score_matured")),
        "maturation_selected_rmsd": _coerce_optional_float(score_data.get("selected_rmsd_backbone")),
        "maturation_nonselected_rmsd": _coerce_optional_float(score_data.get("nonselected_rmsd_backbone")),
        "ppiflow_primary_loop": str(score_data.get("primary_loop")).strip() if score_data.get("primary_loop") not in (None, "") else None,
        "ppiflow_primary_loop_rmsd": _coerce_optional_float(score_data.get("primary_loop_rmsd")),
        "ppiflow_primary_loop_target_contact_delta": _coerce_optional_int(score_data.get("primary_loop_target_contact_delta")),
        "ppiflow_primary_loop_target_distance_delta": _coerce_optional_float(score_data.get("primary_loop_target_distance_delta")),
        "ppiflow_primary_loop_epitope_contact_delta": _coerce_optional_int(score_data.get("primary_loop_epitope_contact_delta")),
        "ppiflow_primary_loop_epitope_distance_delta": _coerce_optional_float(score_data.get("primary_loop_epitope_distance_delta")),
        "ppiflow_objective_mode": str(score_data.get("objective_mode")).strip().lower() if score_data.get("objective_mode") not in (None, "") else None,
        "ppiflow_objective_score": _coerce_optional_float(score_data.get("objective_score")),
    }
    for field_name, new_value in scalar_fields.items():
        if getattr(design, field_name, None) != new_value:
            setattr(design, field_name, new_value)
            changed = True

    loop_metrics = score_data.get("loop_metrics") if isinstance(score_data.get("loop_metrics"), dict) else None
    if getattr(design, "ppiflow_loop_metrics", None) != loop_metrics:
        design.ppiflow_loop_metrics = loop_metrics
        changed = True
    return changed


def _apply_ppiflow_filter_fields(design: "Design", filter_payload: Dict[str, Any]) -> bool:
    changed = False
    passed = _coerce_optional_bool(filter_payload.get("passed"))
    reason = filter_payload.get("filter_reason")
    normalized_reason = str(reason).strip() if reason not in (None, "") else None
    if getattr(design, "ppiflow_filter_passed", None) != passed:
        design.ppiflow_filter_passed = passed
        changed = True
    if getattr(design, "ppiflow_filter_reason", None) != normalized_reason:
        design.ppiflow_filter_reason = normalized_reason
        changed = True
    return changed


def _inherit_source_design_metrics(
    design: "Design",
    source_design: Optional[Design],
    *,
    structure_path: Optional[Path] = None,
) -> bool:
    changed = False

    if source_design is not None:
        scalar_fields = (
            "binder_length",
            "antibody_type",
            "humanness_score",
            "cdr_h1_length",
            "cdr_h2_length",
            "cdr_h3_length",
            "cdr_l1_length",
            "cdr_l2_length",
            "cdr_l3_length",
            "fr2_contacts",
            "de_loop",
            "fr3_contacts",
            "fr4_contacts",
            "rfd_rog",
            "passed_screen",
            "rfa_hotspot_covered_count",
        )
        for field_name in scalar_fields:
            if getattr(design, field_name, None) is not None:
                continue
            source_value = getattr(source_design, field_name, None)
            if source_value is None:
                continue
            setattr(design, field_name, source_value)
            changed = True

        geometry_fields = {
            field_name: getattr(source_design, field_name, None)
            for field_name in (
                "epitope_contact_count",
                "epitope_min_distance",
                "epitope_min_atom_distance",
                "epitope_nearest_antibody_residue",
                "epitope_nearest_target_residue",
                "epitope_nearest_antibody_atom",
                "epitope_nearest_target_atom",
                "epitope_mapping_mode",
                "epitope_centroid_distance",
                "target_contact_count",
                "target_min_distance",
                "target_min_atom_distance",
                "target_nearest_antibody_residue",
                "target_nearest_target_residue",
                "target_nearest_antibody_atom",
                "target_nearest_target_atom",
                "target_centroid_distance",
                "detected_antibody_chains",
                "detected_target_chain",
                "antibody_residue_count",
                "target_residue_count",
                "epitope_residue_count",
            )
        }
        changed = _apply_geometry_metrics(design, geometry_fields, overwrite=False) or changed

    if getattr(design, "rog", None) is None:
        computed_rog = compute_gyration_radius(structure_path) if structure_path else None
        fallback_rog = computed_rog if computed_rog is not None else (getattr(source_design, "rog", None) if source_design is not None else None)
        if fallback_rog is not None:
            design.rog = fallback_rog
            changed = True

    return changed


async def ingest_maturation_data(
    job_id: str,
    output_path: Path,
    session: AsyncSession
) -> int:
    """
    Parse PPIFlow maturation score JSONs and update matching designs.
    
    Parse PPIFlow score/filter JSONs and update matching designs.

    Preferred inputs are *_maturation_score.json or *_partial_flow_score.json.
    If only *_maturation_filter.json exists, the ingester can still hydrate
    provenance and any embedded score payload from the filter report.
    """
    # Check multiple possible locations (maturation_child publishes to run/ppiflow/results/)
    maturation_dirs = [
        output_path / "run" / "ppiflow" / "results",
        output_path / "ppiflow" / "results",
        output_path / "collected" / "backbone_refine",
        output_path / "collected" / "maturation",
        output_path / "collected" / "ppiflow_generator_raw",
        output_path / "collected" / "ppiflow_generator_filtered",
        output_path,
    ]
    
    score_files = []
    for d in maturation_dirs:
        if d.exists():
            score_files.extend(d.glob("*_maturation_score.json"))
            score_files.extend(d.glob("*_partial_flow_score.json"))
            score_files.extend(d.glob("*_maturation_filter.json"))
    
    if not score_files:
        return 0
    
    # Deduplicate by filename (same file may appear in multiple search paths)
    seen = set()
    unique_files = []
    for f in score_files:
        if f.name not in seen:
            seen.add(f.name)
            unique_files.append(f)
    score_files = unique_files
    
    print(f"[Ingester] Found {len(score_files)} maturation score JSONs to process")
    
    updated_count = 0
    job_result = await session.execute(select(Job).where(Job.id == job_id))
    current_job = job_result.scalar_one_or_none()
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    geometry_applied_design_ids: set[str] = set()
    context_params = job_context.get("params") or {}
    epitope_residues = _parse_epitope_residues(
        context_params.get("epitope_residues") or context_params.get("selected_residues")
    )
    antibody_chain_hint = context_params.get("antibody_chains") or context_params.get("binder_chains")
    target_chain_hint = (
        context_params.get("antigen_chains")
        or context_params.get("target_chains")
        or context_params.get("target_chain")
    )
    reference_target_pdb = await _resolve_job_param_from_lineage(
        session,
        current_job,
        context_params,
        "target_pdb",
    )
    resolved_epitope_contact_threshold = await _resolve_job_param_from_lineage(
        session,
        current_job,
        context_params,
        "rfantibody_contact_distance_threshold",
        "contact_distance_threshold",
    )
    resolved_target_contact_threshold = await _resolve_job_param_from_lineage(
        session,
        current_job,
        context_params,
        "rfantibody_target_contact_distance_threshold",
        "target_contact_distance_threshold",
    )
    epitope_contact_distance_threshold = float(resolved_epitope_contact_threshold or 8.0)
    target_contact_distance_threshold = float(resolved_target_contact_threshold or 12.0)
    
    for json_path in score_files:
        stem = json_path.stem
        if stem.endswith("_maturation_score"):
            design_name = stem.replace("_maturation_score", "")
            record_kind = "maturation_score"
        elif stem.endswith("_partial_flow_score"):
            design_name = stem.replace("_partial_flow_score", "")
            record_kind = "partial_flow_score"
        elif stem.endswith("_maturation_filter"):
            design_name = stem.replace("_maturation_filter", "")
            record_kind = "maturation_filter"
        else:
            design_name = stem
            record_kind = "unknown"

        try:
            import json as json_mod
            data = json_mod.loads(json_path.read_text())
        except Exception as e:
            print(f"[Ingester] Error parsing maturation JSON {json_path}: {e}")
            continue
        score_data = data.get("score_data") if isinstance(data, dict) and isinstance(data.get("score_data"), dict) else data
        if not isinstance(score_data, dict):
            score_data = {}
        
        # Find matching design in DB (try both with and without job_id for child jobs)
        result = await session.execute(
            select(Design).where(
                Design.job_id == job_id,
                Design.name == design_name
            )
        )
        design = result.scalar_one_or_none()
        
        if not design:
            import sqlalchemy as sa
            # 1. Broaden search to include batch family and iteration source lineage
            job_info = await session.execute(
                select(Job).where(Job.id == job_id)
            )
            current_job = job_info.scalar_one_or_none()
            
            design_job_ids = [job_id]
            if current_job:
                if current_job.parent_job_id:
                    design_job_ids.append(current_job.parent_job_id)
                if current_job.batch_id:
                    batch_res = await session.execute(
                        select(Job.id).where(Job.batch_id == current_job.batch_id)
                    )
                    design_job_ids.extend([row[0] for row in batch_res.all()])
                
                # Check for iteration Source ID stored in params
                params_dict = _parse_job_params(current_job.params)
                if params_dict.get("iteration_source_job_id"):
                    design_job_ids.append(params_dict["iteration_source_job_id"])
                if params_dict.get("iteration_source_root_job_id"):
                    design_job_ids.append(params_dict["iteration_source_root_job_id"])

            if len(design_job_ids) > 1:
                # Deduplicate and query
                unique_ids = list(set(design_job_ids))
                result = await session.execute(
                    select(Design).where(
                        Design.job_id.in_(unique_ids),
                        Design.name == design_name
                    ).order_by(sa.desc(Design.created_at))  # Get newest if duplicates
                )
                design = result.scalars().first()
        
        if not design:
            continue
        
        _apply_ppiflow_score_fields(design, score_data)

        if epitope_residues and design.id not in geometry_applied_design_ids:
            try:
                geometry_metrics = compute_contact_geometry_metrics(
                    pdb_path=Path(design.pdb_path),
                    epitope_residues=epitope_residues,
                    antibody_chain=design.detected_antibody_chains or antibody_chain_hint,
                    target_chain=design.detected_target_chain or target_chain_hint,
                    epitope_contact_distance_threshold=epitope_contact_distance_threshold,
                    target_contact_distance_threshold=target_contact_distance_threshold,
                    reference_target_pdb=reference_target_pdb,
                )
                _apply_geometry_metrics(design, geometry_metrics, overwrite=True)
            except Exception as exc:
                print(f"[Ingester] Failed PPIFlow contact-geometry scoring for {design.name}: {exc}")
            geometry_applied_design_ids.add(design.id)

        fam_json_path = _find_fampnn_sidecar_path(Path(design.pdb_path), output_path)
        fam_payload = _load_json_payload(fam_json_path)
        fam_metrics = _extract_fampnn_metrics(fam_payload, Path(design.pdb_path))
        fampnn_record = _build_fampnn_payload(fam_payload, fam_metrics)
        if fam_json_path and not design.json_path:
            design.json_path = str(fam_json_path)
        if fam_metrics.get("avg_psce") is not None:
            design.fampnn_psce = fam_metrics["avg_psce"]
        if fam_metrics.get("binder_length") is not None:
            design.binder_length = fam_metrics["binder_length"]
        if fam_metrics.get("mpnn_score") is not None:
            design.mpnn_score = fam_metrics["mpnn_score"]

        confidence_metrics = dict(design.confidence_metrics or {})
        if fampnn_record:
            confidence_metrics["fampnn"] = fampnn_record
        design.confidence_metrics = confidence_metrics or None
        if confidence_metrics:
            flag_modified(design, "confidence_metrics")

        lineage = await _resolve_parent_design_lineage(
            session,
            job_context,
            design.name,
            cache=lineage_cache,
        )
        provenance_ppiflow = (
            (design.provenance or {}).get("ppiflow")
            if isinstance(design.provenance, dict)
            else None
        )
        structure_cdr_lengths = _coalesce_cdr_lengths(
            _parse_hlt_cdr_lengths(Path(design.pdb_path) if design.pdb_path else None),
            _parse_source_cdr_lengths(
                provenance_ppiflow.get("source_pdb_path") if isinstance(provenance_ppiflow, dict) else None
            ),
            _extract_design_cdr_lengths(design),
            _parse_source_cdr_lengths(lineage.get("source_pdb_path")),
            lineage.get("source_cdr_lengths"),
        )
        design.cdr_h1_length = structure_cdr_lengths.get("H1")
        design.cdr_h2_length = structure_cdr_lengths.get("H2")
        design.cdr_h3_length = structure_cdr_lengths.get("H3")
        design.cdr_l1_length = structure_cdr_lengths.get("L1")
        design.cdr_l2_length = structure_cdr_lengths.get("L2")
        design.cdr_l3_length = structure_cdr_lengths.get("L3")
        design.plddt_overall = None
        design.residue_plddt = None
        provenance = copy.deepcopy(design.provenance or {})
        provenance.setdefault("job", job_context.get("provenance"))
        provenance["ppiflow"] = copy.deepcopy(provenance.get("ppiflow") or {})
        provenance["ppiflow"].update({
            "record_kind": record_kind,
            "selected_loop_scope": design.selected_loop_scope or job_context.get("selected_loop_scope"),
            "stage_settings": (job_context.get("provenance") or {}).get("stage_settings"),
        })
        if lineage.get("source_design_name"):
            provenance["ppiflow"]["source_design_name"] = lineage["source_design_name"]
        if lineage.get("source_pdb_path"):
            provenance["ppiflow"]["source_pdb_path"] = lineage["source_pdb_path"]
        if record_kind == "maturation_filter":
            provenance["ppiflow"]["maturation_filter"] = data
            provenance["ppiflow"]["maturation_filter_json"] = str(json_path)
            if score_data:
                provenance["ppiflow"]["maturation_score"] = score_data
        else:
            score_key = "partial_flow_score" if record_kind == "partial_flow_score" else "maturation_score"
            provenance["ppiflow"][score_key] = score_data
            provenance["ppiflow"][f"{score_key}_json"] = str(json_path)

        filter_json = json_path if record_kind == "maturation_filter" else json_path.with_name(
            json_path.name.replace("_maturation_score.json", "_maturation_filter.json").replace("_partial_flow_score.json", "_maturation_filter.json")
        )
        if filter_json.exists():
            filter_payload = _load_json_payload(filter_json)
            if filter_payload:
                _apply_ppiflow_filter_fields(design, filter_payload)
                provenance["ppiflow"]["maturation_filter"] = filter_payload
                provenance["ppiflow"]["maturation_filter_json"] = str(filter_json)
                filter_score_data = filter_payload.get("score_data") if isinstance(filter_payload, dict) else None
                if isinstance(filter_score_data, dict):
                    provenance["ppiflow"]["maturation_score"] = filter_score_data
                filter_score_json = filter_payload.get("score_json") if isinstance(filter_payload, dict) else None
                if filter_score_json:
                    provenance["ppiflow"]["maturation_score_json"] = str(filter_score_json)

        sidecar_candidates = _ordered_unique([design.name] + _candidate_source_design_names(design.name))
        for candidate_name in sidecar_candidates:
            anchors_json = json_path.with_name(f"{candidate_name}_anchors.json")
            anchors_payload = _load_json_payload(anchors_json)
            if anchors_payload:
                provenance["ppiflow"]["anchors"] = anchors_payload
                provenance["ppiflow"]["anchors_json"] = str(anchors_json)
                break
        for candidate_name in sidecar_candidates:
            interface_json = json_path.with_name(f"{candidate_name}_interface_score.json")
            interface_payload = _load_json_payload(interface_json)
            if interface_payload:
                provenance["ppiflow"]["interface_score"] = interface_payload
                provenance["ppiflow"]["interface_score_json"] = str(interface_json)
                break
        for candidate_name in sidecar_candidates:
            rotamer_json = json_path.with_name(f"{candidate_name}_rotamer_enrichment.json")
            rotamer_payload = _load_json_payload(rotamer_json)
            if rotamer_payload:
                provenance["ppiflow"]["rotamer_enrichment"] = rotamer_payload
                provenance["ppiflow"]["rotamer_enrichment_json"] = str(rotamer_json)
                break
        for candidate_name in sidecar_candidates:
            enriched_pdb = json_path.with_name(f"{candidate_name}_enriched_complex.pdb")
            if enriched_pdb.exists():
                provenance["ppiflow"]["enriched_complex_pdb"] = str(enriched_pdb)
                break
        for candidate_name in sidecar_candidates:
            ppiflow_positions_path = json_path.with_name(f"{candidate_name}_ppiflow_positions.txt")
            if ppiflow_positions_path.exists():
                provenance["ppiflow"]["ppiflow_positions"] = ppiflow_positions_path.read_text().strip()
                provenance["ppiflow"]["ppiflow_positions_txt"] = str(ppiflow_positions_path)
                break
        for candidate_name in sidecar_candidates:
            cdr_positions_path = json_path.with_name(f"{candidate_name}_cdr_positions.txt")
            if cdr_positions_path.exists():
                provenance["ppiflow"]["cdr_positions"] = cdr_positions_path.read_text().strip()
                provenance["ppiflow"]["cdr_positions_txt"] = str(cdr_positions_path)
                break
        if fam_json_path and fampnn_record:
            provenance["ppiflow"]["fampnn"] = fampnn_record
            provenance["ppiflow"]["fampnn_json"] = str(fam_json_path)

        for field_name, field_value in _design_lineage_fields(job_context, lineage).items():
            if getattr(design, field_name, None) in (None, "", [], {}, ()):
                setattr(design, field_name, field_value)
        design.stage_family = design.stage_family or job_context.get("stage_family") or "ppiflow"
        design.stage_mode = design.stage_mode or job_context.get("stage_mode") or "maturation"
        design.selected_loop_scope = design.selected_loop_scope or job_context.get("selected_loop_scope")
        design.provenance = provenance
        flag_modified(design, "provenance")
        
        updated_count += 1
    
    if updated_count > 0:
        await session.commit()
        print(f"[Ingester] Updated {updated_count} designs with maturation metrics")
    
    return updated_count


async def ingest_published_maturation_structures(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
    current_job: Optional[Job] = None,
) -> int:
    """
    Backfill completed maturation-child outputs directly from published PDBs.

    This keeps valid PPIFlow child results viewable even when richer CSV-based
    ingestion does not run.
    """
    result_dirs = [
        output_path / "run" / "ppiflow" / "results",
        output_path / "ppiflow" / "results",
    ]
    published_results_dir = next((path for path in result_dirs if path.exists()), None)
    if published_results_dir is None:
        return 0

    existing_names = set(
        (
            await session.execute(
                select(Design.name).where(Design.job_id == job_id)
            )
        ).scalars().all()
    )

    approved_names = {
        report.stem.replace("_maturation_filter", "")
        for report in published_results_dir.glob("*_maturation_filter.json")
    }

    structure_paths: list[Path] = []
    if approved_names:
        for name in sorted(approved_names):
            for ext in ("pdb", "cif", "mmcif"):
                candidate = published_results_dir / f"{name}.{ext}"
                if candidate.exists():
                    structure_paths.append(candidate)
                    break
    else:
        for ext in ("*.pdb", "*.cif", "*.mmcif"):
            structure_paths.extend(sorted(published_results_dir.glob(ext)))

    created = 0
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    for structure_path in structure_paths:
        design_name = structure_path.stem
        if design_name in existing_names:
            continue

        fam_json_path = _find_fampnn_sidecar_path(structure_path, output_path)
        fam_payload = _load_json_payload(fam_json_path)
        fam_metrics = _extract_fampnn_metrics(fam_payload, structure_path)
        fampnn_record = _build_fampnn_payload(fam_payload, fam_metrics)
        lineage = await _resolve_parent_design_lineage(
            session,
            job_context,
            design_name,
            cache=lineage_cache,
        )
        structure_cdr_lengths = _coalesce_cdr_lengths(
            _parse_hlt_cdr_lengths(structure_path),
            _parse_source_cdr_lengths(lineage.get("source_pdb_path")),
            lineage.get("source_cdr_lengths"),
        )
        sample_index = _parse_ppiflow_sample_index(design_name)
        ppiflow_provenance = {
            "source": "published_maturation_structures",
            "structure_path": str(structure_path),
        }
        if sample_index is not None:
            ppiflow_provenance["sample_index"] = sample_index
        if lineage.get("source_design_name"):
            ppiflow_provenance["source_design_name"] = lineage["source_design_name"]
        if lineage.get("source_pdb_path"):
            ppiflow_provenance["source_pdb_path"] = lineage["source_pdb_path"]
        filter_json = structure_path.with_name(f"{design_name}_maturation_filter.json")
        filter_payload = _load_json_payload(filter_json)
        if filter_payload:
            ppiflow_provenance["maturation_filter"] = filter_payload
            ppiflow_provenance["maturation_filter_json"] = str(filter_json)
        design_prefix = _ppiflow_design_prefix(design_name)
        rotamer_json = structure_path.with_name(f"{design_prefix}_rotamer_enrichment.json")
        rotamer_payload = _load_json_payload(rotamer_json)
        if rotamer_payload:
            ppiflow_provenance["rotamer_enrichment"] = rotamer_payload
            ppiflow_provenance["rotamer_enrichment_json"] = str(rotamer_json)
        enriched_pdb = structure_path.with_name(f"{design_prefix}_enriched_complex.pdb")
        if enriched_pdb.exists():
            ppiflow_provenance["enriched_complex_pdb"] = str(enriched_pdb)
        ppiflow_positions_path = structure_path.with_name(f"{design_prefix}_ppiflow_positions.txt")
        if ppiflow_positions_path.exists():
            ppiflow_provenance["ppiflow_positions"] = ppiflow_positions_path.read_text().strip()
            ppiflow_provenance["ppiflow_positions_txt"] = str(ppiflow_positions_path)
        cdr_positions_path = structure_path.with_name(f"{design_prefix}_cdr_positions.txt")
        if cdr_positions_path.exists():
            ppiflow_provenance["cdr_positions"] = cdr_positions_path.read_text().strip()
            ppiflow_provenance["cdr_positions_txt"] = str(cdr_positions_path)
        confidence_metrics: Dict[str, Any] = {}
        if fampnn_record:
            ppiflow_provenance["fampnn"] = fampnn_record
            confidence_metrics["fampnn"] = fampnn_record

        session.add(Design(
            id=str(uuid.uuid4()),
            job_id=job_id,
            name=design_name,
            pdb_path=str(structure_path),
            json_path=str(fam_json_path) if fam_json_path else None,
            backbone_id=parse_backbone_id(design_name),
            **_design_lineage_fields(job_context, lineage),
            stage_family=job_context.get("stage_family") or "ppiflow",
            stage_mode=job_context.get("stage_mode") or "maturation",
            selected_loop_scope=job_context.get("selected_loop_scope"),
            provenance={
                **job_context.get("provenance", {}),
                "artifact_group": "ppiflow",
                "ppiflow": ppiflow_provenance,
            },
            plddt_overall=None,
            residue_plddt=None,
            mpnn_score=fam_metrics.get("mpnn_score"),
            fampnn_psce=fam_metrics.get("avg_psce"),
            binder_length=fam_metrics.get("binder_length"),
            confidence_metrics=confidence_metrics or None,
            cdr_h1_length=structure_cdr_lengths.get("H1"),
            cdr_h2_length=structure_cdr_lengths.get("H2"),
            cdr_h3_length=structure_cdr_lengths.get("H3"),
            cdr_l1_length=structure_cdr_lengths.get("L1"),
            cdr_l2_length=structure_cdr_lengths.get("L2"),
            cdr_l3_length=structure_cdr_lengths.get("L3"),
            is_favorite=False,
            created_at=datetime.utcnow(),
        ))
        existing_names.add(design_name)
        created += 1

    if created > 0:
        await session.commit()
        print(f"[Ingester] Backfilled {created} published maturation structures for job {job_id}")

    return created


def _discover_collected_ppiflow_structures(output_path: Path) -> list[tuple[str, Path]]:
    discovered: list[tuple[str, Path]] = []
    seen_design_names: set[str] = set()
    for stage_name in ("backbone_refine", "maturation", "ppiflow_generator_filtered", "ppiflow_generator_raw"):
        stage_dir = output_path / "collected" / stage_name
        if not stage_dir.exists():
            continue
        for ext in ("*.pdb", "*.cif", "*.mmcif"):
            for structure_path in sorted(stage_dir.glob(ext)):
                if not _is_final_ppiflow_structure_path(structure_path):
                    continue
                design_name = structure_path.stem
                if design_name in seen_design_names:
                    continue
                seen_design_names.add(design_name)
                discovered.append((stage_name, structure_path))
    return discovered


async def ingest_collected_ppiflow_structures(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
    current_job: Optional[Job] = None,
) -> int:
    """
    Ingest stage-only parent jobs that publish collected PPIFlow outputs under
    ``collected/backbone_refine`` or ``collected/maturation``.

    These jobs do not emit ``all_designs.csv`` and should not fall back to the
    generic raw-PDB scanner because that will pick up intermediates and inputs.
    """
    structure_entries = _discover_collected_ppiflow_structures(output_path)
    if not structure_entries:
        return 0

    existing_names = set(
        (
            await session.execute(
                select(Design.name).where(Design.job_id == job_id)
            )
        ).scalars().all()
    )

    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    created = 0

    for stage_name, structure_path in structure_entries:
        design_name = structure_path.stem
        if design_name in existing_names:
            continue

        ingested_stage_mode = stage_name
        if stage_name in {"ppiflow_generator_raw", "ppiflow_generator_filtered"}:
            ingested_stage_mode = "generator_backbone_refine"

        fam_json_path = _find_fampnn_sidecar_path(structure_path, output_path)
        fam_payload = _load_json_payload(fam_json_path)
        fam_metrics = _extract_fampnn_metrics(fam_payload, structure_path)
        fampnn_record = _build_fampnn_payload(fam_payload, fam_metrics)
        lineage = await _resolve_parent_design_lineage(
            session,
            job_context,
            design_name,
            cache=lineage_cache,
        )
        structure_cdr_lengths = _coalesce_cdr_lengths(
            _parse_hlt_cdr_lengths(structure_path),
            _parse_source_cdr_lengths(lineage.get("source_pdb_path")),
            lineage.get("source_cdr_lengths"),
        )
        sample_index = _parse_ppiflow_sample_index(design_name)
        ppiflow_provenance = {
            "source": "collected_ppiflow_structures",
            "structure_path": str(structure_path),
            "stage_name": stage_name,
        }
        if sample_index is not None:
            ppiflow_provenance["sample_index"] = sample_index
        if lineage.get("source_design_name"):
            ppiflow_provenance["source_design_name"] = lineage["source_design_name"]
        if lineage.get("source_pdb_path"):
            ppiflow_provenance["source_pdb_path"] = lineage["source_pdb_path"]
        filter_json = structure_path.with_name(f"{design_name}_maturation_filter.json")
        filter_payload = _load_json_payload(filter_json)
        if filter_payload:
            ppiflow_provenance["maturation_filter"] = filter_payload
            ppiflow_provenance["maturation_filter_json"] = str(filter_json)
        design_prefix = _ppiflow_design_prefix(design_name)
        rotamer_json = structure_path.with_name(f"{design_prefix}_rotamer_enrichment.json")
        rotamer_payload = _load_json_payload(rotamer_json)
        if rotamer_payload:
            ppiflow_provenance["rotamer_enrichment"] = rotamer_payload
            ppiflow_provenance["rotamer_enrichment_json"] = str(rotamer_json)
        enriched_pdb = structure_path.with_name(f"{design_prefix}_enriched_complex.pdb")
        if enriched_pdb.exists():
            ppiflow_provenance["enriched_complex_pdb"] = str(enriched_pdb)
        ppiflow_positions_path = structure_path.with_name(f"{design_prefix}_ppiflow_positions.txt")
        if ppiflow_positions_path.exists():
            ppiflow_provenance["ppiflow_positions"] = ppiflow_positions_path.read_text().strip()
            ppiflow_provenance["ppiflow_positions_txt"] = str(ppiflow_positions_path)
        cdr_positions_path = structure_path.with_name(f"{design_prefix}_cdr_positions.txt")
        if cdr_positions_path.exists():
            ppiflow_provenance["cdr_positions"] = cdr_positions_path.read_text().strip()
            ppiflow_provenance["cdr_positions_txt"] = str(cdr_positions_path)
        confidence_metrics: Dict[str, Any] = {}
        if fampnn_record:
            ppiflow_provenance["fampnn"] = fampnn_record
            confidence_metrics["fampnn"] = fampnn_record

        session.add(Design(
            id=str(uuid.uuid4()),
            job_id=job_id,
            name=design_name,
            pdb_path=str(structure_path),
            json_path=str(fam_json_path) if fam_json_path and fam_json_path.exists() else None,
            backbone_id=parse_backbone_id(design_name),
            **_design_lineage_fields(
                job_context,
                lineage,
                artifact_class_override=(
                    infer_antibody_artifact_class_from_stage("ppiflow", ingested_stage_mode)
                    or job_context.get("artifact_class")
                ),
            ),
            stage_family="ppiflow",
            stage_mode=ingested_stage_mode,
            selected_loop_scope=job_context.get("selected_loop_scope"),
            provenance={
                **job_context.get("provenance", {}),
                "artifact_group": "ppiflow",
                "ppiflow": ppiflow_provenance,
            },
            plddt_overall=None,
            residue_plddt=None,
            mpnn_score=fam_metrics.get("mpnn_score"),
            fampnn_psce=fam_metrics.get("avg_psce"),
            binder_length=fam_metrics.get("binder_length"),
            confidence_metrics=confidence_metrics or None,
            cdr_h1_length=structure_cdr_lengths.get("H1"),
            cdr_h2_length=structure_cdr_lengths.get("H2"),
            cdr_h3_length=structure_cdr_lengths.get("H3"),
            cdr_l1_length=structure_cdr_lengths.get("L1"),
            cdr_l2_length=structure_cdr_lengths.get("L2"),
            cdr_l3_length=structure_cdr_lengths.get("L3"),
            is_favorite=False,
            created_at=datetime.utcnow(),
        ))
        existing_names.add(design_name)
        created += 1

    if created > 0:
        await session.commit()
        print(f"[Ingester] Backfilled {created} collected PPIFlow structures for job {job_id}")

    return created



async def ingest_loose_files(
    job_id: str,
    output_path: Path,
    session: AsyncSession,
    current_job: Optional[Job] = None,
) -> int:
    """Ingest designs from individual JSON/PDB files (fallback)."""
    
    job_params: Dict[str, Any] = {}
    job_result = await session.execute(select(Job.mode, Job.params).where(Job.id == job_id))
    job_row = job_result.one_or_none()
    job_mode = job_row[0] if job_row else None
    raw_job_params = job_row[1] if job_row else None
    job_params = _parse_job_params(raw_job_params)
    is_maturation_child = str(job_mode or "").strip().lower() == "maturation_child"
    job_context = _job_stage_context(current_job)
    lineage_cache: Dict[str, Optional[Design]] = {}
    allow_chain_ordered_metrics = _job_has_explicit_binder_target_roles(current_job)
    allow_validation_interface_metrics = _job_supports_inferred_validation_roles(current_job, job_params)
    validation_role_fields = _validation_role_fields(current_job, job_params)
    detected_antibody_chains = validation_role_fields.get("detected_antibody_chains")
    detected_target_chain = validation_role_fields.get("detected_target_chain")

    epitope_residues = _parse_epitope_residues(
        job_params.get("epitope_residues") or job_params.get("selected_residues")
    )

    plr_final_candidate_dir = job_params.get("plr_final_candidate_dir")
    plr_final_path = None
    current_model_id = str(getattr(current_job, "model_id", "") or "").strip().lower() if current_job is not None else ""
    current_mode = str(getattr(current_job, "mode", "") or "").strip().lower() if current_job is not None else ""
    if current_job is not None and (
        current_model_id == "protein_local_redesign"
        or (
            current_model_id == "protein_modification_experimental"
            and (
                current_mode == "region_redesign"
                or str(job_params.get("modification_mode") or "").strip().lower() == "region_redesign"
            )
        )
    ):
        raw_final_path = str(plr_final_candidate_dir or "").strip()
        if raw_final_path:
            candidate = Path(raw_final_path).expanduser()
            if not candidate.is_absolute():
                candidate = (get_data_root() / candidate).resolve()
            if candidate.exists():
                plr_final_path = candidate

    boltzgen_filtered_path = None
    if current_job is not None and str(getattr(current_job, "model_id", "")).strip().lower() == "boltzgen":
        boltzgen_filtered_path = _boltzgen_filtered_output_dir(output_path)

    # Locations to search for confidence/metrics JSONs
    # Boltz outputs often in pdb_files/predictions/
    # RF3 outputs in pdb_files/rf3/output/*/
    if plr_final_path is not None:
        search_paths = [plr_final_path]
    elif boltzgen_filtered_path is not None:
        search_paths = [boltzgen_filtered_path]
    else:
        search_paths = [
            output_path / "pdb_files" / "predictions",
            output_path / "pdb_files" / "validated_designs",
            output_path / "pdb_files",
            output_path / "collected",
            output_path,
        ]
    
    # Also search RF3 nested output directories
    if plr_final_path is None and boltzgen_filtered_path is None:
        rf3_base = output_path / "pdb_files" / "rf3" / "output"
        if rf3_base.exists():
            for subdir in rf3_base.iterdir():
                if subdir.is_dir():
                    search_paths.append(subdir)
                    # Also search seed-*/sample-* subdirs
                    for sample_dir in subdir.glob("seed-*_sample-*"):
                        if sample_dir.is_dir():
                            search_paths.append(sample_dir)

    # Protenix outputs: predictions/{design_name}/ containing .cif + confidence.json
    if plr_final_path is None and boltzgen_filtered_path is None:
        protenix_base = output_path / "pdb_files" / "predictions"
        if not protenix_base.exists():
            protenix_base = output_path / "run" / "protenix" / "predictions"
        protenix_run_base = output_path / "run" / "protenix_complex" / "predictions"
        for pbase in [protenix_base, protenix_run_base]:
            if pbase.exists():
                for subdir in pbase.iterdir():
                    if subdir.is_dir():
                        search_paths.append(subdir)
    
    designs_created = 0
    
    # Track ingested names to avoid duplicates
    ingested_names = set()
    
    print(f"[Ingester DEBUG] Search paths: {[str(p) for p in search_paths]}")

    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        
        recursive_scan = search_dir.name in {"pdb_files", "validated_designs", "collected"} or "collected" in search_dir.parts
        json_files = set(list(search_dir.rglob("confidence_*.json")) if recursive_scan else list(search_dir.glob("confidence_*.json")))
        boltz_aligned_jsons = list(search_dir.rglob("*_boltzpred.json")) if recursive_scan else list(search_dir.glob("*_boltzpred.json"))
        json_files.update(boltz_aligned_jsons)
        json_files = sorted(json_files)
        print(f"[Ingester DEBUG] {search_dir}: {len(json_files)} confidence JSONs found")
        
        # BOLTZ2: Look for confidence_*.json patterns
        for json_file in json_files:
            try:
                raw_name = json_file.stem
                if raw_name.endswith("_boltzpred"):
                    artifact_name = raw_name.replace("_boltzpred", "")
                else:
                    artifact_name = raw_name.replace("confidence_", "")

                design_name = (
                    _normalize_boltzgen_design_name(artifact_name)
                    if job_context.get("stage_family") == "boltzgen"
                    else artifact_name
                )
                
                # Skip input templates (no numeric suffix) - these are not actual designs
                # Actual designs are named like: boltzgen_input_0, boltzgen_input_1, etc.
                import re
                if not re.search(r'_\d+$', design_name):
                    print(f"[Ingester] Skipping input template: {design_name}")
                    continue
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding Structure (CIF preferred for complexes, PDB fallback)
                structure_candidates = [
                    search_dir / f"{design_name}.cif",
                    search_dir / f"{artifact_name}.cif",
                    output_path / "pdb_files" / f"{design_name}.cif",
                    output_path / "pdb_files" / f"{artifact_name}.cif",
                    search_dir / f"{raw_name}.pdb",
                    search_dir / f"{artifact_name}.pdb",
                    search_dir / f"{design_name}_boltzpred.pdb",
                    search_dir / f"{design_name}.pdb",
                    output_path / "pdb_files" / f"{raw_name}.pdb",
                    output_path / "pdb_files" / f"{artifact_name}.pdb",
                    output_path / "pdb_files" / f"{design_name}_boltzpred.pdb",
                    output_path / "pdb_files" / f"{design_name}.pdb",
                    output_path / "pdb_files" / "predictions" / f"{raw_name}.pdb",
                    output_path / "pdb_files" / "predictions" / f"{design_name}_boltzpred.pdb",
                    output_path / "pdb_files" / "predictions" / f"{design_name}.pdb",
                ]
                structure_path = next((candidate for candidate in structure_candidates if candidate.exists()), None)

                if structure_path is None:
                    continue
                    
                # Read Boltz2 metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)

                aligned_pdb_name = str(metrics.get('aligned_pdb') or '').strip()
                if aligned_pdb_name:
                    aligned_candidate = json_file.parent / aligned_pdb_name
                    if aligned_candidate.exists():
                        structure_path = aligned_candidate
                
                # Boltz2 format: complex_plddt, ptm, iptm, confidence_score, complex_pde
                plddt = metrics.get('complex_plddt') or metrics.get('plddt')
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0
                
                conf_score = metrics.get('confidence_score')
                ptm = metrics.get('ptm')
                ligand_iptm = metrics.get('ligand_iptm')
                
                # NEW: Extract interface metrics
                iptm = metrics.get('iptm')
                protein_iptm = metrics.get('protein_iptm')
                complex_iplddt = metrics.get('complex_iplddt')
                complex_ipde = metrics.get('complex_ipde')
                chains_ptm = metrics.get('chains_ptm')  # dict: {"0": 0.76, "1": 0.51}
                pair_chains_iptm = metrics.get('pair_chains_iptm')  # NxN matrix
                has_clash_raw = metrics.get('full_has_clash')
                if has_clash_raw is None:
                    has_clash_raw = metrics.get('has_clash')
                disorder = metrics.get('disorder') or metrics.get('full_disorder_prob_mean')
                num_recycles = metrics.get('num_recycles')
                rmsd_overall = metrics.get('rmsd_overall') or metrics.get('boltz_overall_rmsd')
                rmsd_binder = metrics.get('rmsd_binder') or metrics.get('boltz_binder_rmsd')
                rmsd_target = metrics.get('rmsd_target') or metrics.get('protenix_target_rmsd') or metrics.get('boltz_target_rmsd')

                # Only store true PAE values. PDE/IPDE-derived summaries are not interchangeable.
                pae = metrics.get('complex_pae') or metrics.get('pae')
                
                # Look for Affinity JSON
                affinity_score = None
                binder_probability = None
                
                # Try multiple locations for affinity file
                affinity_file = search_dir / f"affinity_{design_name}.json"
                if not affinity_file.exists():
                    affinity_file = output_path / "pdb_files" / "predictions" / f"affinity_{design_name}.json"
                    
                if affinity_file.exists():
                    try:
                        with open(affinity_file, 'r') as af:
                            aff_metrics = json.load(af)
                            affinity_score = aff_metrics.get('affinity_pred_value')
                            binder_probability = aff_metrics.get('affinity_probability_binary')
                    except Exception as e:
                        print(f"[Ingester] Error parsing affinity file {affinity_file}: {e}")

                # Extract per-residue pLDDT from PDB B-factors
                _, residue_plddt = extract_plddt_from_pdb(structure_path)
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))
                
                structure_role_fields = _resolve_validation_structure_role_fields(
                    structure_path=Path(structure_path),
                    job_params=job_params,
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                )
                geometry_fields = dict(structure_role_fields)
                if allow_validation_interface_metrics:
                    geometry_fields.update(
                        _compute_validation_geometry_fields(
                            structure_path=Path(structure_path),
                            job_params=job_params,
                            detected_antibody_chains=structure_role_fields.get("detected_antibody_chains"),
                            detected_target_chain=structure_role_fields.get("detected_target_chain"),
                            epitope_residues=epitope_residues,
                        )
                    )
                
                # Create design
                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                fam_json_path = _find_fampnn_sidecar_path(Path(structure_path), output_path)
                fam_payload = _load_json_payload(fam_json_path)
                fam_metrics = _extract_fampnn_metrics(fam_payload, Path(structure_path))
                fampnn_record = _build_fampnn_payload(fam_payload, fam_metrics)
                boltzgen_mode = str(job_params.get("boltzgen_mode") or "").strip().lower()
                fam_provenance: Dict[str, Any] = {
                    "source": (
                        "fampnn"
                        if fampnn_record and fampnn_record.get("fampnn_avg_psce") is not None
                        else ("boltzgen" if job_context.get("stage_family") == "boltzgen" else "loose_file")
                    ),
                    "structure_path": str(structure_path),
                }
                if job_context.get("stage_family") == "boltzgen":
                    fam_provenance["generator_family"] = "boltzgen"
                if boltzgen_mode:
                    fam_provenance["generator_mode"] = boltzgen_mode
                if lineage.get("source_design_name"):
                    fam_provenance["source_design_name"] = lineage["source_design_name"]
                if lineage.get("source_pdb_path"):
                    fam_provenance["source_pdb_path"] = lineage["source_pdb_path"]
                if fampnn_record:
                    fam_provenance["fampnn"] = fampnn_record
                combined_confidence = dict(metrics) if isinstance(metrics, dict) else {}
                if fampnn_record:
                    combined_confidence["fampnn"] = fampnn_record
                aligned_error_fields = _strict_aligned_error_fields(
                    structure_path=Path(structure_path),
                    summary_json_path=Path(json_file) if json_file else None,
                    detected_antibody_chains=structure_role_fields.get("detected_antibody_chains"),
                    detected_target_chain=structure_role_fields.get("detected_target_chain"),
                )

                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(json_file) if json_file else (str(fam_json_path) if fam_json_path.exists() else None),
                    
                    # Backbone grouping
                    backbone_id=parse_backbone_id(design_name),
                    **_design_lineage_fields(
                        job_context,
                        lineage,
                        producer_job=current_job,
                        producer_payload=metrics,
                    ),
                    stage_family=(metrics.get("stage_family") or job_context.get("stage_family")),
                    stage_mode=(metrics.get("stage_mode") or job_context.get("stage_mode")),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance={
                        **job_context.get("provenance", {}),
                        **fam_provenance,
                    },
                    confidence_metrics=combined_confidence or None,
                    **_geometry_design_fields(geometry_fields),
                    **aligned_error_fields,

                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    conf_score=safe_float(conf_score),
                    ligand_iptm=safe_float(ligand_iptm),
                    rmsd_overall=safe_float(rmsd_overall),
                    rmsd_binder=safe_float(rmsd_binder),
                    rmsd_target=safe_float(rmsd_target),
                    affinity_score=safe_float(affinity_score),
                    binder_probability=safe_float(binder_probability),
                    residue_plddt=residue_plddt,
                    
                    # NEW: Interface metrics
                    iptm=safe_float(iptm),
                    protein_iptm=safe_float(protein_iptm),
                    complex_iplddt=safe_float(complex_iplddt),
                    complex_ipde=safe_float(complex_ipde),
                    chains_ptm=chains_ptm,
                    pair_chains_iptm=pair_chains_iptm,
                    disorder=safe_float(disorder),
                    num_recycles=safe_int(num_recycles),
                    has_clash=(bool(has_clash_raw) if has_clash_raw is not None else None),
                    binder_length=fam_metrics.get("binder_length"),
                    antibody_type=_infer_antibody_type_from_job_params(job_params),
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing Boltz2 file {json_file}: {e}")
        
        # RF3: Look for *_summary_confidences.json patterns
        rf3_jsons = list(search_dir.rglob("*_summary_confidences.json")) if recursive_scan else list(search_dir.glob("*_summary_confidences.json"))
        for json_file in rf3_jsons:
            try:
                # Filename format: DESIGNNAME_summary_confidences.json
                design_name = json_file.stem.replace("_summary_confidences", "")
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding structure file (RF3 outputs .cif not .pdb)
                # Try CIF first (RF3 default), then PDB as fallback
                structure_path = None
                
                # Check for CIF with _model suffix
                cif_path = search_dir / f"{design_name}_model.cif"
                if not cif_path.exists():
                    cif_path = search_dir / f"{design_name}.cif"
                if not cif_path.exists():
                    cif_path = search_dir.parent / f"{design_name}_model.cif"
                if not cif_path.exists():
                    cif_path = search_dir.parent / f"{design_name}.cif"
                
                if cif_path.exists():
                    structure_path = cif_path
                else:
                    # Fallback to PDB
                    pdb_path = search_dir / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        pdb_path = search_dir.parent / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        pdb_path = search_dir.parent.parent / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        # Try without seed/sample suffix
                        base_name = design_name.rsplit("_seed-", 1)[0]
                        pdb_path = output_path / "pdb_files" / f"{base_name}.pdb"
                    if pdb_path.exists():
                        structure_path = pdb_path
                
                if not structure_path:
                    print(f"[Ingester] No structure file found for RF3 design {design_name}")
                    continue
                    
                # Read RF3 metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)
                
                # RF3 format: overall_plddt, ptm, iptm, overall_pae, ranking_score
                plddt = metrics.get('overall_plddt')
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0
                
                pae = metrics.get('overall_pae')
                ptm = metrics.get('ptm')
                iptm = metrics.get('iptm')
                ranking_score = metrics.get('ranking_score')
                
                # Use ranking_score as confidence if available
                conf_score = ranking_score if ranking_score is not None else None
                
                # Extract per-residue pLDDT from structure B-factors (works for both PDB and CIF via Biotite)
                from .structure_utils import get_residue_plddt
                _, residue_plddt = get_residue_plddt(structure_path)
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))
                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                aligned_error_fields = _strict_aligned_error_fields(
                    structure_path=Path(structure_path),
                    summary_json_path=Path(json_file),
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                )
                
                # Create design
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),  # Can be .cif or .pdb
                    json_path=str(json_file),
                    
                    # Backbone grouping
                    backbone_id=parse_backbone_id(design_name),
                    **_design_lineage_fields(
                        job_context,
                        lineage,
                        producer_job=current_job,
                        producer_payload=metrics,
                    ),
                    stage_family=job_context.get("stage_family"),
                    stage_mode=job_context.get("stage_mode"),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance=job_context.get("provenance", {}),
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    iptm=safe_float(iptm),  # NEW: Store RF3 iptm
                    conf_score=safe_float(conf_score),
                    residue_plddt=residue_plddt,
                    confidence_metrics=metrics,
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    **aligned_error_fields,
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing RF3 file {json_file}: {e}")

        # PROTENIX: Current CLI emits *_summary_confidence_sample_*.json alongside
        # *_sample_*.cif in predictions/. Keep legacy confidence.json parsing too.
        protenix_jsons = set()
        if search_dir.name in {"pdb_files", "predictions", "validated_designs", "collected"}:
            for json_path in search_dir.rglob("*_summary_confidence_sample_*.json"):
                protenix_jsons.add(json_path)
            for sub in search_dir.iterdir():
                if sub.is_dir():
                    conf_json = sub / "confidence.json"
                    if conf_json.exists():
                        protenix_jsons.add(conf_json)
        else:
            for json_path in search_dir.glob("*_summary_confidence_sample_*.json"):
                protenix_jsons.add(json_path)
            conf_json = search_dir / "confidence.json"
            if conf_json.exists():
                protenix_jsons.add(conf_json)

        for json_file in sorted(protenix_jsons):
            try:
                structure_path = None
                design_name = json_file.parent.name
                stem = json_file.stem

                # New format:
                #   <name>_summary_confidence_sample_<rank>.json
                #   <name>_sample_<rank>.cif
                if "_summary_confidence_sample_" in stem:
                    base_name, sample_rank = stem.rsplit("_summary_confidence_sample_", 1)
                    design_name = f"{base_name}_sample_{sample_rank}"
                    candidate = json_file.with_name(f"{design_name}.cif")
                    if not candidate.exists():
                        candidate = json_file.with_name(f"{design_name}.pdb")
                    if candidate.exists():
                        structure_path = candidate

                if design_name in ingested_names:
                    continue

                # Legacy format: confidence.json in per-sample subdir
                if structure_path is None:
                    cif_files = list(json_file.parent.glob("*.cif"))
                    if cif_files:
                        structure_path = cif_files[0]
                    else:
                        pdb_files = list(json_file.parent.glob("*.pdb"))
                        if not pdb_files:
                            print(f"[Ingester] No CIF/PDB found for Protenix design {design_name}")
                            continue
                        structure_path = pdb_files[0]

                # Read Protenix confidence metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)

                aligned_pdb_name = str(metrics.get('aligned_pdb') or '').strip()
                if aligned_pdb_name:
                    aligned_candidate = json_file.parent / aligned_pdb_name
                    if aligned_candidate.exists():
                        structure_path = aligned_candidate

                # Protenix confidence keys vary across releases:
                #   current: plddt/ptm/iptm/gpde/chain_*/*_iptm/ranking_score/has_clash
                #   legacy: complex_plddt/complex_pde/...
                plddt = (
                    metrics.get('full_plddt')
                    or metrics.get('complex_plddt')
                    or metrics.get('plddt')
                )
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0

                conf_score = metrics.get('ranking_score') or metrics.get('confidence_score')
                ptm = metrics.get('full_ptm') or metrics.get('ptm')
                iptm = metrics.get('full_iptm') or metrics.get('iptm')
                protein_iptm = metrics.get('protein_iptm')
                ligand_iptm = metrics.get('ligand_iptm')
                complex_iplddt = metrics.get('complex_iplddt')
                complex_ipde = (
                    metrics.get('complex_ipde')
                    or metrics.get('gpde')
                    or metrics.get('complex_pde')
                )
                chains_ptm = metrics.get('chain_ptm') or metrics.get('chains_ptm')
                pair_chains_iptm = metrics.get('chain_pair_iptm') or metrics.get('pair_chains_iptm')
                chain_plddt = metrics.get('chain_plddt')
                has_clash = metrics.get('full_has_clash')
                if has_clash is None:
                    has_clash = metrics.get('has_clash')
                disorder = metrics.get('disorder')
                if disorder is None:
                    disorder = metrics.get('full_disorder_prob_mean')
                num_recycles = metrics.get('num_recycles')
                rmsd_overall = metrics.get('rmsd_overall') or metrics.get('protenix_overall_rmsd')
                rmsd_binder = metrics.get('rmsd_binder') or metrics.get('protenix_binder_rmsd')
                rmsd_target = metrics.get('rmsd_target') or metrics.get('protenix_target_rmsd') or metrics.get('boltz_target_rmsd')

                # Only store true PAE values. gpde/PDE are different metrics and must not be
                # backfilled into the PAE column if we want strict downstream interface scoring.
                pae = metrics.get('complex_pae') or metrics.get('pae')

                # Extract per-residue pLDDT from CIF B-factors
                _, residue_plddt = extract_plddt_from_pdb(structure_path)
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))

                plddt_binder = None
                plddt_target = None
                if allow_chain_ordered_metrics and isinstance(chain_plddt, list) and len(chain_plddt) >= 2:
                    plddt_binder = chain_plddt[0]
                    plddt_target = chain_plddt[1]
                    if plddt_binder is not None and plddt_binder <= 1.0:
                        plddt_binder *= 100.0
                    if plddt_target is not None and plddt_target <= 1.0:
                        plddt_target *= 100.0

                structure_role_fields = _resolve_validation_structure_role_fields(
                    structure_path=Path(structure_path),
                    job_params=job_params,
                    detected_antibody_chains=detected_antibody_chains,
                    detected_target_chain=detected_target_chain,
                )
                geometry_fields = (
                    _compute_validation_geometry_fields(
                        structure_path=Path(structure_path),
                        job_params=job_params,
                        detected_antibody_chains=structure_role_fields.get("detected_antibody_chains"),
                        detected_target_chain=structure_role_fields.get("detected_target_chain"),
                        epitope_residues=epitope_residues,
                    )
                    if allow_validation_interface_metrics else {}
                )
                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                aligned_error_fields = _strict_aligned_error_fields(
                    structure_path=Path(structure_path),
                    summary_json_path=Path(json_file),
                    detected_antibody_chains=structure_role_fields.get("detected_antibody_chains"),
                    detected_target_chain=structure_role_fields.get("detected_target_chain"),
                )

                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(json_file),

                    backbone_id=parse_backbone_id(design_name),
                    **_design_lineage_fields(
                        job_context,
                        lineage,
                        producer_job=current_job,
                        producer_payload=metrics,
                    ),
                    stage_family=(metrics.get("stage_family") or job_context.get("stage_family")),
                    stage_mode=(metrics.get("stage_mode") or job_context.get("stage_mode")),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance=job_context.get("provenance", {}),
                    **_geometry_design_fields(geometry_fields),

                    plddt_overall=safe_float(plddt),
                    plddt_binder=safe_float(plddt_binder),
                    plddt_target=safe_float(plddt_target),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    iptm=safe_float(iptm),
                    protein_iptm=safe_float(protein_iptm),
                    rmsd_overall=safe_float(rmsd_overall),
                    rmsd_binder=safe_float(rmsd_binder),
                    rmsd_target=safe_float(rmsd_target),
                    conf_score=safe_float(conf_score),
                    ligand_iptm=safe_float(ligand_iptm),
                    complex_iplddt=safe_float(complex_iplddt),
                    complex_ipde=safe_float(complex_ipde),
                    chains_ptm=chains_ptm,
                    pair_chains_iptm=pair_chains_iptm,
                    residue_plddt=residue_plddt,
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    disorder=safe_float(disorder),
                    num_recycles=safe_int(num_recycles),
                    has_clash=(bool(has_clash) if has_clash is not None else None),
                    confidence_metrics=metrics,
                    **aligned_error_fields,

                    is_favorite=False,
                    created_at=datetime.utcnow()
                )

                # Store clash info in notes if present
                if has_clash:
                    design.notes = 'steric_clash_detected'

                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

            except Exception as e:
                print(f"[Ingester] Error parsing Protenix file {json_file}: {e}")
                
    # If still no designs, try just finding raw structures (e.g. valid job but missing metadata)
    if designs_created == 0:
        print("[Ingester] No JSON metrics found. Scanning for raw structure files...")

        # Determine job type for model-specific ingestion logic
        job_model_id = None
        try:
            job_result = await session.execute(select(Job.model_id).where(Job.id == job_id))
            job_model_id = job_result.scalar_one_or_none()
        except Exception:
            pass
        is_oligo = (job_model_id or "").lower() in ("oligo_design", "oligo_designer")

        # --- Oligo-specific ingestion ---
        # For oligo_design jobs: ONLY ingest from run/rebuilt/ (full-atom PDBs).
        # Do NOT fallback to rglob which grabs backbone PDBs from run/rfdpoly/ and run/nampnn/.
        if is_oligo:
            print("[Ingester] Oligo design job detected — using oligo-specific ingestion")
            structure_paths = []
            rebuilt_dir = output_path / "run" / "rebuilt"
            if rebuilt_dir.exists():
                structure_paths.extend(list(rebuilt_dir.glob("out_*.pdb")))
                # Also check nested rebuilt/rebuilt/ (older publishDir layout)
                nested = rebuilt_dir / "rebuilt"
                if nested.exists():
                    structure_paths.extend(list(nested.glob("out_*.pdb")))
                print(f"[Ingester] Found {len(structure_paths)} rebuilt PDBs in {rebuilt_dir}")
            
            if not structure_paths:
                print(f"[Ingester] No rebuilt PDBs found for oligo job under {rebuilt_dir}")

            # Parse NA-MPNN quality metrics from nampnn_metrics.json
            nampnn_design_metrics = {}
            for metrics_path in [
                output_path / "run" / "nampnn" / "nampnn_metrics.json",
                output_path / "run" / "rebuilt" / "rebuild_metrics.json",
            ]:
                if metrics_path.exists():
                    try:
                        with open(metrics_path) as f:
                            parsed = json.load(f)
                        # nampnn_metrics.json has a 'designs' list with per-design metrics
                        if "designs" in parsed:
                            for d in parsed["designs"]:
                                conf = d.get("overall_confidence")
                                rec = d.get("seq_rec")
                                header = d.get("header", "")
                                if conf is not None or rec is not None:
                                    nampnn_design_metrics[header] = {
                                        "overall_confidence": conf,
                                        "seq_rec": rec,
                                    }
                        # rebuild_metrics.json has 'nampnn_metrics' dict
                        if "nampnn_metrics" in parsed:
                            for key, metrics in parsed["nampnn_metrics"].items():
                                nampnn_design_metrics[key] = metrics
                        print(f"[Ingester] Parsed {len(nampnn_design_metrics)} design metrics from {metrics_path.name}")
                    except Exception as e:
                        print(f"[Ingester] Warning: could not parse {metrics_path}: {e}")

            for structure_path in structure_paths:
                design_name = structure_path.stem
                if design_name in ingested_names:
                    continue
                    
                # For oligo jobs: B-factors contain NA-MPNN design confidence (not pLDDT)
                # Extract them but label correctly
                bfactor_avg, residue_bfactors = extract_plddt_from_pdb(structure_path)
                
                # Look up NA-MPNN metrics for this design
                overall_confidence = None
                seq_rec = None
                for key, metrics in nampnn_design_metrics.items():
                    if design_name.replace("out_", "") in key or key in design_name:
                        overall_confidence = metrics.get("overall_confidence")
                        seq_rec = metrics.get("seq_rec")
                        break
                
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=None,
                    
                    backbone_id=parse_backbone_id(design_name),
                    
                    # For oligo: B-factors are NA-MPNN design confidence, NOT pLDDT
                    # Store in plddt_overall for viewer compatibility but note it's design confidence
                    plddt_overall=bfactor_avg if bfactor_avg and bfactor_avg > 0 else None,
                    residue_plddt=residue_bfactors,
                    
                    # NA-MPNN quality metrics
                    conf_score=overall_confidence,  # overall_confidence from FASTA header
                    mpnn_score=seq_rec,  # sequence recovery from FASTA header
                    
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                _inherit_source_design_metrics(
                    design,
                    lineage.get("source_design"),
                    structure_path=Path(structure_path),
                )
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

        # --- Standard (non-oligo) ingestion ---
        else:
            structure_paths = []

            if is_maturation_child:
                published_results_dir = output_path / "run" / "ppiflow" / "results"
                if published_results_dir.exists():
                    # For maturation children, only ingest the published final outputs.
                    # Do not recurse into the whole directory tree because that can pull
                    # in intermediate PPIFlow/redesign artifacts and misrepresent them
                    # as final matured designs.
                    approved_names = {
                        report.stem.replace("_maturation_filter", "")
                        for report in published_results_dir.glob("*_maturation_filter.json")
                    }
                    if approved_names:
                        for name in sorted(approved_names):
                            for ext in ("pdb", "cif", "mmcif"):
                                candidate = published_results_dir / f"{name}.{ext}"
                                if candidate.exists():
                                    structure_paths.append(candidate)
                                    break
                    else:
                        structure_paths.extend(sorted(published_results_dir.glob("*.pdb")))
                        structure_paths.extend(sorted(published_results_dir.glob("*.cif")))
                        structure_paths.extend(sorted(published_results_dir.glob("*.mmcif")))
                if not structure_paths:
                    print(f"[Ingester] No published maturation result structures found under {output_path}")
                    return 0

            # For non-oligo jobs: prefer a caller-selected final directory when one
            # exists, otherwise fall back to the normal output tree scan.
            if not is_maturation_child and plr_final_path is not None:
                structure_paths.extend(sorted(plr_final_path.glob("*.pdb")))
                structure_paths.extend(sorted(plr_final_path.glob("*.cif")))
                structure_paths.extend(sorted(plr_final_path.glob("*.mmcif")))

            # For non-oligo jobs: prefer run/rebuilt/ over raw structures
            if not is_maturation_child and plr_final_path is None:
                rebuilt_dir = output_path / "run" / "rebuilt"
                if rebuilt_dir.exists():
                    structure_paths.extend(list(rebuilt_dir.glob("*.pdb")))
                    nested = rebuilt_dir / "rebuilt"
                    if nested.exists():
                        structure_paths.extend(list(nested.glob("*.pdb")))
                    print(f"[Ingester] Found {len(structure_paths)} rebuilt PDBs in {rebuilt_dir}")

                if not structure_paths:
                    def _is_ingestable_raw_structure(path: Path) -> bool:
                        try:
                            rel_parts = path.relative_to(output_path).parts
                        except Exception:
                            rel_parts = path.parts
                        if not rel_parts:
                            return True
                        if rel_parts[0] in {"input", "configs", "spawn", "gates", ".nextflow"}:
                            return False
                        if path.stem in {"normalized_target", "target_template"}:
                            return False
                        return True

                    structure_paths.extend(
                        [path for path in output_path.rglob("*.pdb") if _is_ingestable_raw_structure(path)]
                    )
                    structure_paths.extend(
                        [path for path in output_path.rglob("*.cif") if _is_ingestable_raw_structure(path)]
                    )
                    structure_paths.extend(
                        [path for path in output_path.rglob("*.mmcif") if _is_ingestable_raw_structure(path)]
                    )

            if not structure_paths:
                print(f"[Ingester] No raw structures found under {output_path}")

            for structure_path in structure_paths:
                design_name = structure_path.stem
                if design_name in ingested_names:
                    continue

                lineage = await _resolve_parent_design_lineage(
                    session,
                    job_context,
                    design_name,
                    cache=lineage_cache,
                )
                fam_json_path = _find_fampnn_sidecar_path(structure_path, output_path)
                fam_payload = _load_json_payload(fam_json_path)
                fam_metrics = _extract_fampnn_metrics(fam_payload, structure_path)
                fampnn_record = _build_fampnn_payload(fam_payload, fam_metrics)
                    
                # For raw RFantibody outputs, the meaningful confidence lives in the
                # .trb sidecar rather than the output PDB B-factors.
                rfa_trb = load_rfantibody_trb_summary(structure_path)
                if rfa_trb:
                    plddt = safe_float(rfa_trb.get("plddt_overall"))
                    residue_plddt = rfa_trb.get("residue_plddt")
                else:
                    plddt, residue_plddt = extract_plddt_from_pdb(structure_path)
                    if fampnn_record and str(job_context.get("stage_family") or "").strip().lower() == "fampnn":
                        if plddt is not None and plddt <= 0:
                            plddt = None
                        if isinstance(residue_plddt, list) and not any(safe_float(value) and safe_float(value) > 0 for value in residue_plddt):
                            residue_plddt = None
                structure_cdr_lengths = _parse_hlt_cdr_lengths(Path(structure_path))
                epitope_contact_count = None
                epitope_min_distance = None
                if epitope_residues and structure_path:
                    epitope_contact_count, epitope_min_distance = calculate_epitope_contacts(
                        structure_path,
                        epitope_residues,
                        antibody_chain="A",
                        target_chain="B",
                    )

                design_provenance = {
                    **job_context.get("provenance", {}),
                    "structure_path": str(structure_path),
                }
                if lineage.get("source_design_name"):
                    design_provenance["source_design_name"] = lineage["source_design_name"]
                if lineage.get("source_pdb_path"):
                    design_provenance["source_pdb_path"] = lineage["source_pdb_path"]
                combined_confidence: Dict[str, Any] = {}
                if fampnn_record:
                    design_provenance["fampnn"] = fampnn_record
                    combined_confidence["fampnn"] = fampnn_record
                if rfa_trb:
                    design_provenance["rfantibody"] = rfa_trb
                    combined_confidence["rfantibody"] = rfa_trb.get("rfa_metadata") or rfa_trb
                    
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(fam_json_path) if fam_json_path and fam_json_path.exists() else None,
                    
                    backbone_id=parse_backbone_id(design_name),
                    **_design_lineage_fields(
                        job_context,
                        lineage,
                        producer_job=current_job,
                        producer_payload=fam_payload,
                    ),
                    stage_family=((fam_payload or {}).get("stage_family") or job_context.get("stage_family")),
                    stage_mode=((fam_payload or {}).get("stage_mode") or job_context.get("stage_mode")),
                    selected_loop_scope=job_context.get("selected_loop_scope"),
                    provenance=design_provenance or None,
                    epitope_contact_count=epitope_contact_count,
                    epitope_min_distance=epitope_min_distance,
                    
                    plddt_overall=plddt,
                    residue_plddt=residue_plddt,
                    mpnn_score=fam_metrics.get("mpnn_score"),
                    fampnn_psce=fam_metrics.get("avg_psce"),
                    binder_length=fam_metrics.get("binder_length"),
                    rfa_hotspot_min_distance=safe_float(rfa_trb.get("rfa_hotspot_min_distance")),
                    rfa_hotspot_avg_min_distance=safe_float(rfa_trb.get("rfa_hotspot_avg_min_distance")),
                    rfa_runtime_seconds=safe_float(rfa_trb.get("rfa_runtime_seconds")),
                    rfa_device=rfa_trb.get("rfa_device"),
                    rfa_diffusion_steps=safe_int(rfa_trb.get("rfa_diffusion_steps")),
                    rfa_noise_scale_ca=safe_float(rfa_trb.get("rfa_noise_scale_ca")),
                    rfa_noise_scale_frame=safe_float(rfa_trb.get("rfa_noise_scale_frame")),
                    rfa_guide_scale=safe_float(rfa_trb.get("rfa_guide_scale")),
                    rfa_plddt_initial=safe_float(rfa_trb.get("rfa_plddt_initial")),
                    rfa_plddt_final=safe_float(rfa_trb.get("rfa_plddt_final")),
                    rfa_plddt_delta=safe_float(rfa_trb.get("rfa_plddt_delta")),
                    rfa_plddt_selected=safe_float(rfa_trb.get("rfa_plddt_selected")),
                    rfa_plddt_nonselected=safe_float(rfa_trb.get("rfa_plddt_nonselected")),
                    rfa_design_loops=rfa_trb.get("rfa_design_loops"),
                    rfa_hotspots=rfa_trb.get("rfa_hotspots"),
                    confidence_metrics=combined_confidence or None,
                    cdr_h1_length=structure_cdr_lengths.get("H1"),
                    cdr_h2_length=structure_cdr_lengths.get("H2"),
                    cdr_h3_length=structure_cdr_lengths.get("H3"),
                    cdr_l1_length=structure_cdr_lengths.get("L1"),
                    cdr_l2_length=structure_cdr_lengths.get("L2"),
                    cdr_l3_length=structure_cdr_lengths.get("L3"),
                    
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                _inherit_source_design_metrics(
                    design,
                    lineage.get("source_design"),
                    structure_path=Path(structure_path),
                )
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

    if designs_created > 0:
        try:
            await session.commit()
            print(f"[Ingester] Ingested {designs_created} designs from loose files for job {job_id}")
        except Exception as e:
            print(f"[Ingester] Error committing loose files: {e}")
            await session.rollback()
            return 0
            
    return designs_created


def extract_pdb_files(output_path: Path) -> Path:
    """
    Extract PDB files from tar.gz archives to a pdb_files directory.
    Returns the path to the directory containing extracted PDBs.
    """
    import tarfile
    
    pdb_dir = output_path / "pdb_files"

    if not output_path.exists():
        print(f"[Ingester] Output path missing, skipping extraction: {output_path}")
        return pdb_dir
    
    # Skip if already extracted
    if pdb_dir.exists() and any(pdb_dir.glob("*.pdb")):
        print(f"[Ingester] PDB files already extracted to {pdb_dir}")
        return pdb_dir
    
    pdb_dir.mkdir(exist_ok=True)
    
    # Look for result tar.gz files to extract
    tar_locations = [
        output_path / "run" / "af2" / "af2_results.tar.gz",
        output_path / "run" / "boltz" / "boltz_results.tar.gz", 
        output_path / "run" / "rf3" / "rf3_results.tar.gz",
        output_path / "results" / "best_designs.tar.gz",
    ]
    
    for tar_path in tar_locations:
        if tar_path.exists():
            try:
                with tarfile.open(tar_path, 'r:gz') as tar:
                    for member in tar.getmembers():
                        if member.name.endswith('.pdb'):
                            # Extract just the filename
                            member.name = Path(member.name).name
                            tar.extract(member, pdb_dir)
                            print(f"[Ingester] Extracted {member.name}")
            except Exception as e:
                print(f"[Ingester] Error extracting {tar_path}: {e}")
    
    pdb_count = len(list(pdb_dir.glob("*.pdb")))
    print(f"[Ingester] Extracted {pdb_count} PDB files to {pdb_dir}")
    return pdb_dir


def find_pdb_path(
    output_path: Path,
    design_name: str,
    *,
    producer_output_key: str | None = None,
    producer_artifact_sha256: str | None = None,
) -> str:
    """Resolve one exact, physical published PDB or fail closed.

    The production publication layout is authoritative, while the older layouts
    remain supported only when one exact regular file exists.  A producer output
    key and digest, when projected by the workflow, bind the selected file to
    the typed terminal producer rather than to an inferred basename.
    """

    root = Path(output_path).absolute()
    raw_key = (producer_output_key or "").strip()
    if raw_key:
        if "\\" in raw_key:
            raise FrustraMPNNPersistenceError(
                "protein_design producer output key is unsafe"
            )
        key_path = Path(raw_key)
        if key_path.is_absolute() or any(part in {"", ".", ".."} for part in key_path.parts):
            raise FrustraMPNNPersistenceError(
                "protein_design producer output key is unsafe"
            )
        filename = key_path.name
    else:
        filename = f"{design_name}.pdb"
    if (
        not filename
        or Path(filename).name != filename
        or Path(filename).suffix.lower() != ".pdb"
    ):
        raise FrustraMPNNPersistenceError(
            "protein_design published structure identity is unsafe"
        )

    supported = (
        root / "results" / "best_designs" / filename,
        root / "pdb_files" / filename,
        root / "pdb_files" / "validated_designs" / filename,
        root / "best_designs" / filename,
    )
    matches: list[tuple[Path, str]] = []
    for candidate in supported:
        try:
            present = candidate.exists() or candidate.is_symlink()
        except OSError as exc:
            raise FrustraMPNNPersistenceError(
                "protein_design published structure is unsafe"
            ) from exc
        if not present:
            continue
        try:
            fd = _open_absolute_no_symlinks(candidate, directory=False)
            try:
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            finally:
                os.close(fd)
        except (OSError, FrustraMPNNPersistenceError) as exc:
            raise FrustraMPNNPersistenceError(
                "protein_design published structure is an unsafe symlink or non-regular file"
            ) from exc
        matches.append((candidate, digest.hexdigest()))

    if not matches:
        raise FrustraMPNNPersistenceError(
            f"protein_design published structure is missing: {filename}"
        )
    if len(matches) != 1:
        raise FrustraMPNNPersistenceError(
            f"protein_design published structure is ambiguous: {filename}"
        )

    selected, observed_sha256 = matches[0]
    expected_sha256 = (producer_artifact_sha256 or "").strip().lower()
    if expected_sha256:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise FrustraMPNNPersistenceError(
                "protein_design producer artifact SHA-256 is invalid"
            )
        if observed_sha256 != expected_sha256:
            raise FrustraMPNNPersistenceError(
                "protein_design published structure SHA-256 contradicts producer identity"
            )
    return os.fspath(selected)


def safe_float(value) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if value is None or value == '' or value == 'NA' or value == 'nan':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def safe_int(value) -> Optional[int]:
    """Safely convert to int, returning None on failure."""
    if value is None or value == '' or value == 'NA' or value == 'nan':
        return None
    try:
        return int(float(value))  # Handle "3.0" -> 3
    except (ValueError, TypeError):
        return None


def extract_plddt_from_pdb(pdb_path):
    """
    Extract pLDDT from structure B-factors.
    Supports both PDB and CIF files via Biotite, with fallback to manual parsing.
    Returns (avg_plddt, per_residue_array).
    """
    path = Path(pdb_path) if not isinstance(pdb_path, Path) else pdb_path
    
    # Try Biotite first (handles PDB and CIF)
    try:
        from .structure_utils import get_residue_plddt
        avg_plddt, per_residue = get_residue_plddt(path)
        if avg_plddt is not None:
            return avg_plddt, per_residue
    except ImportError:
        pass  # Biotite not available, fall through to manual
    except Exception as e:
        print(f"[Ingester] Biotite extraction failed for {path}, trying manual: {e}")
    
    # Fallback: Manual PDB parsing (only works for .pdb files)
    if not str(path).lower().endswith('.pdb'):
        print(f"[Ingester] Cannot manually parse non-PDB file: {path}")
        return None, None
        
    try:
        residue_scores = []  # One score per residue (CA atom)
        all_scores = []  # All atom scores for average
        
        with open(path, 'r') as f:
            for line in f:
                if line.startswith("ATOM  ") or line.startswith("HETATM"):
                    # B-factor is columns 61-66 (1-indexed) -> 60-66 (0-indexed)
                    try:
                        bfactor = float(line[60:66].strip())
                        all_scores.append(bfactor)
                        
                        # Extract CA atoms only for per-residue (one per residue)
                        atom_name = line[12:16].strip()
                        if atom_name == "CA":
                            residue_scores.append(round(bfactor, 2))
                    except ValueError:
                        pass
        
        avg_plddt = sum(all_scores) / len(all_scores) if all_scores else None
        per_residue = residue_scores if residue_scores else None
        
        return avg_plddt, per_residue
    except Exception:
        return None, None


async def get_job_summary_metrics(output_dir: str) -> dict:
    """
    Read success_metrics.json for job summary stats.
    """
    metrics_path = Path(output_dir) / "results" / "success_metrics.json"
    
    if not metrics_path.exists():
        return {}
    
    try:
        with open(metrics_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[Ingester] Error reading metrics: {e}")
        return {}
