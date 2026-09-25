"""Mounted UI bridge into actual BMS routes and offline robot HTTP transport."""
import asyncio
from ipaddress import ip_address
import json
import sys
import httpx
import pytest
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_operator_controls import make_client
from bioxp_calibration_route_bridge import FIXTURE

FLAGS = {"CheckForStaticTipLoss": False, "CheckSnapTips": True, "LogPressure": True}

def relay_pipette(method="get", resource="flags", request=None, saved=False, error_status=None):
    calls = []
    async def transport(req):
        body = json.loads(req.content) if req.content else None
        calls.append({"method": req.method, "path": req.url.path, "body": body})
        if error_status:
            return httpx.Response(error_status, json={"detail": "robot failed"})
        if resource == "set":
            return httpx.Response(200, json={"measured_z": 0, "committed_revision_id": FIXTURE["after"]["saved_revision_id"]})
        if resource == "calibration":
            return httpx.Response(200, json=FIXTURE["after" if saved else "before"])
        values = dict(FLAGS)
        if saved:
            values.update({"CheckForStaticTipLoss": True, "LogPressure": False})
        if body:
            values.update(body)
        return httpx.Response(200, json={"operation_parameters": {**values, "Mode": "WebMode"}})
    target = ValidatedBioXpTarget(api_url="http://offline-fixture:8123", scheme="http", hostname="offline-fixture",
        port=8123, resolved_addresses=(ip_address("192.0.2.1"),))
    robot = BioXpRobotClient(target, transport=httpx.MockTransport(transport))
    with pytest.MonkeyPatch.context() as patch:
        client, runtime = make_client(patch)
        runtime.connection.client = robot
        path = "/api/bioxp/calibration-settings/manual-tip-set" if resource == "set" else (
            "/api/bioxp/calibration-settings" if resource == "calibration" else "/api/bioxp/operation-parameters")
        if method == "post":
            response = client.post(path, json=request)
        elif method == "patch":
            response = client.patch(path, json=request)
        else:
            response = client.get(path, params=request or {"expected_connection_generation": 77})
        leases = runtime.connection.active_request_calls
    asyncio.run(robot.close())
    return {"status": response.status_code, "data": response.json(), "robot_requests": calls, "leases": leases}

if __name__ == "__main__":
    print(json.dumps(relay_pipette(**json.load(sys.stdin))))
