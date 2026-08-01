from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import requests


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def spawn_replicas(
    *,
    parent_job_id: str,
    parent_name: str,
    normalized_config: Path,
    metadata_path: Path,
    preparation_bundle: Path,
    api_url: str,
) -> dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    config = json.loads(normalized_config.read_text(encoding="utf-8"))
    replica_count = int(metadata["replicas"])
    engine = str(metadata["engine"])
    base_seed = int(config["random_seed"])
    try:
        scheduler_gpu_id = int(config["execution"]["gpu_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("MD replica execution.gpu_id must identify one physical scheduler GPU") from exc
    if scheduler_gpu_id < 0:
        raise ValueError("MD replica execution.gpu_id must identify one physical scheduler GPU")
    execution_plan_sha256 = _digest(config)
    compatibility_key = _digest({
        "engine": config.get("engine"),
        "engine_runtime": config.get("engine_runtime"),
        "chemistry": config.get("chemistry"),
        "protocol": config.get("protocol"),
        "input_hashes": {
            key: value for key, value in config.get("input", {}).items()
            if key.endswith("_sha256")
        },
    })
    created: list[dict[str, Any]] = []

    for replica_index in range(replica_count):
        name = f"{parent_name} - MD replica {replica_index + 1}/{replica_count}"
        payload: dict[str, Any] = {
            "name": name,
            "model_id": "molecular_dynamics",
            "mode": "replica",
            "params": {
                "md_job_config": str(normalized_config.resolve()),
                "md_preparation_bundle": str(preparation_bundle.resolve()),
                "md_replica_index": replica_index,
                "md_replica_seed": base_seed + replica_index,
                "md_engine": engine,
                "md_replica_count": replica_count,
                "lineage_root_job_id": parent_job_id,
                "md_execution_plan_sha256": execution_plan_sha256,
                "md_compatibility_key": compatibility_key,
                "md_attempt": 0,
            },
            "parent_job_id": parent_job_id,
            "batch_id": parent_job_id,
            "batch_name": parent_name,
            "child_stage": "md_replica",
            "pinned_gpu": scheduler_gpu_id,
        }
        response = requests.post(f"{api_url.rstrip('/')}/api/jobs", json=payload, timeout=30)
        if not response.ok:
            raise RuntimeError(
                f"failed to create MD replica {replica_index}: HTTP {response.status_code} {response.text[:500]}"
            )
        child = response.json()
        created.append(
            {
                "id": child["id"],
                "name": child["name"],
                "replica_index": replica_index,
                "replica_seed": base_seed + replica_index,
                "status": child["status"],
            }
        )

    return {
        "schema": "bms.md.replica-spawn.v1",
        "parent_job_id": parent_job_id,
        "engine": engine,
        "replica_count": replica_count,
        "children": created,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create durable BMS MD replica child jobs")
    parser.add_argument("--parent-job-id", required=True)
    parser.add_argument("--parent-name", required=True)
    parser.add_argument("--normalized-config", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--preparation-bundle", type=Path, required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--output", type=Path, default=Path("spawn_md_replicas.json"))
    args = parser.parse_args()

    result = spawn_replicas(
        parent_job_id=args.parent_job_id,
        parent_name=args.parent_name,
        normalized_config=args.normalized_config,
        metadata_path=args.metadata,
        preparation_bundle=args.preparation_bundle,
        api_url=args.api_url,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
