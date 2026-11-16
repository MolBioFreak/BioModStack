"""Reads reconciliation receipts saved on older results. The repair writer and its CLI are retired; nothing writes these now."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, cast

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker

from services.sqlite_backup import open_verified_sqlite_backup

_REQUIRED_STAGES = ("fastq_align", "dimer_qc", "fastq_qc", "construct_verification")
_STAGE_SUFFIXES = {
    "fastq_align": (
        "align/aligned.bam",
        "align/aligned.bam.bai",
        "align/reference.fasta",
        "align/reference.fasta.fai",
        "align/fastq_align.log",
    ),
    "dimer_qc": (
        "multimer_qc/dimer_breakpoint_call.tsv",
        "multimer_qc/dimer_evidence_by_position.tsv",
        "multimer_qc/dimer_read_events.tsv",
        "multimer_qc/dimer_breakpoint_sequences.tsv",
        "multimer_qc/dimer_secondary_anomalies.tsv",
        "multimer_qc/dimer_secondary_summary.tsv",
    ),
    "fastq_qc": (
        "fastq_qc/read_lengths.tsv",
        "fastq_qc/fastq_qc_summary.tsv",
        "fastq_qc/fastq_alignment_stats.tsv",
        "fastq_qc/fastq_coverage.tsv",
        "fastq_qc/per_base_support.tsv",
        "fastq_qc/qc_manifest.json",
        "fastq_qc/igv_report.html",
        "fastq_qc/fastq_consensus.fasta",
    ),
    "construct_verification": (
        "verification/qc_manifest.json",
        "verification/verification_summary.tsv",
        "verification/variants.vcf",
        "verification/per_base_metrics.tsv",
        "verification/evidence.html",
        "verification/topology_evidence.json",
    ),
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESULT_PREFIX_RE = re.compile(r"^bms_results/[^/]+$")
_RECEIPT_KEY = "ont_fastq_qc_reconciliation_v1"
_HIERARCHY_KEY = "alignment_hierarchy_authority_v1"
_DIGEST_DOMAIN = b"bms.ont-fastq-qc-reconciliation.v1\0"


class OntFastqQcReconciliationError(RuntimeError):
    """Raised when an ONT FASTQ-QC query-mirror repair is unsafe."""

    exit_code = 2


class OntFastqQcReconciliationConflict(OntFastqQcReconciliationError):
    """Raised when a reconciliation compare-and-swap loses authority."""

    exit_code = 3


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise OntFastqQcReconciliationError(f"unsupported protected value type: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return rfc8785.dumps(_json_value(value))
    except (TypeError, ValueError) as exc:
        raise OntFastqQcReconciliationError("reconciliation evidence is not canonical JSON") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def reconciliation_authority_digest(label: str, value: Any) -> str:
    if not isinstance(label, str) or not label or "\0" in label:
        raise OntFastqQcReconciliationError("reconciliation digest label is invalid")
    return hashlib.sha256(_DIGEST_DOMAIN + label.encode("utf-8") + b"\0" + _canonical_bytes(value)).hexdigest()


def _protected_preimage(job: Any) -> dict[str, Any]:
    return {
        "schema": "bms.ont-fastq-qc-protected-row.v1",
        "job_id": str(job.id),
        "status": copy.deepcopy(getattr(job, "status", None)),
        "queue_status": copy.deepcopy(getattr(job, "queue_status", None)),
        "awaiting_input": copy.deepcopy(getattr(job, "awaiting_input", None)),
        "paused": copy.deepcopy(getattr(job, "paused", None)),
        "completed_at": copy.deepcopy(getattr(job, "completed_at", None)),
        "params": copy.deepcopy(getattr(job, "params", None)),
        "provenance": copy.deepcopy(getattr(job, "provenance", None)),
        "completed_stages": copy.deepcopy(getattr(job, "completed_stages", None)),
        "stage_outputs": copy.deepcopy(getattr(job, "stage_outputs", None)),
        "output_dir": copy.deepcopy(getattr(job, "output_dir", None)),
        "error_message": copy.deepcopy(getattr(job, "error_message", None)),
    }


def _stage_output_prefix(stage_outputs: Mapping[str, tuple[str, ...]]) -> str:
    prefixes: set[str] = set()
    seen: set[str] = set()
    for stage in _REQUIRED_STAGES:
        outputs = stage_outputs.get(stage)
        suffixes = _STAGE_SUFFIXES[stage]
        if not isinstance(outputs, tuple) or len(outputs) != len(suffixes):
            raise OntFastqQcReconciliationError(f"reconciliation stage output count is invalid: {stage}")
        for output, suffix in zip(outputs, suffixes, strict=True):
            if not isinstance(output, str) or not output.endswith("/" + suffix):
                raise OntFastqQcReconciliationError(f"reconciliation stage output role is invalid: {stage}")
            prefix = output[: -(len(suffix) + 1)]
            if _RESULT_PREFIX_RE.fullmatch(prefix) is None or output.startswith("/") or ".." in output.split("/"):
                raise OntFastqQcReconciliationError(f"reconciliation stage output path is invalid: {stage}")
            if output in seen:
                raise OntFastqQcReconciliationError("reconciliation stage outputs contain a duplicate")
            seen.add(output)
            prefixes.add(prefix)
    if len(prefixes) != 1:
        raise OntFastqQcReconciliationError("reconciliation stage outputs cross result roots")
    return next(iter(prefixes))


@lru_cache(maxsize=1)
def _receipt_contract_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[3] / "schemas/ngs/ont_fastq_qc_reconciliation_receipt_v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as exc:
        raise OntFastqQcReconciliationError("reconciliation receipt schema is unavailable") from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _valid_backup(backup: Any) -> bool:
    return (
        isinstance(backup, dict)
        and isinstance(backup.get("backup_id"), str)
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", backup["backup_id"]) is not None
        and isinstance(backup.get("sha256"), str)
        and _SHA256_RE.fullmatch(backup["sha256"]) is not None
        and type(backup.get("size_bytes")) is int
        and backup["size_bytes"] > 0
        and backup.get("integrity_check") == "ok"
        and backup.get("foreign_key_violations") == 0
    )


def _valid_source_snapshot(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if set(snapshot) != {
        "schema",
        "database_identity_sha256",
        "source_size_bytes",
        "source_sha256",
        "page_size",
        "page_count",
        "schema_version",
        "data_version",
        "integrity_check",
        "foreign_key_violations",
    }:
        return False
    return (
        snapshot.get("schema") == "bms.sqlite-backup-source-preimage.v1"
        and isinstance(snapshot.get("database_identity_sha256"), str)
        and _SHA256_RE.fullmatch(snapshot["database_identity_sha256"]) is not None
        and type(snapshot.get("source_size_bytes")) is int
        and snapshot["source_size_bytes"] > 0
        and isinstance(snapshot.get("source_sha256"), str)
        and _SHA256_RE.fullmatch(snapshot["source_sha256"]) is not None
        and type(snapshot.get("page_size")) is int
        and snapshot["page_size"] >= 512
        and type(snapshot.get("page_count")) is int
        and snapshot["page_count"] >= 1
        and type(snapshot.get("schema_version")) is int
        and snapshot["schema_version"] >= 0
        and type(snapshot.get("data_version")) is int
        and snapshot["data_version"] >= 0
        and snapshot.get("integrity_check") == "ok"
        and snapshot.get("foreign_key_violations") == 0
    )


def _validate_bound_receipt(receipt: Mapping[str, Any]) -> None:
    errors = sorted(_receipt_contract_validator().iter_errors(dict(receipt)), key=lambda item: list(item.path))
    if errors:
        path = ".".join(str(part) for part in errors[0].path) or "root"
        raise OntFastqQcReconciliationError(f"reconciliation receipt violates its contract at {path}")
    backup = receipt.get("backup")
    backup_mapping = cast(Mapping[str, Any], backup) if isinstance(backup, Mapping) else {}
    source_snapshot = receipt.get("source_snapshot")
    if (
        not _valid_backup(backup)
        or not _valid_source_snapshot(source_snapshot)
        or receipt.get("backup_source_preimage_sha256")
        != reconciliation_authority_digest("backup-source-preimage", source_snapshot)
        or receipt.get("receipt_sha256")
        != reconciliation_authority_digest(
            "receipt",
            {key: value for key, value in receipt.items() if key != "receipt_sha256"},
        )
    ):
        raise OntFastqQcReconciliationError("reconciliation receipt digest or backup binding is invalid")
    stage_outputs = receipt.get("stage_outputs")
    normalized_outputs = {
        key: tuple(value) for key, value in stage_outputs.items()
    } if isinstance(stage_outputs, dict) else {}
    _stage_output_prefix(normalized_outputs)


def validate_persisted_reconciliation_receipt(
    receipt: Mapping[str, Any],
    *,
    job: Any,
    expected_package: Mapping[str, Any],
    provenance_preimage: Mapping[str, Any] | None = None,
) -> None:
    """Validate persisted authority against the backup preimage and current protected row."""
    _validate_bound_receipt(receipt)
    job_id = str(getattr(job, "id", ""))
    completed_stages = list(getattr(job, "completed_stages", None) or [])
    raw_stage_outputs = getattr(job, "stage_outputs", None)
    stage_outputs = raw_stage_outputs if isinstance(raw_stage_outputs, Mapping) else {}
    canonical_outputs = {key: list(stage_outputs.get(key) or []) for key in _REQUIRED_STAGES}
    provenance = getattr(job, "provenance", None)
    provenance = provenance if isinstance(provenance, Mapping) else {}
    current_receipt_free = {
        key: copy.deepcopy(value) for key, value in provenance.items() if key != _RECEIPT_KEY
    }
    hierarchy = current_receipt_free.get(_HIERARCHY_KEY)
    hierarchy_document = hierarchy.get("document") if isinstance(hierarchy, Mapping) else None
    hierarchy_digest = hierarchy.get("digest") if isinstance(hierarchy, Mapping) else None
    source_snapshot = receipt.get("source_snapshot")

    backup_job = None
    if provenance_preimage is None:
        backup = receipt.get("backup")
        backup_id = backup.get("backup_id") if isinstance(backup, Mapping) else None
        results_dir = os.environ.get("BMS_RESULTS_DIR")
        if not isinstance(backup_id, str) or Path(backup_id).name != backup_id or not results_dir:
            raise OntFastqQcReconciliationError("reconciliation backup authority is unavailable")
        backup_path = Path(results_dir).resolve().parent / "backups" / "ngs-reconciliation" / backup_id
        try:
            backup_record = cast(Mapping[str, Any], backup)
            connection = open_verified_sqlite_backup(
                backup_path,
                expected_size_bytes=int(backup_record["size_bytes"]),
                expected_sha256=str(backup_record["sha256"]),
            )
            try:
                row = connection.execute(
                    "SELECT id, status, queue_status, awaiting_input, paused, completed_at, params, provenance, "
                    "completed_stages, stage_outputs, output_dir, error_message FROM jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
            finally:
                connection.close()
            if row is None:
                raise OntFastqQcReconciliationError("reconciliation backup job is unavailable")
            completed_at = datetime.fromisoformat(str(row[5]).replace("Z", "+00:00")) if row[5] else None
            backup_job = SimpleNamespace(
                id=row[0], status=row[1], queue_status=row[2], awaiting_input=bool(row[3]), paused=bool(row[4]),
                completed_at=completed_at,
                params=json.loads(row[6]) if isinstance(row[6], str) else row[6],
                provenance=json.loads(row[7]) if isinstance(row[7], str) else row[7],
                completed_stages=json.loads(row[8]) if isinstance(row[8], str) else row[8],
                stage_outputs=json.loads(row[9]) if isinstance(row[9], str) else row[9],
                output_dir=row[10], error_message=row[11],
            )
            provenance_preimage = backup_job.provenance
        except (OSError, RuntimeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
            raise OntFastqQcReconciliationError("reconciliation backup authority is unavailable") from exc
    if not isinstance(provenance_preimage, Mapping):
        raise OntFastqQcReconciliationError("reconciliation provenance preimage is unavailable")
    expected_receipt_free = copy.deepcopy(dict(provenance_preimage))
    preimage_hierarchy = expected_receipt_free.get(_HIERARCHY_KEY)
    if preimage_hierarchy is None:
        expected_receipt_free[_HIERARCHY_KEY] = copy.deepcopy(hierarchy)
    elif preimage_hierarchy != hierarchy:
        raise OntFastqQcReconciliationError("reconciliation hierarchy preimage is inconsistent")
    mutable_access_keys = {
        "alignment_access_rotation_count",
        "alignment_access_token_sha256",
        "alignment_access_scheme",
        "alignment_access_revoked",
        _HIERARCHY_KEY,
        "ont_fastq_qc_hierarchy_v1",
    }
    current_stable = {key: value for key, value in current_receipt_free.items() if key not in mutable_access_keys}
    expected_stable = {key: value for key, value in expected_receipt_free.items() if key not in mutable_access_keys}

    identity_fields = {
        "project_id": (hierarchy_document.get("project") or {}).get("id"),
        "global_experiment_id": (hierarchy_document.get("global_experiment") or {}).get("id"),
        "domain_experiment_id": (hierarchy_document.get("domain_experiment") or {}).get("id"),
        "state_revision_id": (hierarchy_document.get("domain_experiment") or {}).get("state_revision_id"),
        "member_receipt_id": (hierarchy_document.get("member") or {}).get("receipt_id"),
        "sample_revision_id": (hierarchy_document.get("sample") or {}).get("revision_id"),
        "reference_revision_id": (hierarchy_document.get("reference") or {}).get("revision_id"),
    } if isinstance(hierarchy_document, Mapping) else {}
    expected = {
        "job_id": job_id,
        "normalized_request_sha256": reconciliation_authority_digest(
            "normalized-request", _normalized_request(job_id)
        ),
        "completed_stages_postimage_sha256": reconciliation_authority_digest(
            "completed-stages-postimage", completed_stages
        ),
        "stage_outputs_postimage_sha256": reconciliation_authority_digest(
            "stage-outputs-postimage", canonical_outputs
        ),
        "receipt_free_provenance_postimage_sha256": reconciliation_authority_digest(
            "receipt-free-provenance-postimage", expected_receipt_free
        ),
        "hierarchy_authority_sha256": hierarchy_digest,
        **identity_fields,
        **dict(expected_package),
    }
    rotation_count = current_receipt_free.get("alignment_access_rotation_count")
    rotation_digest = current_receipt_free.get("alignment_access_token_sha256")
    access_scheme = current_receipt_free.get("alignment_access_scheme")
    access_revoked = current_receipt_free.get("alignment_access_revoked")
    rotation_valid = (
        (
            rotation_count is None
            and rotation_digest is None
            and access_scheme is None
            and access_revoked is None
        )
        or (
            type(rotation_count) is int and rotation_count >= 0
            and access_scheme == "opaque_job_capability_v1"
            and (
                (
                    isinstance(rotation_digest, str)
                    and _SHA256_RE.fullmatch(rotation_digest) is not None
                    and access_revoked is None
                )
                or (rotation_digest is None and access_revoked is True)
            )
        )
    )
    preimage_valid = True
    if backup_job is not None:
        preimage_valid = (
            receipt.get("completed_stages_preimage_sha256") == reconciliation_authority_digest(
                "completed-stages-preimage", backup_job.completed_stages
            )
            and receipt.get("stage_outputs_preimage_sha256") == reconciliation_authority_digest(
                "stage-outputs-preimage", backup_job.stage_outputs
            )
            and receipt.get("provenance_preimage_sha256") == reconciliation_authority_digest(
                "provenance-preimage", backup_job.provenance
            )
            and receipt.get("protected_row_preimage_sha256") == reconciliation_authority_digest(
                "protected-row-preimage", _protected_preimage(backup_job)
            )
        )
    if (
        receipt.get("completed_stages") != completed_stages
        or receipt.get("stage_outputs") != canonical_outputs
        or any(receipt.get(key) != value for key, value in expected.items())
        or current_stable != expected_stable
        or not rotation_valid
        or not preimage_valid
        or not isinstance(hierarchy_document, Mapping)
        or not isinstance(hierarchy_digest, str)
        or _canonical_sha256(hierarchy_document) != hierarchy_digest
        or not isinstance(source_snapshot, Mapping)
        or receipt.get("database_identity_sha256") != source_snapshot.get("database_identity_sha256")
        or getattr(job, "status", None) != "completed"
        or getattr(job, "queue_status", None) != "completed"
    ):
        raise OntFastqQcReconciliationError("persisted reconciliation receipt is not bound to current authority")


def _normalized_request(job_id: str) -> dict[str, str]:
    return {
        "schema": "bms.ont-fastq-qc-reconciliation-request.v1",
        "job_id": job_id,
        "operation": "apply",
    }


