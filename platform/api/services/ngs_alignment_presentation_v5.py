"""Exact immutable v4 NGS alignment-presentation package builder."""

from __future__ import annotations

from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable

import pyarrow as pa
import pyarrow.parquet as pq
import pysam
import rfc8785


CATALOG_SCHEMA = pa.schema([
    ("read_id", pa.string()),
    ("source_record_count", pa.uint32()),
    ("mapped_primary_count", pa.uint16()),
    ("supplementary_count", pa.uint16()),
    ("mapped_supplementary_count", pa.uint16()),
    ("secondary_count", pa.uint16()),
    ("unmapped_count", pa.uint16()),
    ("alignment_state", pa.string()),
    ("in_preview", pa.bool_()),
    ("preview_rank", pa.uint32()),
    ("length", pa.uint64()),
    ("mean_quality", pa.float64()),
    ("contig", pa.string()),
    ("start_1based", pa.uint64()),
    ("alignment_end_1based", pa.uint64()),
    ("strand", pa.string()),
    ("mapq", pa.uint16()),
    ("cigar", pa.string()),
    ("flags", pa.uint16()),
    ("unmapped", pa.bool_()),
    ("aligned_query_bases", pa.uint64()),
    ("aligned_reference_bases", pa.uint64()),
    ("inserted_bases", pa.uint64()),
    ("deleted_bases", pa.uint64()),
    ("skipped_reference_bases", pa.uint64()),
    ("clipped_bases", pa.uint64()),
    ("edit_distance", pa.uint64()),
    ("reference_substitution_count", pa.uint64()),
    ("reference_substitution_rate", pa.float64()),
    ("aligned_fraction", pa.float64()),
    ("clipped_fraction", pa.float64()),
    ("reference_disagreement_rate", pa.float64()),
    ("dorado_tag_parse_valid", pa.bool_()),
    ("dorado_tag_move_stride_samples", pa.uint32()),
    ("dorado_tag_emitted_bases", pa.uint64()),
    ("dorado_tag_start_sample", pa.uint64()),
    ("dorado_tag_end_sample", pa.uint64()),
    ("canonical_record_ordinal", pa.uint64()),
])

LOCATOR_SCHEMA = pa.schema([
    ("read_id", pa.string()),
    ("source_record_ordinal", pa.uint64()),
    ("bgzf_virtual_offset", pa.uint64()),
    ("record_class", pa.string()),
    ("contig", pa.string()),
    ("start_0based", pa.uint64()),
    ("end_0based_exclusive", pa.uint64()),
    ("flags", pa.uint16()),
    ("record_fingerprint_sha256", pa.string()),
])


def _canonical_tag_value(value: Any) -> Any:
    if isinstance(value, array):
        return {"subtype": value.typecode, "values": list(value)}
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    if isinstance(value, (list, tuple)):
        return [_canonical_tag_value(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite BAM optional tag")
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (str, int)) or value is None:
        return value
    raise ValueError("unencodable BAM optional tag")


def _canonical_json_string(value: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("unencodable canonical JSON string") from exc


def _canonical_json_key(value: str) -> bytes:
    try:
        return value.encode("utf-16-be")
    except UnicodeEncodeError as exc:
        raise ValueError("unencodable canonical JSON object key") from exc


def _canonical_json_bytes(value: Any) -> bytes:
    """Serialize the closed fingerprint payload exactly as RFC 8785."""

    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if type(value) is int:
        if not -(2**53) + 1 <= value <= 2**53 - 1:
            raise ValueError("integer exceeds safe canonical JSON domain")
        return str(value).encode("ascii")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite canonical JSON number")
        return rfc8785.dumps(value)
    if isinstance(value, str):
        return _canonical_json_string(value)
    if isinstance(value, (list, tuple)):
        return b"[" + b",".join(_canonical_json_bytes(item) for item in value) + b"]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical JSON object keys must be strings")
        items = sorted(value.items(), key=lambda item: _canonical_json_key(item[0]))
        return b"{" + b",".join(
            _canonical_json_string(key) + b":" + _canonical_json_bytes(item)
            for key, item in items
        ) + b"}"
    raise ValueError("unencodable canonical JSON value")


def _canonical_preencoded_object(fields: list[tuple[str, bytes]]) -> bytes:
    fields.sort(key=lambda item: _canonical_json_key(item[0]))
    return b"{" + b",".join(
        _canonical_json_string(key) + b":" + encoded
        for key, encoded in fields
    ) + b"}"


def _canonical_b_array_values(value: array) -> bytes:
    if value.typecode in {"b", "B", "h", "H", "i", "I"}:
        return json.dumps(
            value.tolist(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    if value.typecode == "f":
        return rfc8785.dumps(value.tolist())
    raise ValueError("unsupported BAM optional tag array subtype")


def _canonical_tag_value_bytes(value: Any) -> bytes:
    if isinstance(value, array):
        return _canonical_preencoded_object([
            ("subtype", _canonical_json_string(value.typecode)),
            ("values", _canonical_b_array_values(value)),
        ])
    return _canonical_json_bytes(_canonical_tag_value(value))


def alignment_record_fingerprint_bytes(
    record: pysam.AlignedSegment,
    typed_tags: list[tuple[str, Any, str]] | None = None,
) -> bytes:
    source_tags = (
        record.get_tags(with_value_type=True)
        if typed_tags is None
        else typed_tags
    )
    encoded_tags: list[tuple[str, str, bytes, bytes]] = []
    for tag, value, sam_type in source_tags:
        tag_text = str(tag)
        sam_type_text = str(sam_type)
        value_bytes = _canonical_tag_value_bytes(value)
        encoded_tags.append((
            tag_text,
            sam_type_text,
            value_bytes,
            b"[" + b",".join((
                _canonical_json_string(tag_text),
                _canonical_json_string(sam_type_text),
                value_bytes,
            )) + b"]",
        ))
    encoded_tags.sort(key=lambda item: (item[0], item[1], item[2]))
    optional_tags_bytes = b"[" + b",".join(item[3] for item in encoded_tags) + b"]"
    sequence = record.query_sequence
    qualities = record.query_qualities
    return _canonical_preencoded_object([
        ("query_name", _canonical_json_bytes(record.query_name)),
        ("flag", _canonical_json_bytes(int(record.flag))),
        ("reference_name", _canonical_json_bytes(record.reference_name)),
        ("reference_start", _canonical_json_bytes(
            None if record.reference_start < 0 else int(record.reference_start)
        )),
        ("mapping_quality", _canonical_json_bytes(int(record.mapping_quality))),
        ("cigar", _canonical_json_bytes(record.cigarstring)),
        ("mate_reference_name", _canonical_json_bytes(record.next_reference_name)),
        ("mate_reference_start", _canonical_json_bytes(
            None if record.next_reference_start < 0 else int(record.next_reference_start)
        )),
        ("template_length", _canonical_json_bytes(int(record.template_length))),
        ("sequence_sha256", _canonical_json_bytes(
            None if sequence is None else hashlib.sha256(sequence.encode("ascii")).hexdigest()
        )),
        ("quality_sha256", _canonical_json_bytes(
            None if qualities is None else hashlib.sha256(bytes(qualities)).hexdigest()
        )),
        ("optional_tags", optional_tags_bytes),
    ])


def alignment_record_fingerprint(
    record: pysam.AlignedSegment,
    typed_tags: list[tuple[str, Any, str]] | None = None,
) -> str:
    return hashlib.sha256(alignment_record_fingerprint_bytes(record, typed_tags)).hexdigest()


def _record_class(flags: int) -> str:
    if flags & 0x800:
        return "supplementary"
    if flags & 0x100:
        return "secondary"
    if flags & 0x4:
        return "unmapped"
    return "primary"


def _validate_read_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("empty BAM query name")
    encoded = value.encode("utf-8")
    if len(encoded) > 254 or any(byte < 0x20 or byte == 0x7F for byte in encoded):
        raise ValueError("unsafe BAM query name")
    return value


def _dorado_projection(typed_tags: list[tuple[str, Any, str]]) -> dict[str, Any]:
    empty = {
        "dorado_tag_parse_valid": False,
        "dorado_tag_move_stride_samples": None,
        "dorado_tag_emitted_bases": None,
        "dorado_tag_start_sample": None,
        "dorado_tag_end_sample": None,
    }
    moves = [(value, kind) for tag, value, kind in typed_tags if tag == "mv"]
    starts = [(value, kind) for tag, value, kind in typed_tags if tag == "ts"]
    ends = [(value, kind) for tag, value, kind in typed_tags if tag == "ns"]
    if len(moves) != 1 or len(starts) != 1 or len(ends) != 1:
        return empty
    move_value, move_kind = moves[0]
    start, start_kind = starts[0]
    end, end_kind = ends[0]
    if (
        not isinstance(move_value, array)
        or move_value.typecode != "b"
        or move_kind not in {"B", "Bc"}
        or start_kind != "i"
        or end_kind != "i"
        or isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or len(move_value) < 2
    ):
        return empty
    stride = int(move_value[0])
    move_bytes = move_value.tobytes()
    value_bytes = move_bytes[1:]
    if (
        stride < 1
        or not value_bytes.isascii()
        or start < 0
        or end <= start
        or stride > 0xFFFFFFFF
        or start > 0xFFFFFFFFFFFFFFFF
        or end > 0xFFFFFFFFFFFFFFFF
    ):
        return empty
    emitted = sum(value_bytes)
    if emitted > 0xFFFFFFFFFFFFFFFF:
        return empty
    return {
        "dorado_tag_parse_valid": True,
        "dorado_tag_move_stride_samples": stride,
        "dorado_tag_emitted_bases": emitted,
        "dorado_tag_start_sample": start,
        "dorado_tag_end_sample": end,
    }


def _catalog_record(
    record: pysam.AlignedSegment,
    *,
    ordinary_unmapped: bool,
    typed_tags: list[tuple[str, Any, str]] | None = None,
) -> dict[str, Any]:
    from services import ngs_alignment_sessions as service

    sequence = record.query_sequence
    qualities = record.query_qualities
    length = None if sequence is None else len(sequence)
    tags = record.get_tags(with_value_type=True) if typed_tags is None else typed_tags
    optional_fields = [f"NM:{kind}:{value}" for tag, value, kind in tags if tag == "NM"]
    cigar = record.cigarstring
    mapped = not bool(record.flag & 0x4)
    metrics = service._alignment_quality_metrics(cigar or "*", optional_fields, length)
    return {
        "length": length,
        "mean_quality": (
            None if qualities is None or len(qualities) == 0
            else sum(int(value) for value in qualities) / len(qualities)
        ),
        "contig": record.reference_name if mapped else None,
        "start_1based": int(record.reference_start) + 1 if mapped and record.reference_start >= 0 else None,
        "alignment_end_1based": (
            int(record.reference_end) if mapped and record.reference_end is not None else None
        ),
        "strand": ("-" if record.is_reverse else "+") if mapped else None,
        "mapq": int(record.mapping_quality) if mapped else None,
        "cigar": cigar if mapped else None,
        "flags": int(record.flag),
        "unmapped": ordinary_unmapped,
        **metrics,
        **(_dorado_projection(tags) if mapped else {
            "dorado_tag_parse_valid": False,
            "dorado_tag_move_stride_samples": None,
            "dorado_tag_emitted_bases": None,
            "dorado_tag_start_sample": None,
            "dorado_tag_end_sample": None,
        }),
    }


def _source_record_projection(
    record: pysam.AlignedSegment,
    *,
    ordinary_unmapped: bool,
    include_catalog: bool = True,
) -> tuple[str, dict[str, Any] | None]:
    typed_tags = record.get_tags(with_value_type=True)
    fingerprint = alignment_record_fingerprint(record, typed_tags)
    catalog = (
        _catalog_record(
            record,
            ordinary_unmapped=ordinary_unmapped,
            typed_tags=typed_tags,
        )
        if include_catalog
        else None
    )
    return fingerprint, catalog


def _write_parquet(
    path: Path,
    rows: list[dict[str, Any]],
    schema: pa.Schema,
    abort_check: Callable[[], None] | None = None,
) -> None:
    checkpoint = abort_check or (lambda: None)
    dictionaries = [
        name for name in ("alignment_state", "strand", "record_class")
        if name in schema.names
    ]
    with pq.ParquetWriter(
        path,
        schema,
        compression="zstd",
        version="2.6",
        use_dictionary=dictionaries,
        write_statistics=True,
    ) as writer:
        if not rows:
            checkpoint()
            writer.write_table(pa.Table.from_pylist([], schema=schema))
        for start in range(0, len(rows), 4096):
            checkpoint()
            writer.write_table(pa.Table.from_pylist(rows[start:start + 4096], schema=schema))
    checkpoint()
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.iterdir() if item.is_file())


def _add_coverage_block(
    boundary: list[int],
    difference: list[int],
    block_start: int,
    block_end: int,
    bin_width: int,
) -> None:
    """Add one half-open reference block in O(1), retaining exact edge bases."""

    if block_end <= block_start:
        return
    first = block_start // bin_width
    last = (block_end - 1) // bin_width
    if first == last:
        boundary[first] += block_end - block_start
        return
    boundary[first] += (first + 1) * bin_width - block_start
    boundary[last] += block_end - last * bin_width
    if first + 1 < last:
        difference[first + 1] += bin_width
        difference[last] -= bin_width


def _materialize_coverage(boundary: list[int], difference: list[int]) -> list[int]:
    running = 0
    coverage: list[int] = []
    for bin_index, edge_bases in enumerate(boundary):
        running += difference[bin_index]
        coverage.append(edge_bases + running)
    return coverage


def _coverage_for_blocks(
    reference_length: int,
    bin_width: int,
    blocks: list[tuple[int, int]],
) -> list[int]:
    bin_count = math.ceil(reference_length / bin_width)
    boundary = [0] * bin_count
    difference = [0] * (bin_count + 1)
    for block_start, block_end in blocks:
        _add_coverage_block(boundary, difference, block_start, block_end, bin_width)
    return _materialize_coverage(boundary, difference)


def _verify_locators(
    source_path: Path,
    rows: list[dict[str, Any]],
    abort_check: Callable[[], None] | None = None,
) -> None:
    checkpoint = abort_check or (lambda: None)
    with pysam.AlignmentFile(source_path, "rb") as source:
        for row in rows:
            checkpoint()
            source.seek(int(row["bgzf_virtual_offset"]))
            try:
                record = next(source)
            except StopIteration as exc:
                raise ValueError("BAM locator did not reopen a record") from exc
            if (
                record.query_name != row["read_id"]
                or int(record.flag) != row["flags"]
                or alignment_record_fingerprint(record) != row["record_fingerprint_sha256"]
            ):
                raise ValueError("BAM locator fingerprint mismatch")


def verify_package_against_source(
    package: dict[str, Any],
    source_path: Path,
    *,
    abort_check: Callable[[], None] | None = None,
) -> None:
    """Re-derive package semantics from the canonical BAM before adoption."""

    from services import ngs_alignment_sessions as service

    checkpoint = abort_check or (lambda: None)
    manifest = package.get("manifest")
    locator_path = package.get("locators_path")
    catalog_path = package.get("catalog_path")
    preview_path = package.get("bam_path")
    coverage_path = package.get("coverage_path")
    if (
        not isinstance(locator_path, Path)
        or not isinstance(catalog_path, Path)
        or not isinstance(preview_path, Path)
        or not isinstance(coverage_path, Path)
        or not isinstance(manifest, dict)
    ):
        raise service.AlignmentPresentationFailure(
            "integrity_mismatch", message="alignment presentation package is incomplete"
        )
    try:
        authority = manifest["authority"]
        if not isinstance(authority, dict):
            raise ValueError("missing nested authority")
        authority_pairs = {
            "job_id": "job_id", "session_id": "session_id", "mode": "mode",
            "source_authority_sha256": "source_authority_sha256",
            "source_manifest_sha256": "source_manifest_sha256",
            "source_artifact_set_sha256": "artifact_set_sha256",
            "source_alignment_pair_sha256": "alignment_pair_sha256",
            "source_reference_sha256": "source_reference_sha256",
            "source_alignment_sha256": "source_alignment_sha256",
            "source_alignment_size_bytes": "source_alignment_size_bytes",
            "source_index_sha256": "source_index_sha256",
            "source_index_size_bytes": "source_index_size_bytes",
            "source_alignment_relative_path": "source_alignment_relative_path",
            "source_index_relative_path": "source_index_relative_path",
            "source_identity": "source_identity", "source_index_identity": "source_index_identity",
            "creation_revision": "creation_revision", "creation_source_tree": "creation_source_tree",
            "policy": "policy",
        }
        if (
            any(authority[key] != manifest[top] for key, top in authority_pairs.items())
            or manifest["package_manifest_sha256"] != authority["source_manifest_sha256"]
            or manifest["catalog"]["schema"] != authority["catalog_schema"]
            or manifest["locators"]["schema"] != authority["locator_schema"]
            or manifest["locators"]["fingerprint_policy"] != authority["fingerprint_policy"]
        ):
            raise ValueError("top-level manifest authority diverges")

        handle = service._open_regular_file_no_symlinks(source_path)
        try:
            service._verify_descriptor(
                handle,
                int(authority["source_alignment_size_bytes"]),
                str(authority["source_alignment_sha256"]),
            )
            canonical_path = Path(service._descriptor_path(handle.fileno()))
            expected_by_offset: dict[int, dict[str, Any]] = {}
            records_by_read: dict[str, list[tuple[dict[str, Any], pysam.AlignedSegment]]] = {}
            with pysam.AlignmentFile(canonical_path, "rb") as source:
                references = list(zip(source.references, source.lengths, strict=True))
                bin_width = int(manifest["coverage_bin_width"])
                coverage_boundary = {
                    name: [0] * math.ceil(length / bin_width) for name, length in references
                }
                coverage_difference = {
                    name: [0] * (math.ceil(length / bin_width) + 1)
                    for name, length in references
                }
                iterator = iter(source.fetch(until_eof=True))
                ordinal = 0
                while True:
                    checkpoint()
                    offset = int(source.tell())
                    try:
                        record = next(iterator)
                    except StopIteration:
                        break
                    flags = int(record.flag)
                    mapped = not bool(flags & 0x4)
                    expected = {
                        "read_id": _validate_read_id(record.query_name),
                        "source_record_ordinal": ordinal,
                        "bgzf_virtual_offset": offset,
                        "record_class": _record_class(flags),
                        "contig": record.reference_name if mapped else None,
                        "start_0based": int(record.reference_start) if mapped and record.reference_start >= 0 else None,
                        "end_0based_exclusive": int(record.reference_end) if mapped and record.reference_end is not None else None,
                        "flags": flags,
                        "record_fingerprint_sha256": alignment_record_fingerprint(record),
                    }
                    expected_by_offset[offset] = expected
                    records_by_read.setdefault(expected["read_id"], []).append((expected, record))
                    if expected["record_class"] == "primary" and expected["contig"] is not None:
                        for block_start, block_end in record.get_blocks():
                            _add_coverage_block(
                                coverage_boundary[expected["contig"]],
                                coverage_difference[expected["contig"]],
                                block_start,
                                block_end,
                                bin_width,
                            )
                    ordinal += 1

            locator_rows = pq.read_table(locator_path, schema=LOCATOR_SCHEMA).to_pylist()
            if len(locator_rows) != len(expected_by_offset) or any(
                expected_by_offset.get(int(row["bgzf_virtual_offset"])) != row
                for row in locator_rows
            ):
                raise ValueError("locator semantics diverge from canonical source")

            catalog_rows = pq.read_table(catalog_path, schema=CATALOG_SCHEMA).to_pylist()
            expected_catalog: list[dict[str, Any]] = []
            for read_id in sorted(records_by_read, key=lambda value: value.encode("utf-8")):
                checkpoint()
                entries = records_by_read[read_id]
                counts = {
                    kind: sum(entry[0]["record_class"] == kind for entry in entries)
                    for kind in ("primary", "supplementary", "secondary", "unmapped")
                }
                mapped_supplementary = sum(
                    entry[0]["record_class"] == "supplementary" and not (entry[0]["flags"] & 0x4)
                    for entry in entries
                )
                state = (
                    "mapped_primary" if counts["primary"] == 1
                    else "ambiguous_primary" if counts["primary"] > 1
                    else "unmapped" if counts["unmapped"] > 0
                    else "no_primary"
                )
                canonical = None
                if state == "mapped_primary":
                    canonical = next(entry for entry in entries if entry[0]["record_class"] == "primary")
                elif state == "unmapped":
                    canonical = next(entry for entry in entries if entry[0]["record_class"] == "unmapped")
                values: dict[str, Any] = {
                    name: None for name in CATALOG_SCHEMA.names
                    if name not in {
                        "read_id", "source_record_count", "mapped_primary_count", "supplementary_count",
                        "mapped_supplementary_count", "secondary_count", "unmapped_count", "alignment_state",
                        "in_preview", "preview_rank", "unmapped", "dorado_tag_parse_valid",
                    }
                }
                if canonical is not None:
                    values.update(_catalog_record(
                        canonical[1], ordinary_unmapped=canonical[0]["record_class"] == "unmapped"
                    ))
                else:
                    values.update({"unmapped": False, "dorado_tag_parse_valid": False})
                expected_catalog.append({
                    "read_id": read_id, "source_record_count": len(entries),
                    "mapped_primary_count": counts["primary"],
                    "supplementary_count": counts["supplementary"],
                    "mapped_supplementary_count": mapped_supplementary,
                    "secondary_count": counts["secondary"], "unmapped_count": counts["unmapped"],
                    "alignment_state": state, "in_preview": False, "preview_rank": None,
                    **values,
                    "canonical_record_ordinal": canonical[0]["source_record_ordinal"] if canonical else None,
                })

            observed_by_id = {row["read_id"]: row for row in catalog_rows}
            for expected in expected_catalog:
                observed = observed_by_id.get(expected["read_id"])
                if observed is None or any(
                    observed[key] != value for key, value in expected.items()
                    if key not in {"in_preview", "preview_rank"}
                ):
                    raise ValueError("catalog semantics diverge from canonical source")

            target = int(manifest["policy"]["target_reads"])
            candidates = [row for row in expected_catalog if row["alignment_state"] == "mapped_primary"]
            lengths = dict(references)
            tile_widths = {name: max(1, math.ceil(length / 64)) for name, length in references}
            strata: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
            bam_sha = str(authority["source_alignment_sha256"])
            for row in candidates:
                key = (
                    str(row["contig"]),
                    max(0, int(row["start_1based"]) - 1) // tile_widths[str(row["contig"])],
                    "reverse" if row["strand"] == "-" else "forward",
                )
                strata.setdefault(key, []).append(row)
            for rows in strata.values():
                rows.sort(key=lambda row: (service._rank_read(bam_sha, row["read_id"]), row["read_id"].encode()))
            keys = sorted(strata)
            quotas = {key: int(target >= len(keys)) for key in keys}
            remaining = target - sum(quotas.values()) if target >= len(keys) else target
            capacities = {key: len(strata[key]) - quotas[key] for key in keys}
            capacity = sum(capacities.values())
            fractions = []
            if remaining and capacity:
                for key in keys:
                    exact = remaining * capacities[key] / capacity
                    whole = math.floor(exact)
                    quotas[key] += whole
                    fractions.append((exact - whole, key))
                leftover = remaining - sum(math.floor(remaining * capacities[key] / capacity) for key in keys)
                for _fraction, key in sorted(fractions, key=lambda item: (-item[0], item[1]))[:leftover]:
                    quotas[key] += 1
            selected = {
                row["read_id"] for key in keys for row in strata[key][:quotas[key]]
            }
            while sum(1 + observed_by_id[read_id]["mapped_supplementary_count"] for read_id in selected) > service.ALIGNMENT_PREVIEW_MAX_RECORDS:
                selected.remove(max(selected, key=lambda value: service._rank_read(bam_sha, value)))
            retained = sorted(selected, key=lambda value: service._rank_read(bam_sha, value))
            if {
                row["read_id"] for row in catalog_rows if row["in_preview"]
            } != set(retained) or any(
                observed_by_id[read_id]["preview_rank"] != rank for rank, read_id in enumerate(retained)
            ):
                raise ValueError("catalog preview membership diverges")

            expected_preview = [
                entry["record_fingerprint_sha256"]
                for entry in sorted(expected_by_offset.values(), key=lambda row: row["source_record_ordinal"])
                if entry["read_id"] in selected and (
                    entry["record_class"] == "primary"
                    or entry["record_class"] == "supplementary" and not (entry["flags"] & 0x4)
                )
            ]
            with pysam.AlignmentFile(preview_path, "rb") as preview:
                observed_preview = [
                    alignment_record_fingerprint(record) for record in preview.fetch(until_eof=True)
                ]
            if observed_preview != expected_preview:
                raise ValueError("preview records diverge from canonical source")

            coverage = {
                name: _materialize_coverage(
                    coverage_boundary[name], coverage_difference[name]
                )
                for name, _length in references
            }
            lines = []
            for contig, length in references:
                for bin_index, aligned_bases in enumerate(coverage[contig]):
                    if aligned_bases:
                        start = bin_index * bin_width
                        end = min(lengths[contig], start + bin_width)
                        lines.append(f"{contig}\t{start}\t{end}\t{aligned_bases / (end - start):.6f}\n")
            if coverage_path.read_bytes() != "".join(lines).encode():
                raise ValueError("coverage semantics diverge from canonical source")
        finally:
            handle.close()
    except (KeyError, OSError, TypeError, ValueError, pa.ArrowException) as exc:
        raise service.AlignmentPresentationFailure(
            "integrity_mismatch", message="alignment presentation semantic authority is invalid"
        ) from exc


def build_alignment_presentation_v5(
    bam: Path,
    *,
    bam_sha256: str,
    bam_size_bytes: int,
    index: Path,
    index_sha256: str,
    index_size_bytes: int,
    source_manifest_sha256: str,
    job_id: str,
    session_id: str,
    mode: str,
    cache_root: Path,
    artifact_set_sha256: str | None,
    alignment_pair_sha256: str | None,
    source_authority_sha256: str | None,
    source_reference_sha256: str | None,
    source_alignment_relative_path: str,
    source_index_relative_path: str,
    expected_manifest_sha256: str | None,
    presentation_namespace_root: Path,
    target_reads: int,
    max_output_bytes: int,
    max_coverage_bins: int,
    max_seconds: float,
    abort_check: Callable[[], None] | None,
) -> dict[str, Any]:
    from services import ngs_alignment_sessions as service

    checkpoint = abort_check or (lambda: None)
    policy = {
        "id": service.ALIGNMENT_PREVIEW_POLICY,
        "version": service.ALIGNMENT_PRESENTATION_POLICY_VERSION,
        "target_reads": target_reads,
        "max_preview_records": service.ALIGNMENT_PREVIEW_MAX_RECORDS,
        "max_preview_bytes": max_output_bytes,
        "max_build_seconds": max_seconds,
        "max_catalog_bytes": service.ALIGNMENT_CATALOG_MAX_BYTES,
        "max_locator_bytes": service.ALIGNMENT_LOCATOR_MAX_BYTES,
        "max_package_bytes": service.ALIGNMENT_PRESENTATION_ENTRY_MAX_BYTES,
        "max_temporary_bytes": service.ALIGNMENT_PRESENTATION_WORK_MAX_BYTES,
    }
    if (
        not 1 <= target_reads <= service.ALIGNMENT_PREVIEW_TARGET_READS
        or not 1 <= max_output_bytes <= service.ALIGNMENT_PREVIEW_MAX_BYTES
        or not 1 <= max_coverage_bins <= service.ALIGNMENT_COVERAGE_MAX_BINS
        or not math.isfinite(max_seconds)
        or not 0 < max_seconds <= service.ALIGNMENT_PRESENTATION_MAX_SECONDS
        or mode not in service.SESSION_MODES
        or any(
            value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (
                source_manifest_sha256,
                source_authority_sha256,
                source_reference_sha256,
                artifact_set_sha256,
                alignment_pair_sha256,
            )
        )
    ):
        raise service.AlignmentPresentationFailure(
            "source_invalid", message="alignment presentation authority or policy is invalid"
        )
    checkpoint()
    creation_revision, creation_source_tree = service._creation_authority()
    admitted_bam_identity = service.source_stat_identity(bam)
    admitted_bai_identity = service.source_stat_identity(index)
    authority = {
        "schema": "bms.ngs.alignment-presentation-authority.v4",
        "job_id": job_id,
        "session_id": session_id,
        "mode": mode,
        "source_authority_sha256": source_authority_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "source_artifact_set_sha256": artifact_set_sha256,
        "source_alignment_pair_sha256": alignment_pair_sha256,
        "source_reference_sha256": source_reference_sha256,
        "source_alignment_sha256": bam_sha256,
        "source_alignment_size_bytes": bam_size_bytes,
        "source_index_sha256": index_sha256,
        "source_index_size_bytes": index_size_bytes,
        "source_alignment_relative_path": source_alignment_relative_path,
        "source_index_relative_path": source_index_relative_path,
        "source_identity": service._canonical_stat_identity(admitted_bam_identity),
        "source_index_identity": service._canonical_stat_identity(admitted_bai_identity),
        "catalog_schema": "bms.ngs.read-catalog.v1",
        "locator_schema": "bms.ngs.read-record-locators.v1",
        "fingerprint_policy": "bms.ngs.alignment-record-fingerprint.v1",
        "creation_revision": creation_revision,
        "creation_source_tree": creation_source_tree,
        "policy": policy,
    }
    cache_key = hashlib.sha256(rfc8785.dumps(authority)).hexdigest()
    namespace = presentation_namespace_root
    parts = namespace.parts
    if not (len(parts) == 5 and parts[1:4] == ("proc", "self", "fd") and parts[4].isdigit()):
        raise service.AlignmentSessionError("presentation namespace is not pinned")
    namespace_fd = int(parts[4])
    destination = namespace / cache_key
    lock_fd = os.open(
        ".generation.lock",
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o640,
        dir_fd=namespace_fd,
    )
    with os.fdopen(lock_fd, "a+b") as lock:
        import fcntl

        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise service.AlignmentSessionError(
                "alignment presentation generation is already in progress"
            ) from exc
        protected = service._presentation_names_for_manifest(namespace, expected_manifest_sha256)
        service._cleanup_presentation_namespace(namespace, namespace_fd, protected_names=protected)
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink():
                raise service.AlignmentSessionError("alignment presentation destination is unsafe")
            with service.open_presentation_authority_root(destination, create=False) as pinned:
                cached = service._load_derived_package(
                    pinned,
                    expected_authority_sha256=cache_key,
                    expected_manifest_sha256=expected_manifest_sha256,
                )
            if cached is not None:
                return cached
            service._remove_locus_transient(namespace_fd, cache_key)
        service._cleanup_presentation_namespace(
            namespace,
            namespace_fd,
            protected_names=protected,
            reserve_bytes=service.ALIGNMENT_PRESENTATION_WORK_MAX_BYTES,
            reserve_entries=1,
        )
        service._remove_locus_transient(namespace_fd, ".generation.tmp")
        os.mkdir(".generation.tmp", mode=0o750, dir_fd=namespace_fd)
        temporary = namespace / ".generation.tmp"
        database: sqlite3.Connection | None = None
        source_handle = index_handle = None
        deadline = time.monotonic() + max_seconds
        def stage_checkpoint() -> None:
            checkpoint()
            if time.monotonic() > deadline:
                raise service.AlignmentPresentationFailure(
                    "build_timeout", message="alignment presentation time limit exceeded"
                )
        renamed = False
        try:
            stage_checkpoint()
            source_handle = service._open_regular_file_no_symlinks(bam)
            index_handle = service._open_regular_file_no_symlinks(index)
            source_identity = service._verify_descriptor(source_handle, bam_size_bytes, bam_sha256)
            index_identity = service._verify_descriptor(index_handle, index_size_bytes, index_sha256)
            if source_identity != admitted_bam_identity or index_identity != admitted_bai_identity:
                raise service.AlignmentPresentationFailure(
                    "source_invalid", message="alignment presentation source changed"
                )
            database_path = temporary / "presentation-build.sqlite3"
            database = sqlite3.connect(database_path)
            database.execute("PRAGMA page_size=4096")
            database.execute(
                f"PRAGMA max_page_count={service.ALIGNMENT_PRESENTATION_WORK_MAX_BYTES // 4096}"
            )
            database.execute("PRAGMA journal_mode=OFF")
            database.execute("PRAGMA synchronous=OFF")
            database.execute("PRAGMA temp_store=FILE")
            database.execute(
                "CREATE TABLE records (read_id TEXT COLLATE BINARY NOT NULL, ordinal INTEGER NOT NULL, "
                "virtual_offset INTEGER NOT NULL, record_class TEXT NOT NULL, contig TEXT, start0 INTEGER, "
                "end0 INTEGER, flags INTEGER NOT NULL, fingerprint TEXT NOT NULL, catalog_json TEXT, "
                "PRIMARY KEY(read_id, ordinal)) WITHOUT ROWID"
            )
            references: list[tuple[str, int]]
            coverage_boundary: dict[str, list[int]]
            coverage_difference: dict[str, list[int]]
            with pysam.AlignmentFile(service._descriptor_path(source_handle.fileno()), "rb") as source:
                header = source.header.to_dict()
                references = list(zip(source.references, source.lengths, strict=True))
                bin_width = max(
                    1,
                    math.ceil(sum(length for _name, length in references) / max_coverage_bins),
                )
                tile_widths = {name: max(1, math.ceil(length / 64)) for name, length in references}
                coverage_boundary = {
                    name: [0] * math.ceil(length / bin_width) for name, length in references
                }
                coverage_difference = {
                    name: [0] * (math.ceil(length / bin_width) + 1)
                    for name, length in references
                }
                iterator = iter(source.fetch(until_eof=True))
                ordinal = 0
                while True:
                    stage_checkpoint()
                    virtual_offset = int(source.tell())
                    try:
                        record = next(iterator)
                    except StopIteration:
                        break
                    read_id = _validate_read_id(record.query_name)
                    flags = int(record.flag)
                    record_class = _record_class(flags)
                    mapped = not bool(flags & 0x4)
                    contig = record.reference_name if mapped else None
                    start0 = int(record.reference_start) if mapped and record.reference_start >= 0 else None
                    end0 = int(record.reference_end) if mapped and record.reference_end is not None else None
                    fingerprint, catalog = _source_record_projection(
                        record,
                        ordinary_unmapped=record_class == "unmapped",
                        include_catalog=record_class in {"primary", "unmapped"},
                    )
                    catalog_json = None
                    if catalog is not None:
                        catalog_json = json.dumps(
                            catalog,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                    database.execute(
                        "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            read_id,
                            ordinal,
                            virtual_offset,
                            record_class,
                            contig,
                            start0,
                            end0,
                            flags,
                            fingerprint,
                            catalog_json,
                        ),
                    )
                    if record_class == "primary" and contig is not None:
                        for block_start, block_end in record.get_blocks():
                            _add_coverage_block(
                                coverage_boundary[contig],
                                coverage_difference[contig],
                                block_start,
                                block_end,
                                bin_width,
                            )
                    ordinal += 1
            database.commit()
            coverage = {
                name: _materialize_coverage(
                    coverage_boundary[name], coverage_difference[name]
                )
                for name, _length in references
            }
            stage_checkpoint()
            aggregate_rows = database.execute(
                "SELECT read_id, COUNT(*), "
                "SUM(record_class='primary'), SUM(record_class='supplementary'), "
                "SUM(record_class='supplementary' AND (flags & 4)=0), "
                "SUM(record_class='secondary'), SUM(record_class='unmapped') "
                "FROM records GROUP BY read_id ORDER BY CAST(read_id AS BLOB)"
            ).fetchall()
            catalog_rows: list[dict[str, Any]] = []
            states = {"mapped_primary": 0, "unmapped": 0, "ambiguous_primary": 0, "no_primary": 0}
            for aggregate in aggregate_rows:
                stage_checkpoint()
                read_id = str(aggregate[0])
                total, primary, supplementary, mapped_supplementary, secondary, unmapped = map(
                    int, aggregate[1:]
                )
                if total > 0xFFFFFFFF or any(
                    value > 0xFFFF
                    for value in (primary, supplementary, mapped_supplementary, secondary, unmapped)
                ):
                    raise service.AlignmentPresentationFailure(
                        "resource_limit", message="logical read record counts exceed catalog types"
                    )
                if primary == 1:
                    state = "mapped_primary"
                    canonical_class = "primary"
                elif primary > 1:
                    state = "ambiguous_primary"
                    canonical_class = None
                elif unmapped > 0:
                    state = "unmapped"
                    canonical_class = "unmapped"
                else:
                    state = "no_primary"
                    canonical_class = None
                states[state] += 1
                canonical_ordinal = None
                values: dict[str, Any] = {
                    name: None
                    for name in CATALOG_SCHEMA.names
                    if name not in {
                        "read_id", "source_record_count", "mapped_primary_count",
                        "supplementary_count", "mapped_supplementary_count", "secondary_count",
                        "unmapped_count", "alignment_state", "in_preview", "preview_rank",
                        "unmapped", "dorado_tag_parse_valid",
                    }
                }
                if canonical_class is not None:
                    canonical = database.execute(
                        "SELECT ordinal, catalog_json FROM records WHERE read_id=? AND record_class=? "
                        "ORDER BY ordinal LIMIT 1",
                        (read_id, canonical_class),
                    ).fetchone()
                    canonical_ordinal = int(canonical[0])
                    values.update(json.loads(str(canonical[1])))
                else:
                    values["unmapped"] = False
                    values["dorado_tag_parse_valid"] = False
                catalog_rows.append({
                    "read_id": read_id,
                    "source_record_count": total,
                    "mapped_primary_count": primary,
                    "supplementary_count": supplementary,
                    "mapped_supplementary_count": mapped_supplementary,
                    "secondary_count": secondary,
                    "unmapped_count": unmapped,
                    "alignment_state": state,
                    "in_preview": False,
                    "preview_rank": None,
                    **values,
                    "canonical_record_ordinal": canonical_ordinal,
                })
            candidate_rows = [row for row in catalog_rows if row["alignment_state"] == "mapped_primary"]
            strata: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
            for row in candidate_rows:
                contig = str(row["contig"])
                strand = "reverse" if row["strand"] == "-" else "forward"
                stratum = (
                    contig,
                    max(0, int(row["start_1based"]) - 1) // tile_widths[contig],
                    strand,
                )
                strata.setdefault(stratum, []).append(row)
            for rows in strata.values():
                rows.sort(key=lambda row: (service._rank_read(bam_sha256, row["read_id"]), row["read_id"].encode("utf-8")))
            ordered_strata = sorted(strata)
            quotas = {stratum: 0 for stratum in ordered_strata}
            if target_reads >= len(ordered_strata):
                quotas = {stratum: 1 for stratum in ordered_strata}
                remaining = target_reads - len(ordered_strata)
            else:
                remaining = target_reads
            capacities = {key: len(strata[key]) - quotas[key] for key in ordered_strata}
            capacity = sum(capacities.values())
            fractions: list[tuple[float, tuple[str, int, str]]] = []
            if remaining and capacity:
                for key in ordered_strata:
                    exact = remaining * capacities[key] / capacity
                    whole = math.floor(exact)
                    quotas[key] += whole
                    fractions.append((exact - whole, key))
                leftover = remaining - sum(math.floor(remaining * capacities[key] / capacity) for key in ordered_strata)
                for _fraction, key in sorted(fractions, key=lambda item: (-item[0], item[1]))[:leftover]:
                    quotas[key] += 1
            selected = {
                row["read_id"]
                for key in ordered_strata
                for row in strata[key][:quotas[key]]
            }
            counts = {row["read_id"]: 1 + int(row["mapped_supplementary_count"]) for row in candidate_rows}
            while sum(counts[read_id] for read_id in selected) > service.ALIGNMENT_PREVIEW_MAX_RECORDS:
                selected.remove(max(selected, key=lambda value: service._rank_read(bam_sha256, value)))
            retained_ids = sorted(selected, key=lambda value: service._rank_read(bam_sha256, value))
            preview_path = temporary / "preview.bam"
            while True:
                stage_checkpoint()
                try:
                    selected_record_count = service._write_bam_for_ids_bounded(
                        preview_path,
                        Path(service._descriptor_path(source_handle.fileno())),
                        retained_ids,
                        byte_limit=max_output_bytes,
                        deadline=deadline,
                        label="alignment presentation",
                        include_supplementary=True,
                        abort_check=stage_checkpoint,
                    )
                    break
                except service._AlignmentDerivativeByteLimit:
                    if not retained_ids:
                        raise service.AlignmentPresentationFailure(
                            "resource_limit", message="alignment preview byte ceiling is too small"
                        )
                    retained_ids.pop()
            stage_checkpoint()
            expected_preview_records = sum(counts[read_id] for read_id in retained_ids)
            if selected_record_count != expected_preview_records:
                raise service.AlignmentPresentationFailure(
                    "integrity_mismatch", message="preview record membership mismatch"
                )
            service._index_bam_with_deadline(
                preview_path,
                deadline=deadline,
                label="alignment presentation",
                byte_limit=service.ALIGNMENT_PREVIEW_INDEX_MAX_BYTES,
            )
            stage_checkpoint()
            preview_index = Path(f"{preview_path}.bai")
            if preview_index.stat().st_size > service.ALIGNMENT_PREVIEW_INDEX_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment preview index byte ceiling exceeded"
                )
            retained = set(retained_ids)
            rank_by_id = {read_id: rank for rank, read_id in enumerate(retained_ids)}
            for row in catalog_rows:
                row["in_preview"] = row["read_id"] in retained
                row["preview_rank"] = rank_by_id.get(row["read_id"])
            locator_rows = [
                {
                    "read_id": str(row[0]),
                    "source_record_ordinal": int(row[1]),
                    "bgzf_virtual_offset": int(row[2]),
                    "record_class": str(row[3]),
                    "contig": row[4],
                    "start_0based": row[5],
                    "end_0based_exclusive": row[6],
                    "flags": int(row[7]),
                    "record_fingerprint_sha256": str(row[8]),
                }
                for row in database.execute(
                    "SELECT read_id, ordinal, virtual_offset, record_class, contig, start0, end0, "
                    "flags, fingerprint FROM records ORDER BY CAST(read_id AS BLOB), ordinal"
                )
            ]
            catalog_path = temporary / "read-catalog.parquet"
            locator_path = temporary / "read-record-locators.parquet"
            stage_checkpoint()
            _write_parquet(
                catalog_path, catalog_rows, CATALOG_SCHEMA, abort_check=stage_checkpoint
            )
            stage_checkpoint()
            _write_parquet(
                locator_path, locator_rows, LOCATOR_SCHEMA, abort_check=stage_checkpoint
            )
            stage_checkpoint()
            if (
                pq.read_table(catalog_path, schema=CATALOG_SCHEMA).to_pylist() != catalog_rows
                or pq.read_table(locator_path, schema=LOCATOR_SCHEMA).to_pylist() != locator_rows
            ):
                raise service.AlignmentPresentationFailure(
                    "integrity_mismatch",
                    message="alignment presentation Parquet round-trip mismatch",
                )
            stage_checkpoint()
            if _directory_bytes(temporary) > service.ALIGNMENT_PRESENTATION_WORK_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment presentation temporary byte ceiling exceeded"
                )
            if catalog_path.stat().st_size > service.ALIGNMENT_CATALOG_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment read catalog byte ceiling exceeded"
                )
            if locator_path.stat().st_size > service.ALIGNMENT_LOCATOR_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment locator byte ceiling exceeded"
                )
            coverage_path = temporary / "full-source-primary.coverage.bedgraph"
            with coverage_path.open("w", encoding="utf-8", newline="\n") as output:
                for contig, length in references:
                    for bin_index, aligned_bases in enumerate(coverage[contig]):
                        stage_checkpoint()
                        if aligned_bases:
                            start = bin_index * bin_width
                            end = min(length, start + bin_width)
                            output.write(
                                f"{contig}\t{start}\t{end}\t{aligned_bases / (end - start):.6f}\n"
                            )
                output.flush()
                os.fsync(output.fileno())
            stage_checkpoint()
            if coverage_path.stat().st_size > service.ALIGNMENT_COVERAGE_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment coverage byte ceiling exceeded"
                )
            database.close()
            database = None
            database_path.unlink()
            paths = {
                "bam": preview_path,
                "index": preview_index,
                "coverage": coverage_path,
                "catalog": catalog_path,
                "locators": locator_path,
            }
            outputs = {
                key: {
                    "filename": path.name,
                    "sha256": service._sha256_file_and_size(path)[0],
                    "size_bytes": service._sha256_file_and_size(path)[1],
                }
                for key, path in paths.items()
            }
            stage_checkpoint()
            primary_record_count = sum(int(row["mapped_primary_count"]) for row in catalog_rows)
            manifest = {
                "schema": "bms.ngs.alignment-presentation-manifest.v4",
                "authority_sha256": cache_key,
                "authority": authority,
                "job_id": job_id,
                "session_id": session_id,
                "mode": mode,
                "source_authority_sha256": source_authority_sha256,
                "source_manifest_sha256": source_manifest_sha256,
                "package_manifest_sha256": source_manifest_sha256,
                "artifact_set_sha256": artifact_set_sha256,
                "alignment_pair_sha256": alignment_pair_sha256,
                "source_reference_sha256": source_reference_sha256,
                "creation_revision": creation_revision,
                "creation_source_tree": creation_source_tree,
                "source_alignment_sha256": bam_sha256,
                "source_alignment_size_bytes": bam_size_bytes,
                "source_index_sha256": index_sha256,
                "source_index_size_bytes": index_size_bytes,
                "source_alignment_relative_path": source_alignment_relative_path,
                "source_index_relative_path": source_index_relative_path,
                "source_identity": service._canonical_stat_identity(source_identity),
                "source_index_identity": service._canonical_stat_identity(index_identity),
                "policy": policy,
                "runtime": {"pysam_version": pysam.__version__, "pyarrow_version": pa.__version__},
                "source_logical_read_count": len(catalog_rows),
                "source_alignment_record_count": len(locator_rows),
                "source_record_counts": {
                    "mapped_primary": primary_record_count,
                    "supplementary": sum(int(row["supplementary_count"]) for row in catalog_rows),
                    "secondary": sum(int(row["secondary_count"]) for row in catalog_rows),
                    "unmapped": sum(int(row["unmapped_count"]) for row in catalog_rows),
                },
                "selected_read_count": len(retained_ids),
                "selected_alignment_record_count": selected_record_count,
                "selected_read_set_sha256": service._selected_set_digest(retained_ids),
                "preview_complete_to_target": len(retained_ids) == min(target_reads, len(candidate_rows)),
                "coverage_primary_read_count": primary_record_count,
                "coverage_bin_width": bin_width,
                "catalog": {
                    "schema": "bms.ngs.read-catalog.v1",
                    "logical_read_count": len(catalog_rows),
                    "mapped_primary_read_count": states["mapped_primary"],
                    "unmapped_read_count": states["unmapped"],
                    "ambiguous_primary_read_count": states["ambiguous_primary"],
                    "no_primary_read_count": states["no_primary"],
                    "content_sha256": outputs["catalog"]["sha256"],
                    "size_bytes": outputs["catalog"]["size_bytes"],
                },
                "locators": {
                    "schema": "bms.ngs.read-record-locators.v1",
                    "record_count": len(locator_rows),
                    "content_sha256": outputs["locators"]["sha256"],
                    "size_bytes": outputs["locators"]["size_bytes"],
                    "fingerprint_policy": "bms.ngs.alignment-record-fingerprint.v1",
                },
                "inventory": [
                    "preview.bam",
                    "preview.bam.bai",
                    "full-source-primary.coverage.bedgraph",
                    "read-catalog.parquet",
                    "read-record-locators.parquet",
                    "presentation-manifest.json",
                ],
                "outputs": outputs,
            }
            manifest_bytes = rfc8785.dumps(manifest)
            stage_checkpoint()
            if len(manifest_bytes) > service.ALIGNMENT_PRESENTATION_MANIFEST_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment presentation manifest byte ceiling exceeded"
                )
            manifest_path = temporary / "presentation-manifest.json"
            with manifest_path.open("wb") as output:
                output.write(manifest_bytes)
                output.flush()
                os.fsync(output.fileno())
            stage_checkpoint()
            package_bytes = _directory_bytes(temporary)
            if package_bytes > service.ALIGNMENT_PRESENTATION_ENTRY_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment presentation package byte ceiling exceeded"
                )
            if package_bytes > service.ALIGNMENT_PRESENTATION_WORK_MAX_BYTES:
                raise service.AlignmentPresentationFailure(
                    "resource_limit", message="alignment presentation temporary byte ceiling exceeded"
                )
            stage_checkpoint()
            directory_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            stage_checkpoint()
            try:
                os.rename(".generation.tmp", cache_key, src_dir_fd=namespace_fd, dst_dir_fd=namespace_fd)
                renamed = True
                os.fsync(namespace_fd)
                stage_checkpoint()
            except OSError as exc:
                raise service.AlignmentPresentationFailure(
                    "publication_failed",
                    retryable=True,
                    message="alignment presentation atomic publication failed",
                ) from exc
        except service.AlignmentPresentationFailure:
            if not renamed:
                service._remove_locus_transient(namespace_fd, ".generation.tmp")
            raise
        except service._AlignmentDerivativeTimeout as exc:
            if not renamed:
                service._remove_locus_transient(namespace_fd, ".generation.tmp")
            raise service.AlignmentPresentationFailure(
                "build_timeout", message="alignment presentation time limit exceeded"
            ) from exc
        except (OSError, ValueError, sqlite3.Error, pa.ArrowException) as exc:
            if not renamed:
                service._remove_locus_transient(namespace_fd, ".generation.tmp")
            raise service.AlignmentPresentationFailure(
                "source_invalid", message="alignment presentation source is invalid"
            ) from exc
        except Exception:
            if not renamed:
                service._remove_locus_transient(namespace_fd, ".generation.tmp")
            raise
        finally:
            if database is not None:
                database.close()
            if index_handle is not None:
                index_handle.close()
            if source_handle is not None:
                source_handle.close()
        with service.open_presentation_authority_root(destination, create=False) as pinned:
            stage_checkpoint()
            package = service._load_derived_package(
                pinned,
                expected_authority_sha256=cache_key,
                abort_check=stage_checkpoint,
            )
        if package is None:
            service._remove_locus_transient(namespace_fd, cache_key)
            raise service.AlignmentPresentationFailure(
                "integrity_mismatch", message="alignment presentation failed integrity validation"
            )
        service._cleanup_presentation_namespace(
            namespace,
            namespace_fd,
            active=destination,
            protected_names=protected,
        )
        return package
