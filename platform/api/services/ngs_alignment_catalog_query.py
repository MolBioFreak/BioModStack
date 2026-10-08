"""Closed full-population SQL projection; optional signal never owns reads."""
from contextlib import contextmanager, ExitStack
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from services.ngs_alignment_derived_products import identity_sha256
from services.ont_read_metrics import RAW_READ_METRIC_SCHEMA, _validate_metric_row
from services.scientific_artifacts.writer import artifact_root, _artifact_receipt_identity, ScientificArtifactError
from services import ngs_alignment_sessions as storage

ALIGNMENT_FIELDS = (
    "read_id", "length", "mean_quality", "mapq", "aligned_query_bases",
    "aligned_reference_bases", "inserted_bases", "deleted_bases", "clipped_bases",
    "edit_distance", "reference_substitution_count", "reference_substitution_rate",
    "aligned_fraction", "clipped_fraction", "reference_disagreement_rate",
)
RAW_FIELDS = (
    "sample_count", "duration_seconds", "sampling_rate_hz", "current_mean_pa",
    "current_median_pa", "current_stddev_pa", "current_mad_pa", "current_min_pa",
    "current_max_pa", "channel_number", "start_mux", "acquisition_start_seconds",
    "time_since_mux_change_seconds", "median_before_pa", "open_pore_level_pa",
    "minknow_event_rate_per_second",
)
DERIVED_FIELDS = ("dorado_emission_rate_bases_per_second", "mapped_signal_span_samples",
                  "samples_per_aligned_reference_base")
SIGNAL_FIELDS = RAW_FIELDS + DERIVED_FIELDS
SORT_FIELDS = ALIGNMENT_FIELDS + SIGNAL_FIELDS


def unavailable_signal(*, raw_run_id=None, raw_observed_generation=None, raw_representation_id=None):
    return {"signal_metrics_state": "unavailable", "raw_run_id": raw_run_id,
        "raw_observed_generation": raw_observed_generation, "raw_representation_id": raw_representation_id,
        "raw_representation_manifest_sha256": None, "signal_metrics_artifact_sha256": None}


@contextmanager
def signal_snapshot(signal):
    """Pin optional inputs before the query reserves its scratch envelope."""
    state = dict(signal or unavailable_signal())
    artifact = state.pop("artifact", None)
    state["signal_cache_retry_sha256"] = artifact["content_sha256"] if artifact is not None else None
    with ExitStack() as stack:
        handle = dataset = None
        if artifact is not None:
            try:
                relative, digest, size = _artifact_receipt_identity(artifact)
                if not relative or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
                    raise ValueError("unsafe raw metrics path")
                directory = stack.enter_context(storage.open_presentation_authority_root(artifact_root(), create=False))
                handle = stack.enter_context(storage.open_verified_artifact_snapshot(
                    directory / relative, expected_sha256=digest, expected_size=size))
                dataset = stack.enter_context(storage.verified_parquet_dataset(handle))
            except storage.AlignmentCapacityUnavailable:
                raise
            except (ScientificArtifactError, storage.AlignmentSessionError, OSError, ValueError, TypeError, pa.ArrowException):
                state["signal_metrics_state"] = "invalid"
                state["signal_metrics_artifact_sha256"] = None
        yield state, artifact, handle, dataset


@contextmanager
def joined_catalog(db, prepared, logical_count):
    """Exact left join; semantic proof belongs to the verified cache generation."""
    state, artifact, handle, dataset = prepared
    state = dict(state)
    empty = pa.Table.from_batches([], schema=RAW_READ_METRIC_SCHEMA)
    db.register("raw_metrics", empty)
    if dataset is not None:
        key = None
        try:
            db.unregister("raw_metrics")
            db.register("raw_metrics", dataset)
            key = identity_sha256({"schema": "bms.ngs.raw-metrics-validation.v1", "artifact": artifact})
            verified, disposition = handle.verified_semantic_value(key, handle)
            if verified and disposition == "invalid":
                raise ValueError("raw metrics generation failed semantic validation")
            if not verified:
                handle.seek(0)
                parquet = pq.ParquetFile(handle)
                if parquet.schema_arrow != RAW_READ_METRIC_SCHEMA or parquet.metadata.num_rows != artifact["row_count"]:
                    raise ValueError("raw metrics schema/count mismatch")
                for batch in parquet.iter_batches(batch_size=4096):
                    for item in batch.to_pylist():
                        _validate_metric_row(item)
                count, distinct_count = db.execute("SELECT count(*), count(DISTINCT read_id) FROM raw_metrics").fetchone()
                if count != distinct_count:
                    raise ValueError("duplicate raw identity")
                handle.remember_semantic_value(key, "ready", handle)
            state["signal_metrics_state"] = "ready"
            state["signal_metrics_artifact_sha256"] = artifact["content_sha256"]
        except storage.AlignmentCapacityUnavailable:
            raise
        except (duckdb.OutOfMemoryException, pa.ArrowMemoryError, MemoryError) as exc:
            raise storage.AlignmentCapacityUnavailable("signal query capacity unavailable") from exc
        except (ScientificArtifactError, storage.AlignmentSessionError, OSError, ValueError, TypeError, duckdb.Error, pa.ArrowException) as exc:
            if key is not None and isinstance(exc, (ValueError, TypeError)):
                handle.remember_semantic_value(key, "invalid", handle)
            db.unregister("raw_metrics")
            db.register("raw_metrics", empty)
            state["signal_metrics_state"] = "invalid"
            state["signal_metrics_artifact_sha256"] = None
    raw = ", ".join(f'raw."{name}"' for name in RAW_FIELDS)
    valid = """catalog.dorado_tag_parse_valid = TRUE AND raw.sample_count >= 1
        AND raw.sampling_rate_hz > 0 AND isfinite(raw.sampling_rate_hz)
        AND catalog.dorado_tag_start_sample >= 0
        AND catalog.dorado_tag_end_sample > catalog.dorado_tag_start_sample
        AND catalog.dorado_tag_end_sample <= raw.sample_count"""
    span = "(catalog.dorado_tag_end_sample - catalog.dorado_tag_start_sample)"
    db.execute(f"""CREATE TEMP VIEW joined_catalog AS SELECT catalog.*, {raw},
        (raw.read_id IS NOT NULL AND regexp_full_match(catalog.read_id, '[A-Za-z0-9_.-]{{1,128}}')) AS signal_available,
        CASE WHEN {valid} THEN {span} END AS mapped_signal_span_samples,
        CASE WHEN {valid} THEN CAST(catalog.dorado_tag_emitted_bases AS DOUBLE) * raw.sampling_rate_hz / {span} END AS dorado_emission_rate_bases_per_second,
        CASE WHEN {valid} AND catalog.aligned_reference_bases > 0 THEN CAST({span} AS DOUBLE) / catalog.aligned_reference_bases END AS samples_per_aligned_reference_base
        FROM catalog LEFT JOIN raw_metrics AS raw ON raw.read_id = catalog.read_id""")
    # Catalog uniqueness is publication authority; validated raw uniqueness makes
    # this left join cardinality-preserving without recounting every interaction.
    state["signal_snapshot_id"] = identity_sha256({"schema": "bms.ngs.signal-snapshot.v1", **state})
    yield state
