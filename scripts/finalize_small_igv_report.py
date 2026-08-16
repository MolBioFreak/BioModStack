#!/usr/bin/env python3
"""Finalize an IGV Reports no-embed shell with governed artifact URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MAX_REPORT_BYTES = 1024 * 1024
OPTIONS_PREFIX = "var options = "
EXPECTED_RESOURCE_ROLES = frozenset(
    {
        "reference",
        "reference_index",
        "alignment",
        "alignment_index",
        "coverage_depth",
        "position_gradient",
        "gc_content",
        "gc_zscore",
        "split_read_density",
        "soft_clip_density",
        "junction_hotspots",
    }
)
TRACK_ROLE_PAIRS = (
    ("alignment", "alignment_index"),
    ("coverage_depth",),
    ("position_gradient",),
    ("gc_content",),
    ("gc_zscore",),
    ("split_read_density",),
    ("soft_clip_density",),
    ("junction_hotspots",),
)
GOVERNED_URL_RE = re.compile(
    r"^/api/jobs/(?P<job>[^/]+)/alignment-session-artifacts/"
    r"(?P<mode>primary|dimer_candidates)/(?P<role>[a-z_]+)/(?P<digest>[0-9a-f]{64})$"
)
ALLOWED_SHELL_RESOURCES = frozenset(
    {"https://cdn.jsdelivr.net/npm/igv@3.5.2/dist/igv.min.js"}
)
URL_ATTRIBUTES = frozenset({"src", "href", "poster", "action", "formaction", "data", "srcset", "xlink:href"})
IGV_REPORT_INLINE_SCRIPT_SHA256 = "1cec7f4f0367d1ced846fd55cf862e0a43c80617732d3d9696c122c71c630e58"
INLINE_JSON_PREFIXES = ("const tableJson = ", "const locusDictionary = ", OPTIONS_PREFIX)
ALLOWED_NORMALIZED_REPORT_SHA256 = frozenset(
    {
        "9749be0e531af9e0703f47c5b4aa4227ff60e30d89ee8d1eaf01b6c75a215692",
        "bb8ab62a8e16fcecc959b268e6bdb4e636323f470b86a2f4b4e499cc4f8c4e38",
    }
)


class _ResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resources: set[str] = set()
        self.inline_scripts: list[str] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_attrs(attrs)
        if tag.casefold() == "script" and not any(name.casefold() == "src" for name, _value in attrs):
            if self._script_parts is not None:
                raise ValueError("IGV report contains malformed script markup")
            self._script_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag
        self._handle_attrs(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._script_parts is not None:
            self.inline_scripts.append("".join(self._script_parts))
            self._script_parts = None

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)

    def _handle_attrs(self, attrs: list[tuple[str, str | None]]) -> None:
        normalized_attrs = {name.casefold(): value for name, value in attrs}
        http_equiv = normalized_attrs.get("http-equiv")
        if isinstance(http_equiv, str) and http_equiv.casefold() == "refresh":
            raise ValueError("IGV report contains an undeclared HTML resource")
        for name, value in attrs:
            normalized = name.casefold()
            if normalized in URL_ATTRIBUTES:
                if value is None or normalized == "srcset":
                    raise ValueError("IGV report contains an unsupported HTML resource attribute")
                self.resources.add(value)
            if normalized == "srcdoc" or normalized.startswith("on"):
                raise ValueError("IGV report contains an undeclared HTML resource")
            if normalized == "style" and value and re.search(r"(?:url\s*\(|@import)", value, re.IGNORECASE):
                raise ValueError("IGV report contains an undeclared HTML resource")


def _validate_inline_scripts(scripts: list[str]) -> None:
    if len(scripts) != 1:
        raise ValueError("IGV report contains an undeclared HTML resource")
    script = scripts[0]
    lines = script.splitlines()
    normalized_lines: list[str] = []
    observed_prefixes: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        prefix = next((candidate for candidate in INLINE_JSON_PREFIXES if stripped.startswith(candidate)), None)
        if prefix is None:
            normalized_lines.append(line)
            continue
        try:
            json.loads(stripped[len(prefix):])
        except json.JSONDecodeError as exc:
            raise ValueError("IGV report inline script contains invalid JSON") from exc
        observed_prefixes.append(prefix)
        normalized_lines.append(f"<{prefix}JSON>")
    if observed_prefixes and set(observed_prefixes) == {OPTIONS_PREFIX}:
        if any(
            line.strip() and line.strip() != f"<{OPTIONS_PREFIX}JSON>"
            for line in normalized_lines
        ):
            raise ValueError("IGV report contains an unexpected inline script")
        return
    if observed_prefixes != list(INLINE_JSON_PREFIXES):
        raise ValueError("IGV report does not contain the expected inline data objects")
    normalized = "\n".join(normalized_lines)
    if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != IGV_REPORT_INLINE_SCRIPT_SHA256:
        raise ValueError("IGV report contains an unexpected inline script")


def _validate_report_template(text: str) -> None:
    normalized_lines: list[str] = []
    observed_prefixes: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        prefix = next((candidate for candidate in INLINE_JSON_PREFIXES if stripped.startswith(candidate)), None)
        if prefix is None:
            normalized_lines.append(line)
            continue
        try:
            json.loads(stripped[len(prefix):])
        except json.JSONDecodeError as exc:
            raise ValueError("IGV report template contains invalid JSON") from exc
        observed_prefixes.append(prefix)
        normalized_lines.append(f"<{prefix}JSON>")
    if observed_prefixes not in ([OPTIONS_PREFIX], list(INLINE_JSON_PREFIXES)):
        raise ValueError("IGV report does not match the approved template")
    normalized = "\n".join(normalized_lines)
    if hashlib.sha256(normalized.encode("utf-8")).hexdigest() not in ALLOWED_NORMALIZED_REPORT_SHA256:
        raise ValueError("IGV report does not match the approved template")


def _shell_resources(text: str) -> set[str]:
    if re.search(r"<style\b[^>]*>.*?(?:url\s*\(|@import)", text, re.IGNORECASE | re.DOTALL):
        raise ValueError("IGV report contains an undeclared HTML resource")
    parser = _ResourceParser()
    parser.feed(text)
    parser.close()
    _validate_inline_scripts(parser.inline_scripts)
    if re.search(
        r"\b(?:fetch|importScripts|Worker|SharedWorker|WebSocket|EventSource)\s*\(|"
        r"\bimport\s*\(|\bXMLHttpRequest\b|\blocation\s*=|\.src\s*=|"
        r"\[\s*['\"](?:src|href|action|formaction|data|poster|srcset|xlink:href)['\"]\s*\]\s*=",
        text,
        re.IGNORECASE,
    ):
        raise ValueError("IGV report contains an undeclared HTML resource")
    if re.search(
        r"\bsetAttribute\s*\(\s*['\"](?:src|href|action|formaction|data|poster|srcset|xlink:href)['\"]\s*,",
        text,
        re.IGNORECASE,
    ):
        raise ValueError("IGV report contains an undeclared HTML resource")
    return parser.resources


def _read_json(path: str | Path) -> Any:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"unsafe or missing report input: {source}")
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON report input: {source}") from exc


def _resource_identity(value: Any) -> tuple[str, str, str, str]:
    if not isinstance(value, str):
        raise ValueError("resource is not a governed artifact URL")
    match = GOVERNED_URL_RE.fullmatch(value)
    if match is None:
        raise ValueError("resource is not a governed artifact URL")
    return match.group("job"), match.group("mode"), match.group("role"), value


def _declared_urls(reference: dict[str, Any], tracks: list[Any]) -> set[str]:
    if len(tracks) != len(TRACK_ROLE_PAIRS):
        raise ValueError("report does not contain the exact governed resource inventory")
    identities = [
        _resource_identity(reference.get("fastaURL")),
        _resource_identity(reference.get("indexURL")),
    ]
    if identities[0][2] != "reference" or identities[1][2] != "reference_index":
        raise ValueError("report does not contain the exact governed resource inventory")

    for track, expected_roles in zip(tracks, TRACK_ROLE_PAIRS, strict=True):
        if not isinstance(track, dict):
            raise ValueError("track configuration entries must be objects")
        keys = ("url", "indexURL") if len(expected_roles) == 2 else ("url",)
        if any(key not in track for key in keys) or any(
            key in track for key in {"url", "indexURL"} - set(keys)
        ):
            raise ValueError("report does not contain the exact governed resource inventory")
        track_identities = [_resource_identity(track[key]) for key in keys]
        if tuple(identity[2] for identity in track_identities) != expected_roles:
            raise ValueError("report does not contain the exact governed resource inventory")
        identities.extend(track_identities)

    jobs = {identity[0] for identity in identities}
    modes = {identity[1] for identity in identities}
    if len(jobs) != 1 or len(modes) != 1:
        raise ValueError("report resources must belong to one job and mode")
    roles = [identity[2] for identity in identities]
    urls = [identity[3] for identity in identities]
    if len(urls) != len(EXPECTED_RESOURCE_ROLES) or set(roles) != EXPECTED_RESOURCE_ROLES or len(set(urls)) != len(urls):
        raise ValueError("report does not contain the exact governed resource inventory")
    return set(urls)


def finalize_report(
    *,
    report: str | Path,
    reference_config: str | Path,
    track_config: str | Path,
    max_bytes: int = MAX_REPORT_BYTES,
    generated_reference_fasta: str | None = None,
    generated_reference_index: str | None = None,
) -> None:
    report_path = Path(report)
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError(f"unsafe or missing IGV report: {report_path}")
    if max_bytes <= 0 or report_path.stat().st_size > max_bytes:
        raise ValueError(f"IGV report exceeds size limit: {max_bytes}")

    reference = _read_json(reference_config)
    tracks = _read_json(track_config)
    if not isinstance(reference, dict) or set(reference) != {"fastaURL", "indexURL"}:
        raise ValueError("reference configuration must contain fastaURL and indexURL")
    if not isinstance(tracks, list):
        raise ValueError("track configuration must be a list")
    declared_urls = _declared_urls(reference, tracks)

    try:
        text = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("IGV report is not valid UTF-8 text") from exc
    folded_text = text.casefold()
    if "data:" in folded_text or ";base64," in folded_text:
        raise ValueError("IGV report contains an embedded data URI")
    shell_resources = _shell_resources(text)
    if not shell_resources <= ALLOWED_SHELL_RESOURCES:
        raise ValueError("IGV report contains an undeclared HTML resource")

    lines = text.splitlines(keepends=True)
    option_indexes = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith(OPTIONS_PREFIX)
    ]
    if len(option_indexes) != 1:
        raise ValueError("IGV report must contain exactly one options object")

    option_index = option_indexes[0]
    line = lines[option_index]
    indent_length = len(line) - len(line.lstrip())
    json_text = line.lstrip()[len(OPTIONS_PREFIX):].rstrip("\r\n")
    try:
        options = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError("IGV report options object is invalid JSON") from exc
    if not isinstance(options, dict) or set(options) != {"reference", "tracks"}:
        raise ValueError("IGV report options do not match the bounded session shape")
    if options.get("tracks") != tracks:
        raise ValueError("IGV report resources do not match declared inputs")
    generated_reference = options.get("reference")
    if not isinstance(generated_reference, dict) or not {"fastaURL"} <= set(generated_reference) <= {"fastaURL", "indexURL"}:
        raise ValueError("IGV report reference does not match generated inputs")
    if (generated_reference_fasta is None) != (generated_reference_index is None):
        raise ValueError("generated reference FASTA and index must be provided together")
    if generated_reference_fasta is None:
        valid_generated_references = (reference,)
    else:
        valid_generated_references = (
            {"fastaURL": generated_reference_fasta},
            {
                "fastaURL": generated_reference_fasta,
                "indexURL": generated_reference_index,
            },
        )
    if generated_reference not in valid_generated_references:
        raise ValueError("IGV report reference does not match generated inputs")

    options["reference"] = reference
    observed_urls = _declared_urls(options["reference"], options["tracks"])
    if observed_urls != declared_urls:
        raise ValueError("IGV report resources do not match declared inputs")
    _validate_report_template(text)

    newline = "\r\n" if line.endswith("\r\n") else "\n"
    lines[option_index] = (
        " " * indent_length
        + OPTIONS_PREFIX
        + json.dumps(options, separators=(",", ":"))
        + newline
    )
    finalized = "".join(lines)
    payload = finalized.encode("utf-8")
    if len(payload) > max_bytes:
        raise ValueError(f"IGV report exceeds size limit: {max_bytes}")

    mode = report_path.stat().st_mode & 0o777
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=report_path.parent,
            prefix=f".{report_path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, report_path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--reference-config", required=True)
    parser.add_argument("--track-config", required=True)
    parser.add_argument("--generated-reference-fasta")
    parser.add_argument("--generated-reference-index")
    parser.add_argument("--max-bytes", type=int, default=MAX_REPORT_BYTES)
    return parser


def main() -> int:
    args = _parser().parse_args()
    finalize_report(
        report=args.report,
        reference_config=args.reference_config,
        track_config=args.track_config,
        max_bytes=args.max_bytes,
        generated_reference_fasta=args.generated_reference_fasta,
        generated_reference_index=args.generated_reference_index,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
