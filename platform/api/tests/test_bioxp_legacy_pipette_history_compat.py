"""Offline regressions for the two preserved pre-semantic-query pipette rows.

Fixture retains the captured 2026-09-06 history shapes (indices 23/24), with
identifiers, timestamps and hashes replaced; no full live ledger is stored.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import OperatorActionHistory, PipetteReceipt

FIXTURE = Path(__file__).parent / "fixtures/bioxp_legacy_pipette_history.json"


def payload():
    return json.loads(FIXTURE.read_text())


def test_legacy_pipette_history_preserves_every_row_and_field():
    raw = payload()
    result = OperatorActionHistory.model_validate(raw).model_dump(mode="json", by_alias=True)
    assert result == raw
    assert all("semantic_query_response_verified" not in row["truth"] for row in result["receipts"])


@pytest.mark.parametrize("value", [False, True])
def test_current_pipette_history_preserves_explicit_semantic_evidence(value):
    raw = payload()
    for row in raw["receipts"]:
        row["truth"]["semantic_query_response_verified"] = value
    assert OperatorActionHistory.model_validate(raw).model_dump(mode="json", by_alias=True) == raw


@pytest.mark.parametrize("field", [
    "delivery_verified", "controller_acknowledged", "completion_verified",
    "hardware_precondition_verified", "hardware_postcondition_verified",
])
@pytest.mark.parametrize("bad", [0, 1, "false", None])
def test_legacy_pipette_history_truth_remains_strict(field, bad):
    raw = payload()
    raw["receipts"][0]["truth"][field] = bad
    with pytest.raises(ValidationError):
        OperatorActionHistory.model_validate(raw)


@pytest.mark.parametrize("change", ["extra_truth", "extra_receipt", "missing_truth",
                                   "invalid_semantic", "null_semantic", "physical_claim",
                                   "unsuppressed_claim", "invalid_source"])
def test_legacy_pipette_history_rejects_unknown_or_incomplete_evidence(change):
    raw = payload()
    row = raw["receipts"][0]
    if change == "extra_truth":
        row["truth"]["unexpected"] = False
    elif change == "extra_receipt":
        row["unexpected"] = False
    elif change == "missing_truth":
        del row["truth"]["delivery_verified"]
    elif change == "invalid_semantic":
        row["truth"]["semantic_query_response_verified"] = "false"
    elif change == "null_semantic":
        row["truth"]["semantic_query_response_verified"] = None
    elif change == "physical_claim":
        row["truth"]["physical_effect_verified"] = True
    elif change == "unsuppressed_claim":
        row["truth"]["physical_effect_claim_suppressed"] = False
    else:
        row["source_identity"]["registry_sha256"] = "invalid"
    with pytest.raises(ValidationError):
        OperatorActionHistory.model_validate(raw)


@pytest.mark.parametrize("index", [0, 1])
def test_pipette_mutation_receipt_still_requires_semantic_query_evidence(index):
    row = payload()["receipts"][index]
    for key in ("status", "outcome", "controller_acknowledged", "physical_effect_verified"):
        del row[key]
    with pytest.raises(ValidationError) as error:
        PipetteReceipt.model_validate(row)
    assert any(e["loc"] == ("truth", "semantic_query_response_verified") and e["type"] == "missing"
               for e in error.value.errors())
