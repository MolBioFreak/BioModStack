"""Fail-closed resolver for Dorado per-barcode resubmission units."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

_UNIT_ID = re.compile(r"^(?:barcode(?:0[1-9]|[1-8][0-9]|9[0-6])|unclassified)$")
_SAMPLE_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _confined(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} symlink is forbidden")
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must be confined beneath the authoritative result root") from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} symlink is forbidden")
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def load_barcode_unit(
    manifest_path: Path,
    result_root: Path,
    unit_id: str,
    *,
    expected_manifest_sha256: str | None = None,
    expected_source_calls_sha256: str | None = None,
    expected_preflight_sha256: str | None = None,
) -> dict[str, Any]:
    """Resolve one exact unit from an authoritative, confined demux manifest."""
    root = Path(result_root)
    if root.is_symlink():
        raise ValueError("authoritative result root symlink is forbidden")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("authoritative result root must be a directory")
    requested = str(unit_id).strip()
    if not _UNIT_ID.fullmatch(requested):
        raise ValueError("unknown or malformed barcode unit")
    manifest = _confined(Path(manifest_path), root, "barcode manifest")
    try:
        manifest_bytes = manifest.read_bytes()
    except OSError as exc:
        raise ValueError("barcode manifest is unreadable or malformed") from exc
    observed_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if expected_manifest_sha256 is not None and (
        not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256)
        or observed_manifest_sha256 != expected_manifest_sha256
    ):
        raise ValueError("barcode manifest does not match the source job terminal-product anchor")
    try:
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("barcode manifest is unreadable or malformed") from exc
    if payload.get("schema") not in {"biomodstack.dorado_demux.v1", "biomodstack.dorado_barcode_units.v1"}:
        raise ValueError("barcode manifest schema is unsupported")
    units = payload.get("units")
    if not isinstance(units, list):
        raise ValueError("barcode manifest units must be a list")
    unit_ids = [item.get("unit_id") for item in units if isinstance(item, dict)]
    if len(unit_ids) != len(units) or len(set(unit_ids)) != len(unit_ids):
        raise ValueError("barcode manifest units must have unique identifiers")
    if payload.get("schema") == "biomodstack.dorado_demux.v1":
        counts = [item.get("read_count") for item in units]
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("barcode manifest read counts are invalid")
        source = payload.get("source_calls")
        if not isinstance(source, dict) or payload.get("total_reads") != sum(counts) or source.get("read_count") != sum(counts):
            raise ValueError("barcode manifest source/unit read-count parity failed")
    matches = [item for item in units if isinstance(item, dict) and item.get("unit_id") == requested]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate barcode unit: {requested}")
    item = matches[0]
    raw_path = Path(str(item.get("bam_path") or ""))
    if raw_path.is_absolute() or not raw_path.parts:
        raise ValueError("barcode BAM path must be a confined relative path")
    bam = _confined(manifest.parent / raw_path, root, "barcode BAM")
    expected = str(item.get("bam_sha256") or "").lower()
    observed = _sha256(bam)
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or observed != expected:
        raise ValueError("barcode BAM identity mismatch")
    raw_unit_manifest = Path(str(item.get("unit_manifest_path") or ""))
    expected_unit_manifest_sha = str(item.get("unit_manifest_sha256") or "").lower()
    if raw_unit_manifest.is_absolute() or not raw_unit_manifest.parts:
        raise ValueError("barcode unit manifest path must be a confined relative path")
    unit_manifest = _confined(manifest.parent / raw_unit_manifest, root, "barcode unit manifest")
    try:
        unit_manifest_bytes = unit_manifest.read_bytes()
    except OSError as exc:
        raise ValueError("barcode unit manifest is unreadable or malformed") from exc
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_unit_manifest_sha)
        or hashlib.sha256(unit_manifest_bytes).hexdigest() != expected_unit_manifest_sha
    ):
        raise ValueError("barcode unit manifest identity mismatch")
    try:
        unit_payload = json.loads(unit_manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("barcode unit manifest is unreadable or malformed") from exc
    if unit_payload.get("schema") != "biomodstack.dorado_barcode_unit.v1" or any(
        unit_payload.get(key) != item.get(key)
        for key in (
            "unit_id",
            "sample_alias",
            "bam_path",
            "bam_sha256",
            "read_count",
            "source_calls_sha256",
            "preflight_sha256",
        )
    ):
        raise ValueError("barcode unit manifest does not match the aggregate manifest")
    source_calls_sha256 = str(unit_payload.get("source_calls_sha256") or "").lower()
    preflight_sha256 = str(unit_payload.get("preflight_sha256") or "").lower()
    if payload.get("schema") == "biomodstack.dorado_demux.v1":
        source = payload.get("source_calls") if isinstance(payload.get("source_calls"), dict) else {}
        expected_source_calls_sha256 = expected_source_calls_sha256 or str(source.get("sha256") or "").lower()
        expected_preflight_sha256 = expected_preflight_sha256 or str(payload.get("preflight_sha256") or "").lower()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", source_calls_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", preflight_sha256)
        or source_calls_sha256 != str(expected_source_calls_sha256 or "").lower()
        or preflight_sha256 != str(expected_preflight_sha256 or "").lower()
    ):
        raise ValueError("barcode unit provenance does not match authoritative products")
    read_count = item.get("read_count")
    if isinstance(read_count, bool) or not isinstance(read_count, int) or read_count < 0:
        raise ValueError("barcode unit read_count is invalid")
    sample_alias = item.get("sample_alias")
    if sample_alias is not None and (
        not isinstance(sample_alias, str)
        or not _SAMPLE_ALIAS.fullmatch(sample_alias)
        or sample_alias == "unclassified"
        or re.fullmatch(r"barcode[0-9]+", sample_alias)
    ):
        raise ValueError("barcode unit sample_alias is invalid")
    return {
        "schema": "biomodstack.ont_barcode_resubmission_unit.v1",
        "unit_id": requested,
        "sample_alias": sample_alias,
        "bam_path": str(bam),
        "bam_sha256": observed,
        "read_count": read_count,
        "source_calls_sha256": source_calls_sha256,
        "preflight_sha256": preflight_sha256,
        "manifest_path": str(manifest),
        "manifest_sha256": observed_manifest_sha256,
        "unit_manifest_path": str(unit_manifest),
        "unit_manifest_sha256": expected_unit_manifest_sha,
    }


def load_barcode_units(
    manifest_path: Path,
    result_root: Path,
    *,
    expected_manifest_sha256: str | None = None,
    expected_source_calls_sha256: str | None = None,
    expected_preflight_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """List all exact units, validating every unit through the same fail-closed resolver."""
    manifest = Path(manifest_path)
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("barcode manifest is unreadable or malformed") from exc
    units = payload.get("units")
    if not isinstance(units, list):
        raise ValueError("barcode manifest units must be a list")
    identifiers = [str(item.get("unit_id") or "") for item in units if isinstance(item, dict)]
    if len(identifiers) != len(units):
        raise ValueError("barcode manifest units must be objects")
    return [
        load_barcode_unit(
            manifest,
            result_root,
            identifier,
            expected_manifest_sha256=expected_manifest_sha256,
            expected_source_calls_sha256=expected_source_calls_sha256,
            expected_preflight_sha256=expected_preflight_sha256,
        )
        for identifier in identifiers
    ]
