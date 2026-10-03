"""Mounted real compiler/store and immutable native producer HTTP receiving."""
from copy import deepcopy
from hashlib import sha256
from ipaddress import ip_address
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from routers.bioxp import router
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from services.bioxp.method_report import occurrence_outcomes, duration_report, application_report
from services.bioxp.method_recovery import resolve_occurrence
from test_bioxp_methods_api import store, create, Connection, BASE, JOB
from test_bioxp_methods_integrated import authored_method, submit_body

FIXTURES = Path(__file__).parent / "fixtures" / "bioxp_methods"
EXPORTS = json.loads((FIXTURES / "native-finish-receiving.json").read_text())["exports"]


def test_recovery_ambiguous_expansion_and_strict_original_values():
    snapshot = {"compilation": {"provenance": [
        {"step_id": "same", "occurrence_id": "one", "loop_path": [0], "native_action_ids": ["a"]},
        {"step_id": "same", "occurrence_id": "two", "loop_path": [1], "native_action_ids": ["b"]}]}}
    assert resolve_occurrence(snapshot, {"step_id": "same"})["status"] == "ambiguous"
    assert resolve_occurrence(snapshot, {"step_id": "same", "loop_path": [False]})["status"] == "unmatched"
    assert resolve_occurrence(snapshot, {"step_id": "same", "loop_path": [0]})["status"] == "matched"
    assert resolve_occurrence(snapshot, {"native_action_id": "a"})["matches"][0]["occurrence_id"] == "one"


def test_duration_does_not_use_requested_dwell_or_invalid_receipt_clocks():
    state = SimpleNamespace(action_results=[{"duration_s": 999, "receipt": r} for r in (
        {"accepted_at": 1, "finished_at": 4}, {"dispatched_at": 4, "finished_at": 1},
        {"dispatched_at": True, "finished_at": 4}, {"dispatched_at": 1, "finished_at": None})])
    assert duration_report(state)["action_intervals"] == []
    assert duration_report(state)["value"] is None


def test_real_thermal_semantic_fields_distinguish_setpoint_from_attainment():
    producer = json.loads((FIXTURES / "thermal-result.json").read_bytes())
    state = SimpleNamespace(**producer["execution"]["runtime_state"])
    fields = application_report(state)["fields"]
    setpoint = {r["field"]: r for r in fields if r["action_id"] == "thermal_setpoint"}
    assert setpoint["setpoint_accepted"]["reported_applied"]["value"] is True
    assert setpoint["temperature_reached"]["status"] == "unknown"
    assert next(r for r in fields if r["field"] == "dwell_complete")["reported_applied"]["value"] is True
    assert duration_report(state)["value"] is None



@pytest.mark.asyncio
@pytest.mark.parametrize("entry", EXPORTS, ids=lambda e: e["file"])
async def test_genuine_native_partial_held_fields_and_clocks(entry):
    raw = (FIXTURES / entry["file"]).read_bytes()
    assert sha256(raw).hexdigest() == entry["sha256"]
    producer = json.loads(raw)
    calls = []
    def receiving(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/protocol/jobs/" + producer["job_id"]
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})
    native = BioXpRobotClient(ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http",
        hostname="robot", port=8123, resolved_addresses=(ip_address("100.64.0.10"),)),
        transport=httpx.MockTransport(receiving))
    app = FastAPI()
    app.include_router(router, prefix="/api/bioxp")
    app.state.bioxp_runtime = SimpleNamespace(connection=Connection(native))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = BASE + "/runs/" + producer["job_id"]
            read = await client.get(url)
            assert read.status_code == 200, read.text
            assert read.json() == producer
            response = await client.get(url + "/report")
            assert response.status_code == 200, response.text
            report = response.json()
            state = producer["execution"]["runtime_state"]
            assert report["action_results"] == state["action_results"]
            assert report["workflow"] == state["workflow"]
            fields = report["reported_applied"]["fields"]
            events = state["action_results"][0]["pipette_result"]["events"]
            assert len(fields) == len(events)
            for field, event in zip(fields, events):
                assert field["reported_applied"] == event["reported_applied"]
                assert field["inputs"] == event["inputs"]
                assert field["status"] == event["status"]
                assert field["channels"] == event["result"]["channels"]
            assert fields[0]["reported_applied"]["completion_verified"] is True
            if "device" in entry["file"]:
                volume = next(f for f in fields if f["field"] == "volume_ul")
                assert volume["channels"][0]["result"]["completion_verified"] is True
                assert volume["channels"][1]["result"]["completion_verified"] is False
            receipt = state["action_results"][0]["receipt"]
            interval = report["duration"]["action_intervals"][0]
            assert interval["value"] == receipt["finished_at"] - receipt["dispatched_at"]
            assert report["duration"]["value"] is None  # not whole-run duration
            assert report["snapshot_available"] is False
            recovered = await client.post(url + "/recovery-draft", json={})
            assert recovered.status_code == 422  # no invented compiler provenance
            assert recovered.json()["detail"]["code"] == "lossless_reconstruction_unavailable"
        assert len(calls) == 3
    finally:
        await native.close()


@pytest.mark.asyncio
async def test_exact_occurrence_recovery_is_advisory_not_motion_gate(store):
    client, transport, _, _ = store
    raw = authored_method()
    raw["steps"].append({**deepcopy(raw["steps"][0]), "step_id": "second"})
    original = {"tips": None, "decimal": "01.00"}
    accepted = await client.post(BASE + "/quick-runs", json=submit_body(method=raw, initial_state=original))
    assert accepted.status_code == 202, accepted.text
    provenance = accepted.json()["method_snapshot"]["compilation"]["provenance"]
    first, second = provenance
    url = BASE + "/runs/" + JOB + "/recovery-draft"
    cases = [({"occurrence_id": first["occurrence_id"], "action_id": first["native_action_ids"][0]}, "matched"),
             ({"action_id": first["native_action_ids"][0]}, "matched"),
             ({"occurrence_id": first["occurrence_id"], "action_id": second["native_action_ids"][0]}, "unmatched"),
             ({"occurrence_id": "unrelated", "action_id": "unrelated"}, "unmatched")]
    before = deepcopy(transport.payload)
    for address, expected in cases:
        response = await client.post(url, json={"occurrence": address})
        assert response.status_code == 200, response.text
        recovered = response.json()
        assert recovered["initial_state"] == original
        linkage = recovered["recovery"]
        assert linkage["original_assumptions"] == {"initial_state": original}
        assert linkage["occurrence_resolution"]["status"] == expected
        assert bool(linkage["occurrence_resolution"]["unmatched"]) == (expected == "unmatched")
        assert linkage["included_occurrences"] == provenance
        assert linkage["excluded_actions"] == [] and linkage["submitted"] is False
    edited = (await client.post(url, json={"initial_state": None})).json()
    assert edited["initial_state"] is None
    assert edited["recovery"]["original_assumptions"] == {"initial_state": original}
    assert edited["recovery"]["assumptions_overridden"] is True
    assert transport.payload == before
    assert sum(route == "protocol_execute" for route, _ in transport.calls) == 1


@pytest.mark.parametrize("child, expected", [({"pending": True}, "running"),
    ({"ok": False}, "failed"), ({"uncertain": True}, "unknown")])
def test_nested_child_defeats_completed_parent(child, expected):
    state = SimpleNamespace(action_results=[{"action_id": "a", "ok": True, "child_outcomes": [child]}],
        stage_states={"s": SimpleNamespace(completed_actions=["a"], current_action_id=None)})
    snapshot = {"compilation": {"provenance": [{"occurrence_id": "o", "native_action_ids": ["a"]}]}}
    row = occurrence_outcomes(snapshot, state)[0]
    assert row["children"][0]["status"] == expected
    assert row["children"][0]["child_outcomes"] == [child]


@pytest.mark.asyncio
async def test_all_bound_discovery_companions_real_save_compile_submit(store):
    client, transport, _, _ = store
    assert (await client.get(BASE + "/schema")).status_code == 200
    assert (await client.get(BASE + "/catalog")).status_code == 200
    examples = (await client.get(BASE + "/examples")).json()
    assert len(examples) == 8
    for entry in examples:
        fixture = entry["bound_fixture"]
        request = {k: fixture[k] for k in ("method", "bindings", "dependencies", "initial_state") if k in fixture}
        saved = await create(client, request["method"], name=entry["id"])
        url = BASE + "/library/" + saved["id"]
        exact = (await client.get(url + "/revisions/1")).json()
        assert exact["method"] == request["method"]
        compiled = (await client.post(BASE + "/compile", json=request)).json()
        assert compiled["document"] is not None, compiled["issues"]
        run = await client.post(url + "/runs", json=submit_body(revision=1, idempotency_key="bound-" + entry["id"],
            **{k:v for k,v in request.items() if k != "method"}))
        assert run.status_code == 202, run.text
        snapshot = run.json()["method_snapshot"]
        assert snapshot["compilation"] == compiled
        run_url = BASE + "/runs/" + run.json()["job_id"]
        report = (await client.get(run_url + "/report")).json()
        assert report["method_snapshot"] == snapshot
        recovery = (await client.post(run_url + "/recovery-draft", json={
            "occurrence": compiled["provenance"][0]})).json()
        assert recovery["method"] == request["method"]
        assert recovery["recovery"]["occurrence_resolution"]["status"] == "matched"
    assert sum(route == "protocol_execute" for route, _ in transport.calls) == len(examples)
