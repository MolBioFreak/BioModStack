"""Shared contracts for immutable scientific table artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

ARTIFACT_REFERENCE_SCHEMA = "bms.scientific-artifact-reference.v1"
ARTIFACT_ROW_REFERENCE_SCHEMA = "bms.scientific-artifact-row-reference.v1"
ARTIFACT_TABLE_SCHEMA = "bms.scientific-artifact-table.v1"
ARTIFACT_AVAILABLE = "available"
ARTIFACT_STATES = frozenset({"staged", ARTIFACT_AVAILABLE, "unavailable", "integrity_failed"})
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def safe_component(value: str, *, max_length: int = 80) -> str:
    text = _SAFE_COMPONENT.sub("_", str(value)).strip("._") or "item"
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 13]}-{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def artifact_reference(
    *,
    artifact_id: str,
    owner_kind: str,
    owner_id: str,
    role: str,
    schema_id: str,
    schema_version: int,
    content_sha256: str,
    size_bytes: int,
    row_count: int,
    relative_path: str,
) -> dict[str, Any]:
    return {
        "schema": ARTIFACT_REFERENCE_SCHEMA,
        "schema_version": 1,
        "artifact_id": artifact_id,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "role": role,
        "schema_id": schema_id,
        "schema_version_data": schema_version,
        "content_sha256": content_sha256,
        "size_bytes": int(size_bytes),
        "row_count": int(row_count),
        "relative_path": relative_path,
    }


def artifact_row_reference(
    installed: Mapping[str, Any], row_locator: int, *, value_field: str | None = None
) -> dict[str, Any]:
    if int(row_locator) < 0:
        raise ValueError("artifact row locator must be non-negative")
    if value_field is None:
        raise ValueError("artifact row reference requires a value field")
    return {
        "schema": ARTIFACT_ROW_REFERENCE_SCHEMA,
        "v": 1,
        "artifact_id": str(installed["artifact_id"]),
        "p": str(installed["relative_path"]),
        "h": str(installed["content_sha256"]),
        "z": int(installed["size_bytes"]),
        "n": int(installed["row_count"]),
        "i": int(row_locator),
        "f": str(value_field),
    }


def is_artifact_reference(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("schema") in {
        ARTIFACT_REFERENCE_SCHEMA,
        ARTIFACT_ROW_REFERENCE_SCHEMA,
    }


def require_row_reference(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != ARTIFACT_ROW_REFERENCE_SCHEMA:
        raise ValueError("value is not a scientific artifact row reference")
    compact_required = {
        "schema", "v", "artifact_id", "p", "h", "z", "n", "i", "f",
    }
    expanded_required = {
        "schema", "schema_version", "artifact_id", "relative_path", "content_sha256",
        "size_bytes", "row_count", "row_locator", "value_field",
    }
    keys = frozenset(value)
    if keys == frozenset(compact_required):
        normalized = {
            "schema": value["schema"],
            "schema_version": value["v"],
            "artifact_id": value["artifact_id"],
            "relative_path": value["p"],
            "content_sha256": value["h"],
            "size_bytes": value["z"],
            "row_count": value["n"],
            "row_locator": value["i"],
            "value_field": value["f"],
        }
        if isinstance(value, dict):
            value.clear()
            value.update(normalized)
        value = normalized
    elif keys != frozenset(expanded_required):
        raise ValueError("scientific artifact row reference fields are not closed")
    if not isinstance(value["row_locator"], int) or value["row_locator"] < 0:
        raise ValueError("scientific artifact row locator is invalid")
    if not isinstance(value["value_field"], str) or not value["value_field"]:
        raise ValueError("scientific artifact value field is invalid")
    if not isinstance(value["content_sha256"], str) or len(value["content_sha256"]) != 64:
        raise ValueError("scientific artifact row reference hash is invalid")
    if not isinstance(value["size_bytes"], int) or value["size_bytes"] < 0:
        raise ValueError("scientific artifact row reference size is invalid")
    if not isinstance(value["row_count"], int) or value["row_count"] < 0:
        raise ValueError("scientific artifact row reference row count is invalid")
    return value


def require_artifact_reference(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != ARTIFACT_REFERENCE_SCHEMA:
        raise ValueError("value is not a scientific artifact reference")
    mapping: Mapping[str, Any] = value
    required = {
        "schema", "schema_version", "artifact_id", "owner_kind", "owner_id",
        "role", "schema_id", "schema_version_data", "content_sha256", "size_bytes",
        "row_count", "relative_path",
    }
    allowed = {
        frozenset(required),
        frozenset(required | {"row_locator"}),
        frozenset(required | {"row_locator", "value_field"}),
    }
    if frozenset(mapping) not in allowed:
        raise ValueError("scientific artifact reference fields are not closed")
    if "row_locator" in mapping and (
        not isinstance(mapping["row_locator"], int) or mapping["row_locator"] < 0
    ):
        raise ValueError("scientific artifact row locator is invalid")
    if "value_field" in mapping and (
        not isinstance(mapping["value_field"], str) or not mapping["value_field"]
    ):
        raise ValueError("scientific artifact value field is invalid")
    if not isinstance(mapping["content_sha256"], str) or len(mapping["content_sha256"]) != 64:
        raise ValueError("scientific artifact reference hash is invalid")
    if not isinstance(mapping["size_bytes"], int) or mapping["size_bytes"] < 0:
        raise ValueError("scientific artifact reference size is invalid")
    if not isinstance(mapping["row_count"], int) or mapping["row_count"] < 0:
        raise ValueError("scientific artifact reference row count is invalid")
    return mapping


def envelope_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Represent a JSON object as deterministic rows without changing values."""
    rows: list[dict[str, Any]] = []
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, list):
            if not value:
                rows.append({"key": str(key), "item_index": -1, "payload_json": "[]"})
            else:
                for index, item in enumerate(value):
                    rows.append({"key": str(key), "item_index": index, "payload_json": json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)})
        else:
            rows.append({"key": str(key), "item_index": -1, "payload_json": json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)})
    return rows


def reconstruct_envelope(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["key"]), []).append(row)
    result: dict[str, Any] = {}
    for key, items in grouped.items():
        ordered = sorted(items, key=lambda item: int(item["item_index"]))
        if len(ordered) == 1 and int(ordered[0]["item_index"]) == -1:
            result[key] = json.loads(str(ordered[0]["payload_json"]))
        else:
            result[key] = [json.loads(str(item["payload_json"])) for item in ordered]
    return result
