"""Final native producer exports (complete facade snapshots) through the real API.

Robot ``testdata/final_method_close`` exports are copied byte-for-byte with the
producer manifest. Each carries the actual ``metadata.bms_method_run`` envelope
created by the BMS facade before native submission. Job JSON values are sliced
from the original wrapper bytes and served by ``BioXpRobotClient`` HTTP
transport; no metadata, identity, result or byte is rewritten or injected.
Physical leaves were inert at capture: this is software receiving evidence,
not liquid/thermal/scientific qualification.
"""
import gzip
from hashlib import sha256
from ipaddress import ip_address
import json
from pathlib import Path

import httpx
import pytest

from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_methods_api import BASE, Connection, create, store  # noqa: F401  (fixture)

FIXTURES = Path(__file__).parent / "fixtures/bioxp_methods/final-close"
MANIFEST_BYTES = (FIXTURES / "manifest.json").read_bytes()
MANIFEST = json.loads(MANIFEST_BYTES)
EXPORTS = {Path(e["file"]).name: e for e in MANIFEST["exports"]}
PRODUCER_MANIFEST_SHA256 = "beb25ddcba18564c0fcaab485fb4a0497578d256c9f68192aa1a640f1a59d004"
ROBOT_COMMIT = "8f9f9a60d651665a1119e9ba3877de82a126345e"  # robot-release commit of final_method_close


def _skip_ws(text, pos, extra=""):
    while text[pos].isspace() or text[pos] in extra:
        pos += 1
    return pos


def member_bytes(raw, wanted):
    """Slice one top-level wrapper value verbatim, without reserializing."""
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder()
    pos = text.index("{") + 1
    while True:
        pos = _skip_ws(text, pos, ",")
        if text[pos] == "}":
            raise KeyError(wanted)
        key, pos = decoder.raw_decode(text, pos)
        start = pos = _skip_ws(text, pos, ":")
        _, pos = decoder.raw_decode(text, pos)
        if key == wanted:
            return text[start:pos].encode("utf-8")


def element_bytes(array, index):
    """Slice one element of a JSON array value verbatim."""
    text = array.decode("utf-8")
    decoder = json.JSONDecoder()
    pos = text.index("[") + 1
    for current in range(index + 1):
        start = pos = _skip_ws(text, pos, ",")
        _, pos = decoder.raw_decode(text, pos)
        if current == index:
            return text[start:pos].encode("utf-8")


def wrapper(name):
    entry = EXPORTS[name]
    compressed = (FIXTURES / entry["file"]).read_bytes()
    assert sha256(compressed).hexdigest() == entry["sha256"]
    raw = gzip.decompress(compressed)
    assert sha256(raw).hexdigest() == entry["uncompressed_sha256"]
    return raw


def job_bytes(name, member="result", index=None):
    value = member_bytes(wrapper(name), member)
    return value if index is None else element_bytes(value, index)


def captures():
    """Every result plus every held readback in all 101 exports."""
    rows = []
    for name in sorted(EXPORTS):
        rows.append((name, "result", None))
        held = json.loads(wrapper(name)).get("held")
        if isinstance(held, dict):
            rows.append((name, "held", None))
        elif isinstance(held, list):
            rows.extend((name, "held", i) for i in range(len(held)))
    return rows


CAPTURES = captures()


def connect(client, raw, control=None):
    """Mount BioXpRobotClient over a transport that serves only original bytes.

    ``control`` handles POST /control (recorded refusal or labeled receipt);
    without it any non-GET request fails the test.
    """
    job = json.loads(raw)
    calls = []

    def receive(request):
        calls.append(request)
        if request.method == "POST" and control is not None:
            assert request.url.path == "/protocol/jobs/" + job["job_id"] + "/control"
            return control(request)
        assert request.method == "GET", "Draft recovery/report must never dispatch or control native work"
        assert request.url.path == "/protocol/jobs/" + job["job_id"]
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    native = BioXpRobotClient(ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http",
        hostname="robot", port=8123, resolved_addresses=(ip_address("100.64.0.10"),)),
        transport=httpx.MockTransport(receive))
    client._transport.app.state.bioxp_runtime.connection = Connection(native)
    return native, calls, job


def test_fixture_corpus_is_the_byte_exact_producer_capture():
    assert sha256(MANIFEST_BYTES).hexdigest() == PRODUCER_MANIFEST_SHA256
    assert len(EXPORTS) == len(MANIFEST["exports"]) == 101
    assert sorted(p.name for p in (FIXTURES / "exports").iterdir()) == sorted(EXPORTS)
    assert MANIFEST["native_status_counts"] == {"completed": 78, "failed": 7, "ambiguous": 2}
    schemas = set()
    for name in EXPORTS:
        whole = json.loads(wrapper(name))
        run = whole["document"]["metadata"]["bms_method_run"]
        schemas.add(run["schema"])
        assert whole["result"]["protocol"]["document"] == whole["document"]
        assert whole["result"]["job_id"] == EXPORTS[name]["job_id"]
        assert whole["result"]["status"] == EXPORTS[name]["status"]
    assert schemas == {"bms.bioxp-method-run.v1"}
    assert len(CAPTURES) == 101 + 4 + 38  # results, four -held dict readbacks, 38 listed holds


@pytest.mark.asyncio
@pytest.mark.parametrize("name,member,index", CAPTURES,
                         ids=[f"{n}:{m}{'' if i is None else i}" for n, m, i in CAPTURES])
async def test_final_facade_snapshot_report_clone_recovery_save(store, name, member, index):
    client, _, _, _ = store
    raw = job_bytes(name, member, index)
    native, calls, job = connect(client, raw)
    url = BASE + "/runs/" + job["job_id"]
    document = job["protocol"]["document"]
    facade = document["metadata"]["bms_method_run"]
    try:
        read = await client.get(url)
        assert read.status_code == 200, read.text
        assert read.json() == job
        response = await client.get(url + "/report")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == job["status"]  # ambiguous/interrupted/failed never relabeled
        assert report["snapshot_available"] is True
        assert report["method_snapshot"] == facade  # exact original envelope, nothing added
        evidence = report["snapshot_evidence"]
        assert evidence["complete_run_envelope"] is True
        assert evidence["source"] == "protocol.document.metadata.bms_method_run"
        assert evidence["initial_state_presence"] == ("submitted" if "initial_state" in facade else "omitted")
        state = job["execution"]["runtime_state"]
        assert report["action_results"] == state["action_results"]
        assert report["workflow"] == state["workflow"]
        assert report["events"] == state["events"]
        actions = [a for s in document["stages"] for a in s["actions"]]
        reported = {c["action_id"]: c for o in report["occurrences"] for c in o["children"]}
        assert set(reported) == {a["action_id"] for a in actions}
        for action in actions:
            child = reported[action["action_id"]]
            evidence_rows = [r for r in state["action_results"]
                             if (r.get("action_id") or r.get("parent_action_id")) == action["action_id"]]
            assert child["results"] == evidence_rows
            if any(r.get("ok") is False for r in evidence_rows):
                assert child["status"] == "failed"
            if not evidence_rows and action["action_id"] not in {
                    a for st in state["stage_states"].values() for a in st["completed_actions"]}:
                assert child["status"] != "completed"  # missing result is never success
        cloned = (await client.post(url + "/clone", json={})).json()
        assert cloned["method"] == facade["method"] and cloned["submitted"] is False
        assert ("initial_state" in cloned) == ("initial_state" in facade)
        assert cloned["snapshot_evidence"] == evidence
        row = report["occurrences"][-1]
        address = {k: row[k] for k in ("occurrence_id", "step_id", "path", "call_path", "loop_path")}
        address["native_action_id"] = row["children"][0]["action_id"]
        recovered = (await client.post(url + "/recovery-draft", json={"occurrence": address})).json()
        assert recovered["method"] == facade["method"]  # never an executable suffix
        assert recovered["bindings"] == facade["bindings"]
        assert recovered["dependencies"] == facade["dependencies"]
        assert ("initial_state" in recovered) == ("initial_state" in facade)
        linkage = recovered["recovery"]
        assert linkage["original_job_id"] == job["job_id"]
        assert linkage["occurrence_resolution"]["status"] == "matched"
        assert linkage["included_occurrences"] == facade["compilation"]["provenance"]
        assert linkage["automatic_setup"] == linkage["excluded_actions"] == []
        assert linkage["submitted"] is False and linkage["snapshot_evidence"] == evidence
        saved = await create(client, recovered["method"], name="Final recovery draft")
        reopened = (await client.get(BASE + "/library/" + saved["id"] + "/revisions/1")).json()
        assert reopened["method"] == facade["method"]
        assert (await client.get(url)).json() == job
        assert [c.method for c in calls] == ["GET"] * 5
        assert job_bytes(name, member, index) == raw
    finally:
        await native.close()


RECOVERY = {
    # name: (failed native action, index of failed field event or None)
    "recovery-post-pickup.json.gz": "method-action-1",
    "recovery-mid-aspirate.json.gz": "method-action-1",
    "recovery-mid-dispense.json.gz": "method-action-1",
    "recovery-held.json.gz": "method-action-1",
    "recovery-aborted.json.gz": "method-action-1",
}
OVERRIDES = [{}, {"initial_state": None},
             {"initial_state": {"tips": None, "custody": "unknown", "volume_ul": "01.00", "deck": {}, "loaded": False}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(RECOVERY))
@pytest.mark.parametrize("override", OVERRIDES, ids=["inherit", "explicit-null", "explicit-object"])
async def test_post_pickup_partial_held_aborted_exact_recovery(store, name, override):
    client, _, _, _ = store
    raw = job_bytes(name)
    native, calls, job = connect(client, raw)
    url = BASE + "/runs/" + job["job_id"]
    facade = job["protocol"]["document"]["metadata"]["bms_method_run"]
    state = job["execution"]["runtime_state"]
    try:
        report = (await client.get(url + "/report")).json()
        assert report["status"] == job["status"] == EXPORTS[name]["status"]
        by_action = {c["action_id"]: (o, c) for o in report["occurrences"] for c in o["children"]}
        # Successful pickup is reported complete; the failed transfer is failed; the
        # never-dispatched eject is not called complete, skipped or not-run.
        assert by_action["method-action-0"][1]["status"] == "completed"
        row, child = by_action[RECOVERY[name]]
        assert child["status"] == row["status"] == "failed"
        assert by_action["method-action-2"][1]["status"] == "unknown"
        assert by_action["method-action-2"][1]["results"] == []
        assert row["occurrence_id"] == "occ-b3917af4ac7dc9d8096faff3"
        assert by_action["method-action-0"][0]["generated_by"] == {
            "policy": "per_transfer", "occurrence_id": row["occurrence_id"]}
        failure = state["action_results"][1]
        assert child["results"] == [failure]
        assert child["child_outcomes"] == failure["child_outcomes"]
        if name == "recovery-held.json.gz":
            assert report["workflow"]["gate"] == "error_hold"
            assert report["workflow"]["source_occurrence_id"] == row["occurrence_id"]
        if name == "recovery-aborted.json.gz":
            assert job["command"]["terminal"] is True
            assert report["status"] == "ambiguous"  # actual terminal outcome of Abort
            assert report["workflow"]["requested_control"] == {"action": "abort"}
            assert report["workflow"]["held_reason"] == "workflow_settlement_unknown"
            assert report["command"]["status"] == "ambiguous"
        if name in ("recovery-mid-aspirate.json.gz", "recovery-mid-dispense.json.gz"):
            events = [e for e in state["events"] if e.get("event") == "cavro_application_event"]
            source = [e["detail"] for e in events if "field" in e["detail"].get("inputs", {})]
            pipette = [e for r in state["action_results"] for e in
                       ((r.get("pipette_result") or {}).get("events") or [])
                       if "field" in (e.get("inputs") or {})]
            fields = report["reported_applied"]["fields"]
            assert len(fields) == len(pipette)
            for field, event in zip(fields, pipette):
                assert field["inputs"] == event["inputs"]
                assert field["status"] == event.get("status", "unknown")
                assert field["channels"] == (event.get("result") or event.get("partial") or {}).get("channels", [])
            assert source, "native cavro application events must be present"
            channel_rows = [ch for f in fields for ch in f["channels"]]
            assert channel_rows, "per-channel results must remain visible"
        address = {k: row[k] for k in ("occurrence_id", "step_id", "path", "call_path", "loop_path")}
        address["native_action_id"] = child["action_id"]
        response = await client.post(url + "/recovery-draft", json={"occurrence": address, **override})
        assert response.status_code == 200, response.text
        recovery = response.json()
        assert recovery["method"] == facade["method"]
        assert recovery["initial_state"] == override.get("initial_state", facade["initial_state"])
        assert facade["initial_state"] is None  # producer explicitly submitted null
        assert recovery["recovery"]["original_assumptions"] == {"initial_state": None}
        assert recovery["recovery"]["assumptions_overridden"] == ("initial_state" in override)
        resolution = recovery["recovery"]["occurrence_resolution"]
        assert resolution["status"] == "matched"
        assert resolution["matches"][0]["native_action_ids"] == [child["action_id"]]
        assert recovery["recovery"]["automatic_setup"] == recovery["recovery"]["excluded_actions"] == []
        assert recovery["recovery"]["submitted"] is False
        # Correct occurrence with the pickup action of a different occurrence: evidence.
        crossed = await client.post(url + "/recovery-draft", json={
            "occurrence": {**address, "native_action_id": "method-action-0"}})
        assert crossed.status_code == 200
        assert crossed.json()["recovery"]["occurrence_resolution"]["status"] == "unmatched"
        saved = await create(client, recovery["method"], name="Post-failure recovery draft")
        reopened = (await client.get(BASE + "/library/" + saved["id"] + "/revisions/1")).json()
        assert reopened["method"] == facade["method"]
        assert (await client.get(url)).json() == job
        assert [c.method for c in calls] == ["GET"] * 4
    finally:
        await native.close()


CONTROL_DOCS = ["recovery-control-abort.json.gz", "recovery-control-ordinary-held.json.gz",
                "recovery-control-ordinary.json.gz", "recovery-control-safe_stop.json.gz",
                "recovery-control-deferred-wake-refused.json.gz"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", CONTROL_DOCS)
@pytest.mark.parametrize("override", OVERRIDES, ids=["inherit", "explicit-null", "explicit-object"])
async def test_control_exports_preserve_omitted_assumptions(store, name, override):
    client, _, _, _ = store
    raw = job_bytes(name)
    native, calls, job = connect(client, raw)
    url = BASE + "/runs/" + job["job_id"]
    facade = job["protocol"]["document"]["metadata"]["bms_method_run"]
    assert "initial_state" not in facade  # producer omitted assumptions
    try:
        report = (await client.get(url + "/report")).json()
        assert report["snapshot_evidence"]["initial_state_presence"] == "omitted"
        row = report["occurrences"][0]
        address = {"occurrence_id": row["occurrence_id"], "native_action_id": row["children"][0]["action_id"]}
        recovery = (await client.post(url + "/recovery-draft", json={"occurrence": address, **override})).json()
        assert ("initial_state" in recovery) == ("initial_state" in override)
        if "initial_state" in override:
            assert recovery["initial_state"] == override["initial_state"]
        assert recovery["recovery"]["original_assumptions"] == {}
        assert recovery["recovery"]["assumptions_overridden"] == ("initial_state" in override)
        assert [c.method for c in calls] == ["GET"] * 2
    finally:
        await native.close()


def _control_body(job, **body):
    # Exactly the producer's request shape (robot tests/test_method_runtime_connected.control).
    return {"expected_connection_generation": 77, "command_id": job["job_id"],
            "idempotency_key": "control-" + body["action"],
            "expected_ownership_generation": job["command"]["ownership_generation"], **body}


@pytest.mark.asyncio
async def test_deferred_pause_and_wake_refusals_relay_recorded_native_409(store):
    client, _, _, _ = store
    name = "recovery-control-deferred-wake-refused.json.gz"
    whole = json.loads(wrapper(name))
    recorded = whole["wire"]
    sent = []

    def control(request):
        body = json.loads(request.content)
        sent.append(body)
        row = recorded[len(sent) - 1]
        assert body["action"] == row["action"]
        # The wrapper holds the parsed 409 body; serve it as the robot's JSON body.
        return httpx.Response(row["status"], json=row["response"])

    native, calls, job = connect(client, job_bytes(name), control)
    url = BASE + "/runs/" + job["job_id"]
    try:
        pause = await client.post(url + "/control", json=_control_body(job, action="pause", mode="deferred"))
        wake = await client.post(url + "/control", json=_control_body(job, action="wake", gate_id="not-reached"))
        for response, row in zip((pause, wake), recorded):
            assert response.status_code == 409, response.text  # refusal remains a refusal
            assert json.dumps(row["response"]["detail"]) in json.dumps(response.json())
        assert [b["action"] for b in sent] == ["pause", "wake"]
        assert sent[0]["mode"] == "deferred" and sent[1]["gate_id"] == "not-reached"
        assert all("expected_connection_generation" not in b for b in sent)
        report = (await client.get(url + "/report")).json()
        assert report["status"] == "completed"  # job ran to completion; no deferred gate fabricated
        assert report["workflow"]["gate"] is None and report["workflow"]["reached_control_id"] is None
        assert [c.method for c in calls] == ["POST", "POST", "GET"]
    finally:
        await native.close()


@pytest.mark.asyncio
async def test_ordinary_pause_continue_same_job_without_replay(store):
    client, _, _, _ = store
    held = json.loads(job_bytes("recovery-control-ordinary-held.json.gz") or b"")
    after_raw = job_bytes("recovery-control-ordinary.json.gz")
    after = json.loads(after_raw or b"")
    gate = held["execution"]["runtime_state"]["workflow"]
    assert gate["gate"] == "ordinary_pause" and gate["requested_control"] == {"action": "pause", "mode": "ordinary"}
    assert after["job_id"] == held["job_id"]
    sent = []

    def control(request):
        sent.append(json.loads(request.content))
        # LABELED CONSTRUCTED CONTROL: the producer did not capture receipt bytes.
        # Fields are copied from the captured after-readback; only the relay is tested.
        workflow = after["execution"]["runtime_state"]["workflow"]
        return httpx.Response(202, json={
            "control_command_id": workflow["reached_control_id"], "idempotency_key": "control-continue",
            "command_id": held["job_id"], "job_id": held["job_id"],
            "ownership_generation": held["command"]["ownership_generation"],
            "state_version": held["command"]["state_version"], "accepted": True, "reached": False,
            "phase": gate["phase"], "gate": gate["gate"], "gate_id": gate["gate_id"],
            "status_path": held["command"]["status_path"]})

    native, calls, job = connect(client, job_bytes("recovery-control-ordinary-held.json.gz"), control)
    url = BASE + "/runs/" + job["job_id"]
    try:
        report = (await client.get(url + "/report")).json()
        assert report["workflow"]["phase"] == "waiting" and report["status"] == "dispatched"
        response = await client.post(url + "/control", json=_control_body(
            job, action="continue", gate=gate["gate"], gate_id=gate["gate_id"]))
        assert response.status_code == 200, response.text
        assert response.json()["job_id"] == held["job_id"]  # same job; no new submission
        assert sent == [{"command_id": held["job_id"], "idempotency_key": "control-continue",
                         "expected_ownership_generation": held["command"]["ownership_generation"],
                         "action": "continue", "gate": "ordinary_pause", "gate_id": gate["gate_id"]}]
    finally:
        await native.close()
    native, calls, job = connect(client, after_raw)
    try:
        report = (await client.get(url + "/report")).json()
        assert report["status"] == "completed" and report["workflow"]["phase"] == "terminal"
        assert report["workflow"]["reached_control_id"] != gate["gate_id"]
        ids = [r["action_id"] for r in after["execution"]["runtime_state"]["action_results"]]
        assert ids == ["method-action-0", "method-action-1"]  # no replay of the pre-pause action
        assert [c["status"] for o in report["occurrences"] for c in o["children"]] == ["completed", "completed"]
    finally:
        await native.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,action", [("recovery-control-abort.json.gz", "abort"),
                                         ("recovery-control-safe_stop.json.gz", "safe_stop")])
async def test_active_wait_abort_and_safe_stop_readback(store, name, action):
    client, _, _, _ = store
    raw = job_bytes(name)
    native, calls, job = connect(client, raw)
    url = BASE + "/runs/" + job["job_id"]
    try:
        report = (await client.get(url + "/report")).json()
        assert report["status"] == "interrupted" and report["command"]["terminal"] is True
        assert report["workflow"]["requested_control"] == {"action": action}
        assert report["workflow"]["reached_control_id"] == report["workflow"]["last_control_id"]
        statuses = {c["action_id"]: c["status"] for o in report["occurrences"] for c in o["children"]}
        assert statuses["method-action-0"] == "failed"  # interrupted wait reports its native failure
        assert statuses["method-action-1"] == "unknown"  # next action never claimed done or skipped
        assert [r["action_id"] for r in job["execution"]["runtime_state"]["action_results"]] == ["method-action-0"]
        assert report["events"][-1]["detail"] == {"outcome": "interrupted"}
        assert [c.method for c in calls] == ["GET"]
    finally:
        await native.close()


@pytest.mark.asyncio
async def test_error_hold_continue_is_not_a_bms_retry(store):
    client, _, _, _ = store
    native, calls, job = connect(client, job_bytes("recovery-held.json.gz"))
    workflow = job["execution"]["runtime_state"]["workflow"]
    try:
        refused = await client.post(BASE + "/runs/" + job["job_id"] + "/control", json=_control_body(
            job, action="continue", gate="error_hold", gate_id=workflow["gate_id"]))
        assert refused.status_code == 422  # existing typed schema; no outbound retry
        assert calls == []
    finally:
        await native.close()
