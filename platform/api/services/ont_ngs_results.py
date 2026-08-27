from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from jsonschema import Draft202012Validator, FormatChecker

from database import Job
from services import ngs_alignment_sessions
from services.job_result_roots import resolve_persisted_job_result_root
from services.ont_ngs_completion import (
    OntNgsCompletionError,
    canonical_ngs_package_authority,
    is_ont_fastq_qc_job,
)
from services.ont_ngs_decision_projection import (
    OntNgsDecisionProjectionError,
    project_verification_manifest,
)
from services.ont_ngs_reconciliation import (
    OntFastqQcReconciliationError,
    validate_persisted_reconciliation_receipt,
)
from services.sequence_qc_manifest import VERIFICATION_SCHEMA, load_sequence_qc_manifest
from services.resource_usage_evidence import (
    GLOBAL_RESOURCE_ADMISSION_PARAM,
    ResourceUsageEvidenceError,
    validate_producer_resource_usage_receipt,
)

_MAX_TABLE_BYTES = 10 * 1024 * 1024
_MAX_COVERAGE_ROWS = 100_000
_MAX_COVERAGE_POINTS = 2_048
_MAX_READ_LENGTH_ROWS = 1_000_000
_READ_LENGTH_BIN_COUNT = 50
_REQUIRED_STAGES = ("fastq_align", "dimer_qc", "fastq_qc", "construct_verification")
_SUMMARY_METRIC_KEYS = frozenset({
    "reference_name", "reference_length", "reads_considered", "mapped_reads",
    "mapping_rate_pct", "fastq_minimap2_preset", "fastq_minimap2_allow_secondary",
    "mean_read_length_bp", "n50_read_length_bp", "estimated_copy_number_mean",
    "dimer_like_reads", "trimer_plus_reads", "mean_coverage_depth",
    "covered_fraction_pct", "consensus_status", "consensus_length", "igv_report_status",
})
_ALIGNMENT_METRIC_KEYS = frozenset({
    "reference_name", "reference_length", "expected_plasmid_size", "min_fastq_read_length",
    "fastq_minimap2_preset", "fastq_minimap2_allow_secondary", "total_reads",
    "reads_passing_length_filter", "total_bases", "mean_read_length_bp",
    "median_read_length_bp", "n50_read_length_bp", "estimated_copy_number_mean",
    "dimer_like_reads", "trimer_plus_reads", "mapped_reads", "unmapped_reads",
    "logical_read_records", "mapped_alignment_records", "unmapped_alignment_records",
    "total_alignment_records", "mapping_rate_pct", "primary_mapped_reads",
    "primary_mapping_rate_pct", "secondary_alignments", "supplementary_alignments",
    "coverage_positions", "covered_positions", "covered_fraction_pct",
    "mean_coverage_depth", "median_coverage_depth", "consensus_status",
    "consensus_name", "consensus_length", "igv_track_window_bp", "igv_report_max_sites",
    "igv_report_flanking_bp", "igv_report_cli_available", "igv_report_status",
})


class OntNgsResultError(RuntimeError):
    """Raised when a persisted ONT result cannot produce a truthful bounded projection."""


def _validated_resource_receipts(job: Job) -> list[dict[str, Any]]:
    params = job.params if isinstance(job.params, dict) else {}
    expected_handoff = params.get(GLOBAL_RESOURCE_ADMISSION_PARAM)
    if not isinstance(expected_handoff, Mapping):
        raise OntNgsResultError("producer resource admission handoff is unavailable")
    try:
        return [
            validate_producer_resource_usage_receipt(
                job,
                expected_handoff,
            )
        ]
    except ResourceUsageEvidenceError as exc:
        raise OntNgsResultError("producer resource receipt history is invalid") from exc


def _rfc3339_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _require_persisted_package_authority(
    job: Job,
    observed: dict[str, Any],
    *,
    sequence_qc_manifest_sha256: str,
    construct_verification_manifest_sha256: str,
    reference_sequence_sha256: str,
    source_fastq_sha256: str,
) -> dict[str, Any]:
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    result_integrity = provenance.get("result_integrity")
    if isinstance(result_integrity, dict) and result_integrity.get("result_kind") == "ngs_sequence_qc":
        authority = result_integrity
        expected = {
            "state": "validated",
            "partial": False,
            "result_kind": "ngs_sequence_qc",
            "workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "reference_sequence_sha256": reference_sequence_sha256,
            "source_fastq_sha256": source_fastq_sha256,
            "resource_evidence_status": "accepted",
            "sequence_qc_manifest_sha256": sequence_qc_manifest_sha256,
            "construct_verification_manifest_sha256": construct_verification_manifest_sha256,
            **observed,
        }
        receipt_digest = authority.get("resource_usage_receipt_sha256")
        receipts = _validated_resource_receipts(job)
        if (
            not isinstance(receipt_digest, str)
            or len(receipt_digest) != 64
            or not any(
                receipt.get("complete") is True
                and receipt.get("receipt_sha256") == receipt_digest
                for receipt in receipts
            )
        ):
            raise OntNgsResultError("persisted terminal resource receipt does not match result authority")
    else:
        reconciliation = provenance.get("ont_fastq_qc_reconciliation_v1")
        authority = reconciliation if isinstance(reconciliation, dict) else {}
        try:
            validate_persisted_reconciliation_receipt(authority)
        except OntFastqQcReconciliationError as exc:
            raise OntNgsResultError("persisted reconciliation authority is invalid") from exc
        expected = {
            "schema": "bms.ont-fastq-qc-reconciliation.v1",
            "job_id": str(job.id),
            "workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "reference_sequence_sha256": reference_sequence_sha256,
            "source_fastq_sha256": source_fastq_sha256,
            "resource_evidence_status": "historical_unavailable",
            "sequence_qc_manifest_sha256": sequence_qc_manifest_sha256,
            "verification_manifest_sha256": construct_verification_manifest_sha256,
            **observed,
        }
    if any(authority.get(key) != value for key, value in expected.items()):
        raise OntNgsResultError("persisted terminal package authority does not match reopened artifacts")
    return authority


def _execution_resources(job: Job, authority: Mapping[str, Any]) -> dict[str, Any]:
    params = job.params if isinstance(job.params, dict) else {}
    status = authority.get("resource_evidence_status")
    common = {
        "accelerator_applicability": "not_applicable",
        "dorado_invoked": False,
    }
    if status == "historical_unavailable":
        return {
            "evidence_status": "historical_unavailable",
            "receipt_schema": None,
            "receipt_id": None,
            "receipt_sha256": None,
            "run_attempt_id": None,
            "execution_invocation_id": None,
            "outcome": None,
            "admitted_cpu_threads": None,
            "observed_memory_peak_bytes": None,
            "observed_pids_peak": None,
            "gpu_index": None,
            "gpu_uuid": None,
            "admitted_vram_bytes": None,
            **common,
            "reason": (
                "No accepted producer resource-use receipt exists for this historical execution; "
                "scheduler and configuration fields are not execution evidence"
            ),
            "scheduler_gpu_assignment": job.assigned_gpu,
            "configured_dorado_device_ignored": params.get("dorado_device"),
        }
    if status != "accepted":
        raise OntNgsResultError("resource evidence status is invalid")
    digest = authority.get("resource_usage_receipt_sha256")
    receipts = _validated_resource_receipts(job)
    matches = [
        receipt
        for receipt in receipts
        if isinstance(receipt, dict) and receipt.get("receipt_sha256") == digest
    ]
    if len(matches) != 1:
        raise OntNgsResultError("accepted producer resource receipt is not unique")
    receipt = matches[0]
    execution = receipt.get("execution")
    admission = receipt.get("admission")
    observed = receipt.get("observed")
    accounting = observed.get("accounting") if isinstance(observed, dict) else None
    invocation_id = execution.get("invocation_id") if isinstance(execution, dict) else None
    required_text = (
        receipt.get("schema"),
        receipt.get("admission_id"),
        receipt.get("run_attempt_id"),
        invocation_id,
    )
    cpu_threads = admission.get("cpu_threads") if isinstance(admission, dict) else None
    gpu_index = admission.get("gpu_index") if isinstance(admission, dict) else None
    gpu_uuid = admission.get("gpu_uuid") if isinstance(admission, dict) else None
    memory_peak = accounting.get("memory_peak_bytes") if isinstance(accounting, dict) else None
    pids_peak = accounting.get("pids_peak") if isinstance(accounting, dict) else None
    if (
        receipt.get("schema") != "bms.workflow-resource-usage.v1"
        or receipt.get("complete") is not True
        or receipt.get("outcome") != "completed"
        or not all(isinstance(value, str) and value for value in required_text)
        or not isinstance(digest, str)
        or len(digest) != 64
        or type(cpu_threads) is not int
        or cpu_threads < 1
        or type(memory_peak) is not int
        or memory_peak < 0
        or type(pids_peak) is not int
        or pids_peak < 1
        or gpu_index is not None
        or gpu_uuid is not None
        or job.assigned_gpu is not None
        or params.get("dorado_device") is not None
    ):
        raise OntNgsResultError("accepted producer resource receipt is not CPU-only and complete")
    return {
        "evidence_status": "accepted",
        "receipt_schema": receipt["schema"],
        "receipt_id": receipt["admission_id"],
        "receipt_sha256": digest,
        "run_attempt_id": receipt["run_attempt_id"],
        "execution_invocation_id": invocation_id,
        "outcome": receipt["outcome"],
        "admitted_cpu_threads": cpu_threads,
        "observed_memory_peak_bytes": memory_peak,
        "observed_pids_peak": pids_peak,
        "gpu_index": None,
        "gpu_uuid": None,
        "admitted_vram_bytes": 0,
        **common,
        "reason": "Accepted CPU-only producer resource-use receipt",
        "scheduler_gpu_assignment": None,
        "configured_dorado_device_ignored": None,
    }


@lru_cache(maxsize=1)
def _result_contract_validator() -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[3] / "schemas/ngs/ont_fastq_qc_result_v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as exc:
        raise OntNgsResultError("ONT FASTQ-QC result schema is unavailable") from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _variant_interval_is_valid(variant: dict[str, Any], reference_length: int) -> bool:
    kind = variant["kind"]
    record_start = variant["record_start_1based"]
    record_end = variant["record_end_1based"]
    affected_start = variant["affected_start_1based"]
    affected_end = variant["affected_end_1based"]
    ref = variant["ref"]
    alt = variant["alt"]
    if (
        variant["normalization"] != "vcf_left_anchored_v1"
        or record_end != record_start + len(ref) - 1
        or record_end > reference_length
        or affected_start > reference_length
        or affected_end > reference_length
    ):
        return False
    if kind == "SNV":
        expected = ("reference_bases", record_start, record_end)
        return len(ref) == len(alt) == 1 and ref != alt and (
            variant["affected_interval_kind"], affected_start, affected_end
        ) == expected
    if kind == "MNV":
        expected = ("reference_bases", record_start, record_end)
        return len(ref) == len(alt) > 1 and ref != alt and (
            variant["affected_interval_kind"], affected_start, affected_end
        ) == expected
    if kind == "DEL":
        expected = ("reference_bases", record_start + len(alt), record_end)
        return len(ref) > len(alt) and ref.startswith(alt) and (
            variant["affected_interval_kind"], affected_start, affected_end
        ) == expected
    if kind == "INS":
        expected = ("between_bases", record_end, record_end)
        return len(alt) > len(ref) and alt.startswith(ref) and (
            variant["affected_interval_kind"], affected_start, affected_end
        ) == expected
    if kind == "COMPLEX":
        expected = ("reference_bases", record_start, record_end)
        return ref != alt and (
            variant["affected_interval_kind"], affected_start, affected_end
        ) == expected
    return False


def validate_ont_fastq_qc_result_contract(value: dict[str, Any]) -> None:
    errors = sorted(
        _result_contract_validator().iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        path = "/".join(str(part) for part in errors[0].absolute_path) or "root"
        raise OntNgsResultError(f"result schema is invalid at {path}: {errors[0].message}")

    authority = value["authority"]
    artifacts = value["artifacts"]
    present_count = sum(item["state"] == "present" for item in artifacts)
    unavailable_count = len(artifacts) - present_count
    if (
        authority["declared_artifact_count"] != len(artifacts)
        or authority["present_artifact_count"] != present_count
        or authority["unavailable_artifact_count"] != unavailable_count
    ):
        raise OntNgsResultError("artifact counts are inconsistent")

    job_id = quote(value["job"]["id"], safe="")
    expected_artifact_prefix = f"/api/jobs/{job_id}/ngs-artifacts/"
    for artifact in artifacts:
        if artifact["state"] != "present":
            continue
        artifact_id = artifact.get("artifact_id")
        if (
            not isinstance(artifact_id, str)
            or artifact_id == artifact["sha256"]
            or artifact["url"] != expected_artifact_prefix + artifact_id
        ):
            raise OntNgsResultError("artifact URL is not bound to the exact Job and opaque artifact_id")

    histogram = value["read_length_histogram"]
    width = histogram["bin_width_bp"]
    bins = histogram["bins"]
    for index, histogram_bin in enumerate(bins):
        if (
            histogram_bin["start_bp"] != index * width
            or histogram_bin["end_bp_exclusive"] != (index + 1) * width
        ):
            raise OntNgsResultError("histogram bins are not canonical fixed_width_v1 bins")
    if sum(histogram_bin["read_count"] for histogram_bin in bins) != histogram["source_row_count"]:
        raise OntNgsResultError("histogram count does not equal its source row count")

    coverage = value["coverage"]
    coverage_points = coverage["points"]
    if any(
        current["reference"] != previous["reference"]
        or current["position_1based"] <= previous["position_1based"]
        for previous, current in zip(coverage_points, coverage_points[1:])
    ):
        raise OntNgsResultError("coverage order is invalid")
    expected_bucket_width = max(
        1,
        math.ceil(coverage["source_row_count"] / (coverage["maximum_point_count"] // 2)),
    )
    if coverage["bucket_width_rows"] != expected_bucket_width:
        raise OntNgsResultError("coverage bucket width is invalid")
    minimum_point = min(
        coverage_points,
        key=lambda item: (item["depth"], item["position_1based"]),
    )
    if (
        coverage["minimum_depth"] != minimum_point["depth"]
        or coverage["minimum_depth_position_1based"] != minimum_point["position_1based"]
    ):
        raise OntNgsResultError("coverage minimum is inconsistent with the envelope")

    summary = value["summary"]
    alignment = value["alignment"]
    verification_summary = value["verification"]["summary"]
    reference_name = summary["reference_name"]
    reference_length = summary["reference_length"]
    if (
        alignment["reference_name"] != reference_name
        or alignment["reference_length"] != reference_length
        or verification_summary["reference_name"] != reference_name
        or verification_summary["reference_length"] != reference_length
        or any(point["reference"] != reference_name for point in coverage_points)
        or any(
            session["reference_contig"] not in (None, reference_name)
            for session in value["alignment_sessions"]
            if session["mode"] == "primary"
        )
        or coverage["source_row_count"] != reference_length
    ):
        raise OntNgsResultError("reference identity is inconsistent across the result")

    variants = value["verification"]["variants"]
    if verification_summary["variant_count"] != len(variants):
        raise OntNgsResultError("variant count is inconsistent")
    if any(not _variant_interval_is_valid(variant, reference_length) for variant in variants):
        raise OntNgsResultError("variant interval is invalid")

    ready = any(session["ready"] is True for session in value["alignment_sessions"])
    if authority["alignment_readiness"] != ("ready" if ready else "unavailable"):
        raise OntNgsResultError("alignment readiness is inconsistent")

    try:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OntNgsResultError("result contains an unsupported or non-finite value") from exc
    if len(encoded) > 256 * 1024:
        raise OntNgsResultError("NGS result projection exceeds the response-size bound")


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        handle = ngs_alignment_sessions._open_regular_file_no_symlinks(path)
        try:
            size_bytes = handle.seek(0, 2)
            handle.seek(0)
            if size_bytes < 1 or size_bytes > _MAX_TABLE_BYTES:
                raise OntNgsResultError(f"{label} exceeds the bounded result projection limit")
            raw = handle.read(_MAX_TABLE_BYTES + 1)
            if len(raw) != size_bytes:
                raise OntNgsResultError(f"{label} changed while it was read")
            return raw
        finally:
            handle.close()
    except OSError as exc:
        raise OntNgsResultError(f"{label} is unavailable") from exc


def _load_manifest(path: Path, **expected: Any) -> tuple[dict[str, Any], str]:
    raw = _read_bytes(path, path.name)
    manifest = load_sequence_qc_manifest(path, raw_bytes=raw, **expected)
    return manifest, hashlib.sha256(raw).hexdigest()


def _present_artifact(manifest: dict[str, Any], kind: str) -> dict[str, Any]:
    matches = [
        item for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and item.get("kind") == kind and item.get("state") == "present"
    ]
    if len(matches) != 1 or matches[0].get("integrity_valid") is not True:
        raise OntNgsResultError(f"required result artifact is not uniquely integrity-valid: {kind}")
    return matches[0]


def _artifact_path(manifest_dir: Path, artifact: dict[str, Any]) -> Path:
    declared_path = artifact.get("declared_path")
    if not isinstance(declared_path, str) or not declared_path:
        raise OntNgsResultError("result artifact has no declared path")
    declared = Path(declared_path)
    if declared.is_absolute() or any(part in {"", ".", ".."} for part in declared.parts):
        raise OntNgsResultError("result artifact escapes its manifest directory")
    return manifest_dir.joinpath(*declared.parts)


def _metric_value(raw: str) -> int | float | str | bool:
    value = raw.strip()
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        parsed = int(value)
        return parsed
    except ValueError:
        pass
    try:
        parsed_float = float(value)
    except ValueError:
        return value
    if not math.isfinite(parsed_float):
        raise OntNgsResultError("result metric is not finite")
    return parsed_float


def _load_metric_table(path: Path, expected_keys: frozenset[str]) -> dict[str, int | float | str | bool]:
    raw = _read_bytes(path, path.name)
    try:
        rows = list(csv.DictReader(raw.decode("utf-8").splitlines(), delimiter="\t"))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise OntNgsResultError(f"result metric table is invalid: {path.name}") from exc
    metrics: dict[str, int | float | str | bool] = {}
    for row in rows:
        key = row.get("metric")
        value = row.get("value")
        if not isinstance(key, str) or not key or value is None or key in metrics:
            raise OntNgsResultError(f"result metric table has an invalid row: {path.name}")
        metrics[key] = _metric_value(value)
    actual_keys = frozenset(metrics)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unknown = sorted(actual_keys - expected_keys)
        raise OntNgsResultError(
            f"result metric table contract mismatch: {path.name}; missing={missing}; unknown={unknown}"
        )
    return metrics


def _load_coverage(
    path: Path,
    *,
    construction_attestation: dict[str, Any] | None = None,
    construction_validated_at: datetime | None = None,
) -> dict[str, Any]:
    raw = _read_bytes(path, path.name)
    try:
        reader = csv.DictReader(raw.decode("utf-8").splitlines(), delimiter="\t")
        rows: list[dict[str, int | str]] = []
        reference_name: str | None = None
        previous_position = 0
        for index, row in enumerate(reader):
            if index >= _MAX_COVERAGE_ROWS:
                raise OntNgsResultError("coverage table exceeds the row limit")
            reference = row.get("reference")
            position = int(str(row.get("position") or ""))
            depth = int(str(row.get("depth") or ""))
            if (
                not reference
                or position < 1
                or depth < 0
                or position != index + 1
                or (reference_name is not None and reference != reference_name)
                or position <= previous_position
            ):
                raise ValueError
            reference_name = reference
            previous_position = position
            rows.append({"reference": reference, "position_1based": position, "depth": depth})
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise OntNgsResultError("coverage table is invalid") from exc
    if not rows:
        raise OntNgsResultError("coverage table is empty")

    minimum_point = min(
        rows,
        key=lambda item: (int(item["depth"]), int(item["position_1based"])),
    )
    bucket_width = max(1, math.ceil(len(rows) / (_MAX_COVERAGE_POINTS // 2)))
    points: list[dict[str, int | str]] = []
    for offset in range(0, len(rows), bucket_width):
        bucket = rows[offset:offset + bucket_width]
        low = min(bucket, key=lambda item: (int(item["depth"]), int(item["position_1based"])))
        high = max(bucket, key=lambda item: (int(item["depth"]), -int(item["position_1based"])))
        extrema = {
            int(low["position_1based"]): low,
            int(high["position_1based"]): high,
        }
        points.extend(sorted(extrema.values(), key=lambda item: int(item["position_1based"])))
    if len(points) > _MAX_COVERAGE_POINTS:
        raise OntNgsResultError("coverage projection exceeds its point bound")
    projection = {
        "method": "minmax_envelope_v1",
        "source_row_count": len(rows),
        "maximum_point_count": _MAX_COVERAGE_POINTS,
        "bucket_width_rows": bucket_width,
        "minimum_depth": int(minimum_point["depth"]),
        "minimum_depth_position_1based": int(minimum_point["position_1based"]),
        "depth_basis": "samtools_depth_aa_default_filters_excludes_deletions_v1",
        "depth_unit": "base_covering_alignment_records",
        "tie_breaking": "minimum:earliest_position;maximum:earliest_position",
        "endpoint_policy": "natural_bucket_extrema_only",
        "circular_policy": "linearized_1based_reference_order_no_wrap",
        "points": points,
    }
    if construction_attestation is None and construction_validated_at is not None:
        normalized = (
            construction_validated_at
            if construction_validated_at.tzinfo is not None
            else construction_validated_at.replace(tzinfo=timezone.utc)
        )
        construction_attestation = {
            "projection_sha256": hashlib.sha256(
                json.dumps(
                    projection,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "source_row_count": len(rows),
            "source_rows_sha256": hashlib.sha256(raw).hexdigest(),
            "validated_at": normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "validator": "bms.ngs.fastq-qc-result-construction-validator.v1",
        }
    if construction_attestation is not None:
        expected_keys = {
            "projection_sha256",
            "source_row_count",
            "source_rows_sha256",
            "validated_at",
            "validator",
        }
        if set(construction_attestation) != expected_keys:
            raise OntNgsResultError("coverage construction attestation keys are invalid")
        source_rows_sha256 = hashlib.sha256(raw).hexdigest()
        if (
            construction_attestation.get("validator") != "bms.ngs.fastq-qc-result-construction-validator.v1"
            or construction_attestation.get("source_rows_sha256") != source_rows_sha256
            or construction_attestation.get("source_row_count") != len(rows)
            or not isinstance(construction_attestation.get("validated_at"), str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", construction_attestation["validated_at"])
        ):
            raise OntNgsResultError("coverage construction attestation is inconsistent")
        projection_sha256 = hashlib.sha256(
            json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if construction_attestation.get("projection_sha256") != projection_sha256:
            raise OntNgsResultError("coverage construction projection digest mismatch")
        projection["construction_attestation"] = dict(construction_attestation)
    return projection


def _load_read_length_histogram(path: Path) -> dict[str, Any]:
    raw = _read_bytes(path, path.name)
    try:
        reader = csv.DictReader(raw.decode("utf-8").splitlines(), delimiter="\t")
        lengths: list[int] = []
        for index, row in enumerate(reader):
            if index >= _MAX_READ_LENGTH_ROWS:
                raise OntNgsResultError("read-length table exceeds the row limit")
            length = int(str(row.get("length_bp") or ""))
            if length < 0:
                raise ValueError
            lengths.append(length)
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise OntNgsResultError("read-length table is invalid") from exc
    if not lengths:
        raise OntNgsResultError("read-length table is empty")

    maximum = max(lengths)
    width = max(1, math.ceil((maximum + 1) / _READ_LENGTH_BIN_COUNT))
    counts = [0] * _READ_LENGTH_BIN_COUNT
    for length in lengths:
        counts[min(length // width, _READ_LENGTH_BIN_COUNT - 1)] += 1
    bins = [
        {
            "start_bp": index * width,
            "end_bp_exclusive": (index + 1) * width,
            "read_count": count,
        }
        for index, count in enumerate(counts)
    ]
    return {
        "method": "fixed_width_v1",
        "source_row_count": len(lengths),
        "bin_width_bp": width,
        "bins": bins,
    }


def _stages(job: Job) -> list[dict[str, Any]]:
    completed = set(job.completed_stages or [])
    outputs = job.stage_outputs if isinstance(job.stage_outputs, dict) else {}
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    terminal_states = provenance.get("stage_terminal_states")
    terminal_states = terminal_states if isinstance(terminal_states, dict) else {}
    result = []
    for stage in _REQUIRED_STAGES:
        terminal = terminal_states.get(stage)
        terminal = terminal if isinstance(terminal, dict) else {}
        terminal_outputs = terminal.get("outputs")
        effective_outputs = outputs.get(stage) if isinstance(outputs.get(stage), list) else terminal_outputs
        status = "complete" if stage in completed or terminal.get("status") == "complete" else "missing"
        output_count = len(effective_outputs) if isinstance(effective_outputs, list) else 0
        result.append({"stage": stage, "status": status, "output_count": output_count})
    return result


def _verification_projection(verification_manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        return project_verification_manifest(verification_manifest)
    except OntNgsDecisionProjectionError as exc:
        raise OntNgsResultError("construct-verification decision projection is invalid") from exc


def _build_file_projection(job: Job) -> dict[str, Any]:
    """Pin one result-root inode across the complete reopen projection."""

    root = resolve_persisted_job_result_root(job)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise OntNgsResultError("persisted result root could not be pinned") from exc
    try:
        identity = os.fstat(descriptor)
        if not stat.S_ISDIR(identity.st_mode):
            raise OntNgsResultError("persisted result root is not a directory")
        return _build_file_projection_from_pinned_root(job, Path(f"/proc/self/fd/{descriptor}"))
    finally:
        os.close(descriptor)


def _build_file_projection_from_pinned_root(job: Job, root: Path) -> dict[str, Any]:
    try:
        canonical_fastq = is_ont_fastq_qc_job(job)
    except OntNgsCompletionError as exc:
        raise OntNgsResultError(str(exc)) from exc
    if not canonical_fastq:
        raise OntNgsResultError("job is not an ONT FASTQ-QC result owner")
    fastq_dir = root / "fastq_qc"
    verification_dir = root / "verification"
    params = job.params if isinstance(job.params, dict) else {}

    fastq_manifest, fastq_digest = _load_manifest(
        fastq_dir / "qc_manifest.json",
        expected_job_id=str(job.id),
        expected_workflow_id="ont_fastq_qc",
        expected_input_mode="fastq",
        expected_analysis_status="completed",
    )
    verification_manifest, verification_digest = _load_manifest(verification_dir / "qc_manifest.json")
    if verification_manifest.get("schema") != VERIFICATION_SCHEMA:
        raise OntNgsResultError("construct-verification manifest schema is invalid")

    summary = _load_metric_table(
        _artifact_path(fastq_dir, _present_artifact(fastq_manifest, "summary")),
        _SUMMARY_METRIC_KEYS,
    )
    alignment = _load_metric_table(
        _artifact_path(fastq_dir, _present_artifact(fastq_manifest, "alignment_stats")),
        _ALIGNMENT_METRIC_KEYS,
    )
    coverage = _load_coverage(
        _artifact_path(fastq_dir, _present_artifact(fastq_manifest, "coverage")),
        construction_validated_at=job.completed_at,
    )
    histogram = _load_read_length_histogram(
        _artifact_path(fastq_dir, _present_artifact(fastq_manifest, "read_lengths"))
    )

    expected_reference = fastq_manifest.get("reference")
    verification_summary = verification_manifest.get("summary")
    if not isinstance(expected_reference, dict) or not isinstance(verification_summary, dict):
        raise OntNgsResultError("result reference summary is incomplete")
    if (
        verification_summary.get("reference_name") != expected_reference.get("name")
        or verification_summary.get("reference_length") != expected_reference.get("length")
    ):
        raise OntNgsResultError("FASTQ-QC and verification reference identities disagree")
    manifest_reference_sha256 = expected_reference.get("expected_sha256")
    persisted_reference_sha256 = params.get("reference_sequence_sha256")
    source_input_path = params.get("fastq_path")
    if (
        not isinstance(manifest_reference_sha256, str)
        or manifest_reference_sha256 != persisted_reference_sha256
        or not isinstance(source_input_path, str)
        or not source_input_path
    ):
        raise OntNgsResultError("FASTQ-QC manifest authority disagrees with persisted job authority")
    verification_inputs = verification_manifest.get("inputs")
    source_reads = verification_inputs.get("source_reads") if isinstance(verification_inputs, dict) else None
    source_fastq_sha256 = source_reads.get("sha256") if isinstance(source_reads, dict) else None
    if (
        not isinstance(source_fastq_sha256, str)
        or len(source_fastq_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_fastq_sha256)
    ):
        raise OntNgsResultError("construct verification source FASTQ authority is invalid")

    package_internal = ngs_alignment_sessions.build_ngs_package_artifacts(
        str(job.id),
        source_reference_sha256=manifest_reference_sha256,
        workflow_id="ont_fastq_qc",
        input_mode="fastq",
        source_input_path=source_input_path,
        job_output_dir=root,
        pinned_root_descriptor=True,
    )
    if len(package_internal) > 256:
        raise OntNgsResultError("NGS package artifact inventory exceeds its bound")
    package_artifacts = [
        {key: value for key, value in descriptor.items() if key not in {"_path", "relative_path"}}
        for descriptor in package_internal
    ]
    try:
        package_authority = canonical_ngs_package_authority(package_artifacts)
    except OntNgsCompletionError as exc:
        raise OntNgsResultError("NGS package authority is invalid") from exc
    persisted_package_authority = _require_persisted_package_authority(
        job,
        package_authority,
        sequence_qc_manifest_sha256=fastq_digest,
        construct_verification_manifest_sha256=verification_digest,
        reference_sequence_sha256=manifest_reference_sha256,
        source_fastq_sha256=source_fastq_sha256,
    )
    governed_alignment_sessions = ngs_alignment_sessions.build_alignment_sessions(
        str(job.id),
        source_reference_sha256=manifest_reference_sha256,
        package_artifact_set_sha256=package_authority["artifact_set_sha256"],
        workflow_id="ont_fastq_qc",
        input_mode="fastq",
        job_output_dir=root,
        pinned_root_descriptor=True,
    )
    alignment_sessions = [
        {
            "session_id": session["session_id"],
            "mode": session["mode"],
            "reference_contig": (
                session["reference"]["contig"]
                if session.get("ready") is True and isinstance(session.get("reference"), dict)
                else None
            ),
            "ready": session["ready"],
            "unavailable_reason": session["unavailable_reason"],
        }
        for session in governed_alignment_sessions
    ]
    if len(alignment_sessions) > 2:
        raise OntNgsResultError("alignment session inventory exceeds its bound")
    session_summaries = [
        {
            "session_id": session.get("session_id"),
            "mode": session.get("mode"),
            "reference_contig": session.get("reference_contig"),
            "ready": session.get("ready") is True,
            "unavailable_reason": session.get("unavailable_reason"),
        }
        for session in alignment_sessions
    ]
    projection: dict[str, Any] = {
        "schema": "bms.ngs.fastq-qc-result.v1",
        "job": {
            "id": str(job.id),
            "name": job.name,
            "status": job.status,
            "queue_status": job.queue_status,
            "workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "created_at": _rfc3339_utc(job.created_at),
            "started_at": _rfc3339_utc(job.started_at),
            "completed_at": _rfc3339_utc(job.completed_at),
            "error_message": job.error_message,
        },
        "authority": {
            "sequence_qc_manifest_sha256": fastq_digest,
            "construct_verification_manifest_sha256": verification_digest,
            "reference_sequence_sha256": manifest_reference_sha256,
            **package_authority,
            "manifest_readiness": "ready",
            "alignment_readiness": "ready" if any(session["ready"] for session in session_summaries) else "unavailable",
        },
        "artifacts": package_artifacts,
        "alignment_sessions": session_summaries,
        "summary": summary,
        "alignment": alignment,
        "read_length_histogram": histogram,
        "coverage": coverage,
        "verification": _verification_projection(verification_manifest),
        "stages": _stages(job),
        "execution_resources": _execution_resources(job, persisted_package_authority),
    }
    validate_ont_fastq_qc_result_contract(projection)
    return projection


async def build_ont_fastq_qc_result(job: Job) -> dict[str, Any]:
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(_build_file_projection, job)
