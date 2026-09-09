"""Offline consumer checks of complete saved robot GET responses.

Fixtures are unchanged audit captures, including the private producer marker.
Only the producer's public projection edits are applied; no rows/evidence are
normalized to fit the consumer. No robot/runtime/network access is required.
"""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import (
    OperatorActionHistory, OperatorActionReceipt, OperatorControlCatalog,
)

FIXTURES = Path(__file__).parent / "fixtures"


def history():
    payload = json.loads((FIXTURES / "bioxp_retained_durable_history_v1.json").read_text())
    for row in payload["receipts"]:
        row.pop("__projection_source", None)
    return payload


def test_complete_retained_history_preserves_all_25_records_and_evidence():
    raw = history()
    assert len(raw["receipts"]) == 25
    result = OperatorActionHistory.model_validate(raw).model_dump(
        mode="json", by_alias=True, exclude_unset=True,
    )
    assert result == raw
    assert [r["command_id"] for r in result["receipts"][23:]] == [
        "882ffcef-25f7-4ad9-ab35-c53accd5c89e",
        "867f8a2e-c940-4f0e-9679-3bdb4269e49a",
    ]


def test_complete_saved_catalog_after_canonical_exclusion():
    raw = json.loads((FIXTURES / "bioxp_retained_catalog_v1.json").read_text())
    with pytest.raises(ValidationError) as exc:
        OperatorControlCatalog.model_validate(raw)
    assert exc.value.error_count() == 12
    excluded = [a for a in raw["actions"] if a["kind"] == "canonical_method"]
    assert [a["action_id"] for a in excluded] == ["oem.deck.move_to_location"]
    raw["actions"] = [a for a in raw["actions"] if a["kind"] != "canonical_method"]
    result = OperatorControlCatalog.model_validate(raw)
    assert len(result.actions) == 237
    assert result.model_dump(mode="json", by_alias=True, exclude_unset=True) == raw


@pytest.mark.parametrize("index", [23, 24])
def test_durable_variant_does_not_widen_mutation_receipts(index):
    with pytest.raises(ValidationError):
        OperatorActionReceipt.model_validate(history()["receipts"][index])


@pytest.mark.parametrize("field,bad", [
    ("schema_version", "bioxp.operator_command_receipt.v2"),
    ("source", "current_operator_plane"), ("status", "succeeded"),
    ("stored_status", "succeeded"), ("automatic_retry", True),
    ("automatic_retry", 0), ("automatic_retry", "false"),
    ("controller_acknowledged", 1), ("remote_acknowledged", "false"),
    ("physical_effect_verified", 0), ("ownership_generation", True),
    ("stream_sequence", -1), ("accepted_at", float("inf")),
    ("state_version", "3"), ("expected_board_epoch_by_board", {"4": True}),
    ("recovery_required", True), ("physical_outcome", "verified"),
    ("__projection_source", "durable"), ("unknown_authority", True),
])
def test_durable_history_rejects_unknown_coerced_or_contradictory_fields(field, bad):
    row = history()["receipts"][23]
    row[field] = bad
    with pytest.raises(ValidationError):
        OperatorActionHistory.model_validate({
            "schema_version": "bioxp.operator_action_history.v1", "receipts": [row],
        })


def test_durable_projection_requires_every_recorded_field():
    row = history()["receipts"][23]
    for field in row:
        incomplete = copy.deepcopy(row)
        del incomplete[field]
        with pytest.raises(ValidationError):
            OperatorActionHistory.model_validate({
                "schema_version": "bioxp.operator_action_history.v1", "receipts": [incomplete],
            })


@pytest.mark.parametrize("stored", [
    "queued", "dispatched", "issued_pending", "stop_requested", "abort_requested",
    "completed", "failed", "ambiguous", "stopped", "aborted", "cancelled", "cleared", "interrupted",
])
def test_legacy_reader_terminal_and_restart_projection_branches(stored):
    row = history()["receipts"][23]
    nonterminal = stored in {"queued", "dispatched", "issued_pending", "stop_requested", "abort_requested"}
    row.update(stored_status=stored, status="ambiguous" if nonterminal else stored,
               recovery_required=nonterminal, physical_outcome="ambiguous" if nonterminal else None,
               terminal_evidence=None, terminal_receipt_id=None, completion_class=None,
               dispatched_at=None, finished_at=None, transition_sequence=None)
    raw = {"schema_version": "bioxp.operator_action_history.v1", "receipts": [row]}
    result = OperatorActionHistory.model_validate(raw)
    assert result.model_dump(mode="json", exclude_unset=True) == raw
    row["status"] = "completed" if nonterminal else "queued"
    with pytest.raises(ValidationError):
        OperatorActionHistory.model_validate(raw)
