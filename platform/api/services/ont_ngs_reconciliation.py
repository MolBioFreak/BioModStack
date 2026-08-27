from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, cast

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import JSON, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job
from services.job_result_roots import resolve_persisted_job_result_root
from services.ont_ngs_completion import (
    is_ont_fastq_qc_job,
    validate_and_prepare_ont_fastq_qc_completion,
)

_RETRY3_JOB_ID = "31f02bd5-830f-4558-aa78-3873c515de68"
_RETRY3_REFERENCE_SHA256 = "0185e3475f9e04c996d2bd2667f83d8655fb12b1e426bc5b674261ac4b2f3be4"
_RETRY3_SOURCE_FASTQ_SHA256 = "957a1c7fb5a4f10089f52b8b26cee37527176575b99ecc5e81a139c1374d8fff"
_RETRY3_SEQUENCE_QC_MANIFEST_SHA256 = "e37f0225c2c7db017b5a3be95bc3a1fb83797918268c3a838a390d1d5378b06b"
_RETRY3_VERIFICATION_MANIFEST_SHA256 = "3d2aa73270c11fe692ed8116aeb86d0f9fd96496da45933fcffb8c8de8a42a38"
_RETRY3_ARTIFACT_SET_SHA256 = "e122e032836df10c0d7e1756fb5ea00d5e65384c6cf942c1f684c155b3a57650"
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
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_RESULT_PREFIX_RE = re.compile(r"^bms_results/[^/]+$")
_RECEIPT_KEY = "ont_fastq_qc_reconciliation_v1"
_HIERARCHY_KEY = "alignment_hierarchy_authority_v1"
_RECEIPT_SCHEMA = "bms.ont-fastq-qc-reconciliation.v1"
_DIGEST_DOMAIN = b"bms.ont-fastq-qc-reconciliation.v1\0"


class OntFastqQcReconciliationError(RuntimeError):
    """Raised when an ONT FASTQ-QC query-mirror repair is unsafe."""

    exit_code = 2


class OntFastqQcReconciliationConflict(OntFastqQcReconciliationError):
    """Raised when a reconciliation compare-and-swap loses authority."""

    exit_code = 3


class OntFastqQcReconciliationBackupError(OntFastqQcReconciliationError):
    """Raised when backup evidence cannot authorize a reconciliation write."""

    exit_code = 4


@dataclass(frozen=True)
class OntFastqQcReconciliationEvidence:
    completed_stages: tuple[str, ...]
    stage_outputs: Mapping[str, tuple[str, ...]]
    workflow_id: str
    input_mode: str
    reference_sequence_sha256: str
    source_fastq_sha256: str
    resource_evidence_status: str
    sequence_qc_manifest_sha256: str
    verification_manifest_sha256: str
    artifact_set_sha256: str
    declared_artifact_count: int
    present_artifact_count: int
    unavailable_artifact_count: int
    result_root_identity_sha256: str


@dataclass(frozen=True)
class OntFastqQcReconciliationPlan:
    job_id: str
    requires_write: bool
    completed_stages: tuple[str, ...]
    stage_outputs: dict[str, tuple[str, ...]]
    provenance: dict[str, Any]
    protected_preimage_sha256: str
    mirror_postimage_sha256: str


@dataclass(frozen=True)
class OntFastqQcReconciliationBackup:
    backup_id: str
    sha256: str
    size_bytes: int
    integrity_check: str
    foreign_key_violations: int
    source_snapshot: Mapping[str, Any]


def _detached_job_copy(job: Any) -> SimpleNamespace:
    return SimpleNamespace(**{
        column.name: copy.deepcopy(getattr(job, column.name, None))
        for column in Job.__table__.columns
    })


async def collect_ont_fastq_qc_reconciliation_evidence(job: Any) -> OntFastqQcReconciliationEvidence:
    detached = _detached_job_copy(job)
    result_root = resolve_persisted_job_result_root(detached)
    root_descriptor = -1
    try:
        root_descriptor = os.open(
            result_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        root_stat = os.fstat(root_descriptor)
        resolved_root = result_root.resolve(strict=True)
        if not stat.S_ISDIR(root_stat.st_mode) or resolved_root.name != result_root.name:
            raise OntFastqQcReconciliationError("reconciliation result root is not a regular directory")
        integrity = await validate_and_prepare_ont_fastq_qc_completion(
            detached,
            historical_reconciliation=True,
            pinned_result_root=Path(f"/proc/self/fd/{root_descriptor}"),
        )
        root_stat_after = os.fstat(root_descriptor)
        path_stat_after = os.stat(result_root, follow_symlinks=False)
        if (
            (root_stat_after.st_dev, root_stat_after.st_ino) != (root_stat.st_dev, root_stat.st_ino)
            or (path_stat_after.st_dev, path_stat_after.st_ino) != (root_stat.st_dev, root_stat.st_ino)
        ):
            raise OntFastqQcReconciliationError("reconciliation result root identity changed during evidence collection")
    except OSError as exc:
        raise OntFastqQcReconciliationError("reconciliation result root is unavailable or unsafe") from exc
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
    required_integrity = {
        "workflow_id",
        "input_mode",
        "reference_sequence_sha256",
        "source_fastq_sha256",
        "resource_evidence_status",
        "sequence_qc_manifest_sha256",
        "construct_verification_manifest_sha256",
        "artifact_set_sha256",
        "declared_artifact_count",
        "present_artifact_count",
        "unavailable_artifact_count",
    }
    if not isinstance(integrity, dict) or not required_integrity.issubset(integrity):
        raise OntFastqQcReconciliationError("ONT completion integrity record is incomplete")
    completed_stages = tuple(detached.completed_stages or ())
    stage_outputs_raw = detached.stage_outputs if isinstance(detached.stage_outputs, dict) else {}
    stage_outputs = {
        str(key): tuple(value) if isinstance(value, list) else ()
        for key, value in stage_outputs_raw.items()
    }
    root_identity_sha256 = reconciliation_authority_digest(
        "result-root-identity",
        {
            "schema": "bms.result-root-identity.v1",
            "job_id": str(detached.id),
            "basename": resolved_root.name,
            "resolved_path_sha256": hashlib.sha256(str(resolved_root).encode("utf-8")).hexdigest(),
            "device": int(root_stat.st_dev),
            "inode": int(root_stat.st_ino),
        },
    )
    evidence = OntFastqQcReconciliationEvidence(
        completed_stages=completed_stages,
        stage_outputs=stage_outputs,
        workflow_id=str(integrity["workflow_id"]),
        input_mode=str(integrity["input_mode"]),
        reference_sequence_sha256=str(integrity["reference_sequence_sha256"]),
        source_fastq_sha256=str(integrity["source_fastq_sha256"]),
        resource_evidence_status=str(integrity["resource_evidence_status"]),
        sequence_qc_manifest_sha256=str(integrity["sequence_qc_manifest_sha256"]),
        verification_manifest_sha256=str(integrity["construct_verification_manifest_sha256"]),
        artifact_set_sha256=str(integrity["artifact_set_sha256"]),
        declared_artifact_count=integrity["declared_artifact_count"],
        present_artifact_count=integrity["present_artifact_count"],
        unavailable_artifact_count=integrity["unavailable_artifact_count"],
        result_root_identity_sha256=root_identity_sha256,
    )
    _validate_evidence(evidence)
    return evidence


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


def _validate_evidence(evidence: OntFastqQcReconciliationEvidence) -> None:
    if evidence.completed_stages != _REQUIRED_STAGES:
        raise OntFastqQcReconciliationError("reconciliation stage plan is not canonical")
    if set(evidence.stage_outputs) != set(_REQUIRED_STAGES):
        raise OntFastqQcReconciliationError("reconciliation stage outputs are incomplete")
    _stage_output_prefix(evidence.stage_outputs)
    if (
        evidence.workflow_id != "ont_fastq_qc"
        or evidence.input_mode != "fastq"
        or evidence.resource_evidence_status != "historical_unavailable"
        or evidence.reference_sequence_sha256 != _RETRY3_REFERENCE_SHA256
        or evidence.source_fastq_sha256 != _RETRY3_SOURCE_FASTQ_SHA256
        or evidence.sequence_qc_manifest_sha256 != _RETRY3_SEQUENCE_QC_MANIFEST_SHA256
        or evidence.verification_manifest_sha256 != _RETRY3_VERIFICATION_MANIFEST_SHA256
        or evidence.artifact_set_sha256 != _RETRY3_ARTIFACT_SET_SHA256
        or evidence.declared_artifact_count != 36
        or evidence.present_artifact_count != 34
        or evidence.unavailable_artifact_count != 2
        or not _SHA256_RE.fullmatch(evidence.result_root_identity_sha256)
    ):
        raise OntFastqQcReconciliationError("reconciliation evidence differs from frozen retry3 authority")


def _validate_hierarchy_record(
    hierarchy_record: Mapping[str, Any],
    *,
    job_id: str,
    evidence: OntFastqQcReconciliationEvidence,
) -> tuple[dict[str, Any], dict[str, str]]:
    record = copy.deepcopy(dict(hierarchy_record)) if isinstance(hierarchy_record, Mapping) else {}
    if set(record) != {"schema", "digest", "document"}:
        raise OntFastqQcReconciliationError("hierarchy authority record is malformed")
    document = record.get("document")
    digest = record.get("digest")
    if (
        record.get("schema") != "biomodstack.alignment-hierarchy-authority.v1"
        or not isinstance(document, dict)
        or not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
        or _canonical_sha256(document) != digest
    ):
        raise OntFastqQcReconciliationError("hierarchy authority record digest is invalid")
    job_authority = document.get("job")
    project = document.get("project")
    global_experiment = document.get("global_experiment")
    domain = document.get("domain_experiment")
    member = document.get("member")
    sample = document.get("sample")
    reference = document.get("reference")
    source = document.get("source_fastq")
    mappings = (job_authority, project, global_experiment, domain, member, sample, reference, source)
    if not all(isinstance(value, Mapping) for value in mappings):
        raise OntFastqQcReconciliationError("hierarchy authority document is incomplete")
    job_authority = cast(Mapping[str, Any], job_authority)
    project = cast(Mapping[str, Any], project)
    global_experiment = cast(Mapping[str, Any], global_experiment)
    domain = cast(Mapping[str, Any], domain)
    member = cast(Mapping[str, Any], member)
    sample = cast(Mapping[str, Any], sample)
    reference = cast(Mapping[str, Any], reference)
    source = cast(Mapping[str, Any], source)
    identities = {
        "project_id": project.get("id"),
        "global_experiment_id": global_experiment.get("id"),
        "domain_experiment_id": domain.get("id"),
        "state_revision_id": domain.get("state_revision_id"),
        "member_receipt_id": member.get("receipt_id"),
        "sample_revision_id": sample.get("revision_id"),
        "reference_revision_id": reference.get("revision_id"),
    }
    if (
        document.get("schema") != "biomodstack.ont-fastq-qc-hierarchy-authority.v1"
        or job_authority.get("id") != job_id
        or job_authority.get("workflow_id") != "ont_fastq_qc"
        or job_authority.get("input_mode") != "fastq"
        or any(not isinstance(value, str) or not value for value in identities.values())
        or reference.get("normalized_sequence_sha256") != evidence.reference_sequence_sha256
        or source.get("sha256") != evidence.source_fastq_sha256
        or source.get("artifact_set_sha256") != evidence.artifact_set_sha256
        or source.get("sequence_qc_manifest_sha256") != evidence.sequence_qc_manifest_sha256
        or source.get("verification_manifest_sha256") != evidence.verification_manifest_sha256
    ):
        raise OntFastqQcReconciliationError("hierarchy authority is cross-bound")
    return record, identities  # type: ignore[return-value]


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


def validate_persisted_reconciliation_receipt(receipt: Mapping[str, Any]) -> None:
    """Validate the complete persisted reconciliation authority before reuse."""
    _validate_bound_receipt(receipt)


def _normalized_request(job_id: str) -> dict[str, str]:
    return {
        "schema": "bms.ont-fastq-qc-reconciliation-request.v1",
        "job_id": job_id,
        "operation": "apply",
    }


def _mirror_postimage_digest(
    completed_stages: tuple[str, ...],
    stage_outputs: Mapping[str, tuple[str, ...]],
    receipt_free_provenance_sha256: str,
) -> str:
    return reconciliation_authority_digest("mirror-postimage", {
        "completed_stages": list(completed_stages),
        "stage_outputs": {key: list(stage_outputs[key]) for key in _REQUIRED_STAGES},
        "receipt_free_provenance_postimage_sha256": receipt_free_provenance_sha256,
    })


def _receipt_matches_current_authority(
    receipt: Mapping[str, Any],
    *,
    job: Any,
    evidence: OntFastqQcReconciliationEvidence,
    hierarchy_record: Mapping[str, Any],
    identities: Mapping[str, str],
    database_identity_sha256: str,
    source_revision: str,
    source_tree: str,
    authorization_class: str,
    receipt_free_provenance: Mapping[str, Any],
) -> bool:
    try:
        _validate_bound_receipt(receipt)
    except OntFastqQcReconciliationError:
        return False
    expected_outputs = {key: tuple(evidence.stage_outputs[key]) for key in _REQUIRED_STAGES}
    scalar_expected = {
        "schema": _RECEIPT_SCHEMA,
        "job_id": str(job.id),
        "authorization_class": authorization_class,
        **identities,
        "hierarchy_authority_sha256": hierarchy_record["digest"],
        "lane": "development",
        "database_identity_sha256": database_identity_sha256,
        "source_commit": source_revision,
        "source_tree": source_tree,
        "normalized_request_sha256": reconciliation_authority_digest(
            "normalized-request", _normalized_request(str(job.id))
        ),
        "completed_stages_postimage_sha256": reconciliation_authority_digest(
            "completed-stages-postimage", list(evidence.completed_stages)
        ),
        "stage_outputs_postimage_sha256": reconciliation_authority_digest(
            "stage-outputs-postimage",
            {key: list(expected_outputs[key]) for key in _REQUIRED_STAGES},
        ),
        "receipt_free_provenance_postimage_sha256": reconciliation_authority_digest(
            "receipt-free-provenance-postimage", receipt_free_provenance
        ),
        "sequence_qc_manifest_sha256": evidence.sequence_qc_manifest_sha256,
        "verification_manifest_sha256": evidence.verification_manifest_sha256,
        "artifact_set_sha256": evidence.artifact_set_sha256,
        "declared_artifact_count": evidence.declared_artifact_count,
        "present_artifact_count": evidence.present_artifact_count,
        "unavailable_artifact_count": evidence.unavailable_artifact_count,
        "result_root_identity_sha256": evidence.result_root_identity_sha256,
        "workflow_id": evidence.workflow_id,
        "input_mode": evidence.input_mode,
        "reference_sequence_sha256": evidence.reference_sequence_sha256,
        "source_fastq_sha256": evidence.source_fastq_sha256,
        "resource_evidence_status": evidence.resource_evidence_status,
        "scientific_artifacts_modified": False,
        "compute_invoked": False,
    }
    return (
        all(receipt.get(key) == value for key, value in scalar_expected.items())
        and receipt.get("completed_stages") == list(evidence.completed_stages)
        and receipt.get("stage_outputs") == {
            key: list(expected_outputs[key]) for key in _REQUIRED_STAGES
        }
    )


def build_ont_fastq_qc_reconciliation_plan(
    job: Any,
    evidence: OntFastqQcReconciliationEvidence,
    *,
    hierarchy_record: Mapping[str, Any],
    database_identity_sha256: str,
    source_revision: str,
    source_tree: str,
    applied_at: datetime,
    principal: str,
    authorization_class: str,
) -> OntFastqQcReconciliationPlan:
    try:
        canonical_job = is_ont_fastq_qc_job(job)
    except Exception as exc:
        raise OntFastqQcReconciliationError("Job workflow authority is invalid") from exc
    if (
        str(getattr(job, "id", "")) != _RETRY3_JOB_ID
        or getattr(job, "status", None) != "completed"
        or getattr(job, "queue_status", None) != "completed"
        or getattr(job, "awaiting_input", None) is True
        or getattr(job, "awaiting_stage", None) is not None
        or bool(getattr(job, "awaiting_payload", None))
        or not canonical_job
    ):
        raise OntFastqQcReconciliationError("Job is not the frozen completed retry3 ONT FASTQ-QC result")
    if not _REVISION_RE.fullmatch(source_revision) or not _REVISION_RE.fullmatch(source_tree):
        raise OntFastqQcReconciliationError("source commit or tree is invalid")
    if not _SHA256_RE.fullmatch(database_identity_sha256):
        raise OntFastqQcReconciliationError("database identity is invalid")
    if not isinstance(principal, str) or not principal or len(principal) > 128:
        raise OntFastqQcReconciliationError("principal is invalid")
    if authorization_class not in {"development_service_owner", "authenticated_ngs_operator"}:
        raise OntFastqQcReconciliationError("authorization class is invalid")
    _validate_evidence(evidence)
    validated_hierarchy, identities = _validate_hierarchy_record(
        hierarchy_record,
        job_id=str(job.id),
        evidence=evidence,
    )

    original_provenance = copy.deepcopy(getattr(job, "provenance", None))
    if not isinstance(original_provenance, dict):
        raise OntFastqQcReconciliationError("Job provenance is unavailable")
    existing_receipt = original_provenance.get(_RECEIPT_KEY)
    receipt_free_provenance = {
        key: copy.deepcopy(value)
        for key, value in original_provenance.items()
        if key != _RECEIPT_KEY
    }
    existing_hierarchy = receipt_free_provenance.get(_HIERARCHY_KEY)
    if existing_hierarchy is None:
        receipt_free_provenance[_HIERARCHY_KEY] = validated_hierarchy
    elif existing_hierarchy != validated_hierarchy:
        raise OntFastqQcReconciliationError("existing hierarchy authority record is inconsistent")

    expected_outputs = {key: tuple(evidence.stage_outputs[key]) for key in _REQUIRED_STAGES}
    receipt_free_postimage_sha256 = reconciliation_authority_digest(
        "receipt-free-provenance-postimage", receipt_free_provenance
    )
    mirror_postimage_sha256 = _mirror_postimage_digest(
        evidence.completed_stages,
        expected_outputs,
        receipt_free_postimage_sha256,
    )
    if existing_receipt is not None:
        current_outputs = getattr(job, "stage_outputs", None)
        normalized_current_outputs = {
            key: tuple(value) for key, value in current_outputs.items()
        } if isinstance(current_outputs, dict) else {}
        if (
            not isinstance(existing_receipt, dict)
            or not _receipt_matches_current_authority(
                existing_receipt,
                job=job,
                evidence=evidence,
                hierarchy_record=validated_hierarchy,
                identities=identities,
                database_identity_sha256=database_identity_sha256,
                source_revision=source_revision,
                source_tree=source_tree,
                authorization_class=authorization_class,
                receipt_free_provenance=receipt_free_provenance,
            )
            or tuple(getattr(job, "completed_stages", ()) or ()) != evidence.completed_stages
            or normalized_current_outputs != expected_outputs
        ):
            raise OntFastqQcReconciliationError("existing reconciliation receipt is inconsistent")
        return OntFastqQcReconciliationPlan(
            job_id=str(job.id),
            requires_write=False,
            completed_stages=evidence.completed_stages,
            stage_outputs=expected_outputs,
            provenance=original_provenance,
            protected_preimage_sha256=existing_receipt["protected_row_preimage_sha256"],
            mirror_postimage_sha256=mirror_postimage_sha256,
        )

    protected_preimage_sha256 = reconciliation_authority_digest(
        "protected-row-preimage", _protected_preimage(job)
    )
    completed_stages_preimage_sha256 = reconciliation_authority_digest(
        "completed-stages-preimage", getattr(job, "completed_stages", None)
    )
    stage_outputs_preimage_sha256 = reconciliation_authority_digest(
        "stage-outputs-preimage", getattr(job, "stage_outputs", None)
    )
    provenance_preimage_sha256 = reconciliation_authority_digest(
        "provenance-preimage", original_provenance
    )
    completed_stages_postimage = list(evidence.completed_stages)
    stage_outputs_postimage = {
        key: list(expected_outputs[key]) for key in _REQUIRED_STAGES
    }
    applied = applied_at if applied_at.tzinfo is not None else applied_at.replace(tzinfo=timezone.utc)
    receipt = {
        "schema": _RECEIPT_SCHEMA,
        "job_id": str(job.id),
        "applied_at": applied.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "principal": principal,
        "authorization_class": authorization_class,
        **identities,
        "hierarchy_authority_sha256": validated_hierarchy["digest"],
        "lane": "development",
        "database_identity_sha256": database_identity_sha256,
        "backup": None,
        "backup_source_preimage_sha256": None,
        "source_commit": source_revision,
        "source_tree": source_tree,
        "normalized_request_sha256": reconciliation_authority_digest(
            "normalized-request", _normalized_request(str(job.id))
        ),
        "completed_stages_preimage_sha256": completed_stages_preimage_sha256,
        "stage_outputs_preimage_sha256": stage_outputs_preimage_sha256,
        "provenance_preimage_sha256": provenance_preimage_sha256,
        "completed_stages_postimage_sha256": reconciliation_authority_digest(
            "completed-stages-postimage", completed_stages_postimage
        ),
        "stage_outputs_postimage_sha256": reconciliation_authority_digest(
            "stage-outputs-postimage", stage_outputs_postimage
        ),
        "receipt_free_provenance_postimage_sha256": receipt_free_postimage_sha256,
        "protected_row_preimage_sha256": protected_preimage_sha256,
        "sequence_qc_manifest_sha256": evidence.sequence_qc_manifest_sha256,
        "verification_manifest_sha256": evidence.verification_manifest_sha256,
        "artifact_set_sha256": evidence.artifact_set_sha256,
        "declared_artifact_count": evidence.declared_artifact_count,
        "present_artifact_count": evidence.present_artifact_count,
        "unavailable_artifact_count": evidence.unavailable_artifact_count,
        "result_root_identity_sha256": evidence.result_root_identity_sha256,
        "workflow_id": evidence.workflow_id,
        "input_mode": evidence.input_mode,
        "reference_sequence_sha256": evidence.reference_sequence_sha256,
        "source_fastq_sha256": evidence.source_fastq_sha256,
        "resource_evidence_status": evidence.resource_evidence_status,
        "completed_stages": completed_stages_postimage,
        "stage_outputs": stage_outputs_postimage,
        "scientific_artifacts_modified": False,
        "compute_invoked": False,
        "receipt_sha256": None,
    }
    provenance = copy.deepcopy(receipt_free_provenance)
    provenance[_RECEIPT_KEY] = receipt
    return OntFastqQcReconciliationPlan(
        job_id=str(job.id),
        requires_write=True,
        completed_stages=evidence.completed_stages,
        stage_outputs=expected_outputs,
        provenance=provenance,
        protected_preimage_sha256=protected_preimage_sha256,
        mirror_postimage_sha256=mirror_postimage_sha256,
    )


def bind_ont_fastq_qc_reconciliation_backup(
    plan: OntFastqQcReconciliationPlan,
    backup: OntFastqQcReconciliationBackup,
) -> OntFastqQcReconciliationPlan:
    if not plan.requires_write:
        return plan
    backup_value = {
        "backup_id": backup.backup_id,
        "sha256": backup.sha256,
        "size_bytes": backup.size_bytes,
        "integrity_check": backup.integrity_check,
        "foreign_key_violations": backup.foreign_key_violations,
    }
    source_snapshot = copy.deepcopy(dict(backup.source_snapshot))
    if not _valid_backup(backup_value) or not _valid_source_snapshot(source_snapshot):
        raise OntFastqQcReconciliationBackupError("reconciliation backup evidence is invalid")
    provenance = copy.deepcopy(plan.provenance)
    receipt = provenance.get(_RECEIPT_KEY)
    if (
        not isinstance(receipt, dict)
        or receipt.get("backup") is not None
        or receipt.get("backup_source_preimage_sha256") is not None
        or receipt.get("receipt_sha256") is not None
    ):
        raise OntFastqQcReconciliationBackupError("reconciliation plan cannot accept backup evidence")
    receipt["backup"] = backup_value
    receipt["source_snapshot"] = source_snapshot
    receipt["backup_source_preimage_sha256"] = reconciliation_authority_digest(
        "backup-source-preimage", source_snapshot
    )
    receipt["receipt_sha256"] = reconciliation_authority_digest(
        "receipt",
        {key: value for key, value in receipt.items() if key != "receipt_sha256"},
    )
    _validate_bound_receipt(receipt)
    provenance[_RECEIPT_KEY] = receipt
    return replace(plan, provenance=provenance)


def _equal(column: Any, value: Any) -> Any:
    # SQLAlchemy stores Python None as JSON text `null` for JSON columns.
    # `IS NULL` cannot match that representation during the guarded CAS.
    if isinstance(column.type, JSON):
        if value is None:
            return or_(column.is_(None), column == JSON.NULL)
        return column == value
    return column.is_(None) if value is None else column == value


async def apply_ont_fastq_qc_reconciliation_plan(
    session: AsyncSession,
    job: Job,
    plan: OntFastqQcReconciliationPlan,
    *,
    current_source_snapshot: Mapping[str, Any],
    current_database_identity_sha256: str,
) -> bool:
    if plan.job_id != job.id:
        raise OntFastqQcReconciliationError("reconciliation plan Job identity mismatch")
    if not plan.requires_write:
        return False
    receipt = plan.provenance.get(_RECEIPT_KEY)
    if not isinstance(receipt, dict):
        raise OntFastqQcReconciliationBackupError("reconciliation plan has no receipt")
    _validate_bound_receipt(receipt)
    if (
        receipt.get("database_identity_sha256") != current_database_identity_sha256
        or receipt.get("source_snapshot") != dict(current_source_snapshot)
        or receipt.get("backup_source_preimage_sha256")
        != reconciliation_authority_digest("backup-source-preimage", current_source_snapshot)
    ):
        raise OntFastqQcReconciliationConflict(
            "reconciliation database or backup-bound source preimage changed before CAS"
        )
    if (
        reconciliation_authority_digest("protected-row-preimage", _protected_preimage(job))
        != plan.protected_preimage_sha256
        or receipt.get("protected_row_preimage_sha256") != plan.protected_preimage_sha256
        or receipt.get("completed_stages_preimage_sha256")
        != reconciliation_authority_digest("completed-stages-preimage", job.completed_stages)
        or receipt.get("stage_outputs_preimage_sha256")
        != reconciliation_authority_digest("stage-outputs-preimage", job.stage_outputs)
        or receipt.get("provenance_preimage_sha256")
        != reconciliation_authority_digest("provenance-preimage", job.provenance)
    ):
        raise OntFastqQcReconciliationConflict("reconciliation plan preimage changed before CAS")
    receipt_free_provenance = {
        key: copy.deepcopy(value)
        for key, value in plan.provenance.items()
        if key != _RECEIPT_KEY
    }
    if (
        receipt.get("completed_stages_postimage_sha256")
        != reconciliation_authority_digest("completed-stages-postimage", list(plan.completed_stages))
        or receipt.get("stage_outputs_postimage_sha256")
        != reconciliation_authority_digest(
            "stage-outputs-postimage",
            {key: list(plan.stage_outputs[key]) for key in _REQUIRED_STAGES},
        )
        or receipt.get("receipt_free_provenance_postimage_sha256")
        != reconciliation_authority_digest(
            "receipt-free-provenance-postimage", receipt_free_provenance
        )
    ):
        raise OntFastqQcReconciliationError("reconciliation postimage authority is invalid")

    predicates = [Job.id == job.id]
    predicates.extend(
        _equal(getattr(Job, column.name), copy.deepcopy(getattr(job, column.name)))
        for column in Job.__table__.columns
        if column.name != "id"
    )
    result = await session.execute(
        update(Job)
        .where(*predicates)
        .values(
            completed_stages=list(plan.completed_stages),
            stage_outputs={key: list(value) for key, value in plan.stage_outputs.items()},
            provenance=copy.deepcopy(plan.provenance),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise OntFastqQcReconciliationConflict("reconciliation CAS lost; no state was published")
    await session.refresh(job)
    persisted_outputs = {
        key: tuple(value) for key, value in (job.stage_outputs or {}).items()
    } if isinstance(job.stage_outputs, dict) else {}
    if (
        tuple(job.completed_stages or ()) != plan.completed_stages
        or persisted_outputs != plan.stage_outputs
        or job.provenance != plan.provenance
    ):
        raise OntFastqQcReconciliationError("reconciliation post-write verification failed")
    return True
