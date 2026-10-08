#!/usr/bin/env python3
"""
Shared helpers for orchestrated child-job resume/retry logic.
"""

from __future__ import annotations

import os
from typing import Dict, Any, Optional, Tuple

import requests

DEFAULT_API_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
RESUME_WORK_DIR = "work"

COMPLETED_STATUSES = {"completed"}
ACTIVE_STATUSES = {"queued", "pending", "running", "awaiting_input"}
FAILED_STATUSES = {"failed", "cancelled"}


def fetch_children_status(
    parent_job_id: str,
    stage: str,
    api_url: str = DEFAULT_API_URL,
    batch_name: str | None = None,
    timeout: int = 10,
) -> Dict[str, Any]:
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
