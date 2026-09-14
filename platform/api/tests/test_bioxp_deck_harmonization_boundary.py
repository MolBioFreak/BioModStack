"""Deck harmonization through actual BMS connection/client/ASGI owners.

Transport is the only robot double. Optional native export supplements the
maintained contract negatives; no service, hardware, or motion replay occurs.
"""
import asyncio
import copy
import json
import os
from pathlib import Path

import httpx
import pytest

from routers.bioxp.operator_controls import router
from services.bioxp.errors import ConnectionStateError
from services.bioxp.operator_models import OperatorActionReceiptV2
from test_bioxp_camera_boundary import Boundary
from test_serial206_bioxp_v2_models import _park_noop_payload, _serial206_catalog_payload


@pytest.mark.parametrize("source", ["contract", "native"])
def test_deck_harmonization_receipt_boundary_survives_failed_readiness(tmp_path, source):
    if source == "native":
        path = os.environ.get("BMS_NATIVE_DECK_EXPORT")
        if not path:
            pytest.skip("requires isolated native producer/store/fresh-process export")
        raw = json.loads(Path(path).read_text())
        detail, compact = raw["detail"], raw["compact"]
    else:
        detail = _park_noop_payload()
        compact = {key: value for key, value in detail.items() if key in OperatorActionReceiptV2.model_fields}
    command_id = compact["command_id"]

    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        generation = await b.connect()
        calls = []
        payload = detail

        async def transport(request):
            calls.append((request.method, request.url.path))
            assert request.method == "GET", "receipt reconciliation cannot POST"
            if request.url.path == "/status":
                raise httpx.ReadTimeout("status unavailable", request=request)
            assert request.url.path.endswith(command_id)
            return httpx.Response(200, json=payload if request.url.params.get("detail") == "true" else compact)

        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        await b.connection._active_status_probe()
        assert b.connection.snapshot().reachable is False
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url="http://bms") as client:
            for _ in range(3):
                for detailed in (False, True):
                    result = await client.get(f"/operator-controls/v2/receipts/{command_id}", params={"detail": str(detailed).lower()})
                    assert result.status_code == 200, result.text
                    row = result.json()
                    assert row["completion_class"] == "source_noop"
                    assert row["status"] == "completed"
                    assert row["physical_effect_verified"] is False
                    if detailed:
                        for field in ("delivery_attempted", "controller_command_acknowledged", "controller_completion_verified", "hardware_postcondition_verified", "physical_observation_verified"):
                            assert row["deck_movement"][field] is False
                        assert row["deck_movement"]["semantic_state_committed"] is True
                        if output := os.environ.get("BMS_DECK_DETAIL_EXPORT"):
                            Path(output).write_text(json.dumps(row))
            before = len(calls)
            with pytest.raises(ConnectionStateError, match="fresh reachable"):
                await b.connection.request_active_v2_enqueue("invoke_operator_action_v2", expected_generation=generation, path_params={"action_id": "oem.deck.move_to_location"}, json_data={})
            assert len(calls) == before
            with pytest.raises(ConnectionStateError, match="generation"):
                await b.connection.request_active_v2_query("operator_action_receipt_v2", expected_generation=generation + 1, path_params={"command_id": command_id})
            # Addressed strict consumer rejects fabricated no-op completions.
            for field, value in [("source_branch", "ordinary"), ("delivery_attempted", True), ("controller_completion_verified", True)]:
                payload = copy.deepcopy(detail)
                payload["deck_movement"][field] = value
                result = await client.get(f"/operator-controls/v2/receipts/{command_id}", params={"detail": "true"})
                assert result.status_code != 200
        await b.connection.disconnect()
        with pytest.raises(ConnectionStateError, match="not actively connected"):
            await b.connection.request_active_v2_query("operator_action_receipt_v2", expected_generation=generation, path_params={"command_id": command_id})
        assert all(method == "GET" for method, _ in calls)

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["LOC_OC", "LOC_PARK", "LOC_TC_BARCODE", "LOC_RC_BARCODE"])
@pytest.mark.parametrize("camera_offset", [False, True])
def test_deck_harmonization_typed_camera_envelope_through_actual_api(tmp_path, target, camera_offset):
    from test_serial206_bioxp_v2_models import compact_payload

    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        generation = await b.connect()
        calls = []

        async def transport(request):
            body = json.loads(request.content)
            calls.append(body)
            assert request.method == "POST"
            assert request.url.path.endswith("/oem.deck.move_to_location")
            assert body["inputs"] == {"target": target, "camera_offset": camera_offset}
            assert "expected_connection_generation" not in body
            return httpx.Response(202, json=compact_payload(action_id="oem.deck.move_to_location"))

        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        body = {"schema_version": "bioxp.operator_action_request.v2", "expected_connection_generation": generation,
                "expected_ownership_generation": 3, "expected_board_epoch_by_board": {"4": 2, "5": 8},
                "idempotency_key": "camera-choice", "inputs": {"target": target, "camera_offset": camera_offset}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url="http://bms") as client:
            result = await client.post("/operator-controls/v2/actions/oem.deck.move_to_location", json=body)
            incompatible = camera_offset and target != "LOC_OC"
            assert result.status_code == (422 if incompatible else 202), result.text
            assert len(calls) == (0 if incompatible else 1)
        await b.connection.disconnect()

    asyncio.run(scenario())


def test_deck_harmonization_explicit_refresh_can_repair_stale_status_not_motion(tmp_path):
    from test_serial206_bioxp_v2_models import compact_payload

    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        generation = await b.connect()
        calls = []

        async def transport(request):
            if request.url.path == "/status":
                raise httpx.ReadTimeout("status unavailable", request=request)
            calls.append((request.method, request.url.path, json.loads(request.content)))
            assert request.method == "POST"
            assert request.url.path.endswith("/oem.deck.collect_authority")
            assert json.loads(request.content)["inputs"] == {}
            return httpx.Response(202, json=compact_payload(action_id="oem.deck.collect_authority"))

        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        await b.connection._active_status_probe()
        body = {"schema_version": "bioxp.operator_action_request.v2", "expected_connection_generation": generation,
                "expected_ownership_generation": 3, "expected_board_epoch_by_board": {},
                "idempotency_key": "explicit-query", "inputs": {}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url="http://bms") as client:
            result = await client.post("/operator-controls/v2/actions/oem.deck.collect_authority", json=body)
            assert result.status_code == 202, result.text
            assert len(calls) == 1
            for action, inputs in [("oem.deck.move_to_location", {"target": "LOC_OC", "camera_offset": False}), ("oem.deck.collect_authority", {"axis": "x"})]:
                result = await client.post("/operator-controls/v2/actions/" + action, json={**body, "expected_board_epoch_by_board": {"4": 2, "5": 8}, "inputs": inputs})
                assert result.status_code in (409, 422)
            assert len(calls) == 1
            result = await client.post("/operator-controls/v2/actions/oem.deck.collect_authority", json={**body, "expected_connection_generation": generation + 1})
            assert result.status_code == 409
            assert len(calls) == 1
        assert b.connection.snapshot().reachable is False  # query ACK is not readiness
        await b.connection.disconnect()

    asyncio.run(scenario())


def test_deck_harmonization_scoped_catalog_through_actual_boundary(tmp_path):
    payload = _serial206_catalog_payload()
    payload["dashboard"]["deck"].update(current_location=None, current_well=None, semantic_state_revision=1)
    payload["actions"][0]["destination_options"][-1].update(enabled=False, disabled_reason="canonical_deck_authority_unavailable:deck_semantic_state_not_authoritative:location_revision")

    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        await b.connect()
        calls = []

        async def transport(request):
            calls.append(request.method)
            assert request.method == "GET"
            assert request.url.path == "/operator/v2/control-catalog"
            return httpx.Response(200, json=payload)

        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url="http://bms") as client:
            for _ in range(3):
                result = await client.get("/operator-controls/v2/catalog")
                assert result.status_code == 200, result.text
                actual = result.json()
                assert actual["actions"][0]["destination_options"] == payload["actions"][0]["destination_options"]
                assert actual["dashboard"]["deck"]["current_location"] is None
                assert actual["dashboard"]["deck"]["current_well"] is None
        await b.connection.disconnect()
        assert calls == ["GET"] * 3

    asyncio.run(scenario())
