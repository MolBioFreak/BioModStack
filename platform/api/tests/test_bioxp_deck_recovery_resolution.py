"""Read-only recovery projection; historical outcome is never rewritten."""
import asyncio
import copy
import json
import os
from pathlib import Path
import httpx
import pytest
from pydantic import ValidationError
from routers.bioxp.operator_controls import router
from services.bioxp.operator_models import OperatorActionReceiptDetailV2
from test_bioxp_camera_boundary import Boundary
from test_serial206_bioxp_v2_models import _park_noop_payload


def resolved_payload():
    p = _park_noop_payload()
    p.update(status="ambiguous", completion_class="recovery_required")
    p["deck_movement"].update(ambiguity_state="recovery_required", recovery_resolution={
        "command_id": p["command_id"], "decision_id": "home-decision", "semantic_state_revision": 18, "transition_sequence": 2})
    return p


def test_recovery_model_preserves_history():
    p = resolved_payload()
    row = OperatorActionReceiptDetailV2.model_validate(p)
    assert row.status == "ambiguous"
    assert row.deck_movement.recovery_resolution.command_id == row.command_id
    for value in (None,):
        p["deck_movement"]["recovery_resolution"] = value
        OperatorActionReceiptDetailV2.model_validate(p)
    del p["deck_movement"]["recovery_resolution"]
    OperatorActionReceiptDetailV2.model_validate(p)


@pytest.mark.parametrize("field,value", [("command_id", "other"), ("decision_id", ""), ("semantic_state_revision", True), ("semantic_state_revision", 0), ("semantic_state_revision", "18"), ("transition_sequence", 0), ("extra", 1)])
def test_recovery_model_rejects_invalid(field, value):
    p = resolved_payload()
    p["deck_movement"]["recovery_resolution"][field] = value
    with pytest.raises(ValidationError):
        OperatorActionReceiptDetailV2.model_validate(p)


@pytest.mark.parametrize("mutation", [
    {"terminal": False}, {"status": "completed"}, {"status": "dispatched", "terminal": False}, {"action_id": "oem.y.move_steps"},
])
def test_recovery_rejects_wrong_lifecycle_and_action(mutation):
    p = resolved_payload()
    p.update(mutation)
    with pytest.raises(ValidationError):
        OperatorActionReceiptDetailV2.model_validate(p)


@pytest.mark.parametrize("field", ["command_id", "decision_id", "semantic_state_revision", "transition_sequence"])
def test_recovery_rejects_incomplete_decision(field):
    p = resolved_payload()
    del p["deck_movement"]["recovery_resolution"][field]
    with pytest.raises(ValidationError):
        OperatorActionReceiptDetailV2.model_validate(p)


@pytest.mark.parametrize("source", ["contract", "native"])
def test_recovery_through_transport_and_asgi(tmp_path, source):
    if source == "native":
        path = os.environ.get("BMS_NATIVE_RECOVERY_EXPORT")
        if not path:
            pytest.skip("native owner export required")
        p = json.loads(Path(path).read_text())["detail"]
    else:
        p = resolved_payload()
    async def scenario():
        b = Boundary(tmp_path)
        b.app.include_router(router)
        await b.connect()
        calls = []
        async def transport(request):
            calls.append(request.method)
            assert request.method == "GET"
            if request.url.path == "/status":
                raise httpx.ReadTimeout("unavailable", request=request)
            return httpx.Response(200, json=p)
        b.clients[0]._client._transport._transport = httpx.MockTransport(transport)
        await b.connection._active_status_probe()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app), base_url="http://bms") as client:
            for _ in range(3):
                response = await client.get(f'/operator-controls/v2/receipts/{p["command_id"]}', params={"detail": "true"})
                assert response.status_code == 200, response.text
                row = response.json()
                assert row["status"] == p["status"] == "ambiguous"
                assert row["deck_movement"]["recovery_resolution"] == p["deck_movement"]["recovery_resolution"]
                if output := os.environ.get("BMS_RECOVERY_DETAIL_EXPORT"):
                    Path(output).write_text(json.dumps(row))
            p["deck_movement"]["recovery_resolution"]["command_id"] = "wrong"
            response = await client.get(f'/operator-controls/v2/receipts/{p["command_id"]}', params={"detail": "true"})
            assert response.status_code != 200
        await b.connection.disconnect()
        assert set(calls) == {"GET"}
    asyncio.run(scenario())
