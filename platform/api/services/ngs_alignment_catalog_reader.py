"""Read-only catalog v2 access, independent of the optional preview.

The existing verified snapshot owner supplies file lifetime/integrity. Its
capacity and cold-copy limitations remain the P3 portable-delivery dependency.
No read path builds, adopts, repairs or retries a derived product.
"""
from contextlib import contextmanager, ExitStack
import base64
import json
import math
import re

import pysam
from services import verified_native_reads as native

from services import ngs_alignment_sessions as storage
from services import ngs_alignment_product_builder as builder
from services import ngs_alignment_presentation_v5 as records
from services.ngs_alignment_derived_products import identity_sha256, resolve_product_source
from services.ngs_alignment_catalog_query import SORT_FIELDS, SIGNAL_FIELDS, joined_catalog, signal_snapshot


class CatalogReadError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def population(row):
    return identity_sha256({"schema": "bms.ngs.read-population.v2", "job_id": row.job_id,
        "session_id": row.session_id, "mode": row.mode,
        "catalog_authority_sha256": row.authority_sha256})


def assert_population(expected, actual):
    if expected is None:
        return
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise CatalogReadError("NGS_READ_POPULATION_INVALID", "Invalid read population.")
    if expected != actual:
        raise CatalogReadError("NGS_READ_POPULATION_STALE", "The read population changed.", 409)


def _cursor(value, binding, *, ordinal=False):
    if value is None:
        return None
    code = "NGS_RECORD_CURSOR" if ordinal else "NGS_READ_CURSOR"
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        data = json.loads(raw)
        canonical = json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if base64.urlsafe_b64encode(canonical).decode("ascii").rstrip("=") != value:
            raise ValueError()
        if set(data) != {"schema", "binding", "last"} or data["schema"] != code + ".v2":
            raise ValueError()
        last = data["last"]
        if ordinal:
            if type(last) is not int or last < 0:
                raise ValueError()
        else:
            if not isinstance(last, dict) or set(last) != {"read_id", "value"}:
                raise ValueError()
            records._validate_read_id(last["read_id"])
            value = last["value"]
            if binding["sort_by"] == "read_id":
                if value != last["read_id"]:
                    raise ValueError()
            elif value is not None and (type(value) not in {int, float} or not math.isfinite(value)):
                raise ValueError()
        other = data["binding"]
        if not isinstance(other, dict) or set(other) != set(binding):
            raise ValueError()
        for key in ("population_id", "locator_sha256", "signal_snapshot_id"):
            if key in other and (not isinstance(other[key], str) or re.fullmatch(r"[0-9a-f]{64}", other[key]) is None):
                raise ValueError()
        query_fields = set(binding) - {"population_id", "locator_sha256", "signal_snapshot_id"}
        def query_bytes(candidate):
            return json.dumps({key: candidate[key] for key in query_fields}, ensure_ascii=False,
                              allow_nan=False, sort_keys=True, separators=(",", ":"))
        if query_bytes(other) != query_bytes(binding):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise CatalogReadError(code + "_INVALID", "Cursor does not match this query.") from exc
    if other != binding:
        raise CatalogReadError(code + "_STALE", "Cursor authority changed.", 409)
    return last


def _next(binding, last, *, ordinal=False):
    raw = json.dumps({"schema": ("NGS_RECORD_CURSOR" if ordinal else "NGS_READ_CURSOR") + ".v2",
        "binding": binding, "last": last}, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@contextmanager
def snapshot(row, root, *, signal=None):
    if row is None or row.product != "catalog" or row.state != "ready":
        raise CatalogReadError("NGS_CATALOG_NOT_READY", "The complete catalog is not ready.", 409)
    with builder._namespace(root, row, create=False) as namespace, ExitStack() as stack:
        directory = stack.enter_context(storage.open_presentation_authority_root(namespace / "sealed", create=False))
        # Verify the sealed manifest/artifact binding, not the source-population
        # reconstruction reserved for publication/adoption.
        manifest = builder._manifest(directory, row, lambda: None,
            expected_manifest=row.manifest_sha256, expected_authority=row.authority_sha256, verify_artifacts=False)
        from services.scientific_artifacts.query import _query_connection
        datasets = {}
        for role in ("catalog", "locators"):
            artifact = manifest["authority"]["artifacts"][role]
            handle = stack.enter_context(storage.open_verified_artifact_snapshot(
                directory / artifact["filename"], expected_sha256=artifact["sha256"],
                expected_size=artifact["size_bytes"]))
            datasets[role] = stack.enter_context(storage.verified_parquet_dataset(handle))
        # Admit query buffers only after the immutable native inputs have their
        # own allocations. ExitStack closes the engine before its input leases.
        prepared_signal = stack.enter_context(signal_snapshot(signal))
        connection = stack.enter_context(_query_connection(None))
        connection.execute("SET autoinstall_known_extensions=false")
        connection.execute("SET autoload_known_extensions=false")
        connection.execute("SET enable_external_access=false")
        for role, dataset in datasets.items():
            connection.register(role, dataset)
        yield connection, {**manifest, "_signal_snapshot": prepared_signal}


def retry_delivery_cache(job, row, root):
    """Explicit operator-only cache recovery, never scientific product retry.

    The caller authorizes the owning job and exact ready request. Verify all
    original product bytes before resetting any corrupt delivery copy.
    """
    if row is None or row.state != "ready":
        raise storage.AlignmentSessionError("ready derived product not found")
    resolve_product_source(job, row)
    with builder._namespace(root, row, create=False) as namespace:
        with storage.open_presentation_authority_root(namespace / "sealed", create=False) as directory:
            manifest = builder._manifest(directory, row, lambda: None,
                expected_manifest=row.manifest_sha256, expected_authority=row.authority_sha256)
            for metadata in manifest["authority"]["artifacts"].values():
                storage.retry_verified_artifact_cache(directory / metadata["filename"],
                    expected_sha256=metadata["sha256"], expected_size=metadata["size_bytes"])



def _rows(connection, query, params):
    result = connection.execute(query, params)
    columns = [item[0] for item in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _public(row):
    from services.ngs_read_overlay_policy import eligibility
    reason = eligibility(row)
    return {**{key: value for key, value in row.items()
               if key != "canonical_record_ordinal" and not key.startswith(("dorado_tag_", "overlay_"))},
            "in_preview": None, "signal_available": row.get("signal_available", False),
            "overlay_eligible": reason is None, "overlay_unavailable_reason": reason}


def _envelope(row):
    return {"job_id": row.job_id, "session_id": row.session_id,
            "population_id": population(row), "catalog_authority_sha256": row.authority_sha256,
            "preview_authority": None, "signal_snapshot_id": None}


def read_page(job, row, root, *, search, alignment_state, limit, cursor, population_id,
              sort_by="read_id", sort_direction="asc", contig=None, start_1based=None,
              end_1based=None, metric_min=None, metric_max=None, signal=None):
    resolve_product_source(job, row)
    identity = population(row)
    assert_population(population_id, identity)
    if (sort_by not in SORT_FIELDS or sort_direction not in {"asc", "desc"}
            or len(search.encode("utf-8")) > 254
            or any(v is not None and (type(v) not in {int, float} or not math.isfinite(v)) for v in (metric_min, metric_max))
            or (sort_by == "read_id" and (metric_min is not None or metric_max is not None))
            or (metric_min is not None and metric_max is not None and metric_min > metric_max)):
        raise CatalogReadError("NGS_READ_QUERY_INVALID", "Invalid catalog sort or metric bounds.")
    locus = None
    if any(v is not None for v in (contig, start_1based, end_1based)):
        if (not contig or type(start_1based) is not int or type(end_1based) is not int
                or start_1based < 1 or end_1based < start_1based):
            raise CatalogReadError("NGS_READ_QUERY_INVALID", "Supply a complete 1-based closed locus.")
        locus = {"contig": contig, "start_1based": start_1based, "end_1based": end_1based}
    binding = {"schema": "bms.ngs.read-query.v3", "population_id": identity,
               "search": search, "alignment_state": alignment_state, "locus": locus,
               "metric_min": metric_min, "metric_max": metric_max, "limit": limit,
               "sort_by": sort_by, "sort_direction": sort_direction, "null_order": "last"}
    with snapshot(row, root, signal=signal) as (db, manifest), joined_catalog(db, manifest["_signal_snapshot"], manifest["statistics"]["logical_read_count"]) as signal_state:
        if sort_by in SIGNAL_FIELDS:
            binding["signal_snapshot_id"] = signal_state["signal_snapshot_id"]
        last = _cursor(cursor, binding)
        where, args = ["contains(read_id, ?)"], [search]
        if alignment_state is not None:
            where.append("alignment_state = ?")
            args.append(alignment_state)
        if locus is not None:
            where.extend(["alignment_state = 'mapped_primary'", "contig = ?", "start_1based <= ?", "alignment_end_1based >= ?"])
            args.extend([contig, end_1based, start_1based])
        for operator, value in ((">=", metric_min), ("<=", metric_max)):
            if value is not None:
                where.append(f'"{sort_by}" {operator} ?')
                args.append(value)
        predicate = " AND ".join(where)
        filtered = db.execute("SELECT count(*) FROM joined_catalog WHERE " + predicate, args).fetchone()[0]
        expression = 'encode(read_id)' if sort_by == "read_id" else f'"{sort_by}"'
        comparison = ">" if sort_direction == "asc" else "<"
        if last is not None:
            if last["value"] is None:
                predicate += f" AND ({expression} IS NULL AND encode(read_id) > encode(?))"
                args.append(last["read_id"])
            else:
                value_sql = "encode(?)" if sort_by == "read_id" else "?"
                predicate += f" AND ({expression} IS NULL OR {expression} {comparison} {value_sql} OR ({expression} = {value_sql} AND encode(read_id) > encode(?)))"
                args.extend([last["value"], last["value"], last["read_id"]])
        result = _rows(db, "SELECT * FROM joined_catalog WHERE " + predicate +
            f" ORDER BY {expression} {sort_direction.upper()} NULLS LAST, encode(read_id) ASC LIMIT ?", [*args, limit + 1])
        more = len(result) > limit
        result = result[:limit]
        return {"schema": "bms.ngs.read-page.v3", **_envelope(row), **signal_state,
                "source_read_count": manifest["statistics"]["logical_read_count"],
                "source_record_count": manifest["statistics"]["record_count"],
                "filtered_read_count": filtered, "reads": [_public(item) for item in result],
                "limit": limit, "sort_by": sort_by, "sort_direction": sort_direction,
                "locus": locus, "null_order": "last", "tie_breaker": ["read_id"],
                "next_cursor": _next(binding, {"read_id": result[-1]["read_id"], "value": result[-1][sort_by]}) if more else None}


@contextmanager
def _source(job, row, *, allocation=None):
    source, inputs = resolve_product_source(job, row)
    with ExitStack() as stack:
        handle = stack.enter_context(storage.open_verified_artifact_snapshot(inputs["alignment_path"],
            expected_sha256=source["alignment_sha256"], expected_size=source["alignment_size_bytes"]))
        index = stack.enter_context(storage.open_verified_artifact_snapshot(inputs["index_path"],
            expected_sha256=source["alignment_index_sha256"], expected_size=source["alignment_index_size_bytes"]))
        if allocation is None:
            from services import global_resource_admission as resources
            try:
                allocation = stack.enter_context(resources.derived_work(
                    owner="ngs-record-reader:" + row.id, storage_root=storage._snapshot_cache_directory(),
                    cpu_threads=1, disk_bytes=0))
            except resources.ResourceCapacityUnavailable as exc:
                raise CatalogReadError("NGS_READ_CAPACITY_UNAVAILABLE", "Record reader capacity is unavailable.", 503) from exc
        with native.alignment(handle, index, threads=1) as bam:
            if not bam.check_index():
                raise storage.AlignmentSessionError("catalog source index integrity mismatch")
            yield bam


def verified_record(bam, locator):
    bam.seek(locator["bgzf_virtual_offset"])
    try:
        read = next(bam)
    except StopIteration as exc:
        raise storage.AlignmentSessionError("catalog locator integrity mismatch") from exc
    kind = records._record_class(int(read.flag))
    fingerprint, _ = records._source_record_projection(read, ordinary_unmapped=kind == "unmapped",
                                                       include_catalog=False)
    if (read.query_name != locator["read_id"] or fingerprint != locator["record_fingerprint_sha256"]
            or kind != locator["record_class"] or int(read.flag) != locator["flags"]
            or (read.reference_name if not read.is_unmapped else None) != locator["contig"]
            or (read.reference_start if not read.is_unmapped else None) != locator["start_0based"]
            or (read.reference_end if not read.is_unmapped else None) != locator["end_0based_exclusive"]):
        raise storage.AlignmentSessionError("catalog locator fingerprint integrity mismatch")
    return read


def _record(bam, locator, include_sequence):
    read = verified_record(bam, locator)
    kind = records._record_class(int(read.flag))
    projected = storage._sam_line_to_read(read.to_string(), include_sequence=include_sequence)
    fields = {"read_id", "contig", "start_1based", "alignment_end_1based", "strand", "flags",
              "mapq", "cigar", "unmapped", "length", "mean_quality", "sequence", "quality"}
    return {**{key: value for key, value in projected.items() if key in fields},
            "source_record_ordinal": locator["source_record_ordinal"], "record_class": kind}


def exact_read(job, row, root, *, read_id, include_sequence, population_id, signal=None):
    records._validate_read_id(read_id)
    resolve_product_source(job, row)
    assert_population(population_id, population(row))
    with snapshot(row, root, signal=signal) as (db, manifest), joined_catalog(db, manifest["_signal_snapshot"], manifest["statistics"]["logical_read_count"]) as signal_state:
        matches = _rows(db, "SELECT * FROM joined_catalog WHERE read_id = ?", [read_id])
        if len(matches) != 1:
            raise CatalogReadError("NGS_RESOURCE_NOT_FOUND", "The exact read was not found.", 404)
        item = matches[0]
        locators = []
        if include_sequence and item["canonical_record_ordinal"] is not None:
            locators = _rows(db, "SELECT * FROM locators WHERE read_id = ? AND source_record_ordinal = ?",
                             [read_id, item["canonical_record_ordinal"]])
            if len(locators) != 1:
                raise storage.AlignmentSessionError("canonical locator integrity mismatch")
    # Release sort/query scratch before native source admission and decoding.
    record = None
    if locators:
        with _source(job, row) as bam:
            record = _record(bam, locators[0], True)
    return {"schema": "bms.ngs.read-lookup.v2", **_envelope(row), **signal_state, "read": _public(item),
            "sequence_available": record is not None and record.get("sequence") is not None,
            "record": record}


def record_page(job, row, root, *, read_id, include_sequence, population_id, limit, cursor, signal=None):
    records._validate_read_id(read_id)
    resolve_product_source(job, row)
    assert_population(population_id, population(row))
    with snapshot(row, root, signal=signal) as (db, manifest), joined_catalog(db, manifest["_signal_snapshot"], manifest["statistics"]["logical_read_count"]) as signal_state:
        binding = {"population_id": population(row),
            "locator_sha256": manifest["authority"]["artifacts"]["locators"]["sha256"],
            "read_id": read_id, "limit": limit, "include_sequence": include_sequence}
        last = _cursor(cursor, binding, ordinal=True)
        total = db.execute("SELECT count(*) FROM locators WHERE read_id = ?", [read_id]).fetchone()[0]
        if not total:
            raise CatalogReadError("NGS_RESOURCE_NOT_FOUND", "The exact read was not found.", 404)
        locators = _rows(db, "SELECT * FROM locators WHERE read_id = ? AND source_record_ordinal > ? "
                         "ORDER BY source_record_ordinal LIMIT ?", [read_id, last if last is not None else -1, limit + 1])
        more = len(locators) > limit
        locators = locators[:limit]
    with _source(job, row) as bam:
        result = [_record(bam, locator, include_sequence) for locator in locators]
    return {"schema": "bms.ngs.alignment-record-page.v2", **_envelope(row), **signal_state,
        "read_id": read_id, "total_record_count": total, "records": result, "limit": limit,
        "next_cursor": _next(binding, locators[-1]["source_record_ordinal"], ordinal=True) if more else None}


def decorate_preview(catalog, preview, root, result):
    """Optional bounded membership; a bad preview never hides catalog data."""
    if (preview is None or preview.state != "ready" or preview.product != "preview"
            or preview.catalog_request_id != catalog.id
            or preview.catalog_authority_sha256 != catalog.authority_sha256
            or preview.source_authority_sha256 != catalog.source_authority_sha256):
        return result
    try:
        with builder._namespace(root, preview, create=False) as namespace:
            with storage.open_presentation_authority_root(namespace / "sealed", create=False) as directory:
                manifest = builder._manifest(directory, preview, lambda: None,
                    expected_manifest=preview.manifest_sha256, expected_authority=preview.authority_sha256, verify_artifacts=False)
                artifact = manifest["authority"]["artifacts"]["membership"]
                with ExitStack() as leases:
                    handles = {role: leases.enter_context(storage.open_verified_artifact_snapshot(
                        directory / metadata["filename"], expected_sha256=metadata["sha256"],
                        expected_size=metadata["size_bytes"]))
                        for role, metadata in manifest["authority"]["artifacts"].items()}
                    handle = handles["membership"]
                    import pyarrow.parquet as pq
                    parquet = pq.ParquetFile(handle)
                    if parquet.schema_arrow != builder.MEMBERSHIP_SCHEMA:
                        return result
                    # Membership is bounded by the preview policy, never the
                    # complete read population. Refuse oversized metadata first.
                    if parquet.metadata.num_rows > preview.request_contract["policy"]["target_reads"]:
                        return result
                    members = {item["read_id"] for batch in parquet.iter_batches(batch_size=4096)
                               for item in batch.to_pylist()}
                    if len(members) != parquet.metadata.num_rows:
                        return result
    except (storage.AlignmentSessionError, OSError, ValueError, KeyError, TypeError):
        return result
    decorated = dict(result)
    decorated["preview_authority"] = preview.authority_sha256
    if "reads" in decorated:
        decorated["reads"] = [{**item, "in_preview": item["read_id"] in members,
            **({"overlay_eligible": False, "overlay_unavailable_reason": "already_in_preview"} if item["read_id"] in members else {})} for item in decorated["reads"]]
    if "read" in decorated:
        item = decorated["read"]
        decorated["read"] = {**item, "in_preview": item["read_id"] in members,
            **({"overlay_eligible": False, "overlay_unavailable_reason": "already_in_preview"} if item["read_id"] in members else {})}
    return decorated
