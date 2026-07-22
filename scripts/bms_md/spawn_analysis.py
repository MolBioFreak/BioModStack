from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import requests

from .aggregate_children import publish_json_immutable


SHA256 = re.compile(r"^[0-9a-f]{64}$")
QUALIFIED_RUNTIME_SHA256 = "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_set_sha256(entries: list[tuple[int, str]]) -> str:
    encoded = json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def spawn_analysis(
    *,
    parent_job_id: str,
    parent_name: str,
    aggregate_manifest: Path,
    api_url: str,
    work_item_dir: Path,
    runtime_sha256: str,
) -> dict[str, Any]:
    if runtime_sha256 != QUALIFIED_RUNTIME_SHA256:
        raise ValueError("qualified MD analysis runtime identity is required")
    aggregate_manifest = aggregate_manifest.expanduser().resolve()
    aggregate = json.loads(aggregate_manifest.read_text(encoding="utf-8"))
    if (
        aggregate.get("schema") != "bms.md.aggregate.v1"
        or aggregate.get("status") != "completed"
        or aggregate.get("job_id") != parent_job_id
        or not isinstance(aggregate.get("replicas"), list)
        or not aggregate["replicas"]
    ):
        raise ValueError("completed parent-bound MD aggregate manifest is required")

    parent_root = aggregate_manifest.parent
    work_item_dir = work_item_dir.expanduser().resolve()
    try:
        work_item_dir.relative_to(parent_root)
    except ValueError as exc:
        raise ValueError("analysis work-item directory must be contained by the MD parent") from exc

    manifests: list[tuple[int, str, Path]] = []
    for replica in aggregate["replicas"]:
        if not isinstance(replica, dict):
            raise ValueError("invalid MD aggregate replica entry")
        replica_index = replica.get("replica_index")
        if isinstance(replica_index, bool) or not isinstance(replica_index, int) or replica_index < 0:
            raise ValueError("invalid MD aggregate replica index")
        manifest = (parent_root / "replicas" / f"replica_{replica_index}" / "manifest.json").resolve()
        try:
            manifest.relative_to(parent_root)
        except ValueError as exc:
            raise ValueError("replica manifest escapes the MD parent") from exc
        if not manifest.is_file() or manifest.is_symlink():
            raise ValueError("replica manifest is unavailable")
        manifest_sha256 = _sha256(manifest)
        manifests.append((replica_index, manifest_sha256, manifest))

    manifests.sort(key=lambda value: value[0])
    manifest_set_sha256 = _manifest_set_sha256([(index, digest) for index, digest, _path in manifests])
    created: list[dict[str, Any]] = []
    total = len(manifests)
    for replica_index, manifest_sha256, manifest in manifests:
        work_item = {
            "schema": "bms.md.analysis-work-item.v1",
            "job_id": parent_job_id,
            "replica_index": replica_index,
            "manifest": str(manifest),
            "manifest_sha256": manifest_sha256,
            "replica_manifest_set_sha256": manifest_set_sha256,
        }
        work_item_path = work_item_dir / f"replica_{replica_index}.json"
        publish_json_immutable(work_item, work_item_path)
        payload: dict[str, Any] = {
            "name": f"{parent_name} - MD analysis {replica_index + 1}/{total}",
            "model_id": "molecular_dynamics",
            "mode": "analyze",
            "params": {
                "md_analysis_work_item": str(work_item_path),
                "md_analysis_sif_sha256": runtime_sha256,
                "md_replica_index": replica_index,
                "md_replica_manifest_sha256": manifest_sha256,
                "md_replica_manifest_set_sha256": manifest_set_sha256,
                "lineage_root_job_id": parent_job_id,
            },
            "parent_job_id": parent_job_id,
            "batch_id": parent_job_id,
            "batch_name": parent_name,
            "child_stage": "md_analysis",
        }
        response = requests.post(f"{api_url.rstrip('/')}/api/jobs", json=payload, timeout=30)
        if not response.ok:
            raise RuntimeError(
                f"failed to create MD analysis child {replica_index}: HTTP {response.status_code} {response.text[:500]}"
            )
        child = response.json()
        created.append(
            {
                "id": child["id"],
                "name": child["name"],
                "replica_index": replica_index,
                "manifest_sha256": manifest_sha256,
                "status": child["status"],
            }
        )

    return {
        "schema": "bms.md.analysis-spawn.v1",
        "parent_job_id": parent_job_id,
        "aggregate_manifest_sha256": _sha256(aggregate_manifest),
        "replica_manifest_set_sha256": manifest_set_sha256,
        "analysis_count": total,
        "children": created,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create durable CPU MD analysis child jobs")
    parser.add_argument("--parent-job-id", required=True)
    parser.add_argument("--parent-name", required=True)
    parser.add_argument("--aggregate-manifest", type=Path, required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--work-item-dir", type=Path, required=True)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--output", type=Path, default=Path("spawn_md_analysis.json"))
    args = parser.parse_args()

    result = spawn_analysis(
        parent_job_id=args.parent_job_id,
        parent_name=args.parent_name,
        aggregate_manifest=args.aggregate_manifest,
        api_url=args.api_url,
        work_item_dir=args.work_item_dir,
        runtime_sha256=args.runtime_sha256,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
