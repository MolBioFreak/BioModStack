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


class BarcodeProductContainmentError(ValueError):
    """Unsafe product paths are custody failures, not unavailable provenance."""


def _confined(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise BarcodeProductContainmentError(f"{label} symlink is forbidden")
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise BarcodeProductContainmentError(f"{label} must be confined beneath the authoritative result root") from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BarcodeProductContainmentError(f"{label} symlink is forbidden")
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _result_root(result_root: Path) -> Path:
    root = Path(result_root)
    if root.is_symlink():
        raise ValueError("authoritative result root symlink is forbidden")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("authoritative result root must be a directory")
    return root


def _read_barcode_manifest(
    manifest: Path, expected_manifest_sha256: str | None = None,
) -> tuple[dict[str, Any], str]:
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
    return payload, observed_manifest_sha256


def _validate_barcode_manifest(
    manifest_path: Path, result_root: Path, payload: dict[str, Any],
    observed_manifest_sha256: str, expected_manifest_sha256: str | None = None,
) -> tuple[Path, Path]:
    root = _result_root(result_root)
    manifest = _confined(Path(manifest_path), root, "barcode manifest")
    if expected_manifest_sha256 is not None and (
        not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256)
        or observed_manifest_sha256 != expected_manifest_sha256
    ):
        raise ValueError("barcode manifest does not match the source job terminal-product anchor")
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
    return root, manifest


def _resolve_barcode_unit(
    manifest: Path, root: Path, payload: dict[str, Any],
    observed_manifest_sha256: str, units_by_id: dict[str, dict[str, Any]],
    unit_id: str, *,
    expected_source_calls_sha256: str | None = None,
    expected_preflight_sha256: str | None = None,
) -> dict[str, Any]:
    requested = str(unit_id).strip()
    if not _UNIT_ID.fullmatch(requested):
        raise ValueError("unknown or malformed barcode unit")
    item = units_by_id.get(requested)
    if item is None:
        raise ValueError(f"unknown or duplicate barcode unit: {requested}")
    raw_path = Path(str(item.get("bam_path") or ""))
    if raw_path.is_absolute() or not raw_path.parts:
        raise BarcodeProductContainmentError("barcode BAM path must be a confined relative path")
    bam = _confined(manifest.parent / raw_path, root, "barcode BAM")
    expected = str(item.get("bam_sha256") or "").lower()
    observed = _sha256(bam)
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or observed != expected:
        raise ValueError("barcode BAM identity mismatch")
    raw_unit_manifest = Path(str(item.get("unit_manifest_path") or ""))
    expected_unit_manifest_sha = str(item.get("unit_manifest_sha256") or "").lower()
    if raw_unit_manifest.is_absolute() or not raw_unit_manifest.parts:
        raise BarcodeProductContainmentError("barcode unit manifest path must be a confined relative path")
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
        source = payload.get("source_calls")
        if not isinstance(source, dict):
            source = {}
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
    root = _result_root(result_root)
    if not _UNIT_ID.fullmatch(str(unit_id).strip()):
        raise ValueError("unknown or malformed barcode unit")
    manifest = _confined(Path(manifest_path), root, "barcode manifest")
    payload, digest = _read_barcode_manifest(manifest, expected_manifest_sha256)
    _validate_barcode_manifest(manifest, root, payload, digest, expected_manifest_sha256)
    return _resolve_barcode_unit(
        manifest, root, payload, digest,
        {item.get("unit_id"): item for item in payload["units"]}, unit_id,
        expected_source_calls_sha256=expected_source_calls_sha256,
        expected_preflight_sha256=expected_preflight_sha256,
    )


def load_barcode_units(
    manifest_path: Path,
    result_root: Path,
    *,
    expected_manifest_sha256: str | None = None,
    expected_source_calls_sha256: str | None = None,
    expected_preflight_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """List exact units, reading and validating the aggregate once per call."""
    manifest = Path(manifest_path)
    try:
        manifest_stat = manifest.stat() if expected_manifest_sha256 is not None else None
    except OSError as exc:
        raise ValueError("barcode manifest is unreadable or malformed") from exc
    payload, digest = _read_barcode_manifest(manifest)
    return _resolve_barcode_catalogs(
        [(manifest, payload, digest)], result_root,
        manifest_stats={manifest: manifest_stat},
        expected_manifest_sha256=expected_manifest_sha256,
        expected_source_calls_sha256=expected_source_calls_sha256,
        expected_preflight_sha256=expected_preflight_sha256,
    )[0]


def _resolve_barcode_catalogs(
    catalogs: list[tuple[Path, dict[str, Any], str]],
    result_root: Path,
    *,
    manifest_stats: dict[Path, Any] | None = None,
    expected_manifest_sha256: str | None = None,
    expected_source_calls_sha256: str | None = None,
    expected_preflight_sha256: str | None = None,
) -> list[list[dict[str, Any]]]:
    """Resolve freshly read catalogs; callers own their byte/digest authentication.

    Reuse only complete, type-exact unit metadata with the same relative-path
    base and explicit common provenance. Different catalogs retain independent
    validation, rather than acquiring a new equality/admission requirement.
    Nothing survives this operation.
    """
    results: list[list[dict[str, Any]]] = []
    previous_key = None
    for manifest, payload, digest in catalogs:
        original_manifest = manifest
        manifest_stat = (manifest_stats or {}).get(manifest)
        units = payload.get("units")
        if not isinstance(units, list):
            raise ValueError("barcode manifest units must be a list")
        identifiers = [str(item.get("unit_id") or "") for item in units if isinstance(item, dict)]
        if len(identifiers) != len(units):
            raise ValueError("barcode manifest units must be objects")
        # The existing list entrypoint accepts an empty list without unit checks.
        if not units:
            results.append([])
            previous_key = None
            continue
        root, manifest = _validate_barcode_manifest(
            manifest, result_root, payload, digest, expected_manifest_sha256,
        )
        key = (manifest.parent, json.dumps(units, sort_keys=True))
        if (
            key == previous_key
            and expected_source_calls_sha256
            and expected_preflight_sha256
        ):
            resolved = [dict(unit, manifest_path=str(manifest), manifest_sha256=digest) for unit in results[-1]]
        else:
            units_by_id = {item.get("unit_id"): item for item in units}
            resolved = [
                _resolve_barcode_unit(
                    manifest, root, payload, digest, units_by_id, identifier,
                    expected_source_calls_sha256=expected_source_calls_sha256,
                    expected_preflight_sha256=expected_preflight_sha256,
                )
                for identifier in identifiers
            ]
        if manifest_stat is not None:
            # Preserve the existing anchored-list replacement check without
            # rereading unchanged catalog bodies for each unit or catalog.
            current_manifest = _confined(original_manifest, root, "barcode manifest")
            current_stat = current_manifest.stat()
            if any(getattr(current_stat, field) != getattr(manifest_stat, field) for field in (
                "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
            )):
                try:
                    current_bytes = current_manifest.read_bytes()
                except OSError as exc:
                    raise ValueError("barcode manifest is unreadable or malformed") from exc
                if hashlib.sha256(current_bytes).hexdigest() != digest:
                    raise ValueError("barcode manifest does not match the source job terminal-product anchor")
        results.append(resolved)
        previous_key = key
    return results
