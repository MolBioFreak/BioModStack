"""Exact-tag overlay admission shared by catalog construction and extraction.

These retained product bounds are not global resource allocations. Their writer
and workload justification remains an execution/release gate.
"""
from array import array
import hashlib
import math
import re
import struct
import pysam

MAX_RECORDS = 256
MAX_BAM_BYTES = 16777216
MAX_INDEX_BYTES = 1048576
MAX_SECONDS = 10


def policy():
    return {"schema": "bms.ngs.read-overlay-policy.v2", "max_records": MAX_RECORDS,
        "max_bam_bytes": MAX_BAM_BYTES, "max_index_bytes": MAX_INDEX_BYTES,
        "max_seconds": MAX_SECONDS, "tags": "all_exact_source_tags_v1",
        "header": "exact_source_header_v1", "bgzf_admission_version": 2,
        "writer": {"pysam": pysam.__version__, "htslib": pysam.__samtools_version__,
            "mode": "wb", "compression_level": 6, "threads": 1, "order": "reference_start_source_ordinal"}}


def header_identity(header):
    text = str(header).encode("utf-8")
    if b"\0" in text:
        raise ValueError("invalid source header text")
    raw = bytearray(b"BAM\x01" + struct.pack("<i", len(text)) + text + struct.pack("<i", len(header.references)))
    for name, length in zip(header.references, header.lengths, strict=True):
        encoded = name.encode("utf-8")
        if not encoded or b"\0" in encoded or length < 1:
            raise ValueError("invalid source reference header")
        raw.extend(struct.pack("<i", len(encoded) + 1) + encoded + b"\0" + struct.pack("<i", length))
    return {"sha256": hashlib.sha256(raw).hexdigest(), "raw_bytes": len(raw)}


def record_raw_bytes(record):
    """Closed standard-BAM sizing; never a probe BAM or SAM reserialization."""
    from services.ngs_alignment_presentation_v5 import _validate_read_id
    name = _validate_read_id(record.query_name).encode("utf-8")
    cigar = record.cigartuples or ()
    if (record.is_unmapped or record.reference_id < 0 or record.reference_id >= record.header.nreferences
            or record.reference_start < 0 or record.reference_end is None
            or record.reference_end <= record.reference_start
            or record.reference_end > record.header.lengths[record.reference_id]
            or not cigar or len(cigar) > 65535 or record.has_tag("CG")
            or any(op not in range(9) or not 0 < count < 2**28 for op, count in cigar)):
        raise ValueError("unsupported mapped record")
    sequence = record.query_sequence
    length = len(sequence or "")
    if sequence is not None:
        sequence.encode("ascii")
    if record.query_qualities is not None and len(record.query_qualities) != length:
        raise ValueError("invalid quality length")
    size = 4 + 32 + len(name) + 1 + 4 * len(cigar) + (length + 1) // 2 + length
    widths = {"c": 1, "C": 1, "s": 2, "S": 2, "i": 4, "I": 4, "f": 4, "d": 8}
    ranges = {"c": (-128, 127), "C": (0, 255), "s": (-32768, 32767), "S": (0, 65535),
              "i": (-2147483648, 2147483647), "I": (0, 4294967295)}
    arrays = {"b": 1, "B": 1, "h": 2, "H": 2, "i": 4, "I": 4, "f": 4}
    for tag, value, kind in record.get_tags(with_value_type=True):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]", tag) is None:
            raise ValueError("invalid tag name")
        size += 3
        if kind in ranges:
            lo, hi = ranges[kind]
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError("invalid integer tag")
            size += widths[kind]
        elif kind in {"f", "d"}:
            if not math.isfinite(value):
                raise ValueError("non-finite tag")
            size += widths[kind]
        elif kind in {"Z", "H", "A"}:
            if not isinstance(value, str):
                raise ValueError("invalid string tag")
            encoded = value.encode("utf-8")
            if b"\0" in encoded or kind == "A" and (len(encoded) != 1 or not 33 <= encoded[0] <= 126):
                raise ValueError("invalid string tag")
            if kind == "H" and (len(encoded) % 2 or re.fullmatch(r"[0-9A-Fa-f]*", value) is None):
                raise ValueError("invalid hex tag")
            size += len(encoded) + (kind != "A")
        elif kind == "B" and isinstance(value, array) and value.typecode in arrays:
            if value.typecode == "f" and any(not math.isfinite(v) for v in value):
                raise ValueError("non-finite array tag")
            size += 5 + len(value) * arrays[value.typecode]
        else:
            raise ValueError("unsupported optional tag type")
    if size - 4 > 2147483647:
        raise ValueError("oversized BAM record")
    return size


def eligibility(row):
    if row.get("in_preview") is True:
        return "already_in_preview"
    state = row["alignment_state"]
    if state != "mapped_primary":
        if state not in {"unmapped", "ambiguous_primary", "no_primary"}:
            raise ValueError("invalid alignment state")
        return state
    if row["mapped_primary_count"] != 1:
        raise ValueError("primary count contradicts catalog state")
    if 1 + row["mapped_supplementary_count"] > MAX_RECORDS:
        return "record_limit"
    if row.get("overlay_writer_compatible") is not True or row.get("overlay_record_raw_bytes") is None:
        return "writer_unsupported"
    bound = row.get("overlay_bgzf_bound_bytes")
    if type(bound) is not int or bound > MAX_BAM_BYTES:
        return "byte_limit"
    return None
