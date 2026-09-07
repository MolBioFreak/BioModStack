from __future__ import annotations

import math

from services.core_protein_scientific_contract import revision_for_job
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


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_numeric_value(subject: Any, *keys: str) -> Optional[float]:
    for key in keys:
        parsed = _safe_float(_value_from_subject(subject, key))
        if parsed is not None:
            return parsed
    return None


def _first_numeric_confidence(confidence: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        parsed = _safe_float(confidence.get(key))
        if parsed is not None:
            return parsed
    return None


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


def build_rosetta_interface_metric(
    *,
    value: Optional[float],
    artifact_path: Optional[str] = None,
    interface_id: Optional[str] = None,
    stage_mode: Optional[str] = "maturation",
) -> Dict[str, Any]:
    return metric_record(
        metric_key="rosetta_interface_score",
        display_name="Rosetta interface score",
        value=value,
        unit="REU",
        direction="more_negative_is_better",
        stage_family="ppiflow",
        stage_mode=stage_mode,
        metric_source="rosetta_interface_analyzer",
        scoring_backend="pyrosetta_interface_analyzer_mover",
        artifact_path=artifact_path,
        formula="Rosetta InterfaceAnalyzerMover dG for antibody-vs-antigen chain groups",
        scope="interface",
        region_scope="antibody_antigen_interface",
        is_model_native=False,
        is_bms_derived=True,
        is_validator_metric=False,
        is_final_rank_metric=False,
        confidence_level="physics_score",
        notes="Raw Rosetta dG in REU; more negative is better. Preserve sign convention before combining with validator confidence.",
        provenance={
            "interface_id": interface_id,
            "score_unit": "REU",
            "score_direction": "more_negative_is_better",
            "dockq_used": False,
            "refold_comparison_used": False,
        },
    )


def build_ppiflow_paper_rank_metric(
    *,
    validator_iptm: Optional[float],
    rosetta_interface_score: Optional[float],
    artifact_path: Optional[str] = None,
    stage_mode: Optional[str] = "maturation",
) -> Dict[str, Any]:
    value = None
    if validator_iptm is not None and rosetta_interface_score is not None:
        value = round(100.0 * validator_iptm - rosetta_interface_score, 6)
    return metric_record(
        metric_key="ppiflow_paper_rank_score",
        display_name="PPIFlow paper-style composite rank",
        value=value,
        unit="composite_score",
        direction="higher_is_better",
        stage_family="ppiflow",
        stage_mode=stage_mode,
        metric_source="validator_confidence_plus_rosetta_interface",
        scoring_backend="biomodstack_metric_harmonization",
        artifact_path=artifact_path,
        formula="100 * validator_iptm - rosetta_interface_score",
        scope="design",
        region_scope="complex",
        is_model_native=False,
        is_bms_derived=True,
        is_validator_metric=True,
        is_final_rank_metric=True,
        confidence_level="paper_style_composite_when_inputs_present",
        notes="Paper-style PPIFlow rank is only valid when validator iPTM and raw Rosetta interface score are both present. DockQ/refold are intentionally excluded unless a reference-backed comparison workflow supplies them explicitly.",
        provenance={
            "validator_iptm": validator_iptm,
            "rosetta_interface_score": rosetta_interface_score,
            "rosetta_score_direction": "more_negative_is_better",
            "dockq_used": False,
            "refold_comparison_used": False,
        },
    )


def _finite_real(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_ppiflow_rank_inputs(subject: Any) -> Dict[str, Any]:
    """Validate bound native scalar evidence once for both rank and completeness.

    This is a pure validator, not an authority check. The public projection callers
    opt into it only with a trusted persisted Job revision.
    """
    confidence = _confidence_payload(subject)
    inputs = confidence.get("ppiflow_rank_inputs", {})
    malformed_inputs = not isinstance(inputs, Mapping)
    if malformed_inputs:
        inputs = {}
    binding = confidence.get("ppiflow_rank_binding")
    valid_binding = (
        isinstance(binding, Mapping)
        and binding.get("candidate_id") == _value_from_subject(subject, "name")
        and isinstance(binding.get("document_id"), str) and bool(binding["document_id"])
        and isinstance(binding.get("structure_sha256"), str)
        and len(binding["structure_sha256"]) == 64
        and all(c in "0123456789abcdef" for c in binding["structure_sha256"])
    )
    binding = binding if isinstance(binding, Mapping) else {}
    states = {}
    values = {}
    records = {}
    for key, label, aliases, kind in (
        ("validator_iptm", "iptm", ("iptm", "validator_iptm"), "native_scalar_iptm"),
        ("rosetta_interface_score", "rosetta", ("rosetta_interface_score", "rosetta_interface_dg"), "raw_interface_score"),
    ):
        record = inputs.get(key)
        state, reason = "ok", None
        if malformed_inputs:
            state, reason = "invalid", "invalid_rank_evidence"
        elif record is None:
            state, reason = "unavailable", f"missing_{label}_evidence"
        elif not isinstance(record, Mapping):
            state, reason = "invalid", f"invalid_{label}_evidence"
        else:
            value = record.get("value")
            digest = record.get("source_sha256")
            if (not _finite_real(value)
                    or (label == "iptm" and not 0 <= value <= 1)):
                state, reason = "invalid", f"invalid_{label}_scalar"
            elif record.get("metric_kind") != kind or (label == "rosetta" and record.get("unit") != "REU"):
                state, reason = "invalid", f"invalid_{label}_kind"
            elif (not isinstance(digest, str) or len(digest) != 64
                  or any(c not in "0123456789abcdef" for c in digest)):
                state, reason = "invalid", f"invalid_{label}_source_hash"
            elif ("ppiflow_rank_binding" in confidence and (
                  not valid_binding
                  or record.get("document_id") != binding["document_id"]
                  or record.get("structure_sha256") != binding["structure_sha256"])):
                state, reason = "invalid", f"invalid_{label}_structure_binding"
            elif (not record.get("candidate_id") or record.get("candidate_id") != _value_from_subject(subject, "name")
                  or not isinstance(record.get("interface_id"), str) or not record["interface_id"].strip()):
                state, reason = "invalid", f"invalid_{label}_binding"
            else:
                duplicates = [_value_from_subject(subject, alias) for alias in aliases]
                duplicates += [confidence.get(alias) for alias in aliases]
                if any(not _finite_real(v) or v != value
                       for v in duplicates if v is not None):
                    state, reason = "invalid", f"conflicting_{label}_aliases"
                else:
                    values[key] = value
                    records[key] = dict(record)
        states[key] = {"state": state, "reason_code": reason}
    if len(records) == 2 and records["validator_iptm"]["interface_id"] != records["rosetta_interface_score"]["interface_id"]:
        states["validator_iptm"] = {"state": "invalid", "reason_code": "interface_scope_mismatch"}
    failure = next((s for s in states.values() if s["state"] == "invalid"), None)
    failure = failure or next((s for s in states.values() if s["state"] != "ok"), None)
    value = None if failure else 100.0 * values["validator_iptm"] - values["rosetta_interface_score"]
    if value is not None and not math.isfinite(value):
        failure = {"state": "invalid", "reason_code": "nonfinite_composite_rank"}
        value = None
    record = build_ppiflow_paper_rank_metric(validator_iptm=None, rosetta_interface_score=None)
    record.update(value=value, **(failure or {"state": "ok", "reason_code": None}))
    record["provenance"].update(inputs=records)
    return {"record": record, "inputs": states}


def build_ppiflow_rank_envelope(
    subject: Any, *, descriptor: Mapping[str, Any], expected_source: Mapping[str, Any],
) -> Dict[str, Any]:
    """Ingress adapter for the closed canonical metric, not a new rank consumer.

    The ingestion owner must hash/validate its actual source document and supply
    its exact approved descriptor and candidate/document mapping. This function
    never derives identity from filenames or substitutes a source hash. Native
    metric_record metadata remains separate from this persisted envelope.
    """
    from services.core_protein_scientific_contract import validate_descriptor, validate_metric

    descriptor = validate_descriptor(descriptor)
    if (descriptor["metric_key"] != "ppiflow_paper_rank_score"
            or descriptor["unit"] != "composite_score"
            or descriptor["direction"] != "higher_is_better"):
        raise ValueError("PPIFlow rank descriptor contradicts its native formula")
    result = validate_ppiflow_rank_inputs(subject)["record"]
    state, value, reason = result["state"], result["value"], result["reason_code"]
    inputs = _confidence_payload(subject).get("ppiflow_rank_inputs")
    if expected_source.get("candidate_id") != _value_from_subject(subject, "name"):
        state, value, reason = "invalid", None, "rank_source_candidate_mismatch"
    elif isinstance(inputs, Mapping):
        for item in inputs.values():
            if isinstance(item, Mapping) and (
                item.get("document_id") != expected_source.get("document_id")
                or item.get("interface_id") != descriptor["scope"]
            ):
                state, value, reason = "invalid", None, "rank_source_scope_mismatch"
                break
    return validate_metric({**descriptor, "state": state, "value": value,
                            "reason_code": reason, "source": dict(expected_source)},
                           expected_source=expected_source)


def build_design_metric_provenance(subject: Any, *, job: Any = None) -> Dict[str, Any]:
    provenance: Dict[str, Any] = {"metrics": []}
    confidence = _confidence_payload(subject)
    artifact_path = _value_from_subject(subject, "json_path") or _value_from_subject(subject, "pdb_path")
    stage_mode = _value_from_subject(subject, "stage_mode") or "maturation"
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
    rosetta_interface_score = _first_numeric_value(subject, "rosetta_interface_score", "rosetta_interface_dg")
    if rosetta_interface_score is None:
        rosetta_interface_score = _first_numeric_confidence(confidence, "rosetta_interface_score", "rosetta_interface_dg")
    validator_iptm = _first_numeric_value(subject, "iptm", "ptm")
    if validator_iptm is None:
        validator_iptm = _first_numeric_confidence(confidence, "iptm", "ptm", "validator_iptm")
    if rosetta_interface_score is not None:
        provenance["metrics"].append(
            build_rosetta_interface_metric(
                value=rosetta_interface_score,
                artifact_path=artifact_path,
                interface_id=_value_from_subject(subject, "rosetta_interface_id"),
                stage_mode=stage_mode,
            )
        )
    if revision_for_job(job) == 1:
        provenance["metrics"].append(validate_ppiflow_rank_inputs(subject)["record"])
    elif validator_iptm is not None and rosetta_interface_score is not None:
        provenance["metrics"].append(
            build_ppiflow_paper_rank_metric(
                validator_iptm=validator_iptm,
                rosetta_interface_score=rosetta_interface_score,
                artifact_path=artifact_path,
                stage_mode=stage_mode,
            )
        )
    return provenance


def build_design_metric_completeness(subject: Any, *, job: Any = None) -> Dict[str, Any]:
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
        or confidence.get("validator_backend") in {"boltz2", "protenix"}
    )
    has_rosetta = bool(
        _has_numeric(subject, "rosetta_interface_score")
        or confidence.get("rosetta_interface_score") is not None
    )
    paper_rank = bool(has_validator and has_rosetta)
    strict_rank = None
    if revision_for_job(job) == 1:
        strict_rank = validate_ppiflow_rank_inputs(subject)
        has_validator = strict_rank["inputs"]["validator_iptm"]["state"] == "ok"
        has_rosetta = strict_rank["inputs"]["rosetta_interface_score"]["state"] == "ok"
        paper_rank = strict_rank["record"]["state"] == "ok"

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
            **({"paper_rank_reason_code": strict_rank["record"]["reason_code"]} if strict_rank else {}),
        },
    }
