#!/usr/bin/env python3
"""
Filter maturation designs by interface improvement thresholds or percentile.
"""
import argparse
import json
import shutil
from pathlib import Path


def load_scores(manifest_path, metric_key):
    with open(manifest_path, "r") as f:
        data = json.load(f)
    scores = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                value = item.get(metric_key)
                if value is None and metric_key == "selected_delta_interface_score":
                    value = item.get("delta_interface_score")
                if value is not None:
                    scores.append(value)
            elif isinstance(item, (int, float)):
                scores.append(item)
    return scores


def percentile_threshold(scores, percentile):
    if not scores:
        return None
    scores = sorted(scores)
    rank = max(int(len(scores) * (percentile / 100.0)) - 1, 0)
    return scores[rank]


def main():
    parser = argparse.ArgumentParser(description="Filter PPIFlow maturation outputs")
    parser.add_argument("--score_json", required=True, help="Score JSON file")
    parser.add_argument("--pdb_path", required=True, help="Matured PDB path")
    parser.add_argument("--output_dir", required=True, help="Output directory for passing PDBs")
    parser.add_argument("--min_improvement", type=float, default=None,
                        help="Minimum delta interface score to pass (more negative is better)")
    parser.add_argument("--percentile", type=float, default=None,
                        help="Optional percentile threshold (e.g., 20 means top 20%%)")
    parser.add_argument("--scores_manifest", default="",
                        help="Optional JSON file with all delta scores")
    parser.add_argument("--report_json", required=True, help="Output report JSON")
    parser.add_argument("--disable_filter", action="store_true",
                        help="Disable rejection and pass all matured structures through")
    parser.add_argument("--objective_mode", default=None,
                        choices=["selected_interface", "loop_target", "loop_epitope", "balanced"],
                        help="Ranking objective used when interpreting score_json")
    parser.add_argument("--objective_threshold", type=float, default=None,
                        help="Threshold for objective_score when objective_mode is not selected_interface (lower is better)")
    args = parser.parse_args()

    with open(args.score_json, "r") as f:
        score_data = json.load(f)

    score_objective_mode = (score_data.get("objective_mode") or "").strip().lower() if isinstance(score_data.get("objective_mode"), str) else None
    requested_objective_mode = args.objective_mode.strip().lower() if args.objective_mode else None
    if requested_objective_mode and score_objective_mode and requested_objective_mode != score_objective_mode:
        raise SystemExit(
            f"objective_mode mismatch: requested {requested_objective_mode}, score_json contains {score_objective_mode}"
        )
    objective_mode = (requested_objective_mode or score_objective_mode or "selected_interface").strip().lower()
    selection_metric = "selected_delta_interface_score"
    selection_value = score_data.get("selected_delta_interface_score")
    if selection_value is None:
        selection_metric = "delta_interface_score"
        selection_value = score_data.get("delta_interface_score")

    threshold = args.min_improvement
    if objective_mode != "selected_interface":
        selection_metric = "objective_score"
        selection_value = score_data.get("objective_score")
        threshold = args.objective_threshold

    if not args.disable_filter and args.percentile is not None and args.scores_manifest:
        scores = load_scores(args.scores_manifest, selection_metric)
        percentile_val = percentile_threshold(scores, args.percentile)
        if percentile_val is not None:
            threshold = percentile_val

    if args.disable_filter:
        passed = True
    elif threshold is None:
        passed = True
    else:
        passed = selection_value is not None and selection_value <= threshold
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if passed:
        shutil.copy2(args.pdb_path, output_dir / Path(args.pdb_path).name)

    report = {
        "passed": passed,
        "selection_metric": selection_metric,
        "selection_value": selection_value,
        "delta_interface_score": score_data.get("delta_interface_score"),
        "objective_mode": objective_mode,
        "score_objective_mode": score_objective_mode,
        "requested_objective_mode": requested_objective_mode,
        "selection_direction": "lower_is_better",
        "objective_score": score_data.get("objective_score"),
        "threshold": threshold,
        "min_improvement": args.min_improvement,
        "objective_threshold": args.objective_threshold,
        "percentile": args.percentile,
        "filter_disabled": args.disable_filter,
        "filter_reason": (
            "filter_disabled"
            if args.disable_filter
            else "no_threshold"
            if threshold is None
            else "passed"
            if passed
            else f"{selection_metric}_above_threshold"
        ),
        "score_json": str(Path(args.score_json).resolve()),
        "score_data": score_data,
        "interface_score_original": score_data.get("interface_score_original"),
        "interface_score_matured": score_data.get("interface_score_matured"),
        "selected_interface_score_original": score_data.get("selected_interface_score_original"),
        "selected_interface_score_matured": score_data.get("selected_interface_score_matured"),
        "rmsd_backbone": score_data.get("rmsd_backbone"),
        "selected_rmsd_backbone": score_data.get("selected_rmsd_backbone"),
        "nonselected_rmsd_backbone": score_data.get("nonselected_rmsd_backbone"),
        "sequence_identity": score_data.get("sequence_identity"),
        "clash_count_ca": score_data.get("clash_count_ca"),
    }
    Path(args.report_json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
