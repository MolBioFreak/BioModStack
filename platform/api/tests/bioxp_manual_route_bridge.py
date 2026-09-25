"""Offline bridge for mounted UI tests: real BMS route and robot HTTP client.

No listener or robot network is used. The caller supplies a synthetic job reply.
The connection-generation fixture is shared with the owning API route tests.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
from ipaddress import ip_address
import json
import sys

import httpx
import pytest

from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_operator_controls import make_client
from test_bioxp_protocol_relay import bundle


def relay_manual_request(request: dict, status: str = "dispatched", error_status: int | None = None) -> dict:
    calls = []
    job_id = "protocol-live-" + sha256(request["idempotency_key"].encode()).hexdigest()
    payload = bundle(status)
    payload["job_id"] = job_id
    payload["command"].update(command_id=job_id, idempotency_key=request["idempotency_key"],
        terminal=status in {"failed", "completed"}, status_path=f"/protocol/jobs/{job_id}")
    payload["protocol"]["document"] = request["document"]
    state = payload["execution"]["runtime_state"]
    state.update(job_id=job_id, protocol_id=request["document"]["protocol_id"])
    state["workflow"].update(command_id=job_id, child_command_ids=[], phase="executing" if status == "dispatched" else "terminal")
    state["action_results"] = [{"kind": "pipette_position", "ok": status != "failed",
        "physical_effect_verified": False, "error": "source lower refused" if status == "failed" else None}]

    async def transport(req: httpx.Request) -> httpx.Response:
        calls.append({"method": req.method, "path": req.url.path, "body": json.loads(req.content)})
        return httpx.Response(error_status or (202 if status == "dispatched" else 200),
            json={"detail": "OEM door interlock denied"} if error_status else payload)

    target = ValidatedBioXpTarget(api_url="http://offline-fixture:8123", scheme="http", hostname="offline-fixture",
        port=8123, resolved_addresses=(ip_address("192.0.2.1"),))
    robot = BioXpRobotClient(target, transport=httpx.MockTransport(transport))
    with pytest.MonkeyPatch.context() as patch:
        client, runtime = make_client(patch)
        runtime.connection.client = robot
        response = client.post("/api/bioxp/protocols/submit", json=request)
        assert runtime.connection.active_request_calls[0]["require_fresh"] is False
    asyncio.run(robot.close())
    assert len(calls) == 1 and calls[0]["path"] == "/protocol/execute"
    assert calls[0]["body"] == {key: value for key, value in request.items() if key != "expected_connection_generation"}
    return {"status": response.status_code, "data": response.json(), "robot_requests": calls}


if __name__ == "__main__":
    print(json.dumps(relay_manual_request(**json.load(sys.stdin))))
