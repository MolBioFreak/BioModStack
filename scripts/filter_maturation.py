#!/usr/bin/env python3
"""
Filter maturation designs by interface improvement thresholds or percentile.
"""
import argparse
import json
import shutil
from pathlib import Path


def load_scores(manifest_path):
    with open(manifest_path, "r") as f:
        data = json.load(f)
    scores = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                value = item.get("selected_delta_interface_score")
                if value is None:
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
    args = parser.parse_args()

    with open(args.score_json, "r") as f:
        score_data = json.load(f)
    delta = score_data.get("selected_delta_interface_score")
    if delta is None:
        delta = score_data.get("delta_interface_score")

    threshold = args.min_improvement
    if not args.disable_filter and args.percentile is not None and args.scores_manifest:
        scores = load_scores(args.scores_manifest)
        percentile_val = percentile_threshold(scores, args.percentile)
        if percentile_val is not None:
            threshold = percentile_val

    if args.disable_filter:
        passed = True
    elif threshold is None:
        passed = True
    else:
        passed = delta is not None and delta <= threshold
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if passed:
        shutil.copy2(args.pdb_path, output_dir / Path(args.pdb_path).name)

    report = {
        "passed": passed,
        "delta_interface_score": delta,
        "threshold": threshold,
        "min_improvement": args.min_improvement,
        "percentile": args.percentile,
        "filter_disabled": args.disable_filter,
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
