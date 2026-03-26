#!/usr/bin/env python3
"""
Collect PPIFlow maturation outputs from child job directories.
"""
import argparse
import json
import shutil
from pathlib import Path


def resolve_dest_name(path: Path, job_idx: int) -> Path:
    """Preserve the original filename unless it collides."""
    dest = Path(path.name)
    if not dest.exists():
        return dest

    prefixed = Path(f"job{job_idx}_{path.name}")
    if not prefixed.exists():
        return prefixed

    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = Path(f"job{job_idx}_{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def collect_files(output_dirs, patterns, subdirs):
    collected = []
    seen_names = set()
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
                    if path.name in seen_names:
                        continue
                    dest = resolve_dest_name(path, job_idx)
                    if not dest.exists():
                        shutil.copy2(path, dest)
                        collected.append(str(dest))
                        seen_names.add(path.name)
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

    search_subdirs = [
        "run/ppiflow/results",
        "run/ppiflow/redesign_debug",
        "ppiflow/results",
        "ppiflow/redesign_debug",
    ]

    pdbs = collect_files(
        output_dirs,
        patterns=["*_ppiflow_sample*.pdb"],
        subdirs=search_subdirs,
    )

    jsons = collect_files(
        output_dirs,
        patterns=[
            "*_anchors.json",
            "*_interface_score.json",
            "*_partial_flow_score.json",
            "*_maturation_score.json",
            "*_maturation_filter.json",
            "*_matured.json",
            "*.json",
        ],
        subdirs=search_subdirs,
    )

    txts = collect_files(
        output_dirs,
        patterns=["*_cdr_positions.txt", "*_ppiflow_positions.txt", "fixed_positions.txt"],
        subdirs=search_subdirs,
    )

    csvs = collect_files(
        output_dirs,
        patterns=["*.csv"],
        subdirs=search_subdirs,
    )

    manifest = {
        "stage": args.stage_name,
        "source_dirs": output_dirs,
        "collected_pdbs": pdbs,
        "collected_jsons": jsons,
        "collected_txts": txts,
        "collected_csvs": csvs,
        "collected_scores": jsons,
        "count_pdbs": len(pdbs),
        "count_jsons": len(jsons),
        "count_txts": len(txts),
        "count_csvs": len(csvs),
        "count_scores": len(jsons),
    }

    with open(args.manifest, "w") as f:
        json.dump(manifest, f, indent=2)

    print(
        f"Collected {len(pdbs)} PDBs, {len(jsons)} JSON files, "
        f"{len(txts)} TXT files and {len(csvs)} CSV files"
    )


if __name__ == "__main__":
    main()
