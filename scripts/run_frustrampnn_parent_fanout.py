#!/usr/bin/env python3
"""Spawn, wait for, and seal scheduler-owned FrustraMPNN child evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

import requests

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


_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_BUNDLE_BYTES = 8 * _MAX_ARTIFACT_BYTES
_MAX_TRANSFER_BYTES = _MAX_BUNDLE_BYTES + 1024 * 1024


def _transfer_member(handle, expected_name: str, limit: int) -> bytes:
    # Parse only one regular USTAR header. Never let tarfile consume extension
    # records, follow links, decompress, or extract paths on our behalf.
    header = handle.read(512)
    if len(header) != 512:
        raise RuntimeError("truncated child bundle header")
    try:
        member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
    except (tarfile.TarError, UnicodeError, ValueError) as exc:
        raise RuntimeError("invalid child bundle header") from exc
    if (member.name != expected_name or member.type != tarfile.REGTYPE
            or member.linkname or member.size < 0 or member.size > limit):
        raise RuntimeError("unsafe or oversized child bundle member")
    payload = handle.read(member.size)
    padding = handle.read((-member.size) % 512)
    if len(payload) != member.size or padding != b"\0" * ((-member.size) % 512):
        raise RuntimeError("truncated child bundle member")
    return payload


def _unpack_result(handle, destination: Path, *, parent_job_id: str,
                   child_id: str, candidate_id: str, result: dict[str, Any]) -> None:
    inventory = json.loads(_transfer_member(handle, "transfer.json", _MAX_MANIFEST_BYTES))
    if (inventory.get("schema_name") != "bms.frustrampnn.parent-result-transfer.v1"
            or inventory.get("parent_job_id") != parent_job_id
            or inventory.get("child_job_id") != child_id
            or inventory.get("candidate_id") != candidate_id
            or inventory.get("manifest_sha256") != result.get("manifest_sha256")):
        raise RuntimeError("child bundle transfer lineage contradicts receipt")
    name = inventory.get("manifest_path")
    if name not in {f"frustrampnn_result_manifest_v{v}.json" for v in (1, 2, 3)}:
        raise RuntimeError("unsupported child result manifest")
    records = inventory.get("files")
    if not isinstance(records, list) or not 1 <= len(records) <= 32:
        raise RuntimeError("invalid child bundle inventory")
    names, total = [], 0
    for record in records:
        relative, size = record.get("relative_path"), record.get("bytes")
        if (not isinstance(relative, str) or _SAFE_ID.fullmatch(relative) is None
                or relative in {".", "..", "transfer.json"} or relative in names
                or type(size) is not int or not 0 <= size <= _MAX_ARTIFACT_BYTES
                or re.fullmatch(r"[a-f0-9]{64}", str(record.get("sha256"))) is None):
            raise RuntimeError("unsafe child bundle inventory")
        names.append(relative)
        total += size
    if names[0] != name or total > _MAX_BUNDLE_BYTES:
        raise RuntimeError("invalid child bundle inventory size/order")
    manifest_bytes = _transfer_member(handle, name, _MAX_MANIFEST_BYTES)
    manifest = json.loads(manifest_bytes)
    if (manifest_bytes != _canonical_bytes(manifest)
            or hashlib.sha256(manifest_bytes).hexdigest() != result.get("manifest_sha256")
            or manifest.get("parent_job_id") != child_id
            or manifest.get("candidate_id") != candidate_id
            or manifest.get("invocation_id") != result.get("invocation_id")
            or manifest.get("request_sha256") != result.get("request_sha256")
            or manifest.get("source_artifact_sha256") != result.get("source_artifact_sha256")):
        raise RuntimeError("child result manifest contradicts durable receipt")
    declared = manifest.get("artifacts")
    if not isinstance(declared, list):
        raise RuntimeError("invalid child manifest artifact inventory")
    terminal_name = f"workflow_component_result_v{manifest.get('schema_version')}.json"
    authority = {record["relative_path"]: record for record in declared}
    if (len(authority) != len(declared)
            or set(names) != {name, terminal_name, *authority}):
        raise RuntimeError("child transfer does not match canonical result inventory")
    for record in records:
        relative = record["relative_path"]
        if relative in authority and any(record[key] != authority[relative][key] for key in ("bytes", "sha256")):
            raise RuntimeError("child transfer hashes contradict result manifest")
        payload = manifest_bytes if relative == name else _transfer_member(handle, relative, record["bytes"])
        if len(payload) != record["bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
            raise RuntimeError("child bundle artifact hash/size mismatch")
        (destination / relative).write_bytes(payload)
    # Only TAR end markers/padding may follow the exact sealed inventory.
    trailing = handle.read(10241)
    if not 1024 <= len(trailing) <= 10240 or any(trailing):
        raise RuntimeError("child bundle has extra members or missing end markers")


def _retrieve_result(*, api_url: str, capability: str, parent_job_id: str,
                     child_id: str, candidate_id: str, result: dict[str, Any],
                     destination: Path) -> None:
    if any(_SAFE_ID.fullmatch(value) is None for value in (parent_job_id, child_id, candidate_id)):
        raise RuntimeError("unsafe child result identity")
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("child result destination already exists")
    endpoint = (f"{api_url.rstrip('/')}/api/frustrampnn/jobs/{parent_job_id}"
                f"/workflow-children/{child_id}/results/{candidate_id}/bundle")
    # The same configured private ingress works for local and remote parents.
    # Never fall back to an API-host path or forward this capability on redirects.
    with requests.get(endpoint, headers={"Authorization": f"Bearer {capability}"},
                      stream=True, allow_redirects=False, timeout=(30, 120)) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise RuntimeError("child result download did not return a complete snapshot")
        with tempfile.TemporaryFile(mode="w+b") as download:
            size = 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                size += len(chunk)
                if size > _MAX_TRANSFER_BYTES:
                    raise RuntimeError("child result download exceeds transfer limit")
                download.write(chunk)
            download.seek(0)
            with tempfile.TemporaryDirectory(prefix=".child-result-", dir=destination.parent) as staging:
                staged = Path(staging) / "bundle"
                staged.mkdir()
                _unpack_result(download, staged, parent_job_id=parent_job_id,
                               child_id=child_id, candidate_id=candidate_id, result=result)
                staged.rename(destination)


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
    if not capability:
        raise ValueError("parent workflow capability is required")
    settings = json.loads(settings_json)
    if _canonical_bytes(settings) != settings_json.encode("utf-8"):
        raise ValueError("settings_json must be compact canonical JSON")

    dataset: list[tuple[tuple[str, str], dict[str, Any], tuple[str, bytes, str]]] = []
    for raw_dir in candidate_dirs:
        metadata, source = _candidate_authority(Path(raw_dir), parent_job_id, parent_workflow_id)
        media_type = "chemical/x-mmcif" if source.suffix.lower() in {".cif", ".mmcif"} else "chemical/x-pdb"
        ordering_identity = (
            str(metadata["producer_candidate_key"]),
            str(metadata["candidate_id"]),
        )
        dataset.append((ordering_identity, metadata, (source.name, source.read_bytes(), media_type)))
    ordering_identities = [item[0] for item in dataset]
    if len(set(ordering_identities)) != len(ordering_identities):
        raise ValueError("terminal structure dataset has duplicate ordering identities")
    dataset.sort(key=lambda item: item[0])
    records = [item[1] for item in dataset]
    files = [("structure_files", item[2]) for item in dataset]
    candidate_ids = [str(record["candidate_id"]) for record in records]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("terminal structure dataset has duplicate candidate IDs")
    manifest = {"candidates": records}

    endpoint = f"{api_url.rstrip('/')}/api/frustrampnn/jobs/{parent_job_id}/workflow-dataset/analyze"
    response = requests.post(
        endpoint,
        data={
            "parent_workflow_id": parent_workflow_id,
            "dataset_manifest": _canonical_bytes(manifest).decode("utf-8"),
            "frustrampnn_settings": settings_json,
            "settings_value_origin": settings_value_origin,
        },
        files=files,
        headers={"Authorization": f"Bearer {capability}"},
        timeout=120,
    )
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

    status_endpoint = f"{api_url.rstrip('/')}/api/jobs/{parent_job_id}/children/status"
    started = time.monotonic()
    status_payload: dict[str, Any]
    while True:
        status_response = requests.get(
            status_endpoint, params={"stage": "frustrampnn"}, timeout=30
        )
        status_response.raise_for_status()
        status_payload = status_response.json()
        observed = [str(value) for value in status_payload.get("child_ids", [])]
        if set(observed) - set(child_ids):
            raise RuntimeError("foreign FrustraMPNN child lineage was observed")
        if status_payload.get("all_done") and set(observed) == set(child_ids):
            break
        if timeout > 0 and time.monotonic() - started > timeout:
            raise RuntimeError("timed out waiting for required FrustraMPNN child Jobs")
        time.sleep(poll_interval)
    if (
        status_payload.get("completed") != len(child_ids)
        or status_payload.get("failed")
        or status_payload.get("cancelled")
    ):
        raise RuntimeError("required FrustraMPNN child Jobs failed or were cancelled")

    receipts: list[dict[str, Any]] = []
    output_bundles.mkdir(parents=True, exist_ok=False)
    copied_ids: list[str] = []
    for child, child_id in zip(children, child_ids, strict=True):
        receipt_response = requests.get(
            f"{api_url.rstrip('/')}/api/frustrampnn/jobs/{child_id}/receipt", timeout=30
        )
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
            or receipt.get("parent_job_id") != parent_job_id
            or receipt.get("status") != "completed"
            or expected_ids != child_candidate_ids
            or len(receipt.get("results", [])) != len(expected_ids)
            or len(result_ids) != len(set(result_ids))
            or set(expected_ids) != set(result_ids)
            or len(expected_ids) != child.get("structure_count")
            or (len(expected_ids) > 1 and not receipt.get("grouped_terminal_artifact"))
        ):
            raise RuntimeError("required FrustraMPNN child durable receipt is incomplete")
        results_by_id = {item["candidate_id"]: item for item in receipt["results"]}
        for candidate_id in expected_ids:
            _retrieve_result(
                api_url=api_url, capability=capability, parent_job_id=parent_job_id,
                child_id=child_id, candidate_id=candidate_id,
                result=results_by_id[candidate_id], destination=output_bundles / candidate_id,
            )
            copied_ids.append(candidate_id)
        receipts.append(receipt)
    if copied_ids != candidate_ids:
        raise RuntimeError("FrustraMPNN child bundle order/cardinality is incomplete")

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
    output_receipt.write_bytes(payload)
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
