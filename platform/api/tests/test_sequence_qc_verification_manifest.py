from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.sequence_qc_manifest import find_manifest_for_job, load_sequence_qc_manifest


CHECK_NAMES = (
    "sequence_identity",
    "read_support",
    "coverage",
    "contamination",
    "topology",
)


def verification_payload() -> dict:
    def evidence(role: str) -> dict:
        return {
            "state": "present",
            "role": role,
            "declared_path": f"{role}.dat",
            "path": f"{role}.dat",
            "sha256": "b" * 64,
            "size_bytes": 1,
            "reason": None,
            "semantic_validation": {"status": "valid", "validator": "test", "reason": None},
        }

    inputs = {
        role: evidence(role)
        for role in (
            "reference",
            "observed",
            "source_reads",
            "support",
            "alignment",
            "alignment_index",
            "alignment_stats",
            "topology",
        )
    }
    inputs["observed"]["independent_from_expected"] = True
    return {
        "artifact_schema_version": 2,
        "schema": "biomodstack.construct_verification.v2",
        "job_id": "job-1",
        "sample_name": "sample",
        "execution": {"status": "SUCCEEDED", "exit_code": 0, "reason_codes": []},
        "verdict": "PASS",
        "reason_codes": ["ALL_CHECKS_PASS"],
        "threshold_profile": {
            "id": "plasmid_strict_v1",
            "version": "1.0.0",
            "sha256": "a" * 64,
            "calibration_status": "calibrated",
            "public_accuracy_validated": True,
            "values": {},
        },
        "inputs": inputs,
        "checks": {
            name: {"status": "pass", "reason_codes": [], "metrics": {}}
            for name in CHECK_NAMES
        },
        "variants": [],
        "summary": {},
        "provenance": {
            "verifier": {"name": "test", "version": "1"},
            "workflow": {"name": "ConstructVerify", "module": "modules/ngs/construct_verify.nf", "version": "2"},
            "tool_versions": {"python": "test", "samtools": "test"},
            "commands": [{"name": "test", "argv": ["test"]}],
            "generated_at": "2026-07-18T00:00:00+00:00",
        },
        "artifacts": [],
    }


def test_verification_manifest_is_preferred_over_legacy_fastq_manifest(tmp_path: Path) -> None:
    job = tmp_path / "job-1"
    verification = job / "verification" / "qc_manifest.json"
    legacy = job / "fastq_qc" / "qc_manifest.json"
    verification.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    verification.write_text(json.dumps(verification_payload()), encoding="utf-8")
    legacy.write_text("{}", encoding="utf-8")

    assert find_manifest_for_job("job-1", results_dir=tmp_path) == verification.resolve()


def test_malformed_verification_json_degrades_to_review(tmp_path: Path) -> None:
    manifest_path = tmp_path / "verification" / "qc_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{broken", encoding="utf-8")

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["verdict"] == "REVIEW"
    assert "MALFORMED_VERIFICATION_MANIFEST" in manifest["reason_codes"]


def test_invalid_check_status_degrades_to_review(tmp_path: Path) -> None:
    payload = verification_payload()
    payload["checks"]["coverage"]["status"] = "unknown"
    manifest_path = tmp_path / "verification" / "qc_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["verdict"] == "REVIEW"
    assert "MALFORMED_VERIFICATION_MANIFEST" in manifest["reason_codes"]


def test_experimental_profile_pass_is_downgraded_by_public_consumer(tmp_path: Path) -> None:
    payload = verification_payload()
    payload["threshold_profile"].update(
        {
            "calibration_status": "experimental",
            "public_accuracy_validated": False,
            "values": {
                "version": "1.0.0",
                "calibration_status": "experimental",
                "public_accuracy_validated": False,
                "automatic_pass_eligible": False,
            },
        }
    )
    manifest_path = tmp_path / "verification" / "qc_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["verdict"] == "REVIEW"
    assert "UNCALIBRATED_PROFILE" in manifest["reason_codes"]
    assert "ALL_CHECKS_PASS" in manifest["reason_codes"]


def test_profile_pass_identity_uses_verifier_canonical_selected_policy_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import hashlib
    from services import sequence_qc_manifest as service

    values = {
        "version": "2.0.0",
        "calibration_status": "calibrated",
        "public_accuracy_validated": True,
        "automatic_pass_eligible": True,
        "min_depth": 20,
    }
    config = {"schema_version": "1", "profiles": {"qualified_v2": values}}
    config_path = tmp_path / "profiles.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(service, "CANONICAL_PROFILE_PATH", config_path)
    canonical_digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    profile = {
        "id": "qualified_v2",
        "version": "2.0.0",
        "calibration_status": "calibrated",
        "public_accuracy_validated": True,
        "sha256": canonical_digest,
        "values": values,
    }

    assert service._profile_is_canonically_pass_eligible(profile) is True
    profile["sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert service._profile_is_canonically_pass_eligible(profile) is False


def test_missing_required_verification_artifact_degrades_to_review(tmp_path: Path) -> None:
    payload = verification_payload()
    payload["artifacts"] = [
        {
            "kind": "verification_summary",
            "path": "missing.tsv",
            "required": True,
            "state": "present",
        }
    ]
    manifest_path = tmp_path / "verification" / "qc_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["verdict"] == "REVIEW"
    assert "REQUIRED_ARTIFACT_MISSING" in manifest["reason_codes"]


def test_required_artifact_digest_tampering_degrades_pass_to_review(tmp_path: Path) -> None:
    artifact_path = tmp_path / "verification" / "summary.tsv"
    artifact_path.parent.mkdir(parents=True)
    original = b"verdict\tPASS\n"
    artifact_path.write_bytes(original)
    payload = verification_payload()
    payload["artifacts"] = [
        {
            "kind": "verification_summary",
            "path": "summary.tsv",
            "required": True,
            "state": "present",
            "sha256": hashlib.sha256(original).hexdigest(),
            "size_bytes": len(original),
            "reason": None,
            "semantic_validation": {"status": "valid", "validator": "test", "reason": None},
        }
    ]
    manifest_path = artifact_path.parent / "qc_manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    artifact_path.write_bytes(b"verdict\tFAIL\n")

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["verdict"] == "REVIEW"
    assert "ARTIFACT_INTEGRITY_INVALID" in manifest["reason_codes"]


def test_required_artifact_semantic_failure_degrades_pass_to_review(tmp_path: Path) -> None:
    artifact_path = tmp_path / "verification" / "summary.tsv"
    artifact_path.parent.mkdir(parents=True)
    content = b"not-a-valid-summary\n"
    artifact_path.write_bytes(content)
    payload = verification_payload()
    payload["artifacts"] = [
        {
            "kind": "verification_summary",
            "path": "summary.tsv",
            "required": True,
            "state": "present",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "reason": None,
            "semantic_validation": {"status": "invalid", "validator": "test", "reason": "bad table"},
        }
    ]
    manifest_path = artifact_path.parent / "qc_manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_sequence_qc_manifest(manifest_path)

    assert manifest["verdict"] == "REVIEW"
    assert "ARTIFACT_SEMANTIC_VALIDATION_FAILED" in manifest["reason_codes"]
