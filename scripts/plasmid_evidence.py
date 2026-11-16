"""Shared validation of canonical plasmid structural summaries.

This authenticates table semantics, not the upstream biological method. A
positive eligible denominator is mandatory for an evaluated anomaly fraction.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any


class StructuralEvidenceUnavailable(ValueError):
    """Required screening was not measured; this is not a biological failure."""


def read_summary(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file():
        raise StructuralEvidenceUnavailable("STRUCTURAL_SCREEN_MISSING")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames or []
        if not fields or len(fields) != len(set(fields)) or any(not name for name in fields):
            raise ValueError("structural summary has invalid headers")
        rows = list(reader)
    if not rows:
        raise StructuralEvidenceUnavailable("STRUCTURAL_SCREEN_NOT_EVALUATED")
    if len(rows) != 1 or any(None in row or None in row.values() for row in rows):
        raise ValueError("structural summary must contain exactly one complete row")
    return rows


def required_count(row: dict[str, str], key: str) -> int:
    value = str(row.get(key, "")).strip()
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        raise ValueError(f"structural {key} must be a non-negative decimal integer")
    return int(value)


def _alias(row: dict[str, str], *names: str) -> str:
    values = {str(row[name]).strip().lower() for name in names if str(row.get(name, "")).strip()}
    if len(values) > 1:
        raise ValueError(f"conflicting structural aliases: {names}")
    return next(iter(values), "")


def breakpoint_contradiction(row: dict[str, str], reference_length: int) -> bool:
    status = _alias(row, "call_status", "breakpoint_status", "status")
    confidence = _alias(row, "call_confidence", "confidence", "breakpoint_confidence")
    if not status:
        raise StructuralEvidenceUnavailable("STRUCTURAL_BREAKPOINT_STATUS_MISSING")
    if status in {"not_run", "not_evaluable", "unavailable", "failed", "error", "unknown"}:
        raise StructuralEvidenceUnavailable("STRUCTURAL_BREAKPOINT_NOT_EVALUATED")
    if status in {"split_detected_unresolved", "seam_only_unresolved"}:
        raise StructuralEvidenceUnavailable("STRUCTURAL_BREAKPOINT_UNRESOLVED")
    if status not in {"split_supported", "provisional_split_supported", "no_junction_evidence", "no_split"}:
        raise ValueError("unsupported structural breakpoint status")
    split_supported = "split" in status and status != "split_detected_unresolved"
    credible = confidence in {"high", "medium"} or status in {"split_supported", "provisional_split_supported"}
    if not split_supported or not credible:
        return False
    boundary = _alias(row, "primary_breakpoint_in_boundary_window", "in_boundary_window")
    if boundary:
        if boundary not in {"0", "false", "no", "n", "outside", "1", "true", "yes", "y", "inside"}:
            raise ValueError("unrecognized structural boundary flag")
        return boundary in {"0", "false", "no", "n", "outside"}
    # The current canonical producer publishes coordinates, not a boundary flag.
    position = required_count(row, "primary_position_mod_ref")
    window = required_count(row, "boundary_window_bp")
    if reference_length <= 0 or not 1 <= position <= reference_length or not 0 < window <= reference_length // 2:
        raise ValueError("invalid structural breakpoint coordinates/window")
    return window < position <= reference_length - window


def validate_structural_rows(
    breakpoint_rows: list[dict[str, str]],
    secondary_rows: list[dict[str, str]],
    reference_length: int,
) -> dict[str, Any]:
    if not breakpoint_rows or not secondary_rows:
        raise StructuralEvidenceUnavailable("STRUCTURAL_SCREEN_NOT_EVALUATED")
    if len(breakpoint_rows) != 1 or len(secondary_rows) != 1:
        raise ValueError("structural summaries must each contain exactly one row")
    secondary = secondary_rows[0]
    numerator = required_count(secondary, "non_boundary_split_reads")
    denominator = required_count(secondary, "aligned_dimer_reads")
    # Do not silently substitute whole-BAM read counts for a dimer-screen cohort.
    if denominator == 0:
        raise StructuralEvidenceUnavailable("STRUCTURAL_SCREEN_NO_ELIGIBLE_READS")
    if numerator > denominator:
        raise ValueError("structural anomaly count exceeds its eligible denominator")
    call_denominator = breakpoint_rows[0].get("aligned_dimer_reads")
    if call_denominator not in {None, ""} and required_count(breakpoint_rows[0], "aligned_dimer_reads") != denominator:
        raise ValueError("structural summaries disagree on their eligible denominator")
    return {
        "non_boundary_split_reads": numerator,
        "aligned_dimer_reads": denominator,
        "secondary_anomaly_fraction": numerator / denominator,
        "contradictory_breakpoint_evidence": breakpoint_contradiction(breakpoint_rows[0], reference_length),
    }


_RAPID_KIT = re.compile(r"SQK-(RAD|RBK|RPB|ULK)")
_LIGATION_KIT = re.compile(r"SQK-(LSK|NBD|MLK)")


def library_prep_from_kits(kits) -> str:
    """Rapid kits cut every molecule; ligation kits attach to the ends and keep it whole."""
    names = {str(kit).strip().upper() for kit in (kits or ()) if str(kit).strip()}
    found = {
        prep for prep, pattern in (("rapid", _RAPID_KIT), ("ligation", _LIGATION_KIT))
        if any(pattern.search(name) for name in names)
    }
    return found.pop() if len(found) == 1 else "unknown"


def linear_end_measurements(segments_by_read, reference_length: int, edge: int) -> dict[str, Any]:
    """Where reads start and stop along a linear construct. Measurements only, no threshold."""
    window = max(1, min(50, reference_length // 2))
    width = max(1, -(-reference_length // 100))
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    change = [0] * (reference_length + 2)
    aligned = spanning = 0
    for segments in segments_by_read.values():
        for segment in segments:
            first = max(1, int(segment["reference_start"]))
            last = min(reference_length, int(segment["reference_end"]))
            if last < first:
                continue
            change[first] += 1
            change[last + 1] -= 1
            if segment.get("supplementary"):
                continue
            aligned += 1
            starts[(first - 1) // width] = starts.get((first - 1) // width, 0) + 1
            ends[(last - 1) // width] = ends.get((last - 1) // width, 0) + 1
            if first <= edge + 1 and last >= reference_length - edge:
                spanning += 1
    depth: list[int] = []
    running = 0
    for position in range(1, reference_length + 1):
        running += change[position]
        depth.append(running)
    return {
        "aligned_reads": aligned,
        "reads_spanning_both_ends": spanning,
        "spanning_fraction": round(spanning / aligned, 6) if aligned else None,
        "spanning_tolerance_bp": edge,
        "end_window_bp": window,
        "mean_depth_first_window": round(sum(depth[:window]) / window, 3),
        "mean_depth_last_window": round(sum(depth[-window:]) / window, 3),
        "read_end_histogram": {
            "bin_width_bp": width,
            "bins": [
                {"start_bp": index * width + 1, "read_starts": starts.get(index, 0), "read_ends": ends.get(index, 0)}
                for index in range(-(-reference_length // width))
            ],
        },
    }
