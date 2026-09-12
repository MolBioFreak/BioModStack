"""Resolve compact JSON references through the verified artifact data plane."""
from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    ARTIFACT_REFERENCE_SCHEMA,
    ARTIFACT_ROW_REFERENCE_SCHEMA,
    require_artifact_reference,
    require_row_reference,
    reconstruct_envelope,
)
from .query import ArtifactQuery, artifact_query, query_rows
from .writer import read_rows


def _mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def resolve_json_value(value: object, *, root: Path | str | None = None) -> Any:
    mapping = _mapping(value)
    if mapping is None or mapping.get("schema") not in {
        ARTIFACT_REFERENCE_SCHEMA,
        ARTIFACT_ROW_REFERENCE_SCHEMA,
    }:
        return value
    if mapping["schema"] == ARTIFACT_ROW_REFERENCE_SCHEMA:
        reference = require_row_reference(mapping)
    else:
        reference = require_artifact_reference(mapping)
    if "row_locator" not in reference:
        return reconstruct_envelope(read_rows(reference, root=root))
    field = reference.get("value_field")
    if not isinstance(field, str) or not field:
        raise ValueError("row-bound artifact reference lacks value_field")
    rows = query_rows(
        reference,
        columns=[field],
        limit=1,
        offset=int(reference["row_locator"]),
        root=str(root) if root is not None else None,
    )
    if len(rows) != 1:
        raise ValueError("row-bound artifact reference points outside the artifact")
    raw = rows[0][field]
    if field.endswith("_json"):
        return json.loads(str(raw))
    return raw


def resolve_json_envelope_fields(
    reference: Mapping[str, Any],
    *,
    keys: Sequence[str],
    root: Path | str | None = None,
    max_items_per_key: int = 128,
    query: ArtifactQuery | None = None,
) -> dict[str, Any]:
    """Resolve a closed, small field projection from a JSON-envelope artifact."""
    if not keys:
        raise ValueError("JSON-envelope field projection cannot be empty")
    rows: list[dict[str, Any]] = []
    with nullcontext(query) if query is not None else artifact_query(reference, root=root) as reader:
        for key in dict.fromkeys(keys):
            projected = reader.query_rows(
                columns=["key", "item_index", "payload_json"],
                limit=max_items_per_key + 1,
                max_limit=max_items_per_key + 1,
                filters={"key": key},
                order_by=["item_index"],
            )
            if len(projected) > max_items_per_key:
                raise ValueError(f"JSON-envelope field {key!r} exceeds the bounded projection")
            rows.extend(projected)
    return reconstruct_envelope(rows)


def query_json_envelope_page(
    reference: Mapping[str, Any],
    *,
    key: str,
    offset: int,
    limit: int,
    root: Path | str | None = None,
    max_limit: int = 100,
    query: ArtifactQuery | None = None,
) -> dict[str, Any]:
    """Read one bounded list field from a verified JSON-envelope artifact."""
    if not key or offset < 0 or not 1 <= limit <= max_limit:
        raise ValueError("JSON-envelope page is outside the supported bounds")
    with nullcontext(query) if query is not None else artifact_query(reference, root=root) as reader:
        total = reader.count_rows(filters={"key": key})
        rows = reader.query_rows(
            columns=["key", "item_index", "payload_json"],
            limit=limit,
            offset=offset,
            max_limit=max_limit,
            filters={"key": key},
            order_by=["item_index"],
        )
    page = rows[:limit]
    decoded = [json.loads(str(row["payload_json"])) for row in page]
    empty_collection = len(decoded) == 1 and page[0]["item_index"] == -1 and decoded[0] == []
    values = [] if empty_collection else decoded
    logical_total = 0 if empty_collection else total
    next_offset = offset + len(values) if offset + len(values) < logical_total else None
    return {
        "key": key,
        "offset": offset,
        "limit": limit,
        "total_count": logical_total,
        "next_offset": next_offset,
        "rows": values,
    }
