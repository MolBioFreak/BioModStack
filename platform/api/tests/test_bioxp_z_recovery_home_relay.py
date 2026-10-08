"""Exact BMS HTTP relay boundary, inert recording connection; no robot transport."""
import unittest
from types import SimpleNamespace
import httpx
from fastapi import FastAPI
from routers.bioxp.operator_controls import router
from routers.bioxp.dependencies import get_bioxp_runtime, require_bioxp_mutation_access


class ZRecoveryHomeRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_existing_native_action_and_empty_inputs(self):
        calls = []
        async def enqueue(route, **kwargs):
            calls.append((route, kwargs))
            return {"synthetic_recording_transport": True}
        app = FastAPI()
        app.include_router(router, prefix="/api/bioxp")
        app.dependency_overrides[get_bioxp_runtime] = lambda: SimpleNamespace(connection=SimpleNamespace(request_active_v2_enqueue=enqueue))
        app.dependency_overrides[require_bioxp_mutation_access] = lambda: None
        body = {"schema_version": "bioxp.operator_action_request.v2", "idempotency_key": "explicit-recovery-intent",
                "expected_connection_generation": 7, "expected_ownership_generation": 4,
                "expected_board_epoch_by_board": {}, "inputs": {}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated.test") as client:
            response = await client.post("/api/bioxp/operator-controls/v2/actions/oem.z.diagnostic_home_axis", json=body)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(calls, [("invoke_operator_action_v2", {"expected_generation": 7,
                "path_params": {"action_id": "oem.z.diagnostic_home_axis"},
                "json_data": {k: v for k, v in body.items() if k != "expected_connection_generation"}})])
            for unsupported in ({"steps": 10000}, {"clear_history": True}, {"reset": True}):
                rejected = await client.post("/api/bioxp/operator-controls/v2/actions/oem.z.diagnostic_home_axis", json={**body, "inputs": unsupported})
                self.assertEqual(rejected.status_code, 422)
            self.assertEqual(len(calls), 1)
