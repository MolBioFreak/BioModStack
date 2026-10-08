from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "reconcile_ppiflow_ranking.py"


def test_reconcile_ppiflow_ranking_reports_paper_and_upstream_formula_gap(tmp_path: Path) -> None:
    rows_path = tmp_path / "ppiflow_rows.csv"
    out_json = tmp_path / "ranked.json"
    with rows_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["design", "ppiflow_objective_score", "validator_iptm", "rosetta_interface_score"])
        writer.writeheader()
        writer.writerow({"design": "good_negative_interface", "ppiflow_objective_score": -3.0, "validator_iptm": 0.8, "rosetta_interface_score": -60.0})
        writer.writerow({"design": "weak_interface", "ppiflow_objective_score": -1.0, "validator_iptm": 0.8, "rosetta_interface_score": -10.0})

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input-csv", str(rows_path), "--out-json", str(out_json)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out_json.read_text())
    assert payload["rank_formula_warning"] == "upstream_code_formula_disagrees_with_paper_if_interface_score_negative"
    ranked = payload["rows"]
    assert ranked[0]["design"] == "good_negative_interface"
    assert ranked[0]["ppiflow_paper_rank_score"] == 140.0
    assert ranked[0]["ppiflow_upstream_code_rank_score"] == 20.0
    assert ranked[0]["metric_completeness"]["paper_rank_available"] is True
    assert ranked[0]["rank_formula"] == "100 * validator_iptm - rosetta_interface_score"


def test_reconcile_ppiflow_ranking_marks_local_only_rows_partial(tmp_path: Path) -> None:
    rows_path = tmp_path / "ppiflow_rows.csv"
    out_json = tmp_path / "ranked.json"
    rows_path.write_text("design,ppiflow_objective_score\nlocal_only,-2.5\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input-csv", str(rows_path), "--out-json", str(out_json)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    row = json.loads(out_json.read_text())["rows"][0]
    assert row["design"] == "local_only"
    assert row["metric_completeness"]["status"] == "partial"
    assert "ppiflow_validator_confidence" in row["metric_completeness"]["missing"]
    assert "ppiflow_rosetta_interface_score" in row["metric_completeness"]["missing"]
    assert row["rank_status"] == "local_triage_only"
