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
            if isinstance(item, dict) and "delta_interface_score" in item:
                scores.append(item["delta_interface_score"])
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
    parser.add_argument("--min_improvement", type=float, default=-1.0,
                        help="Minimum delta interface score to pass (more negative is better)")
    parser.add_argument("--percentile", type=float, default=None,
                        help="Optional percentile threshold (e.g., 20 means top 20%%)")
    parser.add_argument("--scores_manifest", default="",
                        help="Optional JSON file with all delta scores")
    parser.add_argument("--report_json", required=True, help="Output report JSON")
    args = parser.parse_args()

    with open(args.score_json, "r") as f:
        score_data = json.load(f)
    delta = score_data.get("delta_interface_score")

    threshold = args.min_improvement
    if args.percentile is not None and args.scores_manifest:
        scores = load_scores(args.scores_manifest)
        percentile_val = percentile_threshold(scores, args.percentile)
        if percentile_val is not None:
            threshold = percentile_val

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
    }
    Path(args.report_json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
