"""B4: actual routes/lease owner, inert robot transport only."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from services.bioxp.models import BioXpProfile
from routers.bioxp.protocols import router
from routers.bioxp.dependencies import require_bioxp_mutation_access
from test_bioxp_connection import _service
from test_bioxp_protocol_relay import bundle, submit_body, control_body, control_receipt, JOB


def test_protocols_bypass_v1_and_execution_without_rebinding(tmp_path):
    async def scenario():
        clients = []
        service = _service(tmp_path, clients)
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        generation = (await service.connect()).generation
        original = clients[0]
        v1_started, execute_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        async def transport(route, **kwargs):
            calls.append((route, kwargs))
            if route == "invoke_operator_action":
                v1_started.set()
                await release.wait()
                return {}
            if route == "protocol_execute":
                execute_started.set()
                await release.wait()
                return bundle()
            if route == "protocol_control": return control_receipt()
            if route == "protocol_review": return bundle()
            if route == "interrupt_operator_action_v1": return {"stopped": True}
            raise AssertionError(route)
        original.request = transport
        app = FastAPI()
        app.state.bioxp_runtime = SimpleNamespace(connection=service)
        app.dependency_overrides[require_bioxp_mutation_access] = lambda: None
        app.include_router(router, prefix="/api/bioxp")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as http:
            v1 = asyncio.create_task(service.request_active("invoke_operator_action", expected_generation=generation))
            await asyncio.wait_for(v1_started.wait(), 1)
            body = {**submit_body(), "expected_connection_generation": generation}
            execute = asyncio.create_task(http.post("/api/bioxp/protocols/submit", json=body))
            try:
                await asyncio.wait_for(execute_started.wait(), 1)
                for operation in ("control", "review"):
                    fields = {"action": "abort"} if operation == "control" else {"stage_id": "stage", "reviewer": "operator"}
                    request = {**control_body(**fields), "expected_connection_generation": generation}
                    result = await asyncio.wait_for(http.post(f"/api/bioxp/protocols/jobs/{JOB}/{operation}", json=request), 1)
                    assert result.status_code == 200, result.text
                stopped = await asyncio.wait_for(service.request_active_safety_interrupt(
                    "interrupt_operator_action_v1", expected_generation=generation,
                    json_data={"idempotency_key": "stop-key"}, path_params={"action_id": "oem.x.stop"}), 1)
                assert stopped == {"stopped": True}
                assert not execute.done() and not v1.done()
                disconnect = asyncio.create_task(service.disconnect())
                await asyncio.sleep(0)
                assert not original.closed and not disconnect.done()
                replacement = await service.connect()
                assert replacement.generation != generation
                rejected = await http.post("/api/bioxp/protocols/submit", json=body)
                assert rejected.status_code == 409
                assert not clients[1].request_calls
                before_exit = service._v2_query_revision
            finally:
                release.set()
                await v1
                response = await execute
            await disconnect
            assert response.status_code == 202, response.text
            assert original.closed
            assert service._v2_query_revision > before_exit
            assert service.snapshot().generation == replacement.generation
            assert [name for name, _ in calls].count("protocol_execute") == 1
            forwarded = next(kwargs["json_data"] for name, kwargs in calls if name == "protocol_execute")
            assert forwarded == {k: v for k, v in body.items() if k != "expected_connection_generation"}
            assert all(lease.lease_count == 0 for lease in service._generation_leases.values())
        await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_protocol_error_releases_exact_generation_lease(tmp_path, failure):
    from services.bioxp.errors import RobotResponseError, RobotTimeoutError
    async def scenario():
        clients = []
        service = _service(tmp_path, clients)
        await service.save_profile(BioXpProfile(api_url="http://robot:8123"))
        generation = (await service.connect()).generation
        count = 0
        async def transport(route, **kwargs):
            nonlocal count
            count += 1
            if failure == "timeout": raise RobotTimeoutError("lost reply", dispatched=True)
            raise RobotResponseError(409, {"detail": "workflow_busy"})
        clients[0].request = transport
        app = FastAPI()
        app.state.bioxp_runtime = SimpleNamespace(connection=service)
        app.dependency_overrides[require_bioxp_mutation_access] = lambda: None
        app.include_router(router, prefix="/api/bioxp")
        before = service._v2_query_revision
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as http:
            response = await http.post("/api/bioxp/protocols/submit", json={**submit_body(), "expected_connection_generation": generation})
        assert response.status_code == (504 if failure == "timeout" else 409), response.text
        assert count == 1 and service._generation_leases[generation].lease_count == 0
        assert service._v2_query_revision == before + 2
        await service.close()
    asyncio.run(scenario())
