#!/usr/bin/env python3
"""Authenticated current-run evidence adjudication for CM phase gates."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class PhaseReviewError(ValueError):
    """Evidence is missing, stale, unauthenticated, or internally inconsistent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PhaseReviewError("evidence artifact path is not canonical")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise PhaseReviewError("evidence artifact path is not canonical")
    return path


def _artifact(root: Path, record: Mapping[str, Any]) -> Path:
    if set(record) != {"role", "relative_path", "sha256", "bytes"}:
        raise PhaseReviewError("evidence artifact record is malformed")
    relative = _relative(record["relative_path"])
    path = root.joinpath(*relative.parts)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PhaseReviewError("evidence artifact is not a no-follow regular file")
    path.resolve(strict=True).relative_to(root.resolve(strict=True))
    if metadata.st_size != record["bytes"] or _sha256(path) != record["sha256"]:
        raise PhaseReviewError("evidence artifact byte identity mismatch")
    return path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def adjudicate(phase: int, evidence_root: Path, manifest_path: Path, output: Path) -> dict[str, Any]:
    if phase not in {4, 12}:
        raise PhaseReviewError("only phase 4 and phase 12 reviews are supported")
    secret = os.environ.get("BMS_CM_EVIDENCE_HMAC_KEY", "").encode()
    if len(secret) < 32:
        raise PhaseReviewError("authenticated evidence key is unavailable")
    root = evidence_root.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise PhaseReviewError("external evidence root is unsafe")
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise PhaseReviewError("current-run evidence manifest must be an object")
    supplied_hmac = manifest.get("authentication_hmac_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "authentication_hmac_sha256"}
    expected_hmac = hmac.new(secret, _canonical(unsigned), hashlib.sha256).hexdigest()
    if not isinstance(supplied_hmac, str) or not hmac.compare_digest(supplied_hmac, expected_hmac):
        raise PhaseReviewError("current-run evidence authentication failed")
    required = {
        "schema_name", "schema_version", "phase", "current_run_id", "principal_id",
        "captured_at", "command", "exit_code", "artifacts",
    }
    if set(unsigned) != required or unsigned["schema_name"] != "cm_phase_evidence" or unsigned["schema_version"] != 1:
        raise PhaseReviewError("current-run evidence manifest fields are incomplete or unknown")
    if unsigned["phase"] != phase or not unsigned["current_run_id"] or not unsigned["principal_id"]:
        raise PhaseReviewError("current-run phase or principal identity mismatch")
    if unsigned["exit_code"] != 0 or not isinstance(unsigned["command"], list) or not unsigned["command"]:
        raise PhaseReviewError("current-run command did not complete successfully")
    records = unsigned["artifacts"]
    if not isinstance(records, list) or not records:
        raise PhaseReviewError("current-run evidence artifact set is empty")
    by_role: dict[str, tuple[Mapping[str, Any], Path]] = {}
    for record in records:
        if not isinstance(record, Mapping) or record.get("role") in by_role:
            raise PhaseReviewError("current-run evidence roles are malformed or duplicated")
        by_role[str(record["role"])] = (record, _artifact(root, record))

    checks: list[dict[str, Any]] = []
    if phase == 4:
        required_roles = {"ensemble_manifest", "native_manifest", "execution_receipt"}
        if not required_roles.issubset(by_role):
            raise PhaseReviewError("phase 4 runtime evidence is incomplete")
        ensemble = _load_json(by_role["ensemble_manifest"][1])
        native = _load_json(by_role["native_manifest"][1])
        receipt = _load_json(by_role["execution_receipt"][1])
        checks.extend([
            {"check": "terminal_complete", "passed": ensemble.get("terminal_status") == "complete"},
            {"check": "resume_authority", "passed": ensemble.get("resumable") is True and isinstance(ensemble.get("resume_descriptor"), dict)},
            {"check": "native_manifest_binding", "passed": ensemble.get("native_manifest_sha256") == hashlib.sha256(_canonical(native)).hexdigest()},
            {"check": "execution_receipt", "passed": receipt.get("status") == "container_executed" and receipt.get("request_sha256") == ensemble.get("request_sha256")},
        ])
    else:
        required_roles = {
            "api_contract_report", "persistence_report", "workflow_report",
            "security_report", "independent_review",
        }
        if not required_roles.issubset(by_role):
            raise PhaseReviewError("phase 12 review evidence is incomplete")
        for role in sorted(required_roles):
            report = _load_json(by_role[role][1])
            passed = (
                isinstance(report, dict)
                and report.get("current_run_id") == unsigned["current_run_id"]
                and report.get("status") == "PASS"
                and report.get("remaining_production_gaps") == []
            )
            checks.append({"check": role, "passed": passed})
    decision = "GO" if checks and all(item["passed"] for item in checks) else "STOP"
    review = {
        "schema_name": "cm_phase_review", "schema_version": 1, "phase": phase,
        "current_run_id": unsigned["current_run_id"], "principal_id": unsigned["principal_id"],
        "evidence_manifest_sha256": _sha256(manifest_path), "checks": checks,
        "decision": decision, "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _atomic_json(output, review)
    return review
