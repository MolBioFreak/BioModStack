#!/usr/bin/env python3
"""
Collect PPIFlow maturation outputs from child job directories.
"""
import argparse
import json
import shutil
from pathlib import Path


def collect_files(output_dirs, patterns, subdirs):
    collected = []
    for job_idx, output_dir in enumerate(output_dirs):
        dir_path = Path(output_dir)
        if not dir_path.exists():
            print(f"Warning: Output dir not found: {output_dir}")
            continue

        for subdir in subdirs:
            search_path = dir_path / subdir if subdir else dir_path
            if not search_path.exists():
                continue
            for pattern in patterns:
                for path in search_path.glob(pattern):
                    dest = Path(f"job{job_idx}_{path.name}")
                    if not dest.exists():
                        shutil.copy2(path, dest)
                        collected.append(str(dest))
                        print(f"Collected: {path} -> {dest}")
    return collected


def main():
    parser = argparse.ArgumentParser(description="Collect maturation outputs from child jobs")
    parser.add_argument("--child_outputs_json", required=True, help="Child outputs JSON from wait_for_children")
    parser.add_argument("--stage_name", default="maturation", help="Stage name")
    parser.add_argument("--manifest", default="collection_manifest.json", help="Output manifest JSON")
    args = parser.parse_args()

    with open(args.child_outputs_json) as f:
        data = json.load(f)

    output_dirs = data.get("child_output_dirs", [])

    pdbs = collect_files(
        output_dirs,
        patterns=["*.pdb"],
        subdirs=["run/ppiflow/results", "ppiflow/results", "pdb_files", ""],
    )

    scores = collect_files(
        output_dirs,
        patterns=["*maturation_score.json", "*maturation_filter.json", "*_matured.json"],
        subdirs=["run/ppiflow/results", "ppiflow/results", "run/ppiflow", "ppiflow", ""],
    )

    manifest = {
        "stage": args.stage_name,
        "source_dirs": output_dirs,
        "collected_pdbs": pdbs,
        "collected_scores": scores,
        "count_pdbs": len(pdbs),
        "count_scores": len(scores),
    }

    with open(args.manifest, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Collected {len(pdbs)} PDBs and {len(scores)} score files")


if __name__ == "__main__":
    main()
