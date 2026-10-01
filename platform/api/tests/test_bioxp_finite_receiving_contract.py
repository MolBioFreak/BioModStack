"""Finite-script receiving compatibility, using digest-bound real producer exports.

No hardware or live HTTP: only the existing robot-client exchange is replaced.
Negative mutations are controlled invalid envelopes, not producer evidence.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from routers.bioxp import operator_controls as receiving
from test_bioxp_c1_receiving_contract import assert_supplied_fields_preserved
from test_bioxp_operator_controls import make_client


FINITE = [
    ("document-public-child_failure.json", "5a2dfe5a-f388-4d66-bd8b-a80ab8379dfc"),
    ("document-public-stop.json", "fe6dd5bb-a701-46e2-a6a8-4d4260258580"),
    ("document-public-success.json", "2a886fb7-f3b2-42d9-8db3-113db5da5b11"),
    ("document-public-success.json", "2f6d1964-4207-4849-a1d1-2c13b655f7b6"),
    ("document-public-success.json", "b03ab065-eb88-4995-95dc-8d40ddeba546"),
    ("inspection-public-ack_without_target.json", "6596a787-a13d-4d2a-95c7-fa76a1950426"),
    ("inspection-public-after_ack.json", "1628c2aa-0688-48b2-8c11-3beb52856494"),
    ("inspection-public-before_ack.json", "52dbd105-4164-45ce-87c8-1211d60da42e"),
    ("inspection-public-same_position.json", "77cce895-a9d3-4306-ba97-cf2780f8d780"),
]


def raw_detail(filename, command_id):
    root = Path(os.environ["RECEIVING_FINAL_ROOT"])
    manifest = json.loads(Path(os.environ["FINITE_RECEIVING_MANIFEST"]).read_text())
    body = (root / filename).read_bytes()
    assert len(body) == manifest[filename]["bytes"]
    assert hashlib.sha256(body).hexdigest() == manifest[filename]["sha256"]
    return json.loads(body)[command_id]


def get_detail(monkeypatch, upstream):
    client, runtime = make_client(monkeypatch, mutations=False)
    runtime.connection.client.responses["operator_action_receipt_v2_detail"] = upstream
    response = client.get(
        "/api/bioxp/operator-controls/v2/receipts/"
        + upstream["command_id"] + "?detail=true"
    )
    assert len(runtime.connection.client.calls) == 1
    client.close()
    return response


@pytest.mark.parametrize("filename,command_id", FINITE, ids=[cid for _, cid in FINITE])
def test_actual_completed_finite_public_detail(monkeypatch, filename, command_id):
    raw = raw_detail(filename, command_id)
    original = copy.deepcopy(raw)
    assert raw["action_id"] == "oem.deck._finite_operation"
    assert raw["status"] == "completed" and raw["terminal"] is True
    assert raw["deck_movement"] is None
    assert raw["physical_effect_verified"] is False
    response = get_detail(monkeypatch, raw)
    assert response.status_code == 200, response.text
    actual = response.json()
    assert_supplied_fields_preserved(actual, receiving._normalize_interrupt_evidence(raw))
    assert actual["deck_movement"] is None
    assert actual["physical_effect_verified"] is False
    assert actual["raw_return_layers"] == raw["raw_return_layers"]
    assert actual["source_receipt"] == raw["source_receipt"]
    assert raw == original


NEGATIVES = [
    "malformed_deck", "deck_extra", "deck_non_strict", "physical_without_deck",
    "physical_with_unverified_deck", "deck_physical_exceeds_parent",
    "completed_move_without_deck", "completed_mov_execution_without_deck",
    "unbound_reconciliation", "wrong_recovery_command", "wrong_recovery_sequence",
    "completed_recovery", "mismatched_target", "unrelated_action_with_deck",
    "extra_envelope", "non_strict_physical",
]
