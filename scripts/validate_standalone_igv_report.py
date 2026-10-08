#!/usr/bin/env python3
"""Validate a bounded, self-contained IGV Reports HTML artifact."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import math
import re
import struct
import sys
import unicodedata
import zlib
from html.parser import HTMLParser
from pathlib import Path


EXPECTED_CSP = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; "
    "frame-src 'none'; child-src 'none'; connect-src data: blob:; img-src data: blob:; "
    "media-src data: blob:; font-src data:; script-src 'unsafe-inline' blob:; "
    "style-src 'unsafe-inline'; worker-src blob:"
)
_RESOURCE_ATTRIBUTES = {
    "src",
    "href",
    "xlink:href",
    "data",
    "poster",
    "action",
    "formaction",
    "background",
    "manifest",
    "archive",
    "codebase",
    "ping",
}
_ALLOWED_DATA_PREFIXES = (
    "data:application/gzip;base64,",
    "data:application/octet-stream;base64,",
    "data:image/gif;base64,",
    "data:image/jpeg;base64,",
    "data:image/png;base64,",
    "data:image/webp;base64,",
    "data:font/woff;base64,",
    "data:font/woff2;base64,",
)
_CSS_RESOURCE = re.compile(
    r"url\(\s*(['\"]?)(.*?)\1\s*\)|@import\s+(?:url\(\s*)?(['\"])(.*?)\3",
    re.IGNORECASE | re.DOTALL,
)
_DYNAMIC_ASSIGNMENTS = {
    "tableJson": re.compile(r"^(\s*const tableJson = ).*$", re.MULTILINE),
    "sessionDictionary": re.compile(r"^(\s*const sessionDictionary = ).*$", re.MULTILINE),
}
_SESSION_PREFIX = "data:application/gzip;base64,"
_TABLE_HEADERS = ["unique_id", "Chrom", "Start", "End", "Name"]
_NESTED_EXPANSION_MULTIPLIER = 4
_BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")
_TRACK_PROFILES = {
    "Aligned Reads": {
        "name": "Aligned Reads", "type": "alignment", "format": "bam",
        "showCoverage": True, "showSoftClips": True, "showMismatches": True,
        "showAllBases": True, "showInsertionText": True, "displayMode": "EXPANDED",
        "visibilityWindow": -1, "height": 500, "order": 1,
    },
    "Coverage Depth": {
        "name": "Coverage Depth", "type": "wig", "format": "bedgraph",
        "graphType": "bar", "autoscale": True, "color": "#4ea6ff", "order": 2,
    },
    "Position Gradient": {
        "name": "Position Gradient", "type": "wig", "format": "bedgraph",
        "graphType": "heatmap", "min": 0, "max": 1, "autoscale": False, "order": 3,
    },
    "GC Content (%)": {
        "name": "GC Content (%)", "type": "wig", "format": "bedgraph",
        "graphType": "line", "autoscale": True, "color": "#2ec27e", "order": 4,
    },
    "GC Z-score": {
        "name": "GC Z-score", "type": "wig", "format": "bedgraph",
        "graphType": "line", "autoscale": True, "color": "#f6d32d", "order": 5,
    },
    "Split-read Density": {
        "name": "Split-read Density", "type": "wig", "format": "bedgraph",
        "graphType": "bar", "autoscale": True, "color": "#ff7800", "order": 6,
    },
    "Soft-clip Density": {
        "name": "Soft-clip Density", "type": "wig", "format": "bedgraph",
        "graphType": "bar", "autoscale": True, "color": "#e01b24", "order": 7,
    },
    "Junction Hotspots": {
        "name": "Junction Hotspots", "type": "annotation", "format": "bed",
        "displayMode": "EXPANDED", "color": "#ffbe6f", "order": 8,
    },
}


class _ResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resources: list[str] = []
        self.css_blocks: list[str] = []
        self.csp_values: list[str] = []
        self.scripts: list[tuple[dict[str, str | None], list[str]]] = []
        self.active_attributes: list[str] = []
        self._style_depth = 0
        self._script: tuple[dict[str, str | None], list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        normalized_attrs = {name.casefold(): value for name, value in attrs}
        if normalized_tag == "style":
            self._style_depth += 1
        if normalized_tag == "script":
            self._script = (normalized_attrs, [])
            self.scripts.append(self._script)
        for name, value in normalized_attrs.items():
            if name.startswith("on") or name == "srcdoc":
                self.active_attributes.append(name)
            if name in _RESOURCE_ATTRIBUTES and value:
                self.resources.append(value)
            elif name in {"srcset", "imagesrcset"} and value:
                self.resources.extend(_srcset_resources(value))
            elif name == "style" and value:
                self.css_blocks.append(value)
        if normalized_tag == "meta":
            http_equiv = str(normalized_attrs.get("http-equiv") or "").casefold()
            content = str(normalized_attrs.get("content") or "")
            if http_equiv == "refresh":
                self.active_attributes.append("meta-refresh")
            elif http_equiv == "content-security-policy":
                self.csp_values.append(content)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "style":
            self._style_depth = max(0, self._style_depth - 1)
        elif normalized_tag == "script":
            self._script = None

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self.css_blocks.append(data)
        if self._script is not None:
            self._script[1].append(data)


def _srcset_resources(value: str) -> list[str]:
    return [candidate.strip().split()[0] for candidate in value.split(",") if candidate.strip()]


def _allowed_embedded_resource(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized.startswith("#") or normalized.startswith(_ALLOWED_DATA_PREFIXES)


def _css_resources(css: str) -> list[str]:
    resources: list[str] = []
    for match in _CSS_RESOURCE.finditer(css):
        value = match.group(2) or match.group(4)
        if value:
            resources.append(value)
    return resources


def _read_regular_utf8(path_value: str | Path, label: str) -> str:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"unsafe or missing {label}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8") from exc


def _parse_html(text: str) -> _ResourceParser:
    parser = _ResourceParser()
    parser.feed(text)
    parser.close()
    return parser


def _normalize_controller(script: str) -> tuple[str, dict[str, object]]:
    normalized = script
    values: dict[str, object] = {}
    markers = {
        "tableJson": "TABLE_JSON",
        "sessionDictionary": "SESSION_DICTIONARY",
    }
    for name, pattern in _DYNAMIC_ASSIGNMENTS.items():
        matches = list(pattern.finditer(normalized))
        if len(matches) != 1:
            raise ValueError("standalone IGV controller assignments are invalid")
        assignment = matches[0].group(0)
        raw_value = assignment.split("=", 1)[1].strip()
        try:
            values[name] = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ValueError("standalone IGV controller data is not valid JSON") from exc
        normalized = pattern.sub(rf'\1"@{markers[name]}@"', normalized, count=1)
    return normalized, values


def _safe_table_text(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if not isinstance(value, str) or "<" in value or ">" in value:
        return False
    return all(character == "\t" or not unicodedata.category(character).startswith("C") for character in value)


def _decode_gzip_data_uri(
    value: object,
    *,
    max_expanded_bytes: int,
    capture_limit: int,
    bgzf: bool = False,
) -> tuple[bytes, int]:
    if not isinstance(value, str) or not value.startswith(_SESSION_PREFIX):
        raise ValueError("standalone IGV session resource is invalid")
    try:
        compressed = base64.b64decode(value[len(_SESSION_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("standalone IGV session resource is not strict base64") from exc
    if max_expanded_bytes <= 0 or len(compressed) < 18 or compressed[:3] != b"\x1f\x8b\x08":
        raise ValueError("standalone IGV session resource is not bounded gzip data")

    if bgzf:
        return _decode_bgzf(compressed, max_expanded_bytes=max_expanded_bytes, capture_limit=capture_limit)

    decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
    captured = bytearray()
    expanded_size = 0
    try:
        for offset in range(0, len(compressed), 1024 * 1024):
            if decoder.eof:
                raise ValueError("standalone IGV gzip data has trailing bytes or members")
            pending = compressed[offset : offset + 1024 * 1024]
            while pending:
                chunk = decoder.decompress(pending, max_expanded_bytes - expanded_size + 1)
                expanded_size += len(chunk)
                if expanded_size > max_expanded_bytes:
                    raise ValueError("standalone IGV resource exceeds expansion budget")
                if len(captured) < capture_limit:
                    captured.extend(chunk[: capture_limit - len(captured)])
                if decoder.unused_data:
                    raise ValueError("standalone IGV gzip data has trailing bytes or members")
                pending = decoder.unconsumed_tail
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("standalone IGV gzip stream is incomplete or non-exact")
    except zlib.error as exc:
        raise ValueError("standalone IGV session gzip data is invalid") from exc
    return bytes(captured), expanded_size


def _decode_bgzf(compressed: bytes, *, max_expanded_bytes: int, capture_limit: int) -> tuple[bytes, int]:
    if not compressed.endswith(_BGZF_EOF):
        raise ValueError("standalone IGV BAM lacks the canonical BGZF EOF block")
    captured = bytearray()
    expanded_size = 0
    offset = 0
    while offset < len(compressed):
        if len(compressed) - offset < 18 or compressed[offset : offset + 4] != b"\x1f\x8b\x08\x04":
            raise ValueError("standalone IGV BAM contains a non-BGZF member")
        extra_length = struct.unpack_from("<H", compressed, offset + 10)[0]
        extra_start = offset + 12
        extra_end = extra_start + extra_length
        if extra_end + 8 > len(compressed):
            raise ValueError("standalone IGV BAM BGZF header is truncated")
        cursor = extra_start
        block_size: int | None = None
        while cursor < extra_end:
            if cursor + 4 > extra_end:
                raise ValueError("standalone IGV BAM BGZF extra field is invalid")
            subfield_id = compressed[cursor : cursor + 2]
            subfield_length = struct.unpack_from("<H", compressed, cursor + 2)[0]
            cursor += 4
            if cursor + subfield_length > extra_end:
                raise ValueError("standalone IGV BAM BGZF extra field is truncated")
            if subfield_id == b"BC" and subfield_length == 2:
                block_size = struct.unpack_from("<H", compressed, cursor)[0] + 1
            cursor += subfield_length
        if block_size is None or block_size > 65536 or offset + block_size > len(compressed):
            raise ValueError("standalone IGV BAM BGZF block size is invalid")
        block = compressed[offset : offset + block_size]
        decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
        try:
            output = decoder.decompress(block, min(65536, max_expanded_bytes - expanded_size) + 1)
        except zlib.error as exc:
            raise ValueError("standalone IGV BAM BGZF block is invalid") from exc
        if (
            len(output) > 65536
            or expanded_size + len(output) > max_expanded_bytes
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise ValueError("standalone IGV BAM BGZF block is non-exact or oversized")
        is_final = offset + block_size == len(compressed)
        if (not output) != is_final or (is_final and block != _BGZF_EOF):
            raise ValueError("standalone IGV BAM BGZF EOF placement is invalid")
        expanded_size += len(output)
        if len(captured) < capture_limit:
            captured.extend(output[: capture_limit - len(captured)])
        offset += block_size
    return bytes(captured), expanded_size


def _validate_reference_payload(payload: bytes) -> None:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("standalone IGV reference FASTA is not ASCII") from exc
    lines = text.splitlines()
    if len(lines) < 2 or not lines[0].startswith(">") or not lines[0][1:].strip():
        raise ValueError("standalone IGV reference FASTA is invalid")
    if any(unicodedata.category(character).startswith("C") for character in lines[0]):
        raise ValueError("standalone IGV reference FASTA header is invalid")
    sequence = "".join(line.strip() for line in lines[1:])
    if not sequence or re.fullmatch(r"[ACGTRYSWKMBDHVNacgtryswkmbdhvn.-]+", sequence) is None:
        raise ValueError("standalone IGV reference FASTA sequence is invalid")


def _validate_track_payload(payload: bytes, track_type: str, track_format: str) -> None:
    if (track_type, track_format) == ("alignment", "bam"):
        if not payload.startswith(b"BAM\x01"):
            raise ValueError("standalone IGV BAM resource is invalid")
        return
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("standalone IGV text track is invalid") from exc
    if not lines or any(not line for line in lines):
        raise ValueError("standalone IGV text track is empty or contains blank records")
    required_fields = 4 if track_format == "bedgraph" else 6
    for line in lines:
        if any(character != "\t" and unicodedata.category(character).startswith("C") for character in line):
            raise ValueError("standalone IGV text track contains control characters")
        fields = line.split("\t")
        if len(fields) != required_fields or not fields[0]:
            raise ValueError("standalone IGV text track record shape is invalid")
        try:
            start, end = int(fields[1]), int(fields[2])
        except ValueError as exc:
            raise ValueError("standalone IGV text track coordinates are invalid") from exc
        if start < 0 or end <= start:
            raise ValueError("standalone IGV text track coordinates are invalid")
        if track_format == "bedgraph":
            try:
                value = float(fields[3])
            except ValueError as exc:
                raise ValueError("standalone IGV bedGraph value is invalid") from exc
            if not math.isfinite(value):
                raise ValueError("standalone IGV bedGraph value is not finite")
        else:
            try:
                score = int(fields[4])
            except ValueError as exc:
                raise ValueError("standalone IGV BED score is invalid") from exc
            if not fields[3] or score < 0 or score > 1000 or fields[5] not in {"+", "-", "."}:
                raise ValueError("standalone IGV BED annotation is invalid")


def _exact_json_value(actual: object, expected: object) -> bool:
    try:
        return json.dumps(actual, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
            expected,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def _validate_session_document(
    value: object,
    *,
    expected_locus: str,
    outer_budget: int,
    nested_budget: int,
) -> tuple[int, int]:
    expanded, outer_size = _decode_gzip_data_uri(
        value,
        max_expanded_bytes=outer_budget,
        capture_limit=outer_budget,
    )
    try:
        document = json.loads(expanded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("standalone IGV session is not valid JSON") from exc
    if not isinstance(document, dict) or set(document) != {"locus", "reference", "tracks"}:
        raise ValueError("standalone IGV session shape is invalid")
    if document["locus"] != expected_locus:
        raise ValueError("standalone IGV session locus does not match its table row")
    reference = document["reference"]
    tracks = document["tracks"]
    if not isinstance(reference, dict) or set(reference) != {"fastaURL"} or not isinstance(tracks, list) or not tracks:
        raise ValueError("standalone IGV session reference or tracks are invalid")

    nested_size = 0
    reference_payload, resource_size = _decode_gzip_data_uri(
        reference["fastaURL"],
        max_expanded_bytes=nested_budget,
        capture_limit=nested_budget,
    )
    nested_size += resource_size
    _validate_reference_payload(reference_payload)

    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("standalone IGV session track is invalid")
        track_name = track.get("name")
        expected_profile = _TRACK_PROFILES.get(track_name) if isinstance(track_name, str) else None
        metadata = {key: item for key, item in track.items() if key != "url"}
        if expected_profile is None or not _exact_json_value(metadata, expected_profile) or set(track) != set(expected_profile) | {"url"}:
            raise ValueError("standalone IGV session track profile is invalid")
        track_type = expected_profile["type"]
        track_format = expected_profile["format"]
        capture_limit = 4 if track_format == "bam" else nested_budget - nested_size
        resource_payload, resource_size = _decode_gzip_data_uri(
            track["url"],
            max_expanded_bytes=nested_budget - nested_size,
            capture_limit=capture_limit,
            bgzf=track_format == "bam",
        )
        nested_size += resource_size
        _validate_track_payload(resource_payload, str(track_type), str(track_format))
    return outer_size, nested_size


def _validate_controller_data(table: object, sessions: object, *, max_bytes: int) -> None:
    if not isinstance(table, dict) or set(table) != {"headers", "rows"}:
        raise ValueError("standalone IGV report table data is invalid")
    headers = table["headers"]
    rows = table["rows"]
    if headers != _TABLE_HEADERS or not isinstance(rows, list) or not rows:
        raise ValueError("standalone IGV report table data is invalid")

    expected_row_ids = list(range(len(rows)))
    expected_loci: list[str] = []
    for expected_id, row in zip(expected_row_ids, rows, strict=True):
        if (
            not isinstance(row, list)
            or len(row) != len(_TABLE_HEADERS)
            or type(row[0]) is not int
            or row[0] != expected_id
            or not isinstance(row[1], str)
            or not row[1]
            or not isinstance(row[2], int)
            or isinstance(row[2], bool)
            or not isinstance(row[3], int)
            or isinstance(row[3], bool)
            or row[2] < 0
            or row[3] < row[2]
            or not isinstance(row[4], str)
            or any(not _safe_table_text(cell) for cell in row[1:])
        ):
            raise ValueError("standalone IGV report table row is invalid")
        expected_loci.append(f"{row[1]}:{row[2]}-{row[3]}")

    if not isinstance(sessions, dict) or set(sessions) != {str(row_id) for row_id in expected_row_ids}:
        raise ValueError("standalone IGV report row/session closure is invalid")

    outer_total = 0
    nested_total = 0
    nested_limit = max_bytes * _NESTED_EXPANSION_MULTIPLIER
    for row_id, expected_locus in zip(expected_row_ids, expected_loci, strict=True):
        outer_size, nested_size = _validate_session_document(
            sessions[str(row_id)],
            expected_locus=expected_locus,
            outer_budget=max_bytes - outer_total,
            nested_budget=nested_limit - nested_total,
        )
        outer_total += outer_size
        nested_total += nested_size


def _validate_executable_identity(
    report_parser: _ResourceParser,
    template_text: str,
    igv_js_text: str,
    max_bytes: int,
) -> None:
    template_parser = _parse_html(template_text)
    if template_parser.csp_values != [EXPECTED_CSP]:
        raise ValueError("governed IGV template has invalid network policy")
    if len(template_parser.scripts) != 2:
        raise ValueError("governed IGV template script inventory is invalid")
    template_library_attrs, template_library_parts = template_parser.scripts[0]
    template_controller_attrs = template_parser.scripts[1][0]
    if (
        template_library_attrs.get("src") != "file:///opt/bms/igv-reports/igv.min.js"
        or "".join(template_library_parts).strip()
    ):
        raise ValueError("governed IGV template library authority is invalid")
    if len(report_parser.scripts) != 2:
        raise ValueError("standalone IGV report script inventory is invalid")
    report_library_attrs, report_library_parts = report_parser.scripts[0]
    report_controller_attrs, report_controller_parts = report_parser.scripts[1]
    if report_library_attrs != template_controller_attrs or report_controller_attrs != template_controller_attrs:
        raise ValueError("standalone IGV report script attributes do not match the governed template")
    if "".join(report_library_parts) != "\n" + igv_js_text:
        raise ValueError("standalone IGV report embedded library identity mismatch")
    template_controller = "".join(template_parser.scripts[1][1])
    report_controller = "".join(report_controller_parts)
    normalized_controller, values = _normalize_controller(report_controller)
    if normalized_controller != template_controller:
        raise ValueError("standalone IGV report controller identity mismatch")
    _validate_controller_data(
        values["tableJson"],
        values["sessionDictionary"],
        max_bytes=max_bytes,
    )


def validate_report(
    report: str | Path,
    *,
    max_bytes: int,
    template: str | Path,
    igv_js: str | Path,
) -> int:
    path = Path(report)
    if path.is_symlink() or not path.is_file():
        raise ValueError("unsafe or missing standalone IGV report")
    size = path.stat().st_size
    if max_bytes <= 0 or size > max_bytes:
        raise ValueError(f"standalone IGV report exceeds size limit: {size} > {max_bytes}")
    text = _read_regular_utf8(path, "standalone IGV report")
    template_text = _read_regular_utf8(template, "governed IGV template")
    igv_js_text = _read_regular_utf8(igv_js, "pinned IGV library")

    parser = _parse_html(text)
    external = [value for value in parser.resources if not _allowed_embedded_resource(value)]
    external.extend(
        value
        for css in parser.css_blocks
        for value in _css_resources(css)
        if not _allowed_embedded_resource(value)
    )
    if external or parser.active_attributes:
        raise ValueError("standalone IGV report contains an external, active, or host-bound resource")
    if parser.csp_values != [EXPECTED_CSP]:
        raise ValueError("standalone IGV report network policy is missing or invalid")
    _validate_executable_identity(parser, template_text, igv_js_text, max_bytes)
    return size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-bytes", required=True, type=int)
    parser.add_argument("--template", required=True)
    parser.add_argument("--igv-js", required=True)
    args = parser.parse_args()
    try:
        size = validate_report(
            args.report,
            max_bytes=args.max_bytes,
            template=args.template,
            igv_js=args.igv_js,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"standalone IGV report valid: {size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
