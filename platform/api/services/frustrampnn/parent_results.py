"""Capability-scoped, bounded snapshots of completed scheduler child results."""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from services.frustrampnn.jobs import ENVELOPE_KEY, FrustraMPNNChildError
from services.frustrampnn.manifests import (
    MAX_BUNDLE_BYTES,
    load_result_manifest_bytes_and_document,
    validate_result_manifest,
)

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def check_child_lineage(parent: Any, child: Any) -> None:
    envelope = (child.params or {}).get(ENVELOPE_KEY) or {}
    if (
        child.model_id != "frustrampnn"
        or child.child_stage != "frustrampnn"
        or child.parent_job_id != str(parent.id)
        or envelope.get("source_parent_job_id") != str(parent.id)
        or envelope.get("execution_owner_job_id") != str(child.id)
        or envelope.get("trigger") != "parent_workflow_terminal_dataset"
    ):
        raise FrustraMPNNChildError("child is not owned by this parent workflow")
    if child.status != "completed":
        raise FrustraMPNNChildError("child result is not completed")


def result_snapshot(child: Any, receipt: dict, candidate_id: str) -> BinaryIO:
    """Return an uncompressed manifest-first TAR of the validator's exact bytes.

    Never enumerate/copy arbitrary output files or reopen paths after validation.
    The existing validator enforces canonical inventory, per-role/aggregate limits,
    no-follow reads, and scientific/request/source closure before any response.
    """
    if _SAFE_ID.fullmatch(candidate_id) is None:
        raise FrustraMPNNChildError("invalid candidate identity")
    selected = [r for r in receipt["candidates"] if r["candidate_id"] == candidate_id]
    results = [r for r in receipt["results"] if r["candidate_id"] == candidate_id]
    if len(selected) != 1 or len(results) != 1 or results[0]["status"] != "succeeded":
        raise FrustraMPNNChildError("candidate has no unique completed durable result")
    result = results[0]
    root = Path(str(child.output_dir))
    if not root.is_absolute():
        raise FrustraMPNNChildError("child result root is not absolute")
    root = root / "frustrampnn" / "results" / candidate_id
    name, manifest_bytes, manifest = load_result_manifest_bytes_and_document(root)
    if (
        hashlib.sha256(manifest_bytes).hexdigest() != result["manifest_sha256"]
        or manifest.get("parent_job_id") != str(child.id)
        or manifest.get("candidate_id") != candidate_id
        or manifest.get("invocation_id") != result["invocation_id"]
        or manifest.get("invocation_id") != selected[0]["invocation_id"]
        or manifest.get("request_sha256") != result["request_sha256"]
        or manifest.get("source_artifact_sha256") != result["source_artifact_sha256"]
        or result["request_sha256"] != selected[0]["component_request_sha256"]
    ):
        raise FrustraMPNNChildError("result manifest contradicts child durable receipt")
    payloads = validate_result_manifest(root, manifest)
    if payloads.get(name) != manifest_bytes or sum(map(len, payloads.values())) > MAX_BUNDLE_BYTES:
        raise FrustraMPNNChildError("result snapshot identity/size is invalid")
    inventory = {
        "schema_name": "bms.frustrampnn.parent-result-transfer.v1",
        "parent_job_id": receipt["parent_job_id"],
        "child_job_id": str(child.id),
        "candidate_id": candidate_id,
        "manifest_path": name,
        "manifest_sha256": result["manifest_sha256"],
        "files": [
            {"relative_path": relative, "bytes": len(payloads[relative]),
             "sha256": hashlib.sha256(payloads[relative]).hexdigest()}
            for relative in [name, *sorted(set(payloads) - {name})]
        ],
    }
    snapshot = tempfile.TemporaryFile(mode="w+b")
    try:
        with tarfile.open(fileobj=snapshot, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            transfer = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
            for relative, payload in [("transfer.json", transfer), *[
                (record["relative_path"], payloads[record["relative_path"]])
                for record in inventory["files"]
            ]]:
                member = tarfile.TarInfo(relative)
                member.size = len(payload)
                member.mode = 0o600
                archive.addfile(member, io.BytesIO(payload))
        snapshot.seek(0)
        return snapshot
    except BaseException:
        snapshot.close()
        raise
