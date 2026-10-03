"""Project unchanged committed native captures through the actual Methods ASGI facade.

Only the native HTTP transport is replaced; no synthetic BMS snapshot is injected.
Input: {filename, state, path: report|recovery-draft, body?} on stdin.
"""
import gzip
import json
import os
from pathlib import Path
import sys
from ipaddress import ip_address
from types import SimpleNamespace

sys.path.insert(0, os.environ.get("BIOXP_METHOD_API_DIR", "../api"))
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.bioxp.methods import router
from services.bioxp.robot_client import BioXpRobotClient
from services.bioxp.target_policy import ValidatedBioXpTarget

request = json.load(sys.stdin)
producer = json.loads(gzip.decompress((Path(os.environ["BIOXP_NATIVE_BMS_SNAPSHOTS"]) / request["filename"]).read_bytes()))
job = producer[request["state"]]

def native_http(req):
    assert req.method == "GET", "Authoring must never mutate native runtime"
    assert req.url.path == "/protocol/jobs/" + job["job_id"]
    return httpx.Response(200, json=job)

native = BioXpRobotClient(ValidatedBioXpTarget(api_url="http://robot:8123", scheme="http", hostname="robot", port=8123, resolved_addresses=(ip_address("100.64.0.10"),)), transport=httpx.MockTransport(native_http))

class Connection:
    generation = 9

    async def request_active_v2_query(self, route, *, expected_generation, **kwargs):
        assert expected_generation == self.generation
        return await native.request(route, **kwargs)

app = FastAPI()
app.state.bioxp_runtime = SimpleNamespace(connection=Connection())
app.include_router(router, prefix="/api/bioxp")
with TestClient(app) as client:
    path = f'/api/bioxp/methods/runs/{job["job_id"]}/{request["path"]}'
    if request["path"] == "report":
        response = client.get(path, params={"expected_connection_generation": 9})
    else:
        response = client.post(path, params={"expected_connection_generation": 9}, json=request["body"])
    assert response.status_code == 200, response.text
    print(response.text)
