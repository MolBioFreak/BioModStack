from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


AGGREGATE_SCHEMA = "bms.md.aggregate.v1"


def aggregate_manifests(manifest_paths: Iterable[Path]) -> dict[str, Any]:
    paths = [Path(path) for path in manifest_paths]
    if not paths:
        raise ValueError("at least one replica manifest is required")

    records: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        if not path.is_file():
            raise ValueError(f"replica manifest is missing: {path}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "bms.md.run.v1":
            raise ValueError(f"unsupported replica manifest schema in {path}")
        if manifest.get("status") != "completed":
            raise ValueError(f"replica {manifest.get('replica_index')} is not completed")
        records.append((path, manifest))

    job_ids = {str(manifest.get("job_id") or "") for _, manifest in records}
    if len(job_ids) != 1 or "" in job_ids:
        raise ValueError("all replica manifests must belong to the same job_id")

    replica_indices = [int(manifest["replica_index"]) for _, manifest in records]
    if len(set(replica_indices)) != len(replica_indices):
        raise ValueError("replica_index values must be unique")

    replicas = []
    artifact_classes = {"replica_manifests"}
    for path, manifest in sorted(records, key=lambda item: int(item[1]["replica_index"])):
        artifacts = manifest.get("artifacts") or {}
        keys = {str(key).lower() for key in artifacts}
        if any("trajectory" in key for key in keys):
            artifact_classes.add("trajectories")
        if any("checkpoint" in key for key in keys):
            artifact_classes.add("checkpoints")
        replicas.append(
            {
                "replica_index": int(manifest["replica_index"]),
                "replica_seed": manifest.get("replica_seed"),
                "manifest": f"replicas/replica_{int(manifest['replica_index'])}/manifest.json",
                "engine": manifest.get("engine"),
                "artifacts": artifacts,
            }
        )

    return {
        "schema": AGGREGATE_SCHEMA,
        "status": "completed",
        "job_id": job_ids.pop(),
        "replicas": replicas,
        "artifact_classes": sorted(artifact_classes),
    }


def write_aggregate(manifest_paths: Iterable[Path], output_path: Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(aggregate_manifests(manifest_paths), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
