"""Real completed deck-move receipts must validate.

The current producer sends ``canonical_inputs = {}`` for deck moves while the
plan target lives in the typed deck evidence. An absent plan target must not
turn a valid completed move into an invalid contract; a supplied plan target
still must match the deck evidence exactly.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import OperatorActionReceiptDetailV2

FIXTURE = Path(__file__).parent / "fixtures/bioxp_deck_move_completed_waste_bin.json"


def _receipt():
    return json.loads(FIXTURE.read_text())


def test_completed_move_receipt_with_empty_canonical_inputs_validates():
    receipt = _receipt()
    assert receipt["action_id"] == "oem.deck.move_to_location"
    assert receipt["canonical_inputs"] == {}
    assert receipt["deck_movement"]["target"] == "WASTE_BIN"
    model = OperatorActionReceiptDetailV2.model_validate(receipt)
    assert model.status == "completed"
    assert model.deck_movement.target == "WASTE_BIN"


def test_supplied_plan_target_still_must_match_deck_evidence():
    receipt = _receipt()
    receipt["canonical_inputs"] = {"target": "LOC_TC"}
    with pytest.raises(ValidationError, match="deck receipt target must match canonical inputs"):
        OperatorActionReceiptDetailV2.model_validate(receipt)


def test_supplied_matching_plan_target_passes():
    receipt = _receipt()
    receipt["canonical_inputs"] = {"target": "WASTE_BIN", "camera_offset": False}
    model = OperatorActionReceiptDetailV2.model_validate(receipt)
    assert model.deck_movement.target == "WASTE_BIN"
