#!/usr/bin/env python3
"""Create deterministic, length-homogeneous RFD3 child batches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


POLICY_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "shape_blueprint" / "rfd3_batch_policies.json"
SEED_MODULUS = 2_147_483_648


class ResourceAdmissionError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value) if not isinstance(value, (bytes, bytearray)) else value).hexdigest()


def _load_registry() -> dict[str, Any]:
    if POLICY_REGISTRY_PATH.is_symlink() or not POLICY_REGISTRY_PATH.is_file() or POLICY_REGISTRY_PATH.stat().st_nlink != 1:
        raise ValueError("RFD3 batch policy registry is unavailable")
    registry = json.loads(POLICY_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or registry.get("schema") != "bms_rfd3_batch_policy_registry_v1":
        raise ValueError("RFD3 batch policy registry schema is invalid")
    return registry


def _validate_request_hash(request: dict[str, Any]) -> None:
    claimed = request.get("request_sha256")
    unsigned = dict(request)
    unsigned.pop("request_sha256", None)
    if not isinstance(claimed, str) or _sha256(unsigned) != claimed:
        raise ValueError("RFD3 batch planner request hash mismatch")


def _policy_for(request: dict[str, Any]) -> tuple[dict[str, Any], str]:
    registry = _load_registry()
    length_policy = request.get("length_policy")
    if not isinstance(length_policy, dict):
        raise ValueError("RFD3 request has no length policy")
    mode = str(length_policy.get("mode"))
    if mode == "uniform_integer_range":
        mode = "deterministic_range"
    expected_id = "fixed_length_v1" if mode == "fixed" else "balanced_bucket_v1"
    policy_id = str(length_policy.get("allocation_policy_id") or expected_id)
    policies = registry.get("allocation_policies")
    if not isinstance(policies, dict) or not isinstance(policies.get(policy_id), dict):
        raise ValueError(f"unknown RFD3 allocation policy: {policy_id}")
    policy = dict(policies[policy_id])
    if policy.get("mode") != mode:
        raise ValueError("RFD3 length mode and allocation policy disagree")
    declared_hash = length_policy.get("allocation_policy_sha256")
    policy_hash = _sha256(policy)
    if declared_hash is not None and declared_hash != policy_hash:
        raise ValueError("RFD3 allocation policy hash mismatch")
    return policy, policy_hash


def _lengths(request: dict[str, Any], policy: dict[str, Any]) -> list[int]:
    length_policy = request["length_policy"]
    minimum = length_policy.get("min")
    maximum = length_policy.get("max")
    if not isinstance(minimum, int) or not isinstance(maximum, int) or not 40 <= minimum <= maximum <= 600:
        raise ValueError("RFD3 length policy is outside [40, 600]")
    count = request.get("num_backbones")
    if not isinstance(count, int) or not 1 <= count <= 200:
        raise ValueError("RFD3 candidate count is outside [1, 200]")
    if policy["mode"] == "fixed":
        if minimum != maximum:
            raise ValueError("fixed RFD3 allocation requires min == max")
        return [minimum] * count
    bucket_count = int(policy["bucket_count"])
    if bucket_count < 2:
        raise ValueError("deterministic range allocation needs at least two buckets")
    return [
        int(round(minimum + bucket_index * (maximum - minimum) / (bucket_count - 1)))
        for bucket_index in (candidate_index % bucket_count for candidate_index in range(count))
    ]


def _resource_admit(lengths: list[int], gpu_memory_gib: int) -> dict[str, Any]:
    registry = _load_registry()
    resource = registry["resource_profiles"]["rfd3_shape_runtime_v1"]
    classes = resource["gpu_memory_classes_gib"]
    if gpu_memory_gib >= 32:
        memory_class = 32
    elif gpu_memory_gib >= 16:
        memory_class = 16
    else:
        raise ResourceAdmissionError(f"RFD3 resource admission requires at least 16 GiB GPU memory, got {gpu_memory_gib} GiB")
    limit = int(classes[str(memory_class)]["max_residue_length"])
    unsupported = sorted({length for length in lengths if length > limit})
    if unsupported:
        raise ResourceAdmissionError(
            f"RFD3 resource admission rejects {memory_class} GiB placement for lengths {unsupported}; "
            f"maximum admitted length is {limit}"
        )
    return {
        "status": "admitted",
        "profile_id": resource["id"],
        "gpu_memory_gib": gpu_memory_gib,
        "memory_class_gib": memory_class,
        "max_admitted_residue_length": limit,
        "observed_boundary": resource["observed_boundary"],
    }


def _candidate_id(request_sha256: str, batch_index: int, local_index: int, effective_seed: int) -> str:
    material = f"{request_sha256}\0{batch_index}\0{local_index}\0{effective_seed}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def plan_batches(request: dict[str, Any], *, gpu_memory_gib: int) -> dict[str, Any]:
    if request.get("schema") != "bms_shape_design_request_v2":
        raise ValueError("RFD3 batch planner requires request schema v2")
    _validate_request_hash(request)
    policy, policy_hash = _policy_for(request)
    lengths = _lengths(request, policy)
    resource_admission = _resource_admit(lengths, gpu_memory_gib)
    child_limit = 32
    grouped: dict[int, list[int]] = {}
    for candidate_index, length in enumerate(lengths):
        grouped.setdefault(length, []).append(candidate_index)
    batches: list[dict[str, Any]] = []
    seed_root = int(request.get("seed", 0))
    for length in sorted(grouped):
        candidate_indexes = grouped[length]
        for start in range(0, len(candidate_indexes), child_limit):
            batch_index = len(batches)
            local_indexes = candidate_indexes[start : start + child_limit]
            candidates = []
            for local_index, candidate_index in enumerate(local_indexes):
                effective_seed = (seed_root + candidate_index) % SEED_MODULUS
                candidates.append(
                    {
                        "candidate_index": candidate_index,
                        "local_candidate_index": local_index,
                        "length": length,
                        "effective_seed": effective_seed,
                        "candidate_id": _candidate_id(request["request_sha256"], batch_index, local_index, effective_seed),
                    }
                )
            batches.append(
                {
                    "batch_index": batch_index,
                    "batch_id": f"rfd3_batch_{batch_index:04d}",
                    "length": length,
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                }
            )
    plan = {
        "schema": "bms_rfd3_batch_plan_v1",
        "status": "planned",
        "request_sha256": request["request_sha256"],
        "geometry_sha256": request.get("geometry_sha256"),
        "candidate_count_total": len(lengths),
        "child_batch_limit": child_limit,
        "seed_root": seed_root,
        "allocation_policy_id": policy["id"],
        "allocation_policy_sha256": policy_hash,
        "resource_admission": resource_admission,
        "batches": batches,
    }
    plan["candidate_id_set_sha256"] = _sha256(
        [candidate["candidate_id"] for batch in batches for candidate in batch["candidates"]]
    )
    plan["batch_plan_sha256"] = _sha256(plan)
    return plan


def child_requests(request: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    if plan.get("status") != "planned":
        raise ValueError("cannot materialize child requests from a non-planned aggregate")
    children: list[dict[str, Any]] = []
    registry = _load_registry()
    fixed_policy_hash = _sha256(registry["allocation_policies"]["fixed_length_v1"])
    for batch in plan["batches"]:
        child = dict(request)
        child["request_id"] = f"{request['request_id']}__{batch['batch_id']}"
        child["length_policy"] = {
            "mode": "fixed",
            "min": batch["length"],
            "max": batch["length"],
            "allocation_policy_id": "fixed_length_v1",
            "allocation_policy_sha256": fixed_policy_hash,
        }
        child["target_length"] = batch["length"]
        child["num_backbones"] = batch["candidate_count"]
        child["candidate_batch_size"] = batch["candidate_count"]
        child["parent_request_sha256"] = request["request_sha256"]
        child["batch_plan_sha256"] = plan["batch_plan_sha256"]
        child["batch_index"] = batch["batch_index"]
        child["batch_id"] = batch["batch_id"]
        child["candidate_ids"] = [candidate["candidate_id"] for candidate in batch["candidates"]]
        child["candidate_effective_seeds"] = [candidate["effective_seed"] for candidate in batch["candidates"]]
        child.pop("request_sha256", None)
        child["request_sha256"] = _sha256(child)
        children.append(child)
    return children


def write_plan(request: dict[str, Any], plan: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "rfd3_batch_plan.json").write_bytes(_canonical(plan) + b"\n")
    child_dir = output_dir / "batch_requests"
    child_dir.mkdir(exist_ok=True)
    for child in child_requests(request, plan):
        filename = f"{child['batch_id']}.json"
        (child_dir / filename).write_bytes(_canonical(child) + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--gpu-memory-gib", required=True, type=int)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    try:
        plan = plan_batches(request, gpu_memory_gib=args.gpu_memory_gib)
    except ResourceAdmissionError as exc:
        plan = {
            "schema": "bms_rfd3_batch_plan_v1",
            "status": "rejected",
            "request_sha256": request.get("request_sha256"),
            "candidate_count_total": request.get("num_backbones"),
            "resource_admission": {"status": "rejected", "reason": {"code": "resource_unsupported", "message": str(exc)}},
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "rfd3_batch_plan.json").write_bytes(_canonical(plan) + b"\n")
        return 2
    write_plan(request, plan, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
