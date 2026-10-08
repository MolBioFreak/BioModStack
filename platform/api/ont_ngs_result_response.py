"""Typed OpenAPI surface for the normative ONT FASTQ-QC result contract."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, RootModel, model_validator


@lru_cache(maxsize=1)
def _normative_result_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "schemas" / "ngs" / "ont_fastq_qc_result_v1.schema.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("normative ONT FASTQ-QC result schema is unavailable") from exc
    if not isinstance(value, dict) or value.get("additionalProperties") is not False:
        raise RuntimeError("normative ONT FASTQ-QC result schema is not closed")
    return value


class OntFastqQcResultResponse(RootModel[dict[str, Any]]):
    """One response whose OpenAPI schema is the normative closed wire contract."""

    model_config = ConfigDict(json_schema_extra=_normative_result_schema())

    @model_validator(mode="before")
    @classmethod
    def validate_normative_contract(cls, value: Any) -> Any:
        from services.ont_ngs_results import (
            OntNgsResultError,
            validate_ont_fastq_qc_result_contract,
        )

        if not isinstance(value, dict):
            raise ValueError("ONT FASTQ-QC response must be an object")
        try:
            validate_ont_fastq_qc_result_contract(value)
        except OntNgsResultError as exc:
            raise ValueError(str(exc)) from exc
        return value
