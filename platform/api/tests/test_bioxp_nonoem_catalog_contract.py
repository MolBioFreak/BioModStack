"""Record-only deck metadata survives the real BMS consumer boundary."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from routers.bioxp.operator_controls import _normalize_interrupt_evidence, router
from services.bioxp.operator_models import OperatorActionReceiptDetailV2, OperatorControlCatalogV2
from test_bioxp_camera_boundary import Boundary
from test_serial206_bioxp_v2_models import _serial206_catalog_payload


@pytest.mark.parametrize("epochs", [None, {}, {"4": 2}, {"4": 2, "5": 8}])
@pytest.mark.parametrize("references", [[], ["x", "y", "z", "g"]])
def test_catalog_evidence_is_not_consumer_admission(tmp_path, epochs, references):
    payload = _serial206_catalog_payload()
    action = payload["actions"][0]
    action["expected_board_epoch_by_board"] = epochs
    action["required_references"] = references
    payload["dashboard"]["deck"]["ambiguity_state"] = "recovery_required"

    async def scenario():
        boundary = Boundary(tmp_path)
        boundary.app.include_router(router)
        await boundary.connect()
        calls = []

        async def transport(request):
            calls.append((request.method, request.url.path))
            assert request.method == "GET"
            assert request.url.path == "/operator/v2/control-catalog"
            return httpx.Response(200, json=payload)

        boundary.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=boundary.app), base_url="http://bms") as client:
                result = await client.get("/operator-controls/v2/catalog")
            assert result.status_code == 200, result.text
            row = result.json()["actions"][0]
            assert row["enabled"] is True
            assert row["expected_board_epoch_by_board"] == epochs
            assert row["required_references"] == references
            assert result.json()["dashboard"]["deck"]["ambiguity_state"] == "recovery_required"
            assert calls == [("GET", "/operator/v2/control-catalog")]
        finally:
            await boundary.connection.disconnect()

    asyncio.run(scenario())


def test_robot_denial_stays_a_denial_without_reference_prerequisites():
    payload = _serial206_catalog_payload(enabled=False)
    payload["actions"][0]["required_references"] = []
    result = OperatorControlCatalogV2.model_validate(payload)
    assert result.actions[0].enabled is False
    assert result.actions[0].disabled_reason == payload["actions"][0]["disabled_reason"]


@pytest.mark.parametrize("epochs", [{"04": 2}, {"6": 2}, {"4": True}, {"5": -1}])
def test_epoch_observation_values_remain_typed(epochs):
    payload = _serial206_catalog_payload()
    payload["actions"][0]["expected_board_epoch_by_board"] = epochs
    with pytest.raises(ValidationError):
        OperatorControlCatalogV2.model_validate(payload)


def test_actual_networkless_robot_producer_exports():
    export = os.environ.get("NON_OEM_NATIVE_EXPORT_DIR")
    if not export:
        pytest.skip("requires frozen networkless robot producer exports")
    paths = sorted(Path(export).glob("*.json"))
    assert len(paths) == 6
    cases = set()
    for path in paths:
        raw = json.loads(path.read_text())
        catalog = OperatorControlCatalogV2.model_validate(_normalize_interrupt_evidence(raw["catalog"]))
        receipt = OperatorActionReceiptDetailV2.model_validate(raw["receipt"])
        action = next(row for row in catalog.actions if row.action_id == "oem.deck.move_to_location")
        assert action.required_references == []
        assert receipt.status == "completed"
        assert receipt.physical_effect_verified is False
        cases.add((raw["reference_mode"], len(raw["caller_epochs"])))
    assert cases == {(mode, count) for mode in ("desynced", "missing_store", "missing_rows") for count in (0, 2)}
