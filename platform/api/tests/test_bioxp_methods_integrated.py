"""Real API/store/compiler/discovery; inert native transport, no hardware.

Producer replay is separately labeled from constructed partial-state controls.
The original UI capture contains incomplete science; it must not become a valid
run merely because the frontend's old transport fixture returned success.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_bioxp_methods_api import store, create, BASE, KEY, JOB
from services.bioxp.errors import RobotResponseError, RobotTimeoutError

FIXTURES = Path(__file__).parent / "fixtures" / "bioxp_methods"
UI = json.loads((FIXTURES / "ui-method-requests.json").read_text())["requests"]


def authored_method():
    # Fixture-authored values, not experimental defaults. Uses catalog operations.
    return {"schema": "bms.bioxp-method.v1", "name": "Fixture transfer",
            "parameters": [], "procedures": [], "unknown": {"blank": "", "null": None, "false": False, "zero": 0},
            "steps": [{"step_id": "transfer", "type": "action", "action": "transfer", "inputs": {
                "source": {"station": "LOC_MS", "location_id": 0, "wells": ["A1"]},
                "destination": {"station": "LOC_TC", "location_id": 2, "wells": ["A1"]},
                "channels": [0], "volume_ul": "12.5000", "aspirate_speed": "30", "dispense_speed": "40",
                "source_position_flag": 1, "destination_position_flag": 2,
                "source_lift_height_steps": None, "destination_lift_height_steps": 100}}]}


def submit_body(**kw):
    return {"idempotency_key": KEY, "expected_generation": 77, "acknowledge_live": True,
            "bindings": {}, "dependencies": {}, **kw}


@pytest.mark.asyncio
async def test_real_source_free_discovery_revision_compile_run_lifecycle(store, monkeypatch):
    client, transport, _, _ = store
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    catalog = (await client.get(BASE + "/catalog")).json()
    assert "transfer" in {a["action"] for a in catalog["actions"]}
    schema = (await client.get(BASE + "/schema")).json()
    assert "method" in schema and "SavedRun" in schema["requests"]
    starters = (await client.get(BASE + "/liquid-classes/starters")).json()
    assert starters and all(x["schema"] == "bms.bioxp-liquid-class.v1" for x in starters)
    assert (await client.get(BASE + "/liquid-classes/source")).status_code == 200
    # Real class library pin is embedded rather than resolving a mutable head.
    liquid = (await client.post(BASE + "/liquid-classes", json={"method": starters[0], "name": "Pinned class"})).json()
    pin = (await client.get(BASE + "/liquid-classes/" + liquid["id"] + "/revisions/1")).json()
    raw = authored_method()
    saved = await create(client, raw)
    url = BASE + "/library/" + saved["id"]
    changed = {**deepcopy(raw), "editor_state": {"collapsed": True}}
    results = await asyncio.gather(*[client.put(url, json={"method": changed, "expected_base_revision": 1}) for _ in range(2)])
    assert sorted(r.status_code for r in results) == [200, 409]
    assert (await client.get(url + "/revisions/1")).json()["method"] == raw
    assert (await client.get(url + "/revisions/2")).json()["method"] == changed
    request = {"method": raw, "bindings": {}, "dependencies": {"pinned_fixture_class": pin}, "initial_state": {}}
    compiled_response = await client.post(BASE + "/compile", json=request)
    assert compiled_response.status_code == 200, compiled_response.text
    compiled = compiled_response.json()
    assert compiled["document"] and compiled["digest"] != "seam-only", compiled["issues"]
    assert len(compiled["provenance"][0]["native_action_ids"]) > 1
    assert not transport.calls
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "1")
    transport.release = asyncio.Event()
    task = asyncio.create_task(client.post(url + "/runs", json=submit_body(revision=1, dependencies=request["dependencies"], initial_state={})))
    await asyncio.wait_for(transport.entered.wait(), 10)
    # Real class update during the blocked submit cannot mutate the snapshot.
    assert (await client.put(BASE + "/liquid-classes/" + liquid["id"], json={
        "method": {**starters[0], "future": None}, "expected_base_revision": 1})).status_code == 200
    transport.release.set()
    response = await task
    assert response.status_code == 202, response.text
    snapshot = response.json()["method_snapshot"]
    assert snapshot["method"] == raw and snapshot["saved"]["revision"] == 1
    assert snapshot["compilation"] == compiled
    sent = transport.calls[0][1]["json_data"]
    assert sent["document"]["metadata"]["bms_method_run"] == snapshot
    assert response.json()["job_id"] == JOB
    original = (await client.get(BASE + "/runs/" + JOB)).json()
    report = (await client.get(BASE + "/runs/" + JOB + "/report")).json()
    assert report["method_snapshot"] == snapshot
    # Mock admission supplies no result for compiled actions, never success.
    assert all(row["status"] == "unknown" for row in report["occurrences"])
    transport.error = RobotResponseError(404, "observation unavailable")
    assert (await client.get(BASE + "/runs/" + JOB)).status_code == 404
    transport.error = None
    control = await client.post(BASE + "/runs/" + JOB + "/control", json={
        "action": "abort", "expected_connection_generation": 77, "expected_ownership_generation": 7,
        "command_id": JOB, "idempotency_key": "control-original-key"})
    assert control.status_code == 200 and control.json()["reached"] is False
    occurrence = compiled["provenance"][0]
    recovery = (await client.post(BASE + "/runs/" + JOB + "/recovery-draft", json={
        "occurrence": occurrence, "initial_state": {"tips": "unknown", "custody": None}})).json()
    assert recovery["method"] == raw and recovery["dependencies"] == request["dependencies"]
    assert recovery["recovery"]["occurrence"] == occurrence
    assert recovery["recovery"]["automatic_setup"] == [] and recovery["recovery"]["submitted"] is False
    assert (await client.get(BASE + "/runs/" + JOB)).json() == original
    assert [route for route, _ in transport.calls].count("protocol_execute") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("original", [{}, {"initial_state": None}, {"initial_state": {"tips": None, "volume": "01.00"}}])
@pytest.mark.parametrize("override", [{}, {"initial_state": None}, {"initial_state": {}}])
async def test_recovery_assumptions_preserve_omitted_null_and_original(store, original, override):
    client, transport, _, _ = store
    response = await client.post(BASE + "/quick-runs", json=submit_body(method=authored_method(), **original))
    assert response.status_code == 202, response.text
    before = deepcopy(transport.payload)
    recovered = (await client.post(BASE + "/runs/" + JOB + "/recovery-draft", json=override)).json()
    expected = override or original
    assert ("initial_state" in recovered) == ("initial_state" in expected)
    if "initial_state" in expected:
        assert recovered["initial_state"] == expected["initial_state"]
    assert transport.payload == before


@pytest.mark.asyncio
async def test_all_published_examples_save_reopen_and_actual_compile(store):
    client, transport, _, _ = store
    examples = (await client.get(BASE + "/examples")).json()
    assert {e["id"] for e in examples} >= {"cfps", "purification", "gibson", "golden_gate", "pcr", "dna_purification", "rna_purification", "cfps_and_purification"}
    for entry in examples:
        raw = entry["method"]
        saved = await create(client, raw, name=entry["id"])
        exact = (await client.get(BASE + "/library/" + saved["id"] + "/revisions/1")).json()
        assert exact["method"] == raw
        response = await client.post(BASE + "/compile", json={"method": exact["method"]})
        assert response.status_code == 200, response.text
        compiled = response.json()
        assert compiled["document"] is None
        assert any(i["code"] == "missing_binding" for i in compiled["issues"])
        refused = await client.post(BASE + "/library/" + saved["id"] + "/runs", json=submit_body(revision=1))
        assert refused.status_code == 422 and refused.json()["detail"]["delivery"] == "not_submitted"
    assert not transport.calls


@pytest.mark.asyncio
async def test_exact_ui_wire_check_compile_raw_save_and_cas(store):
    client, transport, _, _ = store
    # Every captured check/compile body, without repairing values/expressions.
    checks = [r for r in UI if r["url"] in (BASE + "/check", BASE + "/compile")]
    assert checks
    for row in checks:
        response = await client.request(row["method"], row["url"], json=row["body"])
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["document"] is None and result["issues"]
    assert not transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [r for r in UI if r["method"] == "put"])
async def test_exact_ui_wire_put_and_stale_revision(store, row):
    # Each captured UI test had its own m1 identity and store.
    client, transport, _, _ = store
    raw = row["body"]["method"]
    saved = await create(client, raw)
    url = BASE + "/library/" + saved["id"]
    response = await client.put(url, json=row["body"])
    assert response.status_code == 200, response.text
    assert (await client.get(url + "/revisions/2")).json()["method"] == raw
    assert (await client.put(url, json=row["body"])).status_code == 409
    assert not transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [r for r in UI if r["method"] == "post" and r["url"] in (BASE + "/library", BASE + "/liquid-classes")])
async def test_exact_ui_wire_create(store, row):
    client, transport, _, _ = store
    response = await client.post(row["url"], json=row["body"])
    assert response.status_code == 201, response.text
    saved = response.json()
    assert (await client.get(row["url"] + "/" + saved["id"] + "/revisions/1")).json()["method"] == row["body"]["method"]
    assert not transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [r for r in UI if r["method"] == "post" and (r["url"].endswith("/runs") or r["url"].endswith("/quick-runs"))])
async def test_exact_ui_wire_unbound_run_has_no_transport(store, row):
    client, transport, _, _ = store
    url = row["url"]
    if not url.endswith("/quick-runs"):
        raw = next(r["body"]["method"] for r in UI if r["url"] == BASE + "/check")
        saved = await create(client, raw)
        url = BASE + "/library/" + saved["id"] + "/runs"
    response = await client.post(url, json=row["body"])
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["delivery"] == "not_submitted"
    assert not transport.calls


@pytest.mark.asyncio
async def test_published_control_and_recovery_schemas_accept_exact_ui_wire(store):
    from jsonschema import Draft202012Validator
    client, transport, _, _ = store
    schemas = (await client.get(BASE + "/schema")).json()["requests"]
    for row in UI:
        if row["url"].endswith("/control"):
            Draft202012Validator(schemas["ProtocolControlRequest"]).validate(row["body"])
        elif row["url"].endswith("/recovery-draft"):
            Draft202012Validator(schemas["Recovery"]).validate(row["body"])
    assert not transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", ["thermal-result.json", "combined-thermal-result.json"])
async def test_real_thermal_producer_exact_receiving_no_relabelled_provenance(fixture):
    from fastapi import FastAPI
    from ipaddress import ip_address
    from types import SimpleNamespace
    import httpx
    from routers.bioxp import router
    from services.bioxp.robot_client import BioXpRobotClient
    from services.bioxp.target_policy import ValidatedBioXpTarget
    from test_bioxp_methods_api import Connection

    raw = (FIXTURES / fixture).read_bytes()
    producer = json.loads(raw)
    calls = []
    def receiving(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET" and request.url.path == "/protocol/jobs/" + producer["job_id"]
        # Actual exported bytes enter the real HTTP robot client, not _job or
        # ProtocolJob test doubles, and no IDs/results/provenance are rewritten.
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})
    native = BioXpRobotClient(ValidatedBioXpTarget(
        api_url="http://robot:8123", scheme="http", hostname="robot", port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),)), transport=httpx.MockTransport(receiving))
    app = FastAPI()
    app.include_router(router, prefix="/api/bioxp")
    app.state.bioxp_runtime = SimpleNamespace(connection=Connection(native))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            job_url = BASE + "/runs/" + producer["job_id"]
            response = await client.get(job_url)
            assert response.status_code == 200, response.text
            assert response.json() == producer
            report = (await client.get(job_url + "/report")).json()
            assert report["action_results"] == producer["execution"]["runtime_state"]["action_results"]
            assert report["events"] == producer["execution"]["runtime_state"]["events"]
            setpoint = next(r for r in report["action_results"] if r["kind"] == "thermal_setpoint")
            assert setpoint["setpoint_accepted"] is True and setpoint["temperature_reached"] is None
            assert report["snapshot_available"] is False and report["occurrences"] == []
            # Native-authored job lacks BMS raw snapshot; never fabricate one.
            assert (await client.post(job_url + "/recovery-draft", json={})).status_code == 422
        assert len(calls) == 3
    finally:
        await native.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (RobotTimeoutError("before dispatch", dispatched=False), "not_dispatched"),
    (RobotTimeoutError("after dispatch", dispatched=True), "outcome_ambiguous"),
    (RobotResponseError(409, {"code": "idempotency_conflict"}), None),
])
async def test_legacy_wire_delivery_does_not_guess_from_status(store, error, expected):
    client, transport, _, _ = store
    compiled = (await client.post(BASE + "/compile", json={"method": authored_method()})).json()
    transport.error = error
    response = await client.post("/api/bioxp/protocols/submit", json={
        "source_type": "native", "document": compiled["document"], "dry_run": False,
        "idempotency_key": KEY, "expected_connection_generation": 77, "live_execution_ack": True})
    detail = response.json()["detail"]
    assert detail.get("dispatch_state") == expected
    assert detail.get("delivery") != "not_submitted"  # only timeout's explicit dispatch_state qualifies
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_legacy_prelease_refusal_has_explicit_wire_evidence(store):
    client, transport, _, _ = store
    compiled = (await client.post(BASE + "/compile", json={"method": authored_method()})).json()
    response = await client.post("/api/bioxp/protocols/submit", json={
        "source_type": "native", "document": compiled["document"], "dry_run": False,
        "idempotency_key": KEY, "expected_connection_generation": 76, "live_execution_ack": True})
    assert response.status_code == 409
    assert response.json()["detail"] == {"delivery": "not_submitted", "dispatch_state": "not_dispatched",
                                         "native_reason": "Connection generation changed"}
    assert not transport.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [{"pending": True}, {"ok": False, "status": "failed"}, {"uncertain": True}])
async def test_real_compiler_partial_occurrence_owned_child_shapes(store, child):
    """Constructed fault controls using native _consume parent_action_id shape.

    Not a native execution claim; native producer exports are replayed separately.
    """
    client, transport, _, _ = store
    response = await client.post(BASE + "/quick-runs", json=submit_body(method=authored_method()))
    assert response.status_code == 202, response.text
    snapshot = response.json()["method_snapshot"]
    provenance = snapshot["compilation"]["provenance"][0]
    actions = provenance["native_action_ids"]
    state = transport.payload["execution"]["runtime_state"]
    state["stage_states"] = {"method": {"stage_id": "method", "title": None, "status": "failed",
        "review_required": False, "current_action_id": actions[1], "completed_actions": actions[:2], "pause_marker_action_id": None}}
    state["action_results"] = [{"action_id": actions[1], "ok": True, "status": "completed"},
                                {"parent_action_id": actions[1], "kind": "owned_child", **child}]
    state["workflow"].update(gate="error_hold", gate_id=actions[1], held_reason="source_error_hold",
                             source_occurrence_id=provenance["occurrence_id"])
    report = (await client.get(BASE + "/runs/" + JOB + "/report")).json()
    row = report["occurrences"][0]
    assert row["children"][0]["status"] == "completed"
    assert row["children"][1]["status"] == ("running" if child.get("pending") else "failed" if child.get("ok") is False else "unknown")
    assert row["children"][1]["results"] == state["action_results"]
    assert all(c["status"] == "unknown" for c in row["children"][2:])
    assert report["workflow"]["gate"] == "error_hold"
    before = deepcopy(transport.payload)
    recovered = (await client.post(BASE + "/runs/" + JOB + "/recovery-draft", json={
        "occurrence": {**provenance, "action_id": actions[1]}, "initial_state": {"tips": "unknown"}})).json()
    assert recovered["method"] == snapshot["method"] and recovered["recovery"]["automatic_setup"] == []
    assert transport.payload == before
    # Failed action Continue is not ordinary pause or an implicit retry.
    result = await client.post(BASE + "/runs/" + JOB + "/control", json={
        "action": "continue", "gate": "error_hold", "gate_id": actions[1], "command_id": JOB,
        "expected_connection_generation": 77, "expected_ownership_generation": 7, "idempotency_key": "no-retry"})
    assert result.status_code == 422
    assert [route for route, _ in transport.calls].count("protocol_execute") == 1
