import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from routers.bioxp.operator_controls import router, operator_control_catalog
from routers.bioxp.dependencies import get_bioxp_runtime
from services.bioxp.errors import RobotTransportError


@pytest.mark.parametrize("view", ["metadata", "assessment"])
def test_actual_wire_through_http_relay(view):
    path = os.environ.get("BMS_CATALOG_SPLIT_WIRE")
    if not path:
        pytest.skip("set BMS_CATALOG_SPLIT_WIRE to actual robot projection export")
    wire = json.loads(Path(path).read_text())[view]
    calls = []
    class Connection:
        generation = 7
        async def request_active_query(self, route, **kwargs):
            calls.append((route, kwargs))
            return {k:v for k,v in wire.items() if k != "canonical"}
        async def request_active_v2_query(self, route, **kwargs):
            calls.append((route, kwargs))
            return wire["canonical"]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_bioxp_runtime] = lambda: SimpleNamespace(connection=Connection())
    with TestClient(app) as client:
        response = client.get("/operator-controls/catalog", params={"view": view, "z_target_steps": -2147483648})
    assert response.status_code == 200, response.text
    assert response.json() == wire
    assert calls == [("operator_control_catalog", {"params": {"view": view, "z_target_steps": -2147483648}, "expected_generation": 7, "require_fresh": False}),
                     ("operator_control_catalog_v2", {"expected_generation": 7, "params": {"schema_version": "bioxp.operator_control_catalog.v2", "view": view}})]


@pytest.mark.parametrize("cancel", [False, True])
def test_split_reads_overlap_and_sibling_is_drained_on_failure_or_cancellation(cancel):
    async def run():
        started = asyncio.Event()
        drained = []
        class Connection:
            generation = 3
            async def request_active_query(self, *args, **kwargs):
                await started.wait()
                if cancel:
                    await asyncio.Future()
                raise RobotTransportError("offline")
            async def request_active_v2_query(self, *args, **kwargs):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    drained.append(True)
        task = asyncio.create_task(operator_control_catalog(z_target_steps=65000, view="assessment", runtime=SimpleNamespace(connection=Connection())))
        await started.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else HTTPException):
            await task
        assert drained == [True]
    asyncio.run(run())


def test_old_robot_is_not_silent_full_poll_fallback():
    async def run():
        class Connection:
            generation = 3
            async def request_active_query(self, *args, **kwargs):
                return {"actions": []}
            request_active_v2_query = request_active_query
        with pytest.raises(HTTPException, match="split-view release required") as error:
            await operator_control_catalog(z_target_steps=None, view="assessment", runtime=SimpleNamespace(connection=Connection()))
        assert error.value.status_code == 426
    asyncio.run(run())
