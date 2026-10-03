"""Exact committed native BMS compiler snapshots through HTTP and mounted API.

No native IDs/metadata/results are rewritten. Physical execution belongs to the
pinned producer; these tests establish receiving, projection and draft storage.
"""
from copy import deepcopy
import gzip
from hashlib import sha256
from ipaddress import ip_address
import json
from pathlib import Path

import httpx
import pytest

from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.method_snapshot import method_snapshot
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_methods_api import BASE, Connection, create, store

FIXTURES = Path(__file__).parent / "fixtures/bioxp_methods/native-close"
MANIFEST = json.loads((FIXTURES / "producer-manifest.json").read_bytes())
EXPORTS = [e for e in MANIFEST["files"] if e["path"].startswith("bms/")]


def member_bytes(raw, wanted):
    """Slice a top-level JSON value verbatim, without reserializing the job."""
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder()
    pos = text.index("{") + 1
    while True:
        while text[pos].isspace() or text[pos] == ",":
            pos += 1
        if text[pos] == "}":
            raise KeyError(wanted)
        key, pos = decoder.raw_decode(text, pos)
        while text[pos].isspace() or text[pos] == ":":
            pos += 1
        start = pos
        _, pos = decoder.raw_decode(text, pos)
        if key == wanted:
            return text[start:pos].encode("utf-8")


def load_export(entry, member="result"):
    compressed = (FIXTURES / Path(entry["path"]).name).read_bytes()
    assert sha256(compressed).hexdigest() == entry["sha256"]
    raw = gzip.decompress(compressed)
    assert sha256(raw).hexdigest() == entry["uncompressed_sha256"]
    return member_bytes(raw, member)


def connect(client, raw):
    job = json.loads(raw)
    calls = []
    def receive(request):
        calls.append(request)
        assert request.method == "GET", "Draft recovery must never dispatch or control native work"
        assert request.url.path == "/protocol/jobs/" + job["job_id"]
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})
    native = BioXpRobotClient(ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http",
        hostname="robot", port=8123, resolved_addresses=(ip_address("100.64.0.10"),)),
        transport=httpx.MockTransport(receive))
    client._transport.app.state.bioxp_runtime.connection = Connection(native)
    return native, calls, job


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", EXPORTS, ids=lambda e: Path(e["path"]).name)
async def test_all_native_bms_original_snapshots_report_clone_recovery_save(store, entry):
    client, _, _, _ = store
    raw = load_export(entry)
    native, calls, job = connect(client, raw)
    url = BASE + "/runs/" + job["job_id"]
    document = job["protocol"]["document"]
    embedded = document["metadata"]["bms_method"]
    assert "bms_method_run" not in document["metadata"]  # actual producer shape
    try:
        read = await client.get(url)
        assert read.status_code == 200, read.text
        assert read.json() == job
        response = await client.get(url + "/report")
        assert response.status_code == 200, response.text
        report = response.json()
        snapshot = report["method_snapshot"]
        assert report["snapshot_available"] is True
        assert snapshot["schema"] == "bms.bioxp-compiler-snapshot.v1"
        assert snapshot["snapshot_evidence"]["original_metadata"] == embedded
        assert snapshot["snapshot_evidence"]["complete_run_envelope"] is False
        assert snapshot["compilation"]["document"] == document
        for key in ("method", "bindings", "dependencies", "initial_state"):
            assert (key in snapshot) == (key in embedded)
            if key in embedded:
                assert snapshot[key] == embedded[key]
        state = job["execution"]["runtime_state"]
        assert report["action_results"] == state["action_results"]
        assert report["workflow"] == state["workflow"]
        assert report["events"] == state["events"]
        actions = [a for s in document["stages"] for a in s["actions"]]
        reported = {c["action_id"]: c for o in report["occurrences"] for c in o["children"]}
        assert set(reported) == {a["action_id"] for a in actions}
        for action in actions:
            child = reported[action["action_id"]]
            evidence = [r for r in state["action_results"]
                        if (r.get("action_id") or r.get("parent_action_id")) == action["action_id"]]
            assert child["results"] == evidence
            if any(r.get("ok") is False for r in evidence):
                assert child["status"] == "failed"
        address = {**actions[0]["metadata"]["bms_method"], "native_action_id": actions[0]["action_id"]}
        recovered_response = await client.post(url + "/recovery-draft", json={"occurrence": address})
        assert recovered_response.status_code == 200, recovered_response.text
        recovered = recovered_response.json()
        assert recovered["method"] == embedded["method"]  # never an executable suffix
        assert recovered["bindings"] == embedded["bindings"]
        assert recovered["dependencies"] == embedded["dependencies"]
        linkage = recovered["recovery"]
        assert linkage["original_job_id"] == job["job_id"]
        assert linkage["occurrence"] == address
        assert linkage["occurrence_resolution"]["status"] == "matched"
        assert linkage["automatic_setup"] == linkage["excluded_actions"] == []
        assert linkage["submitted"] is False
        assert linkage["snapshot_evidence"] == snapshot["snapshot_evidence"]
        assert linkage["included_occurrences"] == snapshot["compilation"]["provenance"]
        assert linkage["original_assumptions"] == {k: embedded[k] for k in ("initial_state",) if k in embedded}
        cloned = (await client.post(url + "/clone", json={})).json()
        assert cloned["method"] == embedded["method"] and cloned["submitted"] is False
        saved = await create(client, recovered["method"], name="Native recovery draft")
        reopened = (await client.get(BASE + "/library/" + saved["id"] + "/revisions/1")).json()
        assert reopened["method"] == embedded["method"]
        assert (await client.get(url)).json() == job
        assert len(calls) == 5
        assert load_export(entry) == raw
    finally:
        await native.close()


@pytest.mark.parametrize("assumptions", [{}, {"initial_state": None}, {"initial_state": {"tips": None, "volume": "00.10"}}])
def test_snapshot_projection_preserves_only_recorded_values_and_facade_precedence(assumptions):
    embedded = {"method": {"schema": "bms.bioxp-method.v1", "steps": [], "unknown": False}, **assumptions}
    document = {"metadata": {"bms_method": embedded}, "stages": [{"actions": [{"action_id": "a", "metadata": None}]}]}
    original = deepcopy(document)
    projected = method_snapshot(document)
    assert projected is not None
    assert {k: projected[k] for k in ("initial_state",) if k in projected} == assumptions
    assert projected["compilation"]["provenance"] == []
    projected["method"]["unknown"] = True
    assert document == original
    facade = {"schema": "bms.bioxp-method-run.v1", "method": {"schema": "bms.bioxp-method.v1"}, **assumptions}
    document["metadata"]["bms_method_run"] = facade
    assert method_snapshot(document) == facade
    assert method_snapshot({"metadata": None}) is None
    assert method_snapshot({"metadata": {"bms_method": {"method": {}}}}) is None


PARTIAL = [(name, member) for name, member in (
    ("bms-46-aspirate.json.gz", "result"), ("bms-46-dispense.json.gz", "result"),
    ("bms-0-error-hold.json.gz", "held"), ("bms-0-error-hold.json.gz", "result"))]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,member", PARTIAL)
@pytest.mark.parametrize("override", [{}, {"initial_state": None},
    {"initial_state": {"tips": None, "custody": "unknown", "volume_ul": "01.00", "deck": {}, "loaded": False}}])
async def test_native_partial_held_aborted_exact_recovery_assumptions(store, name, member, override):
    client, _, _, _ = store
    entry = next(e for e in EXPORTS if Path(e["path"]).name == name)
    raw = load_export(entry, member)
    native, calls, job = connect(client, raw)
    url = BASE + "/runs/" + job["job_id"]
    try:
        report = (await client.get(url + "/report")).json()
        row = report["occurrences"][0]
        child = row["children"][0]
        assert child["status"] == row["status"] == "failed"
        state = job["execution"]["runtime_state"]
        if member == "held":
            assert report["workflow"]["gate"] == "error_hold"
            assert report["workflow"]["source_occurrence_id"] == row["occurrence_id"]
            refused = await client.post(url + "/control", json={
                "action": "continue", "gate": "error_hold", "gate_id": report["workflow"]["gate_id"],
                "command_id": job["job_id"], "expected_connection_generation": 77,
                "expected_ownership_generation": job["command"]["ownership_generation"],
                "idempotency_key": "receiving-no-retry"})
            assert refused.status_code == 422  # existing typed control schema; no retry transport
        elif name == "bms-0-error-hold.json.gz":
            assert report["workflow"]["phase"] == "terminal"
            assert report["workflow"]["requested_control"] == {"action": "abort"}
        else:
            events = state["action_results"][0]["pipette_result"]["events"]
            fields = report["reported_applied"]["fields"]
            source_events = [e for e in events if "field" in e.get("inputs", {})]
            assert len(fields) == len(source_events)
            assert any(e["status"] == "failed" for e in source_events)
            assert any(e["status"] == "completed" for e in source_events)
            for field, event in zip(fields, source_events):
                assert field["inputs"] == event["inputs"]
                assert field["status"] == event["status"]
                assert field["reported_applied"] == event["reported_applied"]
                assert field["channels"] == (event.get("result") or event.get("partial") or {}).get("channels", [])
        original = report["method_snapshot"]
        address = {k: row[k] for k in ("occurrence_id", "step_id", "path", "call_path", "loop_path")}
        address["native_action_id"] = child["action_id"]
        response = await client.post(url + "/recovery-draft", json={"occurrence": address, **override})
        assert response.status_code == 200, response.text
        recovery = response.json()
        assert recovery["method"] == original["method"]
        assert recovery["initial_state"] == override.get("initial_state", original["initial_state"])
        assert recovery["recovery"]["original_assumptions"] == {"initial_state": original["initial_state"]}
        assert recovery["recovery"]["assumptions_overridden"] == ("initial_state" in override)
        assert recovery["recovery"]["occurrence_resolution"]["status"] == "matched"
        assert recovery["recovery"]["automatic_setup"] == recovery["recovery"]["excluded_actions"] == []
        bad = await client.post(url + "/recovery-draft", json={"occurrence": {**address, "native_action_id": "not-in-this-occurrence"}})
        assert bad.status_code == 200  # evidence, not an admission gate
        assert bad.json()["recovery"]["occurrence_resolution"]["status"] == "unmatched"
        assert (await client.get(url)).json() == job
        assert len(calls) == 4
    finally:
        await native.close()
