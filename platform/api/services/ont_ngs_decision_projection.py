from __future__ import annotations

import copy
from typing import Any, Mapping


class OntNgsDecisionProjectionError(RuntimeError):
    pass


_CHECKS: dict[str, tuple[str, str, dict[str, str]]] = {
    "contamination": (
        "expected_reference_screen",
        "Expected-reference mapping and unmapped-fraction screen only.",
        {
            "screen_basis": "categorical",
            "organism_identity_claimed": "boolean",
            "total_reads": "reads",
            "mapped_reads": "reads",
            "unmapped_reads": "reads",
            "unmapped_fraction": "fraction",
        },
    ),
    "coverage": (
        "coverage",
        "Coverage completeness and low-depth exclusion across the bound reference.",
        {
            "row_count": "reference_positions",
            "coverage_fraction": "fraction",
            "low_depth_fraction": "fraction",
            "low_depth_positions": "reference_positions",
            "minimum_depth": "alignment_observations",
            "mixed_allele_positions": "reference_positions",
            "strand_imbalanced_positions": "reference_positions",
        },
    ),
    "read_support": (
        "read_support",
        "Per-position depth, allele-mixture, and strand-balance evidence.",
        {
            "row_count": "reference_positions",
            "coverage_fraction": "fraction",
            "low_depth_fraction": "fraction",
            "low_depth_positions": "reference_positions",
            "minimum_depth": "alignment_observations",
            "mixed_allele_positions": "reference_positions",
            "strand_imbalanced_positions": "reference_positions",
        },
    ),
    "sequence_identity": (
        "sequence_identity",
        "Observed consensus identity and edit evidence against the bound reference.",
        {
            "canonicalization": "categorical",
            "consensus_support_validation": "evidence",
            "edit_cost": "edits",
            "identity_fraction": "fraction",
            "observed_length": "base_pairs",
            "orientation": "categorical",
            "reference_length": "base_pairs",
            "rotation_offset": "base_pairs",
        },
    ),
    "topology": (
        "topology",
        "Circular-boundary support and contradictory breakpoint evidence.",
        {
            "aligned_dimer_reads": "reads",
            "alignment_records": "alignment_records",
            "contradictory_breakpoint_evidence": "boolean",
            "edge_window_bp": "base_pairs",
            "evidence_basis": "categorical",
            "expected_topology": "categorical",
            "mapped_unique_reads": "reads",
            "non_boundary_split_reads": "reads",
            "origin_spanning_reads": "reads",
            "evidence_sha256": "sha256_digests",
            "reason": "categorical_or_null",
            "schema": "schema_id",
            "secondary_anomaly_fraction": "fraction",
            "samtools_returncode": "exit_code",
            "state": "categorical",
        },
    ),
}


def _project_check(raw: Any, *, purpose: str, units: dict[str, str]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {"status", "reason_codes", "metrics"}:
        raise OntNgsDecisionProjectionError("decision check shape is invalid")
    status = raw.get("status")
    reasons = raw.get("reason_codes")
    metrics = raw.get("metrics")
    if status not in {"pass", "review", "fail", "not_evaluated"}:
        raise OntNgsDecisionProjectionError("decision check status is invalid")
    if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
        raise OntNgsDecisionProjectionError("decision check reasons are invalid")
    if not isinstance(metrics, Mapping) or set(metrics) != set(units):
        raise OntNgsDecisionProjectionError("decision check metrics are invalid")
    return {
        "status": status,
        "purpose": purpose,
        "reason_codes": list(reasons),
        "metrics": dict(metrics),
        "units": dict(units),
    }


def _project_variant(raw: Any) -> dict[str, Any]:
    required = {
        "id",
        "kind",
        "position_1based",
        "end_1based",
        "ref",
        "alt",
        "support_status",
        "depth",
        "support_fraction",
        "circular_event_id",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise OntNgsDecisionProjectionError("variant shape is invalid")
    kind = raw.get("kind")
    position = raw.get("position_1based")
    end = raw.get("end_1based")
    ref = raw.get("ref")
    alt = raw.get("alt")
    if (
        kind not in {"SNV", "INS", "DEL", "MNV", "COMPLEX"}
        or not isinstance(position, int)
        or isinstance(position, bool)
        or position < 1
        or not isinstance(end, int)
        or isinstance(end, bool)
        or end < position
        or not isinstance(ref, str)
        or not ref
        or not isinstance(alt, str)
        or not alt
        or end != position + len(ref) - 1
    ):
        raise OntNgsDecisionProjectionError("variant coordinates are invalid")
    affected_kind = "reference_bases"
    affected_start = position
    affected_end = end
    if kind == "DEL":
        if len(ref) <= len(alt) or not ref.startswith(alt):
            raise OntNgsDecisionProjectionError("deletion is not normalized")
        affected_start = position + len(alt)
    elif kind == "INS":
        if len(alt) <= len(ref) or not alt.startswith(ref):
            raise OntNgsDecisionProjectionError("insertion is not normalized")
        affected_kind = "between_bases"
        affected_start = end
        affected_end = end
    return {
        "id": raw.get("id"),
        "kind": kind,
        "normalization": "vcf_left_anchored_v1",
        "record_start_1based": position,
        "record_end_1based": end,
        "affected_interval_kind": affected_kind,
        "affected_start_1based": affected_start,
        "affected_end_1based": affected_end,
        "ref": ref,
        "alt": alt,
        "support_status": raw.get("support_status"),
        "depth": raw.get("depth"),
        "support_fraction": raw.get("support_fraction"),
        "circular_event_id": raw.get("circular_event_id"),
    }


def _project_topology_source(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {"status", "reason_codes", "metrics"}:
        raise OntNgsDecisionProjectionError("topology check shape is invalid")
    metrics = raw.get("metrics")
    if not isinstance(metrics, Mapping) or "provenance" not in metrics:
        raise OntNgsDecisionProjectionError("topology evidence is invalid")
    provenance = metrics.get("provenance")
    expected_provenance = {
        "alignment_bam_sha256",
        "breakpoint_call_sha256",
        "reference_sha256",
        "samtools_command",
        "samtools_returncode",
        "samtools_stderr",
        "secondary_summary_sha256",
    }
    if not isinstance(provenance, Mapping) or set(provenance) != expected_provenance:
        raise OntNgsDecisionProjectionError("topology evidence is invalid")
    projected_metrics = {key: value for key, value in metrics.items() if key != "provenance"}
    projected_metrics["evidence_sha256"] = {
        "alignment_bam": provenance.get("alignment_bam_sha256"),
        "breakpoint_call": provenance.get("breakpoint_call_sha256"),
        "reference": provenance.get("reference_sha256"),
        "secondary_summary": provenance.get("secondary_summary_sha256"),
    }
    projected_metrics["samtools_returncode"] = provenance.get("samtools_returncode")
    return {
        "status": raw.get("status"),
        "reason_codes": raw.get("reason_codes"),
        "metrics": projected_metrics,
    }


def project_verification_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    checks = manifest.get("checks")
    variants = manifest.get("variants")
    if not isinstance(checks, Mapping) or set(checks) != set(_CHECKS):
        raise OntNgsDecisionProjectionError("decision checks are invalid")
    if not isinstance(variants, list):
        raise OntNgsDecisionProjectionError("variants are invalid")
    projected_checks: dict[str, Any] = {}
    for source_name, (result_name, purpose, units) in _CHECKS.items():
        raw_check = checks[source_name]
        if source_name == "topology":
            raw_check = _project_topology_source(raw_check)
        projected_checks[result_name] = _project_check(raw_check, purpose=purpose, units=units)
    threshold_profile = copy.deepcopy(manifest.get("threshold_profile"))
    if isinstance(threshold_profile, dict) and isinstance(threshold_profile.get("values"), dict):
        description = threshold_profile["values"].get("description")
        if isinstance(description, str):
            threshold_profile["values"]["description"] = description.replace(
                "contamination-screen",
                "expected-reference mapping and unmapped-fraction screen",
            )
    return {
        "verdict": manifest.get("verdict"),
        "reason_codes": manifest.get("reason_codes") or [],
        "summary": manifest.get("summary") or {},
        "checks": projected_checks,
        "variants": [_project_variant(item) for item in variants],
        "threshold_profile": threshold_profile,
    }
