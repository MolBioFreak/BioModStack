"""Offline receiving/SQLite/preview qualification; no robot execution."""
from copy import deepcopy
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from bioxp_workflow_authoring import WorkflowPreviewRequest, preview, native_intent, expand_transfer, discovery
from routers.bioxp import router
from test_bioxp_workflow_drafts import store, create, URL


def transfer() -> dict:
    return dict(operation="transfer", source=dict(station="LOC_MS", location_id="0", wells=["B2", "A1"]),
        destination=dict(station="LOC_TC", location_id=2, wells=["D4", "C3"]), channels=[3, 1],
        volume_ul="12.500", aspirate_speed="30", dispense_speed="40", source_position_flag="1",
        destination_position_flag=2, source_lift_height_steps=None, destination_lift_height_steps="100")


def draft(*intents):
    return {"schema": "bms.bioxp-workflow-draft.v2", "steps": [dict(step_id=f"s{i}", intent=x) for i, x in enumerate(intents)],
        "editor_state": {"uncommitted": {"raw": "", "future": None, "flag": False}},
        "deck_plan": {"labware": [], "materials": [], "assignments": []}}


def run(*intents):
    return preview(WorkflowPreviewRequest.model_validate(dict(protocol_id="differential", draft=draft(*intents)))).model_dump()


@pytest.mark.asyncio
async def test_v2_sqlite_raw_roundtrip_and_update_without_readiness(store):
    value = draft(transfer(), {"operation": "future", "unknown": [False, None, "", 0]}, {})
    value["steps"][0]["intent"]["volume_ul"] = ""
    value["deck_plan"] = dict(labware=[dict(id="", station="not-proven", name="", profile_id="unknown")],
        materials=[dict(id="m", name="", kind="sample", description="")],
        assignments=[dict(id="a", labware_id="missing", well="", material_id="unknown", volume_ul="0.0000000001"),
                     dict(id="a", labware_id="", well="future", material_id="", volume_ul=None)])
    async with store() as (client, sessions):
        response = await create(client, params=value)
        assert response.status_code == 201, response.text
        identifier = response.json()["id"]
        assert response.json()["params"] == value
    async with store() as (client, sessions):
        assert (await client.get(f"{URL}/{identifier}")).json()["params"] == value
        value["steps"].reverse()
        assert (await client.put(f"{URL}/{identifier}", json={"params": value})).status_code == 200
        assert (await client.get(f"{URL}/{identifier}")).json()["params"] == value
        async with sessions() as session:
            raw = await session.scalar(text("SELECT params FROM user_templates WHERE id=:id"), {"id": identifier})
            assert json.loads(raw) == value


@pytest.mark.asyncio
async def test_v2_structural_invalid_is_atomic(store):
    async with store() as (client, _):
        value = draft()
        identifier = (await create(client, params=value)).json()["id"]
        for invalid in [None, {}, {"labware": {}, "materials": [], "assignments": []},
                        {"labware": [], "materials": [], "assignments": [dict(id="x")]}]:
            bad = {**value, "deck_plan": invalid}
            assert (await client.put(f"{URL}/{identifier}", json={"params": bad})).status_code == 422
            assert (await client.get(f"{URL}/{identifier}")).json()["params"] == value


@pytest.mark.asyncio
async def test_disconnected_router_preview_schema_without_client_or_jobs(monkeypatch):
    from services.bioxp.robot_client import BioXpRobotClient
    def forbidden(*a, **kw):
        raise AssertionError("Preview must not construct robot client")
    async def network(*a, **kw):
        raise AssertionError("Preview must not send outbound HTTP")
    monkeypatch.setattr(BioXpRobotClient, "__init__", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", network)
    app = FastAPI()
    app.include_router(router, prefix="/api/bioxp")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/bioxp/workflows/preview", json=dict(protocol_id="offline", draft=draft(transfer())))
        assert r.status_code == 200, r.text
        assert len(r.json()["actions"]) == 16
        schema = await c.get("/api/bioxp/workflows/schema")
        assert schema.status_code == 200
        assert schema.json() == discovery()
        assert "WorkflowPlan" in schema.json()["preview_request"]["$defs"]
        assert "WorkflowTransferIntent" == schema.json()["transfer"]["title"]
        assert "/api/bioxp/workflows/preview" in app.openapi()["paths"]


def test_transfer_order_provenance_no_implicit_steps_or_mutation():
    t = transfer()
    original = deepcopy(t)
    result = run(t)
    assert not result["issues"]
    actions = result["actions"]
    assert [a["label"] for a in actions] == ["move", "lower", "aspirate", "lift", "move", "lower", "dispense", "lift"] * 2
    assert [a["pair_index"] for a in actions] == [0] * 8 + [1] * 8
    assert [a["well"] for a in actions[::4]] == ["B2", "D4", "A1", "C3"]
    assert all(a["step_id"] == "s0" for a in actions)
    assert [a["index"] for a in actions] == list(range(16))
    assert [a["params"] for a in actions] == [a["params"] for a in result["document"]["stages"][0]["actions"]]
    assert actions[2]["params"]["channels"] == [3, 1]
    assert actions[3]["params"]["height_steps"] is None
    assert t == original


@pytest.mark.parametrize("change", [
    {"volume_ul": ""}, {"channels": []}, {"channels": [0, 0]}, {"source_position_flag": None},
    {"destination_lift_height_steps": ""}, {"aspirate_speed": 0}, {"volume_ul": 1001},
    {"source": dict(station="x", location_id=32, wells=["A1", "B1"])},
    {"destination": dict(station="y", location_id=2, wells=["A1"])},
    {"source": dict(station="x", location_id=0, wells=["BAD", "A1"])},
])
def test_incomplete_transfer_has_only_per_row_issue(change):
    result = run({**transfer(), **change}, {"operation": "lower", "location_id": 0})
    assert result["document"] is None
    assert result["actions"] == []
    assert result["issues"][0]["step_id"] == "s0"


def source_mix_blank_delays():
    return dict(operation="source_mix", volume_ul="10", air_ul="15", aspirate_speed="100",
                dispense_speed="20", aspirate_delay_ms="", dispense_delay_ms="", cycles="2",
                mix_type="N", tip_dip=True)


@pytest.mark.parametrize("schema", ["v1", "v2"])
def test_source_mix_blank_delays_preserve_raw_draft(schema):
    value = draft(source_mix_blank_delays())
    if schema == "v1":
        value["schema"] = "bms.bioxp-workflow-draft.v1"
        value.pop("deck_plan")
    original = deepcopy(value)
    request = WorkflowPreviewRequest.model_validate(dict(protocol_id="legacy", draft=value))
    result = preview(request)
    assert not result.issues
    assert len(result.actions) == 1
    assert result.actions[0].params == dict(operation="source_mix", volume_ul=10, air_ul=15,
        aspirate_speed=100, dispense_speed=20, aspirate_delay_ms=None, dispense_delay_ms=None,
        cycles=2, mix_type="N", tip_dip=True)
    assert request.draft.model_dump(by_alias=True) == original
    assert value == original


@pytest.mark.parametrize("field", ["aspirate_delay_ms", "dispense_delay_ms"])
@pytest.mark.parametrize("value", [None, "0", 0])
def test_source_mix_existing_null_and_zero_delays(field, value):
    intent = source_mix_blank_delays()
    intent[field] = value
    result = run(intent)
    assert not result["issues"]
    assert result["actions"][0]["params"][field] == (None if value is None else 0)


@pytest.mark.parametrize("field,value", [
    ("aspirate_delay_ms", " "), ("dispense_delay_ms", "\t"),
    ("volume_ul", ""), ("air_ul", ""), ("aspirate_speed", ""),
    ("dispense_speed", ""), ("cycles", ""),
])
def test_blank_delay_conversion_does_not_expand_to_other_values(field, value):
    intent = source_mix_blank_delays()
    intent[field] = value
    result = run(intent)
    assert result["document"] is None and result["actions"] == []
    assert result["issues"][0]["step_id"] == "s0"
    assert field in result["issues"][0]["message"]


@pytest.mark.parametrize("field", ["aspirate_delay_ms", "dispense_delay_ms"])
def test_blank_delay_conversion_does_not_supply_missing_fields(field):
    intent = source_mix_blank_delays()
    del intent[field]
    assert run(intent)["document"] is None


def test_blank_native_lift_height_still_requires_explicit_value():
    assert run(dict(operation="lift", location_id=2, height_steps=""))["document"] is None


@pytest.mark.parametrize("labels", [("LOC_TC", "LOC_MS"), ("", ""), ("future", "unproven")])
def test_transfer_station_tracks_native_location_not_raw_label(labels):
    intent = transfer()
    baseline = run(intent)
    intent["source"]["station"], intent["destination"]["station"] = labels
    original = deepcopy(intent)
    result = run(intent)
    assert not result["issues"]
    assert result["document"] == baseline["document"]
    assert result["actions"] == baseline["actions"]
    for start in range(0, len(result["actions"]), 4):
        group = result["actions"][start:start + 4]
        location = group[0]["params"]["location_id"]
        assert group[1]["params"]["location_id"] == group[3]["params"]["location_id"] == location
        assert all(action["station"] == discovery()["native_locations"][str(location)] for action in group)
    assert intent == original


@pytest.mark.parametrize("well", ["A1", "B12", "a1", "b12"])
def test_load_tip_exact_well_preserves_case(well):
    intent = dict(operation="load_tip", tray=1, well=well, overpress=False, lift_z=False)
    result = run(intent)
    assert not result["issues"]
    assert result["actions"][0]["params"] == intent


@pytest.mark.parametrize("well", ["A1\n", "A1\r\n", " A1", "A1 ", "A0", "B13", "C1"])
def test_load_tip_invalid_well_is_row_issue_without_normalization(well):
    intent = dict(operation="load_tip", tray=1, well=well, overpress=False, lift_z=False)
    original = deepcopy(intent)
    result = run(intent)
    assert result["document"] is None and result["actions"] == []
    assert result["issues"][0]["step_id"] == "s0"
    assert "well" in result["issues"][0]["message"]
    assert intent == original


def native_cases():
    return [
        dict(operation="move", location_id="0", well="a1", position_flag="0"),
        dict(operation="lower", location_id=2), dict(operation="lift", location_id=2, height_steps=None),
        dict(operation="lift", location_id=2, height_steps="-12"),
        *[dict(operation=op, channels=[3, 0], volume_ul="15.25", speed="30") for op in ("aspirate", "dispense")],
        dict(operation="mix", channels=[0, 1, 2, 3], volume_ul="1", aspirate_speed=20, dispense_speed=30, cycles="2"),
        dict(operation="load_tip", tray="1", well="b12", overpress=False, lift_z=True),
        dict(operation="measure_fluid_height", speed="300"),
        dict(operation="source_fluid_offset", plate="OCMS", speed=300, transfer_fluid=False, skip_steps=4),
        dict(operation="diagnostic_detect_fluid"), dict(operation="source_calwith_fluid"),
        dict(operation="source_load_tips", tip_type="200", pipette="-1", force_new_tip=False),
        dict(operation="source_mix", volume_ul="10", air_ul="15", aspirate_speed=100, dispense_speed=20,
             aspirate_delay_ms=None, dispense_delay_ms="0", cycles=0, mix_type="N", tip_dip=False),
        *[dict(operation=op, volume_ul="5") for op in ("source_aspirate_air", "source_dispense_air")],
        dict(operation="source_purge", speed="30", amp=False, ntd=True),
        *[dict(operation="diagnostic_pipette", diagnostic=d) for d in [
            dict(action="aspirate", channels=[], volume_ul=0, speed="1"),
            dict(action="dispense", channels=[3, 0], volume_ul=1500, speed="30"),
            dict(action="eject", channels=[]), dict(action="plunger_up", steps="0"),
            dict(action="plunger_down", steps="2147483647"),
            *[dict(action=x) for x in ("dispense_all", "diagnoses", "initialize", "get_data", "last_error")]]]]


def test_all_native_operations_and_export_differential_cases():
    cases = native_cases()
    combined = run(*cases)
    assert not combined["issues"], combined["issues"]
    t = run(transfer())
    export = [dict(request=dict(protocol_id="differential", steps=[native_intent(s) for s in cases]), expected=combined["document"]),
              dict(request=dict(protocol_id="differential", steps=[s for s, *_ in expand_transfer(transfer())]), expected=t["document"])]
    # Independent legacy-boundary expectation: exactly the two blank delays become null.
    blank = source_mix_blank_delays()
    native_blank = dict(operation="source_mix", volume_ul=10, air_ul=15, aspirate_speed=100,
                        dispense_speed=20, aspirate_delay_ms=None, dispense_delay_ms=None,
                        cycles=2, mix_type="N", tip_dip=True)
    blank_result = run(blank)
    assert not blank_result["issues"]
    export.append(dict(request=dict(protocol_id="differential", steps=[native_blank]), expected=blank_result["document"]))
    reversed_labels = transfer()
    reversed_labels["source"]["station"] = "LOC_TC"
    reversed_labels["destination"]["station"] = "LOC_MS"
    reversed_result = run(reversed_labels)
    assert reversed_result["document"] == t["document"]
    export.append(dict(request=deepcopy(export[1]["request"]), expected=reversed_result["document"]))
    invalid = [
        *[dict(operation="load_tip", tray=1, well=well, overpress=False, lift_z=False)
          for well in ("A1\n", "A1\r\n", " A1", "A1 ", "A0", "B13", "C1")],
        dict(operation="move", location_id=32, well="A1", position_flag=1),
        dict(operation="move", location_id=0, well="I1", position_flag=1),
        dict(operation="move", location_id=0, well="96", position_flag=1),
        dict(operation="aspirate", channels=[0, 0], volume_ul=1, speed=1),
        dict(operation="aspirate", channels=[0], volume_ul=0, speed=1),
        dict(operation="dispense", channels=[0], volume_ul=1001, speed=1),
        dict(operation="dispense", channels=[0], volume_ul=1, speed=0),
        dict(operation="mix", channels=[0], volume_ul=1, aspirate_speed=1, dispense_speed=1, cycles=51),
        dict(operation="diagnostic_pipette", diagnostic=dict(action="eject", channels=[1, 1])),
        dict(operation="diagnostic_pipette", diagnostic=dict(action="plunger_up", steps=2147483648)),
    ]
    for step in invalid:
        assert run(step)["document"] is None
        export.append(dict(request=dict(protocol_id="differential", steps=[step]), expected=None))
    if os.getenv("BIOXP_AUTHORING_DIFFERENTIAL_EXPORT"):
        Path(os.environ["BIOXP_AUTHORING_DIFFERENTIAL_EXPORT"]).write_text(json.dumps(export, indent=2))


@pytest.mark.parametrize("intent", [{}, {"operation": "future"}, {"operation": "source_purge"},
    {"operation": "lower", "location_id": False}, {"operation": "move", "location_id": 0, "well": True, "position_flag": 1},
    {"operation": "diagnostic_pipette", "diagnostic": {"action": "eject", "channels": [0, 0]}}])
def test_raw_legacy_invalid_is_issue_not_execution(intent):
    assert run(intent)["document"] is None


def test_v1_acceptance_and_empty_preview():
    value = draft(dict(operation="lower", location_id="0"))
    value["schema"] = "bms.bioxp-workflow-draft.v1"
    value.pop("deck_plan")
    assert preview(WorkflowPreviewRequest.model_validate(dict(protocol_id="legacy", draft=value))).document
    assert run()["issues"][0]["step_id"] is None
