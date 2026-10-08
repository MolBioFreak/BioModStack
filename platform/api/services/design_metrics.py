from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional


def _value_from_subject(subject: Any, key: str) -> Any:
    if isinstance(subject, Mapping):
        return subject.get(key)
    return getattr(subject, key, None)


def _confidence_payload(subject: Any) -> Mapping[str, Any]:
    payload = _value_from_subject(subject, "confidence_metrics")
    return payload if isinstance(payload, Mapping) else {}


def _has_numeric(subject: Any, *keys: str) -> bool:
    for key in keys:
        value = _value_from_subject(subject, key)
        if isinstance(value, (int, float)):
            return True
    return False


def _has_nonempty(subject: Any, *keys: str) -> bool:
    for key in keys:
        value = _value_from_subject(subject, key)
        if value not in (None, "", [], {}):
            return True
    return False


def metric_record(
    *,
    metric_key: str,
    display_name: str,
    value: Any,
    unit: Optional[str],
    direction: str,
    stage_family: str,
    stage_mode: Optional[str],
    metric_source: str,
    scoring_backend: str,
    artifact_path: Optional[str],
    formula: str,
    scope: str = "design",
    region_scope: str = "all_residues",
    is_model_native: bool = False,
    is_bms_derived: bool = False,
    is_validator_metric: bool = False,
    is_final_rank_metric: bool = False,
    confidence_level: str = "raw_model_output",
    notes: str = "",
    provenance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "metric_key": metric_key,
        "display_name": display_name,
        "value": value,
        "unit": unit,
        "direction": direction,
        "stage_family": stage_family,
        "stage_mode": stage_mode,
        "metric_source": metric_source,
        "scoring_backend": scoring_backend,
        "artifact_path": artifact_path,
        "formula": formula,
        "scope": scope,
        "region_scope": region_scope,
        "is_model_native": is_model_native,
        "is_bms_derived": is_bms_derived,
        "is_validator_metric": is_validator_metric,
        "is_final_rank_metric": is_final_rank_metric,
        "confidence_level": confidence_level,
        "notes": notes,
        "provenance": dict(provenance or {}),
    }


def build_fampnn_psce_metric(
    *,
    value: Optional[float],
    artifact_path: Optional[str] = None,
    stage_mode: Optional[str] = "sequence_design",
) -> Dict[str, Any]:
    return metric_record(
        metric_key="fampnn_avg_psce",
        display_name="FA-MPNN average pSCE",
        value=value,
        unit="angstrom",
        direction="lower_is_better",
        stage_family="fampnn",
        stage_mode=stage_mode,
        metric_source="fampnn_output_pdb_bfactor",
        scoring_backend="fampnn",
        artifact_path=artifact_path,
        formula="mean predicted sidechain error/confidence extracted from output structure B-factors",
        scope="design",
        region_scope="all_residues",
        is_model_native=True,
        is_bms_derived=True,
        is_validator_metric=False,
        is_final_rank_metric=False,
        confidence_level="qc_gate",
        notes="pSCE is predicted sidechain error/confidence; it is not binding evidence or a complete FA-MPNN sequence-design rank metric.",
    )


def build_ppiflow_local_objective_metric(
    *,
    value: Optional[float],
    objective_mode: Optional[str] = None,
    artifact_path: Optional[str] = None,
    stage_mode: Optional[str] = "maturation",
) -> Dict[str, Any]:
    return metric_record(
        metric_key="bms_ppiflow_local_objective_score",
        display_name="BMS local PPIFlow objective",
        value=value,
        unit="bms_local_score",
        direction="lower_is_better",
        stage_family="ppiflow",
        stage_mode=stage_mode,
        metric_source="ppiflow_local_score_json",
        scoring_backend="biomodstack_local_pair_energy_geometry",
        artifact_path=artifact_path,
        formula=f"BMS local maturation objective ({objective_mode or 'unspecified'}); not upstream PPIFlow paper composite rank",
        scope="design",
        region_scope="selected_regions",
        is_model_native=False,
        is_bms_derived=True,
        is_validator_metric=False,
        is_final_rank_metric=False,
        confidence_level="local_heuristic",
        notes="This is a BMS-local refinement heuristic and should not be reported as upstream PPIFlow paper final rank.",
        provenance={
            "objective_mode": objective_mode,
            "af3score_used": False,
            "rosetta_interface_score_used": False,
            "upstream_ppiflow_paper_rank_used": False,
        },
    )


def build_design_metric_provenance(subject: Any) -> Dict[str, Any]:
    provenance: Dict[str, Any] = {"metrics": []}
    fampnn_psce = _value_from_subject(subject, "fampnn_psce")
    if fampnn_psce is not None:
        provenance["metrics"].append(
            build_fampnn_psce_metric(
                value=fampnn_psce,
                artifact_path=_value_from_subject(subject, "pdb_path"),
                stage_mode=_value_from_subject(subject, "stage_mode") or "sequence_design",
            )
        )
    ppiflow_objective = _value_from_subject(subject, "ppiflow_objective_score")
    if ppiflow_objective is not None:
        provenance["metrics"].append(
            build_ppiflow_local_objective_metric(
                value=ppiflow_objective,
                objective_mode=_value_from_subject(subject, "ppiflow_objective_mode"),
                artifact_path=_value_from_subject(subject, "json_path"),
                stage_mode=_value_from_subject(subject, "stage_mode") or "maturation",
            )
        )
    return provenance


def build_design_metric_completeness(subject: Any) -> Dict[str, Any]:
    confidence = _confidence_payload(subject)
    has_psce = _value_from_subject(subject, "fampnn_psce") is not None
    has_seq_probs = bool(
        _has_nonempty(subject, "fampnn_seq_prob_metrics", "fampnn_sequence_confidence")
        or confidence.get("fampnn_seq_probs_available") is True
        or confidence.get("fampnn_mean_sampled_log_prob") is not None
    )
    has_mutation_score = bool(
        _has_numeric(subject, "fampnn_mutation_score", "fampnn_parent_child_delta_score")
        or confidence.get("fampnn_mutation_score") is not None
    )

    has_local_ppiflow = _has_numeric(subject, "ppiflow_objective_score")
    has_validator = bool(
        _has_numeric(subject, "iptm", "ptm")
        or _has_nonempty(subject, "pair_chains_iptm")
        or confidence.get("iptm") is not None
        or confidence.get("pair_chains_iptm") is not None
        or confidence.get("validator_backend") in {"af3score", "boltz2", "protenix"}
    )
    has_rosetta = bool(
        _has_numeric(subject, "rosetta_interface_score")
        or confidence.get("rosetta_interface_score") is not None
    )
    paper_rank = bool(has_validator and has_rosetta)

    missing: List[str] = []
    if has_psce and not has_seq_probs:
        missing.append("fampnn_seq_probs")
    if has_local_ppiflow and not has_validator:
        missing.append("ppiflow_validator_confidence")
    if has_local_ppiflow and not has_rosetta:
        missing.append("ppiflow_rosetta_interface_score")
    if has_local_ppiflow and not paper_rank:
        missing.append("ppiflow_paper_composite_rank")

    return {
        "overall_status": "complete" if not missing else "partial",
        "missing": missing,
        "fampnn": {
            "pse_available": has_psce,
            "psce_available": has_psce,
            "seq_probs_available": has_seq_probs,
            "mutation_score_available": has_mutation_score,
        },
        "ppiflow": {
            "local_objective_available": has_local_ppiflow,
            "validator_confidence_available": has_validator,
            "rosetta_interface_score_available": has_rosetta,
            "paper_rank_available": paper_rank,
        },
    }
