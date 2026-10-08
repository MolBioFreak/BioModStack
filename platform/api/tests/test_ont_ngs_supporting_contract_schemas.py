from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_ROOT = REPO_ROOT / "schemas" / "ngs"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def test_runtime_error_model_matches_the_normative_closed_enums() -> None:
    from routers.ngs_alignment_sessions import OntNgsErrorV1

    normative = _schema("ont_ngs_error_v1.schema.json")
    runtime = OntNgsErrorV1.model_json_schema(by_alias=True)
    assert runtime["properties"]["code"]["enum"] == normative["properties"]["code"]["enum"]
    assert runtime["properties"]["resource"]["enum"] == normative["properties"]["resource"]["enum"]


def test_governed_error_contract_rejects_unknown_codes_and_extra_fields() -> None:
    validator = Draft202012Validator(_schema("ont_ngs_error_v1.schema.json"), format_checker=FormatChecker())
    value = {
        "schema": "bms.ngs.error.v1",
        "code": "NGS_CAPABILITY_DENIED",
        "message": "Alignment access denied.",
        "job_id": "31f02bd5-830f-4558-aa78-3873c515de68",
        "resource": "result",
        "retryable": True,
    }
    assert list(validator.iter_errors(value)) == []
    assert list(validator.iter_errors({**value, "code": "UNKNOWN"}))
    assert list(validator.iter_errors({**value, "path": "/private/result"}))
