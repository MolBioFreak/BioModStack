from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from scripts.probes.conformational_mapping.phase_review_common import PhaseReviewError, adjudicate


KEY = b"current-run-evidence-key-32-bytes-minimum"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write_report(root: Path, role: str, run_id: str, *, status: str = "PASS") -> dict:
    path = root / f"{role}.json"
    path.write_bytes(_canonical({"current_run_id": run_id, "status": status, "remaining_production_gaps": []}))
    return {"role": role, "relative_path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _manifest(root: Path, run_id: str, records: list[dict]) -> Path:
    unsigned = {
        "schema_name": "cm_phase_evidence", "schema_version": 1, "phase": 12,
        "current_run_id": run_id, "principal_id": "operator", "captured_at": "2026-07-19T00:00:00Z",
        "command": ["bounded-current-run"], "exit_code": 0, "artifacts": records,
    }
    payload = {**unsigned, "authentication_hmac_sha256": hmac.new(KEY, _canonical(unsigned), hashlib.sha256).hexdigest()}
    path = root / "manifest.json"
    path.write_bytes(_canonical(payload))
    return path


def test_cm12_release_manifest_requires_all_authenticated_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CM_EVIDENCE_HMAC_KEY", KEY.decode())
    roles = ["api_contract_report", "persistence_report", "workflow_report", "security_report", "independent_review"]
    manifest = _manifest(tmp_path, "run-1", [_write_report(tmp_path, role, "run-1") for role in roles])
    review = adjudicate(12, tmp_path, manifest, tmp_path / "review.json")
    assert review["decision"] == "GO"
    assert all(item["passed"] for item in review["checks"])


def test_cm12_release_manifest_rejects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CM_EVIDENCE_HMAC_KEY", KEY.decode())
    roles = ["api_contract_report", "persistence_report", "workflow_report", "security_report", "independent_review"]
    records = [_write_report(tmp_path, role, "run-1") for role in roles]
    manifest = _manifest(tmp_path, "run-1", records)
    (tmp_path / "workflow_report.json").write_text("{}")
    with pytest.raises(PhaseReviewError, match="byte identity"):
        adjudicate(12, tmp_path, manifest, tmp_path / "review.json")


def test_cm12_release_manifest_records_stop_for_factual_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CM_EVIDENCE_HMAC_KEY", KEY.decode())
    roles = ["api_contract_report", "persistence_report", "workflow_report", "security_report", "independent_review"]
    records = [_write_report(tmp_path, role, "run-1", status="STOP" if role == "workflow_report" else "PASS") for role in roles]
    review = adjudicate(12, tmp_path, _manifest(tmp_path, "run-1", records), tmp_path / "review.json")
    assert review["decision"] == "STOP"
    assert {item["check"] for item in review["checks"] if not item["passed"]} == {"workflow_report"}
