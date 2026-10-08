#!/usr/bin/env python3
"""
Shared helpers for orchestrated child-job resume/retry logic.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Also support package imports by existing API tests/native consumers.
_SCRIPT_ROOT = str(Path(__file__).resolve().parent)
if _SCRIPT_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPT_ROOT)
from typing import Dict, Any, Optional, Tuple

import requests

DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
RESUME_WORK_DIR = "work"

COMPLETED_STATUSES = {"completed"}
# Process completion exposes bytes, not validated scientific results.
OUTPUT_AVAILABLE_STATUSES = COMPLETED_STATUSES | {"execution_finished"}
ACTIVE_STATUSES = {"queued", "pending", "running", "awaiting_input"}
FAILED_STATUSES = {"failed", "cancelled"}


def component_runtime_enabled() -> bool:
    from lib.component_adapter import runtime_from_environment
    return runtime_from_environment() is not None


def submit_child_job(payload: dict, *, parent_job_id: str, stage: str,
                     child_key: str, required: bool = True) -> str:
    from lib.component_adapter import submit_child
    return submit_child(payload, parent_job_id=parent_job_id, stage=stage,
                        child_key=child_key, required=required)


def seal_validated_child_files(child_id: str, *, output_dir: Path, result: dict,
                               files: list[Path], role: str) -> None:
    """Bind files already accepted by a native validator; never validate by count."""
    from lib.component_adapter import runtime_from_environment, complete_validated_child
    from component_runtime import ResultReference, file_identity

    runtime = runtime_from_environment()
    if runtime is None:
        raise RuntimeError("native sealing requires component context")
    original = runtime.child_status(child_id)
    if Path(original["output_dir"]).resolve() != output_dir.resolve():
        raise ValueError("validated output does not belong to the submitted child")
    references = []
    for path in files:
        checksum, size = file_identity(path)
        references.append(ResultReference(child_id,
            path.resolve().relative_to(runtime.artifact_root.resolve()).as_posix(),
            checksum, size, role))
    complete_validated_child(child_id, result={**result, "output_dir": original["output_dir"]},
                             references=references)


def complete_native_collection(data: dict, accepted: dict[str, list[Path]], *, authority: str) -> dict:
    """Seal the native collector's retained set, NOT whole-job scientific validity.

    Call after existing copy/filter policy has run. Empty/duplicate-suppressed
    yields are represented by a real scoped receipt, not fabricated science.
    """
    import hashlib
    from lib.component_adapter import runtime_from_environment, complete_validated_child
    from component_runtime import ResultReference, canonical_bytes, file_identity

    runtime = runtime_from_environment()
    if runtime is None:
        return {}
    identities = data["child_ids"]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate collection child identity")
    rows = [runtime.child_status(identity) for identity in identities]
    available = {row["output_dir"]: row for row in rows
                 if row["status"] in OUTPUT_AVAILABLE_STATUSES}
    if set(data["child_output_dirs"]) != set(available) or set(accepted) != set(available):
        raise ValueError("collection does not cover exact available child outputs")
    receipts = []
    for output_dir, row in available.items():
        files = list(dict.fromkeys(Path(p).resolve() for p in accepted[output_dir]))
        records = []
        for path in files:
            if not path.is_relative_to(Path(output_dir).resolve()):
                raise ValueError("collected source does not belong to child")
            checksum, size = file_identity(path)
            records.append(dict(path=path.relative_to(runtime.artifact_root.resolve()).as_posix(),
                                sha256=checksum, size_bytes=size))
        receipt = dict(schema="bms.native-component-collection.v1", scope="component_collection",
                       scientific_validation=False, authority=authority,
                       attempt_id=runtime.attempt_id, child_id=row["job_id"],
                       required=row["required"], files=records)
        encoded = canonical_bytes(receipt)
        directory = runtime.artifact_root / ".bms-component-collections" / row["job_id"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (hashlib.sha256(authority.encode()).hexdigest() + ".json")
        # Same atomic immutable writer used by native stage receipts.
        import tempfile
        fd, temporary = tempfile.mkstemp(dir=directory, prefix=".pending-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != encoded:
                    raise ValueError("native collection receipt conflicts")
        finally:
            os.unlink(temporary)
        role = "native_component_collection"
        references = [ResultReference(row["job_id"], item["path"], item["sha256"], item["size_bytes"], role)
                      for item in records]
        references.append(ResultReference(row["job_id"], path.relative_to(runtime.artifact_root).as_posix(),
            hashlib.sha256(encoded).hexdigest(), len(encoded), role))
        complete_validated_child(row["job_id"], result={"native_collection": receipt, "output_dir": output_dir},
                                 references=references)
        receipts.append(path.relative_to(runtime.artifact_root).as_posix())
    # The unchanged native wait owns the cohort's at-least-one-success rule.
    # Required/optional members still undergo the shared exact-ID join.
    runtime.join_children(identities)
    return dict(scope="component_collection", receipts=receipts, exact_join=True)


def _runtime_children_status(runtime, parent_job_id: str, stage: str | None,
                             batch_name: str | None) -> dict:
    children = []
    for row in runtime.children(parent_job_id=parent_job_id, stage=stage):
        payload = row["payload"]
        if batch_name and payload.get("batch_name") != batch_name:
            continue
        params = payload.get("params") or {}
        children.append({**row, "id": row["job_id"],
                         "name": payload.get("name"), "params": params,
                         "job_index": params.get("job_index"),
                         "batch_index": params.get("batch_index"),
                         "pinned_gpu": payload.get("pinned_gpu")})
    counts = {key: sum(c["status"] == key for c in children)
              for key in ("completed", "execution_finished", "failed", "cancelled", "running")}
    outputs = [c["output_dir"] for c in children
               if c["status"] in OUTPUT_AVAILABLE_STATUSES and c.get("output_dir")]
    total = len(children)
    return {**counts, "children": children, "child_ids": [c["job_id"] for c in children],
            "total": total, "pending": sum(c["status"] in {"queued", "pending"} for c in children),
            "output_available": counts["completed"] + counts["execution_finished"],
            "all_done": bool(total) and sum(counts[k] for k in ("completed", "execution_finished", "failed", "cancelled")) == total,
            "success_rate": 100 * counts["completed"] / total if total else 0,
            "child_output_dirs": outputs, "child_output_dirs_all": outputs}


def fetch_children_status(
    parent_job_id: str,
    stage: str,
    api_url: str = DEFAULT_API_URL,
    batch_name: str | None = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    from lib.component_adapter import runtime_from_environment
    runtime = runtime_from_environment()
    if runtime is not None:
        return _runtime_children_status(runtime, parent_job_id, stage, batch_name)
    params = {"stage": stage}
    if batch_name:
        params["batch_name"] = batch_name

    resp = requests.get(
        f"{api_url}/api/jobs/{parent_job_id}/children/status",
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def children_by_name(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    children = payload.get("children", []) if isinstance(payload, dict) else []
    return {
        str(child.get("name")): child
        for child in children
        if child.get("name")
    }


def _normalize_slot_value(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        normalized = str(value).strip()
        return normalized or None


def child_slot_key(child: Dict[str, Any] | None) -> Optional[Tuple[str, int | str]]:
    if not isinstance(child, dict):
        return None
    for key in ("job_index", "batch_index"):
        value = _normalize_slot_value(child.get(key))
        if value is not None:
            return key, value
    return None


def children_by_slot(payload: Dict[str, Any]) -> Dict[Tuple[str, int | str], Dict[str, Any]]:
    children = payload.get("children", []) if isinstance(payload, dict) else []
    indexed: Dict[Tuple[str, int | str], Dict[str, Any]] = {}
    for child in children:
        key = child_slot_key(child)
        if key is not None:
            indexed[key] = child
    return indexed


def find_existing_child(
    payload: Dict[str, Any],
    *,
    child_name: str | None = None,
    job_index: Any = None,
    batch_index: Any = None,
) -> Dict[str, Any] | None:
    by_slot = children_by_slot(payload)
    for key_name, raw_value in (("job_index", job_index), ("batch_index", batch_index)):
        normalized = _normalize_slot_value(raw_value)
        if normalized is None:
            continue
        existing = by_slot.get((key_name, normalized))
        if existing is not None:
            return existing
    if child_name:
        return children_by_name(payload).get(child_name)
    return None


def preferred_child_gpu(child: Dict[str, Any] | None, fallback: Any = None) -> Optional[int]:
    for key in ("pinned_gpu", "assigned_gpu"):
        value = _normalize_slot_value((child or {}).get(key))
        if isinstance(value, int) and value >= 0:
            return value
    fallback_value = _normalize_slot_value(fallback)
    if isinstance(fallback_value, int) and fallback_value >= 0:
        return fallback_value
    return None


def child_status_kind(child: Dict[str, Any] | None) -> str:
    status = str((child or {}).get("status") or "").strip().lower()
    if status in COMPLETED_STATUSES:
        return "completed"
    if status == "execution_finished":
        return "output_available"
    if status in ACTIVE_STATUSES:
        return "active"
    if status in FAILED_STATUSES:
        return "failed"
    return "missing"


def apply_child_resume_params(
    params: Dict[str, Any],
    existing_child: Dict[str, Any] | None,
    resume_work_dir: str = RESUME_WORK_DIR,
) -> Dict[str, Any]:
    updated = dict(params or {})
    if not existing_child:
        return updated

    resume_job_id = existing_child.get("job_id")
    resume_source_dir = existing_child.get("output_dir")
    resume_stage_work_dir = existing_child.get("stage_work_dir")

    if resume_job_id:
        updated["resume_job_id"] = resume_job_id
    if resume_source_dir:
        updated["resume_source_dir"] = resume_source_dir
        updated.setdefault("resume_work_dir", resume_work_dir)
    if resume_stage_work_dir:
        updated["resume_stage_work_dir"] = resume_stage_work_dir

    return updated
