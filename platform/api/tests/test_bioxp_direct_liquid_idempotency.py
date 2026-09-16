"""Direct-liquid identity crosses the real router, lease and HTTP construction.

The transport records bytes; it is not a replacement robot validation oracle.
The robot middleware/receipt contract is checked separately against robot source.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from ipaddress import ip_address
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import bioxp
from services.bioxp.connection import BioXpConnectionService
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_operator_controls import FakeRobotClient


CASES = [
    ("pipettes/readback", "pipette_readback", "/liquid/readback", {"include_data": False}),
    ("pipettes/application/plan", "pipette_application_plan", "/liquid/application/plan",
     {"operation": "detect_fluid", "fluid_class": "RC"}),
]


class LeasedConnection:
    """Real request dispatch methods; only the already-established lease is fake."""
    request_active = BioXpConnectionService.request_active
    request_active_query = BioXpConnectionService.request_active_query
    _request_client = staticmethod(BioXpConnectionService._request_client)

    def __init__(self, client):
        self.client = client
        self._v1_workflow_lock = asyncio.Lock()

    def snapshot(self):
        return SimpleNamespace(generation=77)

    @asynccontextmanager
    async def active_request_lease(self, *, expected_generation, require_fresh):
        assert expected_generation == 77
        assert require_fresh is True
        yield self.client

    active_query_lease = active_request_lease


def make_http_client(monkeypatch, route, *, failure=None):
    monkeypatch.setenv("BMS_BIOXP_MUTATIONS_ENABLED", "0")
    requests = []
    response = FakeRobotClient().responses[route]
    if route == "pipette_readback":
        response = {**response, "semantic_query_response_verified": response["receipt_truth"]["semantic_query_response_verified"]}

    def transport(request):
        requests.append(request)
        if failure is not None:
            raise failure("lost response", request=request)
        return httpx.Response(200, json=response)

    robot = BioXpRobotClient(
        ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http", hostname="robot",
                            port=8123, resolved_addresses=(ip_address("100.64.0.10"),)),
        transport=httpx.MockTransport(transport),
    )
    app = FastAPI()
    app.state.bioxp_runtime = SimpleNamespace(connection=LeasedConnection(robot))
    app.include_router(bioxp.router, prefix="/api/bioxp")
    return TestClient(app), requests


@pytest.mark.parametrize("suffix,route,path,body", CASES)
def test_direct_liquid_identity_reaches_http_unchanged(monkeypatch, suffix, route, path, body):
    client, requests = make_http_client(monkeypatch, route)
    key = "f33:request-12345678"
    for _ in range(2):
        response = client.post(f"/api/bioxp/operator-controls/{suffix}?expected_connection_generation=77", json=body,
                               headers={"Idempotency-Key": key})
        assert response.status_code == 200, response.text
    assert [(r.method, r.url.path) for r in requests] == [("POST", path)] * 2
    assert [r.headers.get("idempotency-key") for r in requests] == [key, key]
    assert [json.loads(r.content) for r in requests] == [body, body]


@pytest.mark.parametrize("suffix,route,path,body", CASES)
@pytest.mark.parametrize("key", [None, "short", "bad key-1234", "x" * 201, "bad-key-123\n"])
def test_direct_liquid_invalid_identity_fails_before_dispatch(monkeypatch, suffix, route, path, body, key):
    client, requests = make_http_client(monkeypatch, route)
    headers = {} if key is None else {"Idempotency-Key": key}
    response = client.post(f"/api/bioxp/operator-controls/{suffix}?expected_connection_generation=77", json=body, headers=headers)
    assert response.status_code == 422
    assert requests == []


@pytest.mark.parametrize("suffix,route,path,body", CASES)
def test_direct_liquid_post_uses_retained_generation(monkeypatch, suffix, route, path, body):
    client, requests = make_http_client(monkeypatch, route)
    def forbidden_snapshot(self):
        raise AssertionError("POST must not substitute current generation")
    monkeypatch.setattr(LeasedConnection, "snapshot", forbidden_snapshot)
    response = client.post(f"/api/bioxp/operator-controls/{suffix}?expected_connection_generation=77",
                           json=body, headers={"Idempotency-Key": "f33:retained-generation"})
    assert response.status_code == 200, response.text
    assert len(requests) == 1


@pytest.mark.parametrize("suffix,route,path,body", CASES)
def test_direct_liquid_post_requires_generation(monkeypatch, suffix, route, path, body):
    client, requests = make_http_client(monkeypatch, route)
    response = client.post(f"/api/bioxp/operator-controls/{suffix}", json=body,
                           headers={"Idempotency-Key": "f33:retained-generation"})
    assert response.status_code == 422
    assert requests == []


@pytest.mark.parametrize("suffix,route,path,body", CASES)
def test_direct_liquid_lost_response_never_issues_recovery_mutation(monkeypatch, suffix, route, path, body):
    client, requests = make_http_client(monkeypatch, route, failure=httpx.ReadTimeout)
    response = client.post(f"/api/bioxp/operator-controls/{suffix}?expected_connection_generation=77", json=body,
                           headers={"Idempotency-Key": "f33:lost-response"})
    assert response.status_code >= 400
    assert [(r.method, r.url.path) for r in requests] == [("POST", path)]
    assert requests[0].headers.get("idempotency-key") == "f33:lost-response"
