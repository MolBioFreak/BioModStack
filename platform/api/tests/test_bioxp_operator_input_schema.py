"""Catalog input metadata relay: no new admission semantics."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import OperatorInputSpec


def test_legacy_catalog_inputs_remain_compatible():
    catalog = json.loads((Path(__file__).parent / "fixtures/bioxp_retained_catalog_v1.json").read_text())
    inputs = [item for action in catalog["actions"] for item in action["inputs"] if "value_type" in item]
    assert inputs
    for item in inputs:
        assert OperatorInputSpec.model_validate(item).json_schema is None


def test_nested_schema_and_typed_defaults_roundtrip_without_loss():
    schema = {"anyOf": [{"type": "object", "properties": {
        "channels": {"type": "array", "items": {"type": "integer", "enum": [0, 1, 2, 3]}},
        "zero": {"type": "number", "default": 0},
        "false": {"type": "boolean", "default": False},
        "empty": {"type": "string", "default": ""},
    }, "additionalProperties": False}, {"type": "null"}], "default": None}
    item = OperatorInputSpec(name="context", label="Context", value_type="json", required=False, json_schema=schema)
    assert item.model_dump()["json_schema"] == schema
    assert OperatorInputSpec.model_validate_json(item.model_dump_json()).json_schema == schema
    with pytest.raises(ValidationError):
        OperatorInputSpec(name="context", label="Context", value_type="json", required=False, json_schema=[])
    with pytest.raises(ValidationError):
        OperatorInputSpec(name="context", label="Context", value_type="json", required=False, unowned_gate=True)
