"""Offline native projection through real BMS ASGI routes and MockTransport."""
import asyncio
from ipaddress import ip_address
import json
import os
from pathlib import Path
import sys
import httpx
import pytest
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget
from test_bioxp_operator_controls import make_client

FIXTURE = json.loads((Path(__file__).parent / "fixtures/bioxp_calibration_settings.json").read_text())
if os.environ.get("BIOXP_NATIVE_CONTRACTS"):
    native = json.loads(Path(os.environ["BIOXP_NATIVE_CONTRACTS"]).read_text())
    FIXTURE = {"before": native["calibration_initial_response"], "after": native["calibration_save_response"],
               "schema": native["calibration_patch_schema"]}

def relay_calibration(method="get", request=None, saved=False, error_status=None, mutations=True):
    calls = []
    payload = FIXTURE["after" if saved or method == "patch" else "before"]
    async def transport(req):
        calls.append({"method": req.method, "path": req.url.path, "body": json.loads(req.content) if req.content else None})
        return httpx.Response(error_status or 200, json={"detail": "settings storage failed"} if error_status else payload)
    target = ValidatedBioXpTarget(api_url="http://offline-fixture:8123", scheme="http", hostname="offline-fixture",
        port=8123, resolved_addresses=(ip_address("192.0.2.1"),))
    robot = BioXpRobotClient(target, transport=httpx.MockTransport(transport))
    with pytest.MonkeyPatch.context() as patch:
        client, runtime = make_client(patch, mutations=mutations)
        runtime.connection.client = robot
        if method == "patch":
            response = client.patch("/api/bioxp/calibration-settings", json=request)
        else:
            response = client.get("/api/bioxp/calibration-settings", params=request or {"expected_connection_generation": 77})
        leases = runtime.connection.active_request_calls
    asyncio.run(robot.close())
    return {"status": response.status_code, "data": response.json(), "robot_requests": calls, "leases": leases}

if __name__ == "__main__":
    print(json.dumps(relay_calibration(**json.load(sys.stdin))))
