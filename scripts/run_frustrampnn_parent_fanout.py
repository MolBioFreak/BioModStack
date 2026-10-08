#!/usr/bin/env python3
"""Spawn, wait for, and seal scheduler-owned FrustraMPNN child evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))
from component_runtime import (
    ComponentBoundary, GroupingLedger, ResultReference, durable_write, ordered_candidates, plan_frustrampnn,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_frustrampnn_groups import immutable_write, reconcile_tree, tree_authority
from child_job_utils import component_runtime_enabled, submit_child_job, fetch_children_status

DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
WORKFLOW_CAPABILITY_ENV = "BMS_STAGE_REPORT_TOKEN"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _candidate_authority(candidate_dir: Path, parent_job_id: str, workflow_id: str) -> tuple[dict[str, Any], Path]:
    metadata_path = candidate_dir / "metadata.json"
    sources = [path for path in candidate_dir.iterdir() if path.name.startswith("source.")]
    if candidate_dir.is_symlink() or not candidate_dir.is_dir() or len(sources) != 1:
        raise ValueError("candidate directory must contain exactly one source structure")
    source = sources[0]
    if source.is_symlink() or source.suffix.lower() not in {".pdb", ".cif", ".mmcif"}:
        raise ValueError("candidate source structure is unsafe")
    payload = metadata_path.read_bytes()
    metadata = json.loads(payload)
    required = {
        "candidate_id", "parent_job_id", "parent_workflow_id", "producer_stage",
        "producer_candidate_key", "requiredness",
    }
    if (
        not isinstance(metadata, dict)
        or set(metadata) != required
        or payload != _canonical_bytes(metadata)
        or _SAFE_ID.fullmatch(str(metadata.get("candidate_id") or "")) is None
        or metadata.get("parent_job_id") != parent_job_id
        or metadata.get("parent_workflow_id") != workflow_id
        or metadata.get("requiredness") != "required"
    ):
        raise ValueError("candidate metadata authority is invalid")
    return metadata, source


def prepare_runtime_child(payload: dict, *, child_id: str, output_root: Path) -> dict:
    """Native input adapter called by the shared compiler, without a host Job row."""
    import tempfile
    from prepare_frustrampnn_candidate import prepare_candidate
    from remote_frustrampnn_batch import materialize_batch
    from lib.component_adapter import runtime_from_environment

    runtime = runtime_from_environment()
    if runtime is None:
        raise ValueError("FrustraMPNN child preparation requires attempt context")
    group = payload["params"]["frustrampnn_component_group"]
    from services.frustrampnn.settings import validate_persisted_requested_settings, requested_settings_sha256
    settings_bytes = _canonical_bytes(group["settings"])
    requested = validate_persisted_requested_settings({**group["settings"],
        "settings_value_origin": group["settings_value_origin"]})
    settings_digest = requested_settings_sha256(requested)
    output_root = output_root.resolve()
    output_root.relative_to(runtime.artifact_root.resolve())
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frustrampnn-prepare-", dir=output_root) as temp:
        directories = []
        for ordinal, candidate in enumerate(group["candidates"]):
            source = runtime.artifact_root / candidate["source_relative_path"]
            source.resolve().relative_to(runtime.artifact_root.resolve())
            source_bytes = source.read_bytes()
            if (len(source_bytes) != candidate["source_size_bytes"] or
                    hashlib.sha256(source_bytes).hexdigest() != candidate["source_sha256"]):
                raise ValueError("FrustraMPNN source binding changed")
            directory = Path(temp) / str(ordinal)
            directory.mkdir()
            # Preserve source/candidate provenance; execution ownership is the child.
            metadata = {**candidate["metadata"], "parent_job_id": child_id,
                        "parent_workflow_id": "frustrampnn_analysis"}
            prepare_candidate(source=source, output_pdb=directory / "canonical_source.pdb",
                request_path=directory / "workflow_component_request_v3.json", metadata=metadata,
                request_version=3, structure_map_path=directory / "frustrampnn_structure_map_v1.json",
                settings_payload=settings_bytes, settings_sha256=settings_digest,
                settings_value_origin=group["settings_value_origin"])
            directories.append(directory)
        manifest, batch = materialize_batch(directories, output_root)
    # Ordinary local/worker compilation consumes the same scheduler authority.
    manifest_bytes = manifest.read_bytes()
    scheduler_manifest = output_root / "inputs/frustrampnn_scheduler_batch_v3.json"
    immutable_write(scheduler_manifest, manifest_bytes)
    envelope = {
        "schema_name": "bms.frustrampnn.scheduler-child.v1", "schema_version": 1,
        "execution_owner_job_id": child_id, "source_parent_job_id": payload["parent_job_id"],
        "source_batch_id": payload.get("batch_id"), "trigger": "workflow_dataset",
        "settings_contract_version": "typed_v2",
        "settings_value_origin": requested.settings_value_origin,
        "normalized_requested_settings": requested.model_dump(mode="json", exclude_none=False),
        "settings_sha256": settings_digest,
        "selection": group["candidates"],
        "component_invocation_ids": [record["invocation_id"] for record in batch["records"]],
        "batch_manifest_relative_path": scheduler_manifest.relative_to(output_root).as_posix(),
        "batch_manifest_size_bytes": len(manifest_bytes),
        "batch_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "supersedes_child_job_id": None, "prior_invocation_ids": [],
        "result_persistence_identity": "(child_job_id, invocation_id)",
    }
    return {"frustrampnn_batch_manifest_path": str(scheduler_manifest),
            "_frustrampnn_child_v1": envelope}


def runtime_child_receipt(child_id: str, output_root: Path) -> dict:
    """Return complete worker-native evidence, not a host database projection."""
    from services.frustrampnn.manifests import validate_result_manifest
    batch = json.loads((output_root / "batches/batch.json").read_bytes())
    if batch["execution_owner_job_id"] != child_id:
        raise ValueError("FrustraMPNN child batch owner mismatch")
    candidates, results = [], []
    for record in batch["records"]:
        request_bytes = (output_root / record["request_relative_path"]).read_bytes()
        if hashlib.sha256(request_bytes).hexdigest() != record["request_sha256"]:
            raise ValueError("FrustraMPNN child request binding changed")
        request = json.loads(request_bytes)
        bundle = output_root / "frustrampnn/results" / record["candidate_id"]
        manifest = json.loads((bundle / "frustrampnn_result_manifest_v3.json").read_bytes())
        validate_result_manifest(bundle, manifest)
        result = json.loads((bundle / "workflow_component_result_v3.json").read_bytes())
        if (result.get("candidate_id") != record["candidate_id"] or
                result.get("invocation_id") != record["invocation_id"] or
                result.get("status") != "succeeded" or
                result.get("request_sha256") != record["request_sha256"]):
            raise ValueError("required FrustraMPNN native result is incomplete")
        candidates.append(request)
        results.append(result)
    grouped = None
    if len(candidates) > 1:
        raw = (output_root / "frustrampnn/batches/grouped_batch_terminal_receipt_v1.json").read_bytes()
        grouped = json.loads(raw)
        unsigned = {k: v for k, v in grouped.items() if k != "receipt_sha256"}
        if (grouped.get("execution_owner_job_id") != child_id or
                grouped.get("receipt_sha256") != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() or
                [r.get("candidate_id") for r in grouped.get("records", [])] !=
                [r["candidate_id"] for r in batch["records"]] or
                any(r.get("status") != "succeeded" for r in grouped["records"])):
            raise ValueError("required FrustraMPNN grouped terminal is incomplete")
    return {"schema_name": "bms.frustrampnn.worker-native-receipt.v1", "job_id": child_id,
            "status": "completed", "candidates": candidates, "results": results,
            "batch_manifest": batch, "grouped_terminal_artifact": grouped}


def execute_parent_fanout(
    *,
    parent_job_id: str,
    parent_workflow_id: str,
    settings_json: str,
    candidate_dirs: Sequence[Path],
    output_receipt: Path,
    output_bundles: Path,
    api_url: str = DEFAULT_API_URL,
    poll_interval: int = 10,
    settings_value_origin: str = "bms_default",
    timeout: int = 0,
    capability: str | None = None,
) -> dict[str, Any]:
    if parent_workflow_id not in {
        "structure_prediction", "complex_prediction", "protein_design",
        "antibody_denovo", "conformational_mapping",
    }:
        raise ValueError("unsupported FrustraMPNN parent workflow")
    if settings_value_origin not in {"bms_default", "operator_request"}:
        raise ValueError("settings_value_origin is invalid")
    if not candidate_dirs:
        raise ValueError("terminal structure dataset is empty")
    capability = str(capability or os.environ.get(WORKFLOW_CAPABILITY_ENV) or "").strip()
    use_runtime = component_runtime_enabled()
    if not capability and not use_runtime:
        raise ValueError("parent workflow capability is required")
    settings = json.loads(settings_json)
    if _canonical_bytes(settings) != settings_json.encode("utf-8"):
        raise ValueError("settings_json must be compact canonical JSON")

    dataset: list[tuple[dict[str, Any], tuple[str, bytes, str], Path]] = []
    for raw_dir in candidate_dirs:
        metadata, source = _candidate_authority(Path(raw_dir), parent_job_id, parent_workflow_id)
        media_type = "chemical/x-mmcif" if source.suffix.lower() in {".cif", ".mmcif"} else "chemical/x-pdb"
        dataset.append((metadata, (source.name, source.read_bytes(), media_type), source))
    dataset = list(ordered_candidates(dataset, lambda item: item[0]))
    records = [item[0] for item in dataset]
    files = [("structure_files", item[1]) for item in dataset]
    candidate_ids = [str(record["candidate_id"]) for record in records]
    manifest = {"candidates": records}
    plan = plan_frustrampnn([
        {**metadata, "input_sha256": hashlib.sha256(source[1]).hexdigest(),
         "upload_filename": source[0], "upload_media_type": source[2],
         "settings_value_origin": settings_value_origin}
        for metadata, source, _path in dataset
    ], settings)
    boundary = None
    if use_runtime:
        from lib.component_adapter import runtime_from_environment
        runtime = runtime_from_environment()
        sources = {}
        # Nextflow task work is not retained custody. Publish the exact snapshot
        # used by the grouping plan before any child can consume its reference.
        retained_root = runtime.artifact_root / "frustrampnn/child_inputs" / hashlib.sha256(
            parent_job_id.encode("utf-8")).hexdigest()
        for metadata, (name, raw, _media_type), _source in dataset:
            retained = retained_root / metadata["candidate_id"] / name
            for directory in retained.parents:
                if directory == runtime.artifact_root:
                    break
                if directory.is_symlink():
                    raise ValueError("FrustraMPNN retained input directory is unsafe")
            retained.resolve().relative_to(runtime.artifact_root.resolve())
            immutable_write(retained.parent / "metadata.json", _canonical_bytes(metadata))
            immutable_write(retained, raw)
            sources[metadata["candidate_id"]] = {"metadata": metadata,
                "source_relative_path": retained.relative_to(runtime.artifact_root).as_posix(),
                "source_sha256": hashlib.sha256(raw).hexdigest(), "source_size_bytes": len(raw)}
        children = []
        for ordinal, group in enumerate(plan.groups):
            members = [sources[member.candidate_id] for member in group]
            payload = {"name": f"{parent_job_id}_frustrampnn_{ordinal}",
                "model_id": "frustrampnn", "mode": "analyze", "parent_job_id": parent_job_id,
                "child_stage": "frustrampnn", "params": {"job_index": ordinal,
                    "frustrampnn_component_group": {"settings": settings,
                        "settings_value_origin": settings_value_origin, "candidates": members}}}
            child_id = submit_child_job(payload, parent_job_id=parent_job_id,
                stage="frustrampnn", child_key=str(ordinal), required=True)
            children.append({"job_id": child_id, "structure_count": len(members),
                             "candidates": [member["metadata"] for member in members]})
        runtime.register_group(f"{parent_job_id}:frustrampnn", [c["job_id"] for c in children])
        fanout = {"schema_name": "bms.structure-dataset-fanout.v1", "parent_job_id": parent_job_id,
            "selected_structure_count": len(records), "child_jobs": children,
            "fanout_id": plan.plan_id, "structures_per_job": settings["structures_per_job"],
            "effective_structures_per_job": settings["structures_per_job"] if settings["batching_enabled"] else 1,
            "replayed": False}
    else:
        ledger = GroupingLedger(output_receipt.with_suffix(".components.sqlite"),
            attempt_id=str(output_receipt.resolve().parent), plan=plan)
        boundary = ComponentBoundary(ledger)
        endpoint = f"{api_url.rstrip('/')}/api/frustrampnn/jobs/{parent_job_id}/workflow-dataset/analyze"
        response = boundary.submit(lambda: requests.post(
            endpoint, data={"parent_workflow_id": parent_workflow_id,
                "dataset_manifest": _canonical_bytes(manifest).decode("utf-8"),
                "frustrampnn_settings": settings_json, "settings_value_origin": settings_value_origin},
            files=files, headers={"Authorization": f"Bearer {capability}"}, timeout=120))
        response.raise_for_status()
        fanout = response.json()
    children = fanout.get("child_jobs")
    if (
        fanout.get("schema_name") != "bms.structure-dataset-fanout.v1"
        or fanout.get("parent_job_id") != parent_job_id
        or fanout.get("selected_structure_count") != len(records)
        or not isinstance(children, list)
        or not children
    ):
        raise RuntimeError("scheduler fan-out response is invalid")
    child_ids = [str(child.get("job_id") or "") for child in children]
    if "" in child_ids or len(set(child_ids)) != len(child_ids):
        raise RuntimeError("scheduler fan-out child identities are invalid")
    fanout_candidate_ids: list[str] = []
    for child in children:
        child_candidates = child.get("candidates")
        if not isinstance(child_candidates, list) or len(child_candidates) != child.get("structure_count"):
            raise RuntimeError("scheduler fan-out candidate grouping is invalid")
        for candidate in child_candidates:
            if not isinstance(candidate, dict) or not candidate.get("candidate_id"):
                raise RuntimeError("scheduler fan-out candidate grouping is invalid")
            fanout_candidate_ids.append(str(candidate["candidate_id"]))
    if fanout_candidate_ids != candidate_ids:
        raise RuntimeError("scheduler fan-out candidate order is invalid")
    plan.require_groups([[str(c["candidate_id"]) for c in child["candidates"]] for child in children])

    status_endpoint = f"{api_url.rstrip('/')}/api/jobs/{parent_job_id}/children/status"
    def observe() -> dict[str, Any]:
        if use_runtime:
            return fetch_children_status(parent_job_id, "frustrampnn")
        status_response = requests.get(
            status_endpoint, params={"stage": "frustrampnn"}, timeout=30
        )
        status_response.raise_for_status()
        return status_response.json()

    def complete(status_payload: dict[str, Any]) -> bool:
        observed = [str(value) for value in status_payload.get("child_ids", [])]
        if len(observed) != len(set(observed)) or set(observed) - set(child_ids):
            raise RuntimeError("foreign FrustraMPNN child lineage was observed")
        rows = status_payload.get("children", [])
        row_ids = [str(item.get("job_id") or "") for item in rows if isinstance(item, dict)]
        if (len(row_ids) != len(rows) or len(set(row_ids)) != len(row_ids)
                or set(row_ids) != set(observed)):
            raise RuntimeError("FrustraMPNN child status lineage is invalid")
        if status_payload.get("all_done") and set(observed) == set(child_ids):
            if any(item.get("status") not in ({"completed", "execution_finished"} if use_runtime else {"completed"}) for item in rows):
                raise RuntimeError("required FrustraMPNN child Jobs failed or were cancelled")
            return True
        return False

    if use_runtime:
        from wait_for_children import wait_for_children
        waited = wait_for_children(parent_job_id, "frustrampnn", poll_interval=poll_interval,
            timeout=timeout, expected_child_ids=child_ids)
        if waited["status"] not in {"complete", "outputs_available"}:
            raise RuntimeError(f"FrustraMPNN child join failed: {waited['status']}")
        status_payload = observe()
        if not complete(status_payload):
            raise RuntimeError("required FrustraMPNN child set is incomplete")
    else:
        status_payload = boundary.wait(observe, complete, poll_interval=poll_interval,
                                       timeout=timeout, clock=time.monotonic, sleep=time.sleep)
    if (
        status_payload.get("completed", 0) + status_payload.get("execution_finished", 0) != len(child_ids)
        or status_payload.get("failed")
        or status_payload.get("cancelled")
    ):
        raise RuntimeError("required FrustraMPNN child Jobs failed or were cancelled")

    child_status = {
        str(item.get("job_id")): item
        for item in status_payload.get("children", [])
        if isinstance(item, dict)
    }
    receipts: list[dict[str, Any]] = []
    if output_bundles.is_symlink() or (output_bundles.exists() and not output_bundles.is_dir()):
        raise ValueError("materialization bundle root is unsafe")
    output_bundles.mkdir(parents=True, exist_ok=True)
    if any(p.name not in candidate_ids for p in output_bundles.iterdir()):
        raise ValueError("materialization contains a foreign bundle")
    copied_ids: list[str] = []
    for child, child_id in zip(children, child_ids, strict=True):
        if use_runtime:
            receipt = runtime_child_receipt(child_id, Path(child_status[child_id]["output_dir"]))
        else:
            receipt_response = requests.get(
                f"{api_url.rstrip('/')}/api/frustrampnn/jobs/{child_id}/receipt", timeout=30)
            receipt_response.raise_for_status()
            receipt = receipt_response.json()
        child_candidate_ids = [
            str(item["candidate_id"]) for item in child["candidates"]
        ]
        expected_ids = [str(item.get("candidate_id")) for item in receipt.get("candidates", [])]
        result_ids = [
            str(item.get("candidate_id"))
            for item in receipt.get("results", [])
            if item.get("status") == "succeeded"
        ]
        if (
            receipt.get("job_id") != child_id
            or receipt.get("status") != "completed"
            or expected_ids != child_candidate_ids
            or expected_ids != result_ids
            or len(expected_ids) != child.get("structure_count")
            or (len(expected_ids) > 1 and not receipt.get("grouped_terminal_artifact"))
        ):
            raise RuntimeError("required FrustraMPNN child durable receipt is incomplete")
        output_root = Path(str((child_status.get(child_id) or {}).get("output_dir") or ""))
        for candidate_id in expected_ids:
            source_bundle = output_root / "frustrampnn" / "results" / candidate_id
            destination = output_bundles / candidate_id
            if (
                not output_root.is_absolute()
                or source_bundle.is_symlink()
                or not source_bundle.is_dir()
            ):
                raise RuntimeError("required FrustraMPNN child bundle is unavailable")
            authority = tree_authority(source_bundle)
            immutable_write(output_receipt.parent / f"{output_receipt.stem}.bundle-{candidate_id}.json",
                            _canonical_bytes(authority))
            reconcile_tree(source_bundle, destination, authority,
                           staging_root=output_bundles.parent / f".{output_bundles.name}.staging")
            copied_ids.append(candidate_id)
        receipts.append(receipt)
        # Retain the exact validated native receipt, without rewriting scientific
        # fields. The shared result ref is a separate placement-neutral binding.
        reference_path = output_receipt.parent / f"{output_receipt.stem}.child-{len(receipts)-1}.json"
        reference_bytes = _canonical_bytes(receipt)
        if reference_path.exists() and reference_path.read_bytes() != reference_bytes:
            raise RuntimeError("durable child receipt conflicts")
        durable_write(reference_path, reference_bytes)
        if use_runtime:
            from child_job_utils import seal_validated_child_files
            # Nextflow task work may be outside retained artifacts; seal the
            # same validated receipt in the original child's retained root.
            retained_receipt = output_root / "frustrampnn/worker_native_receipt_v1.json"
            immutable_write(retained_receipt, reference_bytes)
            files = [retained_receipt, output_root / "batches/batch.json"]
            for candidate_id in expected_ids:
                bundle = output_root / "frustrampnn/results" / candidate_id
                files.extend(bundle / name for name, item in tree_authority(bundle).items()
                             if item["kind"] == "file")
            if receipt.get("grouped_terminal_artifact"):
                files.append(output_root / "frustrampnn/batches/grouped_batch_terminal_receipt_v1.json")
            seal_validated_child_files(child_id, output_dir=output_root, result=receipt,
                files=files, role="frustrampnn-native-child-evidence")
        if boundary is not None:
            boundary.result(ResultReference(plan.component_id(len(receipts)-1),
                reference_path.name, hashlib.sha256(reference_bytes).hexdigest(),
                len(reference_bytes), "frustrampnn-native-child-receipt"), output_receipt.parent)
    if copied_ids != candidate_ids:
        raise RuntimeError("FrustraMPNN child bundle order/cardinality is incomplete")

    if boundary is not None:
        boundary.join(output_receipt.parent)
    if use_runtime:
        runtime.join_group(f"{parent_job_id}:frustrampnn")
    terminal = {
        "schema_name": "bms.frustrampnn.parent-fanout-terminal.v1",
        "schema_version": 1,
        "parent_job_id": parent_job_id,
        "parent_workflow_id": parent_workflow_id,
        "status": "complete",
        "requiredness": "required",
        "candidate_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "child_job_ids": child_ids,
        "fanout": {
            "fanout_id": fanout["fanout_id"],
            "structures_per_job": fanout["structures_per_job"],
            "effective_structures_per_job": fanout["effective_structures_per_job"],
            "replayed": fanout["replayed"],
        },
        "child_receipts": receipts,
    }
    terminal["receipt_sha256"] = hashlib.sha256(_canonical_bytes(terminal)).hexdigest()
    payload = _canonical_bytes(terminal) + b"\n"
    durable_write(output_receipt, payload)
    return {**terminal, "receipt_file_sha256": hashlib.sha256(payload).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-job-id", required=True)
    parser.add_argument("--parent-workflow-id", required=True)
    settings_source = parser.add_mutually_exclusive_group(required=True)
    settings_source.add_argument("--settings-json")
    settings_source.add_argument("--settings-json-file", type=Path)
    parser.add_argument("--settings-value-origin", required=True)
    parser.add_argument("--candidate-dir", action="append", type=Path, required=True)
    parser.add_argument("--output-receipt", type=Path, default=Path("frustrampnn_parent_terminal_v1.json"))
    parser.add_argument("--output-bundles", type=Path, default=Path("frustrampnn_child_bundles"))
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--poll-interval", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=0)
    args = parser.parse_args()
    settings_json = (
        args.settings_json
        if args.settings_json is not None
        else args.settings_json_file.read_text(encoding="utf-8")
    )
    execute_parent_fanout(
        parent_job_id=args.parent_job_id,
        parent_workflow_id=args.parent_workflow_id,
        settings_json=settings_json,
        settings_value_origin=args.settings_value_origin,
        candidate_dirs=args.candidate_dir,
        output_receipt=args.output_receipt,
        output_bundles=args.output_bundles,
        api_url=args.api_url,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
