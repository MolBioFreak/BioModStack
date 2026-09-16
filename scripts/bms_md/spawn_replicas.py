from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import requests

from scripts.child_job_utils import component_runtime_enabled, submit_child_job


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
        if component_runtime_enabled():
            child_id = submit_child_job(
                payload, parent_job_id=parent_job_id, stage="md_replica",
                child_key=str(replica_index), required=True,
            )
            child = {"id": child_id, "name": payload["name"], "status": "queued"}
        else:
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

    if component_runtime_enabled():
        from scripts.lib.component_adapter import runtime_from_environment
        runtime_from_environment().register_group(
            f"{parent_job_id}:md_replica", [child["id"] for child in created])

    return {
        "schema": "bms.md.replica-spawn.v1",
        "parent_job_id": parent_job_id,
        "engine": engine,
        "replica_count": replica_count,
        "children": created,
    }


def prepare_replica_retry(runtime, *, component_id: str, operation_id: str,
                          failure_code: str):
    """Native format adapter for the shared retry/compiler (no execution owner)."""
    from component_runtime import ComponentRequest, digest
    from .contract import RETRYABLE_INFRASTRUCTURE_FAILURES

    if failure_code not in RETRYABLE_INFRASTRUCTURE_FAILURES or not operation_id:
        raise ValueError("MD retry requires an allowlisted infrastructure failure")
    original = runtime.request(component_id)
    payload = original.payload
    if (original.stage != 'md_replica' or original.parent_job_id != runtime.root_job_id
            or payload.get('model_id') != 'molecular_dynamics' or payload.get('mode') != 'replica'):
        raise ValueError("MD replica retry requires its original native component request")
    params = dict(payload['params'])
    config = json.loads(Path(params['md_job_config']).read_bytes())
    if (_digest(config) != params.get('md_execution_plan_sha256')
            or params.get('md_replica_seed') != int(config['random_seed']) + int(params['md_replica_index'])
            or params.get('md_replica_count') != config['replicas']
            or params.get('md_engine') != config['engine']):
        raise ValueError('retained MD retry configuration or replica identity changed')
    for key in ('md_resume_checkpoint', 'md_resume_checkpoint_sha256',
                'md_resume_output_dir', 'md_resume_segment_id'):
        params.pop(key, None)
    params['md_attempt'] = int(params.get('md_attempt', 0)) + 1
    payload['params'] = params
    replacement = ComponentRequest.capture(parent_job_id=original.parent_job_id,
        stage=original.stage, child_key='retry:' + digest([component_id, operation_id]),
        payload=payload, required=original.required)
    ids = runtime.group_children(f'{runtime.root_job_id}:md_replica')
    prior = runtime.retry_status(operation_id)
    expected_id = replacement.component_id if prior else component_id
    if expected_id not in ids:
        raise ValueError("retry component is not in the current required replica set")
    children = []
    for identity in ids:
        request = replacement if identity == expected_id else runtime.request(identity)
        child_params = request.payload['params']
        children.append(dict(id=request.component_id, name=request.payload.get('name', ''),
            replica_index=child_params['md_replica_index'],
            replica_seed=child_params['md_replica_seed'],
            attempt=child_params.get('md_attempt', 0), status='queued'))
    children.sort(key=lambda row: row['replica_index'])
    count = int(params['md_replica_count'])
    if [row['replica_index'] for row in children] != list(range(count)):
        raise ValueError("retry requires the complete original replica roster")
    receipt = dict(schema='bms.md.replica-spawn.v1', parent_job_id=runtime.root_job_id,
        engine=params['md_engine'], replica_count=count, children=children)
    return replacement, receipt


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
