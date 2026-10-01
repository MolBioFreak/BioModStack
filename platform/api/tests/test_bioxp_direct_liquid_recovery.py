"""HTTP-boundary recovery contract; not actual robot-store acceptance."""
from contextlib import asynccontextmanager
from ipaddress import ip_address
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import bioxp
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_direct_liquid_idempotency import LeasedConnection


KEY = "f33:retained-request"
URL = "/api/bioxp/operator-controls/pipettes/requests"


def envelope(kind="readback"):
    return {"schema": "bioxp.direct-liquid.lookup.v1", "request_kind": kind,
            "idempotency_key": KEY, "lookup_state": "unknown", "reason": "identity_not_found",
            "retry_forbidden": True, "live_query_performed": False, "record": None}


def client_for(payload, status=200):
    requests = []
    def transport(request):
        requests.append(request)
        return (httpx.Response(status, content=payload, headers={"content-type": "application/json"})
                if isinstance(payload, bytes) else httpx.Response(status, json=payload))
    robot = BioXpRobotClient(ValidatedBioXpTarget(
        api_url="http://robot:8123", scheme="http", hostname="robot", port=8123,
        resolved_addresses=(ip_address("100.64.0.10"),)), transport=httpx.MockTransport(transport))
    class Connection(LeasedConnection):
        def snapshot(self):
            raise AssertionError("must use caller generation, never current snapshot")
        @asynccontextmanager
        async def active_query_lease(self, *, expected_generation, require_fresh):
            assert expected_generation == 77
            assert isinstance(require_fresh, bool)
            yield self.client
    app = FastAPI()
    app.state.bioxp_runtime = SimpleNamespace(connection=Connection(robot))
    app.include_router(bioxp.router, prefix="/api/bioxp")
    return TestClient(app), requests


@pytest.mark.parametrize("kind", ["readback", "application_plan"])
def test_lookup_uses_pinned_get_without_body(kind):
    client, requests = client_for(envelope(kind))
    response = client.get(URL, params={"request_kind": kind, "expected_connection_generation": 77},
                          headers={"Idempotency-Key": KEY})
    assert response.status_code == 200, response.text
    assert response.json() == envelope(kind)
    assert response.headers["cache-control"] == "no-store"
    assert len(requests) == 1
    request = requests[0]
    assert (request.method, request.url.path) == ("GET", "/liquid/requests")
    assert dict(request.url.params) == {"request_kind": kind}
    assert request.headers["idempotency-key"] == KEY
    assert request.content == b""


@pytest.mark.parametrize("query,headers", [
    ("request_kind=readback&expected_connection_generation=77&extra=x", [("Idempotency-Key", KEY)]),
    ("request_kind=readback&request_kind=readback&expected_connection_generation=77", [("Idempotency-Key", KEY)]),
    ("request_kind=readback&expected_connection_generation=77&expected_connection_generation=77", [("Idempotency-Key", KEY)]),
    ("request_kind=readback&expected_connection_generation=77", [("Idempotency-Key", KEY)] * 2),
])
def test_lookup_rejects_ambiguous_ingress(query, headers):
    client, requests = client_for(envelope())
    assert client.get(URL + "?" + query, headers=headers).status_code == 422
    assert requests == []


@pytest.mark.parametrize("kind", ["readback", "application_plan"])
@pytest.mark.parametrize("query,duplicate", [
    ("expected_connection_generation=77&extra=x", False),
    ("expected_connection_generation=77&expected_connection_generation=77", False),
    ("expected_connection_generation=77", True),
])
def test_post_rejects_ambiguous_ingress(kind, query, duplicate):
    client, requests = client_for({})
    suffix = "readback" if kind == "readback" else "application/plan"
    body = {"include_data": False} if kind == "readback" else {"operation": "move_to_waste"}
    headers = [("Idempotency-Key", KEY)] * (2 if duplicate else 1)
    response = client.post("/api/bioxp/operator-controls/pipettes/" + suffix + "?" + query, json=body, headers=headers)
    assert response.status_code == 422, response.text
    assert requests == []


def test_readback_preserves_producer_hardware_truth_level():
    result = {**record("readback")["result"], "hardware_truth_level": "hardware_query"}
    client, _ = client_for({**result, "semantic_query_response_verified": result["receipt_truth"]["semantic_query_response_verified"]})
    response = client.post("/api/bioxp/operator-controls/pipettes/readback?expected_connection_generation=77",
        json={"include_data": False}, headers={"Idempotency-Key": KEY})
    assert response.status_code == 200, response.text
    assert response.json() == {**result, "semantic_query_response_verified": result["receipt_truth"]["semantic_query_response_verified"]}


def record(kind):
    from test_bioxp_operator_controls import FakeRobotClient
    plan = kind == "application_plan"
    result = FakeRobotClient().responses["pipette_application_plan" if plan else "pipette_readback"]
    operation = "application_plan:" + result["operation"] if plan else "live_readback"
    return {"command_id": "a" * 32, "pipette_operation_id": result["receipt_id"],
            "canonical_request_sha256": "b" * 64, "operation": operation,
            "entrypoint_id": "legacy.record" if plan else "direct.liquid.readback",
            "caller_class": "legacy" if plan else "direct_api",
            "control_class": "pipette_state_command" if plan else "hardware_query",
            "action_id": "pipette." + operation, "command_status": "completed",
            "pipette_status": "completed", "outcome": "completed", "failure_code": None,
            "ownership_generation": 2, "connection_generation": None,
            "requested_inputs": {"operation": result["operation"], "home_z_after": True, "fluid_class": "RC"} if plan else {"include_data": False},
            "result": result}


@pytest.mark.parametrize("kind", ["readback", "application_plan"])
def test_lookup_preserves_typed_original_record(kind):
    payload = {**envelope(kind), "lookup_state": "resolved", "reason": None, "record": record(kind)}
    client, _ = client_for(payload)
    response = client.get(URL, params={"request_kind": kind, "expected_connection_generation": 77}, headers={"Idempotency-Key": KEY})
    assert response.status_code == 200, response.text
    assert response.json() == payload


@pytest.mark.parametrize("state,reason,status,result", [
    ("pending", "nonterminal", "running", False),
    ("incomplete", "outcome_unresolved", "future_unknown", False),
    ("incomplete", "receipt_incomplete", "completed", False),
    ("resolved", None, "failed", False),
    ("resolved", None, "observed", True),
])
def test_lookup_state_evidence_matrix(state, reason, status, result):
    r = record("readback")
    r.update(command_status=status, pipette_status=status, outcome=status,
             result=r["result"] if result else None)
    # A durable receipt has its own identity, distinct from the child row ID.
    r["pipette_operation_id"] = "c" * 32
    payload = {**envelope(), "lookup_state": state, "reason": reason, "record": r}
    client, _ = client_for(payload)
    response = client.get(URL, params={"request_kind": "readback", "expected_connection_generation": 77}, headers={"Idempotency-Key": KEY})
    assert response.status_code == 200, response.text
    assert response.json() == payload


@pytest.mark.parametrize("state,reason,status", [("conflict", "identity_scope_conflict", 409), ("unavailable", "store_unavailable", 503)])
def test_lookup_validates_controlled_error_envelopes(state, reason, status):
    payload = {**envelope(), "lookup_state": state, "reason": reason}
    client, _ = client_for(payload, status)
    response = client.get(URL, params={"request_kind": "readback", "expected_connection_generation": 77}, headers={"Idempotency-Key": KEY})
    assert response.status_code == status
    assert response.json() == payload
    assert response.headers["cache-control"] == "no-store"
