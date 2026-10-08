from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from .aggregate import aggregate_manifests


def _find_run_manifest(output_dir: Path) -> Path:
    matches: list[Path] = []
    for candidate in output_dir.rglob("manifest.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema") == "bms.md.run.v1":
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one bms.md.run.v1 manifest below {output_dir}, found {len(matches)}")
    return matches[0]


def collect_children(child_status_path: Path, output_dir: Path) -> dict[str, Any]:
    status = json.loads(child_status_path.read_text(encoding="utf-8"))
    child_dirs = [Path(value).expanduser().resolve() for value in status.get("child_output_dirs") or []]
    if not child_dirs:
        raise ValueError("no completed MD child output directories were supplied")

    output_dir.mkdir(parents=True, exist_ok=True)
    collected_manifests: list[Path] = []
    for child_dir in child_dirs:
        source_manifest = _find_run_manifest(child_dir)
        run_manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        replica_index = int(run_manifest["replica_index"])
        source_replica_dir = source_manifest.parent
        target_replica_dir = output_dir / "replicas" / f"replica_{replica_index}"
        if target_replica_dir.exists():
            shutil.rmtree(target_replica_dir)
        target_replica_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_replica_dir, target_replica_dir)
        collected_manifests.append(target_replica_dir / "manifest.json")

    aggregate = aggregate_manifests(collected_manifests)
    aggregate["lineage"] = {
        "total_children": int(status.get("total") or len(child_dirs)),
        "completed_children": int(status.get("completed") or len(child_dirs)),
        "failed_children": int(status.get("failed") or 0),
        "cancelled_children": int(status.get("cancelled") or 0),
        "child_ids": list(status.get("child_ids") or []),
    }
    if aggregate["lineage"]["failed_children"] or aggregate["lineage"]["cancelled_children"]:
        aggregate["status"] = "partial_failure"
    (output_dir / "manifest.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect durable MD replica child outputs")
    parser.add_argument("--child-status", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    aggregate = collect_children(args.child_status, args.output_dir)
    print(args.output_dir / "manifest.json")


if __name__ == "__main__":
    main()
