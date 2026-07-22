from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .aggregate_children import publish_file_immutable, publish_json_immutable


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_regular_file(root: Path, raw: str) -> Path:
    candidate = root / raw
    if candidate.is_symlink():
        raise ValueError("analysis artifact must not be a symbolic link")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("analysis artifact escapes its child output root") from exc
    if not resolved.is_file():
        raise ValueError("analysis artifact is not a regular file")
    return resolved


def _find_sidecar(child_dir: Path) -> Path:
    matches = sorted(child_dir.rglob("md_analysis_replica_*.artifacts.json"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one analysis artifact sidecar below {child_dir}, found {len(matches)}")
    return matches[0]


def _replica_manifest_hashes(parent_root: Path, aggregate: dict[str, Any]) -> dict[int, str]:
    hashes: dict[int, str] = {}
    for replica in aggregate["replicas"]:
        if not isinstance(replica, dict):
            raise ValueError("invalid aggregate replica entry")
        index = replica.get("replica_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index in hashes:
            raise ValueError("invalid or duplicate aggregate replica index")
        manifest = parent_root / "replicas" / f"replica_{index}" / "manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            raise ValueError("replica manifest is unavailable")
        hashes[index] = _sha256(manifest)
    return hashes


def collect_analysis(child_status_path: Path, aggregate_manifest: Path, output_dir: Path) -> dict[str, Any]:
    status = json.loads(child_status_path.read_text(encoding="utf-8"))
    aggregate_manifest = aggregate_manifest.expanduser().resolve()
    parent_root = aggregate_manifest.parent
    output_dir = output_dir.expanduser().resolve()
    if output_dir != parent_root:
        raise ValueError("analysis collection output must be the MD parent root")
    aggregate = json.loads(aggregate_manifest.read_text(encoding="utf-8"))
    if (
        aggregate.get("schema") != "bms.md.aggregate.v1"
        or aggregate.get("status") != "completed"
        or not isinstance(aggregate.get("job_id"), str)
        or not isinstance(aggregate.get("replicas"), list)
        or not aggregate["replicas"]
    ):
        raise ValueError("completed MD aggregate manifest is required")
    parent_job_id = aggregate["job_id"]
    replica_hashes = _replica_manifest_hashes(parent_root, aggregate)

    child_dirs = [Path(value).expanduser().resolve() for value in status.get("child_output_dirs") or []]
    completed_records: list[dict[str, Any]] = []
    seen_replicas: set[int] = set()
    analysis_root = output_dir / "analysis"
    for child_dir in child_dirs:
        sidecar_path = _find_sidecar(child_dir)
        sidecar_bytes = sidecar_path.read_bytes()
        sidecar_sha256 = hashlib.sha256(sidecar_bytes).hexdigest()
        sidecar = json.loads(sidecar_bytes)
        replica_index = sidecar.get("replica")
        if (
            sidecar.get("schema") != "bms.md.analysis-artifacts.v1"
            or sidecar.get("status") != "completed"
            or sidecar.get("job_id") != parent_job_id
            or isinstance(replica_index, bool)
            or not isinstance(replica_index, int)
            or replica_index not in replica_hashes
            or replica_index in seen_replicas
            or sidecar.get("input_manifest_sha256") != replica_hashes[replica_index]
            or not isinstance(sidecar.get("artifacts"), dict)
            or not sidecar["artifacts"]
        ):
            raise ValueError("analysis artifact sidecar identity is invalid")
        seen_replicas.add(replica_index)

        artifact_records: list[dict[str, Any]] = []
        for name, record in sorted(sidecar["artifacts"].items()):
            if not isinstance(name, str) or not isinstance(record, dict):
                raise ValueError("analysis artifact record is invalid")
            relative = record.get("path")
            if not isinstance(relative, str) or Path(relative).name != relative:
                raise ValueError("analysis artifact path must be one contained basename")
            source = _contained_regular_file(sidecar_path.parent, relative)
            expected_size = record.get("bytes")
            expected_sha256 = record.get("sha256")
            if (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size < 0
                or not isinstance(expected_sha256, str)
                or not SHA256.fullmatch(expected_sha256)
                or source.stat().st_size != expected_size
                or _sha256(source) != expected_sha256
            ):
                raise ValueError("analysis artifact checksum is invalid")
            destination = analysis_root / relative
            publish_file_immutable(
                source,
                destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            artifact_records.append(
                {
                    "name": name,
                    "path": relative,
                    "bytes": expected_size,
                    "sha256": expected_sha256,
                    "semantic_role": record.get("semantic_role"),
                }
            )

        published_sidecar = analysis_root / sidecar_path.name
        publish_file_immutable(
            sidecar_path,
            published_sidecar,
            expected_size=len(sidecar_bytes),
            expected_sha256=sidecar_sha256,
        )
        completed_records.append(
            {
                "replica_index": replica_index,
                "input_manifest_sha256": replica_hashes[replica_index],
                "artifact_sidecar": sidecar_path.name,
                "artifact_sidecar_sha256": sidecar_sha256,
                "artifacts": artifact_records,
            }
        )

    completed_records.sort(key=lambda item: item["replica_index"])
    failed = int(status.get("failed") or 0)
    cancelled = int(status.get("cancelled") or 0)
    required = len(replica_hashes)
    is_complete = not failed and not cancelled and seen_replicas == set(replica_hashes)
    collection = {
        "schema": "bms.md.analysis-collection.v1",
        "status": "completed" if is_complete else "partial_failure",
        "job_id": parent_job_id,
        "aggregate_manifest_sha256": _sha256(aggregate_manifest),
        "replica_manifest_set_sha256": hashlib.sha256(
            json.dumps(sorted(replica_hashes.items()), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "required_analysis_children": required,
        "completed_analysis_children": len(completed_records),
        "failed_analysis_children": failed,
        "cancelled_analysis_children": cancelled,
        "child_ids": list(status.get("child_ids") or []),
        "analyses": completed_records,
    }
    if is_complete:
        publish_json_immutable(collection, analysis_root / "manifest.json")
    else:
        partial_identity = hashlib.sha256(
            json.dumps(collection, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        publish_json_immutable(collection, analysis_root / "collections" / f"partial_{partial_identity}.json")
    return collection


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect durable CPU MD analysis child outputs")
    parser.add_argument("--child-status", type=Path, required=True)
    parser.add_argument("--aggregate-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    collect_analysis(args.child_status, args.aggregate_manifest, args.output_dir)
    print(args.output_dir / "analysis" / "manifest.json")


if __name__ == "__main__":
    main()
