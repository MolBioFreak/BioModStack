"""Package-local runtime implementation authority for NGS/MolBio phases N1-N6."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785  # type: ignore[import-not-found]
from jsonschema import Draft202012Validator

from services.ngs_molbio_capabilities import capability_inventory

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECORD = _REPO_ROOT / "platform/api/config/ngs_molbio_runtime/runtime_implementation_v1.json"
_SCHEMA = _REPO_ROOT / "schemas/ngs_molbio_runtime/runtime-implementation-v1.schema.json"
_DENOMINATOR_RELATIVE = "schemas/ngs_molbio_runtime/runtime-source-denominator-v1.json"
_DENOMINATOR = _REPO_ROOT / _DENOMINATOR_RELATIVE
_N0_RECEIPT = _REPO_ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json"


class NgsMolBioRuntimeAuthorityError(RuntimeError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NgsMolBioRuntimeAuthorityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except NgsMolBioRuntimeAuthorityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NgsMolBioRuntimeAuthorityError(f"runtime authority is unreadable: {path}") from exc
    if type(value) is not dict:
        raise NgsMolBioRuntimeAuthorityError(f"runtime authority must be an object: {path}")
    return value, raw


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_sha256(document: dict[str, Any]) -> str:
    value = dict(document)
    value.pop("content_sha256", None)
    return _sha256(rfc8785.dumps(value))


def _source_denominator() -> dict[str, Any]:
    denominator, _raw = _read(_DENOMINATOR)
    paths = denominator.get("paths")
    if (
        set(denominator) != {"schema", "paths", "content_sha256"}
        or denominator.get("schema") != "bms.ngs-molbio.runtime-source-denominator.v1"
        or type(paths) is not list
        or not paths
        or len(paths) > 256
        or any(type(path) is not str or not path or path.startswith("/") for path in paths)
        or any(".." in Path(path).parts for path in paths)
        or len(paths) != len(set(paths))
        or _DENOMINATOR_RELATIVE not in paths
        or denominator.get("content_sha256") != _content_sha256(denominator)
    ):
        raise NgsMolBioRuntimeAuthorityError("runtime source denominator is invalid or digest-divergent")
    return denominator


def runtime_implementation_record() -> dict[str, Any]:
    schema, _schema_raw = _read(_SCHEMA)
    denominator = _source_denominator()
    record, _record_raw = _read(_RECORD)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise NgsMolBioRuntimeAuthorityError(
            f"runtime implementation record is invalid at {location}: {errors[0].message}"
        )
    if record["content_sha256"] != _content_sha256(record):
        raise NgsMolBioRuntimeAuthorityError("runtime implementation content digest mismatch")
    if record.get("source_denominator") != {
        "path": _DENOMINATOR_RELATIVE,
        "content_sha256": denominator["content_sha256"],
    }:
        raise NgsMolBioRuntimeAuthorityError("runtime record source denominator binding mismatch")
    capability_inventory()
    n0_receipt, _n0_raw = _read(_N0_RECEIPT)
    n0_content_hash = n0_receipt.get("content_sha256")
    if not isinstance(n0_content_hash, str) or n0_content_hash != _content_sha256(n0_receipt):
        raise NgsMolBioRuntimeAuthorityError("N0 verification receipt content digest mismatch")
    if record.get("n0_receipt_content_sha256") != n0_content_hash:
        raise NgsMolBioRuntimeAuthorityError("runtime record is not bound to the installed N0 receipt")
    if record.get("n0_package_fingerprint") != n0_receipt.get("payload_fingerprint_sha256"):
        raise NgsMolBioRuntimeAuthorityError("runtime record N0 package fingerprint mismatch")
    if (
        type(record.get("successor_source_commit")) is not str
        or type(record.get("successor_source_tree")) is not str
        or len(record["successor_source_commit"]) != 40
        or len(record["successor_source_tree"]) != 40
    ):
        raise NgsMolBioRuntimeAuthorityError("runtime successor source identity is invalid")
    expected_paths = denominator["paths"]
    actual_paths = [row["path"] for row in record["source_authorities"]]
    if actual_paths != expected_paths:
        raise NgsMolBioRuntimeAuthorityError(
            "runtime source denominator path set or order is incomplete, duplicated, or divergent"
        )
    for row in record["source_authorities"]:
        relative = row["path"]
        path = (_REPO_ROOT / relative).resolve()
        try:
            path.relative_to(_REPO_ROOT.resolve())
            raw = path.read_bytes()
        except (ValueError, OSError) as exc:
            raise NgsMolBioRuntimeAuthorityError(
                f"runtime source authority is unavailable: {relative}"
            ) from exc
        if len(raw) != row["size_bytes"] or _sha256(raw) != row["sha256"]:
            raise NgsMolBioRuntimeAuthorityError(
                f"runtime source authority digest or size mismatch: {relative}"
            )
    phase_ids = [row["phase_id"] for row in record["phases"]]
    if phase_ids != ["N1", "N2", "N3", "N4", "N5", "N6"]:
        raise NgsMolBioRuntimeAuthorityError("runtime phase order or denominator is invalid")
    return copy.deepcopy(record)


__all__ = ["NgsMolBioRuntimeAuthorityError", "runtime_implementation_record"]
