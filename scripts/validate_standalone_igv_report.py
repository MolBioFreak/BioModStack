#!/usr/bin/env python3
"""Validate a bounded, self-contained IGV Reports HTML artifact."""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import io
import json
import math
import re
import sys
import unicodedata
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
_RESOURCE_PREVIEW_BYTES = 1024 * 1024
_TRACK_KEYS = {
    ("alignment", "bam"): frozenset(
        {
            "name", "type", "format", "url", "showCoverage", "showSoftClips",
            "showMismatches", "showAllBases", "showInsertionText", "displayMode",
            "visibilityWindow", "height", "order",
        }
    ),
    ("wig", "bedgraph"): frozenset(
        {"name", "type", "format", "url", "graphType", "autoscale", "color", "order"}
    ),
    ("wig", "bedgraph:bounded"): frozenset(
        {"name", "type", "format", "url", "graphType", "min", "max", "autoscale", "order"}
    ),
    ("annotation", "bed"): frozenset(
        {"name", "type", "format", "url", "displayMode", "color", "order"}
    ),
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
) -> tuple[bytes, int]:
    if not isinstance(value, str) or not value.startswith(_SESSION_PREFIX):
        raise ValueError("standalone IGV session resource is invalid")
    try:
        compressed = base64.b64decode(value[len(_SESSION_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("standalone IGV session resource is not strict base64") from exc
    if max_expanded_bytes <= 0 or len(compressed) < 18 or compressed[:3] != b"\x1f\x8b\x08":
        raise ValueError("standalone IGV session resource is not bounded gzip data")
    captured = bytearray()
    expanded_size = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, max_expanded_bytes - expanded_size + 1))
                if not chunk:
                    break
                expanded_size += len(chunk)
                if expanded_size > max_expanded_bytes:
                    raise ValueError("standalone IGV resource exceeds expansion budget")
                if len(captured) < capture_limit:
                    captured.extend(chunk[: capture_limit - len(captured)])
    except (EOFError, OSError) as exc:
        raise ValueError("standalone IGV session gzip data is invalid") from exc
    return bytes(captured), expanded_size


def _validate_reference_payload(payload: bytes) -> None:
    lines = payload.splitlines()
    if len(lines) < 2 or not lines[0].startswith(b">") or not any(line.strip() for line in lines[1:]):
        raise ValueError("standalone IGV reference FASTA is invalid")


def _validate_track_payload(payload: bytes, track_type: str, track_format: str) -> None:
    if (track_type, track_format) == ("alignment", "bam"):
        if not payload.startswith(b"BAM\x01"):
            raise ValueError("standalone IGV BAM resource is invalid")
        return
    try:
        line = next(line for line in payload.decode("utf-8").splitlines() if line.strip())
    except (UnicodeDecodeError, StopIteration) as exc:
        raise ValueError("standalone IGV text track is invalid") from exc
    fields = line.split("\t")
    required_fields = 4 if track_format == "bedgraph" else 3
    if len(fields) < required_fields:
        raise ValueError("standalone IGV text track is invalid")
    try:
        start, end = int(fields[1]), int(fields[2])
        if track_format == "bedgraph":
            float(fields[3])
    except ValueError as exc:
        raise ValueError("standalone IGV text track coordinates are invalid") from exc
    if start < 0 or end < start:
        raise ValueError("standalone IGV text track coordinates are invalid")


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
        capture_limit=_RESOURCE_PREVIEW_BYTES,
    )
    nested_size += resource_size
    _validate_reference_payload(reference_payload)

    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("standalone IGV session track is invalid")
        track_type = track.get("type")
        track_format = track.get("format")
        if not isinstance(track_type, str) or not isinstance(track_format, str):
            raise ValueError("standalone IGV session track type is invalid")
        schema_key = (track_type, track_format)
        if schema_key == ("wig", "bedgraph") and ("min" in track or "max" in track):
            schema_key = ("wig", "bedgraph:bounded")
        expected_keys = _TRACK_KEYS.get(schema_key)
        if expected_keys is None or set(track) != expected_keys:
            raise ValueError("standalone IGV session track shape is invalid")
        resource_payload, resource_size = _decode_gzip_data_uri(
            track["url"],
            max_expanded_bytes=nested_budget - nested_size,
            capture_limit=_RESOURCE_PREVIEW_BYTES,
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
            or row[0] != expected_id
            or isinstance(row[0], bool)
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
