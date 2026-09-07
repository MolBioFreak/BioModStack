"""Regression for safe GET history captured from the ea848 legacy producer."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from services.bioxp.operator_models import OperatorActionHistory, OperatorActionReceipt

FIXTURE = Path(__file__).parent / "fixtures/bioxp_legacy_history_ea848.json"
FLAGS = ("completion_ambiguous", "completion_verified", "delivery_verified",
         "hardware_postcondition_verified", "hardware_precondition_verified",
         "reconciliation_required", "retry_forbidden")


def payload():
    return json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("limit", [8, 25])
def test_live_legacy_history_preserves_evidence(limit):
    raw = payload()
    raw["receipts"] = raw["receipts"][:limit]
    result = OperatorActionHistory.model_validate(raw).model_dump(mode="json")
    for before, after in zip(raw["receipts"][:4], result["receipts"][:4]):
        for key in (*FLAGS, "source_identity", "physical_effect_verified", "status"):
            assert after[key] == before[key]


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("bad", [0, 1, "false", None])
def test_history_flags_are_strict(flag, bad):
    raw = payload()
    raw["receipts"][0][flag] = bad
    with pytest.raises(ValidationError):
        OperatorActionHistory.model_validate(raw)


def test_history_identity_and_authority_remain_closed():
    for change in ("extra", "hash", "boolean", "authority"):
        raw = payload()
        row = raw["receipts"][0]
        if change == "extra":
            row["source_identity"]["unexpected"] = True
        elif change == "hash":
            row["source_identity"]["registry_sha256"] = "not-a-hash"
        elif change == "boolean":
            row["source_identity"]["release_verified"] = 1
        else:
            row["authority_receipt_id"] = "unpaired"
        with pytest.raises(ValidationError):
            OperatorActionHistory.model_validate(raw)


def test_mutation_receipt_contract_is_not_widened():
    with pytest.raises(ValidationError):
        OperatorActionReceipt.model_validate(payload()["receipts"][0])
