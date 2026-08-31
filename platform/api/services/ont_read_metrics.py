"""Persist and query literature-backed ONT per-read signal metrics."""
from __future__ import annotations

import hashlib
import asyncio
from functools import partial
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import OntRawSignalRepresentation, ScientificArtifactReceipt
from services.scientific_artifacts import (
    artifact_reference,
    canonical_sha256,
    publish_table_rows,
    query_rows_by_values,
)
from services.scientific_artifacts.writer import _artifact_id

RAW_READ_METRICS_OWNER_KIND = "ont_raw_signal_representation"
RAW_READ_METRICS_ROLE = "literature_backed_read_metrics"
RAW_READ_METRICS_SCHEMA_ID = "bms.ont.literature-backed-read-metrics.v1"
RAW_READ_METRICS_CONTRACT = "bms.ont.literature-backed-read-metrics.v1"
RAW_READ_METRICS_CONTRACT_SHA256 = hashlib.sha256(RAW_READ_METRICS_CONTRACT.encode("utf-8")).hexdigest()
RAW_READ_METRICS_MAX_ROWS = 500_000
RAW_READ_METRICS_MAX_BYTES = 512 * 1024 * 1024

RAW_READ_METRIC_COLUMNS = (
    "read_id",
    "sample_count",
    "sampling_rate_hz",
    "duration_seconds",
    "channel_number",
    "start_mux",
    "start_time_samples",
    "acquisition_start_seconds",
    "time_since_mux_change_seconds",
    "num_reads_since_mux_change",
    "num_minknow_events",
    "minknow_event_rate_per_second",
    "median_before_pa",
    "open_pore_level_pa",
    "tracked_scaling_shift",
    "tracked_scaling_scale",
    "predicted_scaling_shift",
    "predicted_scaling_scale",
    "current_mean_pa",
    "current_median_pa",
    "current_stddev_pa",
    "current_mad_pa",
    "current_min_pa",
    "current_max_pa",
)

RAW_READ_METRIC_SCHEMA = pa.schema([
    ("read_id", pa.string()),
    ("sample_count", pa.int64()),
    ("sampling_rate_hz", pa.int64()),
    ("duration_seconds", pa.float64()),
    ("channel_number", pa.int64()),
    ("start_mux", pa.int64()),
    ("start_time_samples", pa.int64()),
    ("acquisition_start_seconds", pa.float64()),
    ("time_since_mux_change_seconds", pa.float64()),
    ("num_reads_since_mux_change", pa.int64()),
    ("num_minknow_events", pa.int64()),
    ("minknow_event_rate_per_second", pa.float64()),
    ("median_before_pa", pa.float64()),
    ("open_pore_level_pa", pa.float64()),
    ("tracked_scaling_shift", pa.float64()),
    ("tracked_scaling_scale", pa.float64()),
    ("predicted_scaling_shift", pa.float64()),
    ("predicted_scaling_scale", pa.float64()),
    ("current_mean_pa", pa.float64()),
    ("current_median_pa", pa.float64()),
    ("current_stddev_pa", pa.float64()),
    ("current_mad_pa", pa.float64()),
    ("current_min_pa", pa.float64()),
    ("current_max_pa", pa.float64()),
])

_INTEGER_COLUMNS = frozenset({
    "sample_count", "sampling_rate_hz", "channel_number", "start_mux",
    "start_time_samples", "num_reads_since_mux_change", "num_minknow_events",
})
_REQUIRED_INTEGER_COLUMNS = frozenset({"sample_count", "sampling_rate_hz"})
_FLOAT_COLUMNS = frozenset(RAW_READ_METRIC_COLUMNS).difference(_INTEGER_COLUMNS).difference({"read_id"})


class OntReadMetricError(ValueError):
    """A raw-read metric artifact failed its closed authority contract."""


def _validate_metric_row(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(RAW_READ_METRIC_COLUMNS):
        raise OntReadMetricError("raw read metric row does not match the closed schema")
    read_id = value.get("read_id")
    if not isinstance(read_id, str) or not read_id or len(read_id) > 255:
        raise OntReadMetricError("raw read metric identity is invalid")
    for column in _INTEGER_COLUMNS:
        candidate = value.get(column)
        if candidate is None and column not in _REQUIRED_INTEGER_COLUMNS:
            continue
        if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
            raise OntReadMetricError(f"raw read metric {column} is invalid")
    if value["sample_count"] < 1 or value["sampling_rate_hz"] < 1:
        raise OntReadMetricError("raw read metric signal bounds are invalid")
    for column in _FLOAT_COLUMNS:
        candidate = value.get(column)
        if candidate is None:
            continue
        if not isinstance(candidate, (int, float)) or isinstance(candidate, bool) or not math.isfinite(float(candidate)):
            raise OntReadMetricError(f"raw read metric {column} is invalid")
        value[column] = float(candidate)
    expected_duration = value["sample_count"] / value["sampling_rate_hz"]
    if not math.isclose(value["duration_seconds"], expected_duration, rel_tol=1e-12, abs_tol=1e-12):
        raise OntReadMetricError("raw read metric duration diverged from sample authority")
    events = value["num_minknow_events"]
    event_rate = value["minknow_event_rate_per_second"]
    if events is None:
        if event_rate is not None:
            raise OntReadMetricError("MinKNOW event-call rate exists without source metadata")
    else:
        expected_event_rate = events / expected_duration
        if event_rate is None or not math.isclose(
            event_rate, expected_event_rate, rel_tol=1e-12, abs_tol=1e-12,
        ):
            raise OntReadMetricError("MinKNOW event-call rate diverged from source metadata")
    return {column: value[column] for column in RAW_READ_METRIC_COLUMNS}


def _load_metric_rows(path: Path, expected: Mapping[str, Any]) -> list[dict[str, Any]]:
    required = {"schema", "contract", "contract_sha256", "sha256", "bytes", "row_count"}
    if (
        set(expected) != required
        or expected.get("schema") != "bms.ont.raw-read-metrics-jsonl.v1"
        or expected.get("contract") != RAW_READ_METRICS_CONTRACT
        or expected.get("contract_sha256") != RAW_READ_METRICS_CONTRACT_SHA256
        or re.fullmatch(r"[0-9a-f]{64}", str(expected.get("sha256", ""))) is None
        or not isinstance(expected.get("bytes"), int)
        or not isinstance(expected.get("row_count"), int)
        or not 1 <= int(expected["bytes"]) <= RAW_READ_METRICS_MAX_BYTES
        or not 1 <= int(expected["row_count"]) <= RAW_READ_METRICS_MAX_ROWS
    ):
        raise OntReadMetricError("raw read metric validation receipt is invalid")
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_size != expected["bytes"]:
        raise OntReadMetricError("raw read metric output is not an exact regular file")
    digest = hashlib.sha256()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open("rb") as handle:
        for line in handle:
            digest.update(line)
            if len(line) > 64 * 1024:
                raise OntReadMetricError("raw read metric row exceeds its byte limit")
            try:
                row = _validate_metric_row(json.loads(line))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise OntReadMetricError("raw read metric JSONL is invalid") from exc
            if row["read_id"] in seen:
                raise OntReadMetricError("raw read metric identities are not unique")
            seen.add(row["read_id"])
            rows.append(row)
            if len(rows) > RAW_READ_METRICS_MAX_ROWS:
                raise OntReadMetricError("raw read metric artifact exceeds its row limit")
    after = os.lstat(path)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or digest.hexdigest() != expected["sha256"]
        or len(rows) != expected["row_count"]
    ):
        raise OntReadMetricError("raw read metric output changed or diverged from its receipt")
    return rows


async def publish_read_metrics_from_validation(
    session: AsyncSession,
    *,
    representation: OntRawSignalRepresentation,
    metrics_path: Path,
    semantic_receipt: Mapping[str, Any],
    validation_runtime_identity: Mapping[str, Any] | None = None,
) -> ScientificArtifactReceipt:
    metrics_identity = semantic_receipt.get("read_metrics")
    if not isinstance(metrics_identity, Mapping):
        raise OntReadMetricError("semantic validation lacks read metric authority")
    if not representation.manifest_sha256 or not re.fullmatch(r"[0-9a-f]{64}", representation.manifest_sha256):
        raise OntReadMetricError("raw representation manifest authority is invalid")
    rows = _load_metric_rows(metrics_path, metrics_identity)
    if representation.read_count is not None and representation.read_count != len(rows):
        raise OntReadMetricError("raw read metric count diverged from the representation")
    if semantic_receipt.get("read_count") != len(rows):
        raise OntReadMetricError("raw read metric count diverged from semantic validation")
    source_receipts = {
        "schema": "bms.ont.raw-read-metric-authority.v1",
        "authority_scope": "raw_signal_source_summary",
        "representation_id": representation.id,
        "representation_manifest_sha256": representation.manifest_sha256,
        "run_id": representation.run_id,
        "observed_generation": representation.observed_generation,
        "read_authority": {
            "identity": "exact_read_id_within_representation",
            "read_count": len(rows),
        },
        "bam_authority": {"state": "joined_at_sort_query"},
        "mapping_authority": {"state": "joined_at_sort_query_when_bound"},
        "package_authority": {
            "representation_manifest_sha256": representation.manifest_sha256,
        },
        "implementation_authority": {
            "metric_contract_sha256": RAW_READ_METRICS_CONTRACT_SHA256,
            "validation_runtime_identity": dict(
                validation_runtime_identity
                if validation_runtime_identity is not None
                else (representation.runtime_identity or {})
            ),
        },
        "metric_contract": RAW_READ_METRICS_CONTRACT,
        "metric_contract_sha256": RAW_READ_METRICS_CONTRACT_SHA256,
        "metrics_jsonl_sha256": metrics_identity["sha256"],
        "metrics_jsonl_bytes": metrics_identity["bytes"],
        "row_count": len(rows),
    }
    source_sha256 = canonical_sha256(source_receipts)
    artifact = await publish_table_rows(
        session,
        owner_kind=RAW_READ_METRICS_OWNER_KIND,
        owner_id=representation.id,
        role=RAW_READ_METRICS_ROLE,
        schema_id=RAW_READ_METRICS_SCHEMA_ID,
        source_sha256=source_sha256,
        rows=rows,
        schema=RAW_READ_METRIC_SCHEMA,
        source_receipts=source_receipts,
    )
    await session.flush()
    receipt = await session.get(ScientificArtifactReceipt, artifact.artifact_id)
    if receipt is None:
        raise OntReadMetricError("raw read metric artifact receipt was not persisted")
    if receipt.source_receipts_json != source_receipts:
        raise OntReadMetricError("raw read metric artifact receipt authority diverged")
    return receipt


def _receipt_reference(receipt: ScientificArtifactReceipt) -> dict[str, Any]:
    return artifact_reference(
        artifact_id=receipt.artifact_id,
        owner_kind=receipt.owner_kind,
        owner_id=receipt.owner_id,
        role=receipt.role,
        schema_id=receipt.schema_id,
        schema_version=receipt.artifact_schema_version,
        content_sha256=receipt.content_sha256,
        size_bytes=receipt.size_bytes,
        row_count=receipt.row_count,
        relative_path=receipt.relative_path,
    )


async def find_read_metric_receipt(
    session: AsyncSession,
    representation: OntRawSignalRepresentation,
) -> ScientificArtifactReceipt | None:
    candidates = list((await session.execute(select(ScientificArtifactReceipt).where(
        ScientificArtifactReceipt.owner_kind == RAW_READ_METRICS_OWNER_KIND,
        ScientificArtifactReceipt.owner_id == representation.id,
        ScientificArtifactReceipt.role == RAW_READ_METRICS_ROLE,
        ScientificArtifactReceipt.schema_id == RAW_READ_METRICS_SCHEMA_ID,
        ScientificArtifactReceipt.artifact_schema_version == 1,
        ScientificArtifactReceipt.availability == "available",
    ))).scalars())
    required_keys = {
        "schema", "authority_scope", "representation_id", "representation_manifest_sha256",
        "run_id", "observed_generation", "read_authority", "bam_authority",
        "mapping_authority", "package_authority", "implementation_authority",
        "metric_contract", "metric_contract_sha256", "metrics_jsonl_sha256",
        "metrics_jsonl_bytes", "row_count",
    }
    exact = []
    for receipt in candidates:
        authority = receipt.source_receipts_json
        if not isinstance(authority, dict) or set(authority) != required_keys:
            continue
        source_sha256 = canonical_sha256(authority)
        implementation = authority.get("implementation_authority")
        if (
            authority.get("schema") != "bms.ont.raw-read-metric-authority.v1"
            or authority.get("authority_scope") != "raw_signal_source_summary"
            or authority.get("representation_id") != representation.id
            or authority.get("representation_manifest_sha256") != representation.manifest_sha256
            or authority.get("run_id") != representation.run_id
            or authority.get("observed_generation") != representation.observed_generation
            or authority.get("read_authority") != {
                "identity": "exact_read_id_within_representation",
                "read_count": receipt.row_count,
            }
            or authority.get("bam_authority") != {"state": "joined_at_sort_query"}
            or authority.get("mapping_authority") != {"state": "joined_at_sort_query_when_bound"}
            or authority.get("package_authority") != {
                "representation_manifest_sha256": representation.manifest_sha256,
            }
            or not isinstance(implementation, dict)
            or set(implementation) != {"metric_contract_sha256", "validation_runtime_identity"}
            or implementation.get("metric_contract_sha256") != RAW_READ_METRICS_CONTRACT_SHA256
            or not isinstance(implementation.get("validation_runtime_identity"), dict)
            or authority.get("metric_contract") != RAW_READ_METRICS_CONTRACT
            or authority.get("metric_contract_sha256") != RAW_READ_METRICS_CONTRACT_SHA256
            or authority.get("row_count") != receipt.row_count
            or receipt.artifact_id != _artifact_id(
                RAW_READ_METRICS_OWNER_KIND,
                representation.id,
                RAW_READ_METRICS_ROLE,
                source_sha256,
            )
        ):
            continue
        exact.append(receipt)
    if len(exact) > 1:
        raise OntReadMetricError("multiple current raw read metric artifacts claim authority")
    return exact[0] if exact else None


async def load_read_metrics_for_ids(
    session: AsyncSession,
    *,
    representation: OntRawSignalRepresentation,
    read_ids: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], ScientificArtifactReceipt | None]:
    if not read_ids:
        return {}, None
    receipt = await find_read_metric_receipt(session, representation)
    if receipt is None:
        return {}, None
    rows = await asyncio.to_thread(partial(
        query_rows_by_values,
        _receipt_reference(receipt),
        key_column="read_id",
        values=list(read_ids),
        columns=RAW_READ_METRIC_COLUMNS,
        max_values=5_000,
    ))
    return {str(row["read_id"]): row for row in rows}, receipt
