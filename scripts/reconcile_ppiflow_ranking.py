#!/usr/bin/env python3
"""Reconcile BMS-local PPIFlow scores with validator/Rosetta paper-style ranks.

This script does not claim paper-aligned ranking unless validator confidence and Rosetta interface
score are both present. It also records the known upstream-code vs preprint sign-convention risk.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


RANK_FORMULA_WARNING = "upstream_code_formula_disagrees_with_paper_if_interface_score_negative"
PAPER_FORMULA = "100 * validator_iptm - rosetta_interface_score"
UPSTREAM_CODE_FORMULA = "100 * validator_iptm + rosetta_interface_score"


def _float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _bool_has(value: Optional[float]) -> bool:
    return value is not None


def _completeness(row: Dict[str, Any]) -> Dict[str, Any]:
    has_local = _bool_has(row.get("ppiflow_objective_score"))
    has_validator = _bool_has(row.get("validator_iptm"))
    has_rosetta = _bool_has(row.get("rosetta_interface_score"))
    paper_rank = has_validator and has_rosetta
    missing: List[str] = []
    if has_local and not has_validator:
        missing.append("ppiflow_validator_confidence")
    if has_local and not has_rosetta:
        missing.append("ppiflow_rosetta_interface_score")
    if has_local and not paper_rank:
        missing.append("ppiflow_paper_composite_rank")
    return {
        "status": "complete" if not missing else "partial",
        "missing": missing,
        "local_objective_available": has_local,
        "validator_confidence_available": has_validator,
        "rosetta_interface_score_available": has_rosetta,
        "paper_rank_available": paper_rank,
    }


def reconcile_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = dict(raw)
    for key in (
        "ppiflow_objective_score",
        "validator_iptm",
        "validator_pair_iptm",
        "iptm",
        "pair_iptm",
        "rosetta_interface_score",
    ):
        if key in row:
            row[key] = _float(row.get(key))

    validator_iptm = row.get("validator_iptm")
    if validator_iptm is None:
        validator_iptm = row.get("validator_pair_iptm") or row.get("pair_iptm") or row.get("iptm")
        row["validator_iptm"] = validator_iptm

    rosetta = row.get("rosetta_interface_score")
    if validator_iptm is not None and rosetta is not None:
        row["ppiflow_paper_rank_score"] = round(100.0 * validator_iptm - rosetta, 6)
        row["ppiflow_upstream_code_rank_score"] = round(100.0 * validator_iptm + rosetta, 6)
        row["rank_formula"] = PAPER_FORMULA
        row["rank_direction"] = "higher_is_better"
        row["interface_score_sign_convention"] = "raw_rosetta_reu_more_negative_is_better"
        row["rank_status"] = "paper_style_composite_available"
    else:
        row["ppiflow_paper_rank_score"] = None
        row["ppiflow_upstream_code_rank_score"] = None
        row["rank_formula"] = None
        row["rank_direction"] = None
        row["interface_score_sign_convention"] = None
        row["rank_status"] = "local_triage_only" if row.get("ppiflow_objective_score") is not None else "insufficient_metrics"

    row["metric_completeness"] = _completeness(row)
    return row


def load_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", required=True, type=Path)
    parser.add_argument("--out-json", required=True, type=Path)
    args = parser.parse_args()

    rows = [reconcile_row(row) for row in load_csv(args.input_csv)]
    rows.sort(
        key=lambda row: (
            row.get("ppiflow_paper_rank_score") is not None,
            row.get("ppiflow_paper_rank_score") if row.get("ppiflow_paper_rank_score") is not None else float("-inf"),
            -(row.get("ppiflow_objective_score") if row.get("ppiflow_objective_score") is not None else float("inf")),
        ),
        reverse=True,
    )
    payload = {
        "rank_formula_warning": RANK_FORMULA_WARNING,
        "paper_formula": PAPER_FORMULA,
        "upstream_code_formula_observed": UPSTREAM_CODE_FORMULA,
        "rows": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"row_count": len(rows), "paper_rank_available": sum(1 for row in rows if row["metric_completeness"]["paper_rank_available"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
