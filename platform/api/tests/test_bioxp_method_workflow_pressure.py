"""Disconnected workflow pressure preference: raw revisions vs compiled policy."""
import json
import os
from pathlib import Path

import pytest

from test_bioxp_methods_api import BASE, create, store
from test_bioxp_methods_integrated import authored_method, submit_body

FIELD = "workflow_pressure_streaming"


@pytest.mark.asyncio
async def test_revision_pressure_policy(store):
    client, transport, _, sessions = store
    raw = authored_method()
    raw["steps"].append({"step_id": "manual", "type": "action", "action": "pressure_stream",
                         "inputs": {"channels": [0], "timeout_ms": 1000, "enabled": False}})
    saved = await create(client, raw)
    url = BASE + "/library/" + saved["id"]
    for base, choice in enumerate((False, True), 1):
        response = await client.put(url, json={"method": {**raw, FIELD: choice}, "expected_base_revision": base})
        assert response.status_code == 200, response.text
    await sessions.kw["bind"].dispose()  # Force the revision reads onto a new SQLite connection.
    previous_stages = None
    for rev, choice in enumerate((None, False, True), 1):
        method = (await client.get(f"{url}/revisions/{rev}")).json()["method"]
        assert method == (raw if choice is None else {**raw, FIELD: choice})
        compiled = (await client.post(BASE + "/compile", json={"method": method})).json()
        document = compiled["document"]
        assert document, compiled["issues"]
        metadata = document["metadata"]["bms_method"]
        assert metadata[FIELD] is (True if choice is None else choice)
        assert metadata["method"] == method
        assert document["stages"][-1]["actions"][-1]["params"]["application"]["operations"][0]["values"] == {"pressure_streaming": False}
        if previous_stages is not None:
            assert document["stages"] == previous_stages
        previous_stages = document["stages"]
    assert not transport.calls
    # Run an older revision with an opposite head. Recovery uses the frozen job.
    job = await client.post(url + "/runs", json=submit_body(revision=2))
    assert job.status_code == 202, job.text
    assert transport.payload["protocol"]["document"]["metadata"]["bms_method"][FIELD] is False
    recovered = await client.post(BASE + "/runs/" + job.json()["job_id"] + "/recovery-draft", json={})
    assert recovered.json()["method"][FIELD] is False


@pytest.mark.asyncio
async def test_pressure_transfer_contract_for_native_receiver(store):
    client, transport, _, _ = store
    documents = {}
    for name, preference in (("default-on", {}), ("off", {FIELD: False})):
        method = {**authored_method(), **preference}
        result = (await client.post(BASE + "/compile", json={"method": method})).json()
        document = result["document"]
        assert document, result["issues"]
        assert document["metadata"]["bms_method"][FIELD] is (name == "default-on")
        documents[name] = document
    assert documents["default-on"]["stages"] == documents["off"]["stages"]
    assert not transport.calls
    if path := os.environ.get("BIOXP_WORKFLOW_PRESSURE_DOCUMENTS"):
        Path(path).write_text(json.dumps(documents, indent=2) + "\n")


@pytest.mark.asyncio
async def test_pressure_discovery_and_strict_admission(store):
    client, transport, _, _ = store
    schema = (await client.get(BASE + "/schema")).json()["method"]
    assert FIELD not in schema["required"]
    assert schema["properties"][FIELD]["type"] == "boolean"
    assert schema["properties"][FIELD]["default"] is True
    for invalid in (None, 0, 1, "false", [], {}):
        method = {**authored_method(), FIELD: invalid}
        assert (await client.post(BASE + "/library", json={"method": method})).status_code == 422
        result = (await client.post(BASE + "/compile", json={"method": method})).json()
        assert result["document"] is None and FIELD in result["issues"][0]["message"]
    assert not transport.calls
