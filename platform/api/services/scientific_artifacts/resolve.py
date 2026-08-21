"""Resolve compact JSON references through the verified artifact data plane."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    ARTIFACT_REFERENCE_SCHEMA,
    ARTIFACT_ROW_REFERENCE_SCHEMA,
    require_artifact_reference,
    require_row_reference,
    reconstruct_envelope,
)
from .query import query_rows
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
