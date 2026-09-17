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
    if status in {"not_run", "unavailable", "failed", "error", "unknown"}:
        raise StructuralEvidenceUnavailable("STRUCTURAL_BREAKPOINT_NOT_EVALUATED")
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
    if reference_length <= 0 or not 0 <= position <= reference_length or not 0 < window <= reference_length // 2:
        raise ValueError("invalid structural breakpoint coordinates/window")
    return window < position < reference_length - window


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
