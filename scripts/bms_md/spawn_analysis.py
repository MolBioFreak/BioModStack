from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import requests

from scripts.child_job_utils import component_runtime_enabled, submit_child_job

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

    if len({index for index, _digest, _path in manifests}) != len(manifests):
        raise ValueError("duplicate MD aggregate replica index")
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
        if component_runtime_enabled():
            from scripts.lib.component_adapter import runtime_from_environment
            generation = runtime_from_environment().context.get('generation', 0)
            child_id = submit_child_job(
                payload, parent_job_id=parent_job_id, stage="md_analysis",
                child_key=f"{generation}:{manifest_set_sha256}:{replica_index}", required=True,
            )
            child = {"id": child_id, "name": payload["name"], "status": "queued"}
        else:
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

    if component_runtime_enabled():
        from scripts.lib.component_adapter import runtime_from_environment
        runtime_from_environment().register_group(
            f"{parent_job_id}:md_analysis", [child["id"] for child in created])

    return {
        "schema": "bms.md.analysis-spawn.v1",
        "parent_job_id": parent_job_id,
        "aggregate_manifest_sha256": _sha256(aggregate_manifest),
        "replica_manifest_set_sha256": manifest_set_sha256,
        "analysis_count": total,
        "children": created,
    }


def prepare_analysis_retry(runtime, *, component_id: str, operation_id: str,
                           failure_code: str):
    """Adapt retained analysis requests to the existing exact-component retry."""
    from component_runtime import ComponentRequest, digest
    from .contract import RETRYABLE_INFRASTRUCTURE_FAILURES

    # Analysis was already explicitly operator-retryable after an execution
    # failure; replica infrastructure-only retry policy remains unchanged.
    if failure_code not in RETRYABLE_INFRASTRUCTURE_FAILURES | {'execution_failed'} or not operation_id:
        raise ValueError("MD analysis retry requires an explicit execution failure and operation")
    original = runtime.request(component_id)
    if (original.stage != 'md_analysis' or original.parent_job_id != runtime.root_job_id
            or original.payload.get('model_id') != 'molecular_dynamics'
            or original.payload.get('mode') != 'analyze'):
        raise ValueError("MD analysis retry requires its original native component request")
    replacement = ComponentRequest.capture(parent_job_id=original.parent_job_id,
        stage=original.stage, child_key='retry:' + digest([component_id, operation_id]),
        payload=original.payload, required=original.required)
    ids = tuple(child['id'] for child in runtime.children(runtime.root_job_id, 'md_analysis')
                if child['required'])
    expected = replacement.component_id if runtime.retry_status(operation_id) else component_id
    if expected not in ids:
        raise ValueError("retry component is not in the current required analysis set")
    children, manifests, parent_roots = [], [], set()
    for identity in ids:
        request = replacement if identity == expected else runtime.request(identity)
        params = request.payload['params']
        item = json.loads(Path(params['md_analysis_work_item']).read_bytes())
        index = params['md_replica_index']
        manifest = Path(item['manifest']).resolve(strict=True)
        manifest_hash = _sha256(manifest)
        if (request.stage != 'md_analysis' or request.parent_job_id != runtime.root_job_id
                or item.get('schema') != 'bms.md.analysis-work-item.v1'
                or item.get('job_id') != runtime.root_job_id or type(index) is not int
                or index < 0 or item.get('replica_index') != index
                or manifest_hash != item.get('manifest_sha256')
                or manifest_hash != params.get('md_replica_manifest_sha256')
                or item.get('replica_manifest_set_sha256') != params.get('md_replica_manifest_set_sha256')):
            raise ValueError('retained MD analysis work-item or dynamics identity changed')
        parent_root = manifest.parent.parent.parent
        if manifest != parent_root / 'replicas' / f'replica_{index}' / 'manifest.json':
            raise ValueError('retained analysis manifest is not a native replica')
        parent_roots.add(parent_root)
        manifests.append((index, manifest_hash))
        children.append(dict(id=request.component_id, name=request.payload.get('name', ''),
            replica_index=index, manifest_sha256=manifest_hash, status='queued'))
    if len(parent_roots) != 1 or len({index for index, _hash in manifests}) != len(manifests):
        raise ValueError('retry requires one exact retained dynamics generation')
    root = parent_roots.pop()
    aggregate_path = root / 'manifest.json'
    aggregate = json.loads(aggregate_path.read_bytes())
    indices = sorted(row['replica_index'] for row in aggregate['replicas'])
    manifest_set = _manifest_set_sha256(sorted(manifests))
    if (aggregate.get('schema') != 'bms.md.aggregate.v1' or aggregate.get('status') != 'completed'
            or aggregate.get('job_id') != runtime.root_job_id
            or sorted(index for index, _hash in manifests) != indices
            or any(runtime.request(identity).payload['params']['md_replica_manifest_set_sha256']
                   != manifest_set for identity in ids)):
        raise ValueError('retry requires the complete immutable analysis roster')
    children.sort(key=lambda row: row['replica_index'])
    return replacement, dict(schema='bms.md.analysis-spawn.v1',
        parent_job_id=runtime.root_job_id, aggregate_manifest_sha256=_sha256(aggregate_path),
        replica_manifest_set_sha256=manifest_set, analysis_count=len(children), children=children,
        aggregate_manifest=str(aggregate_path), artifact_root=str(runtime.artifact_root))


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
