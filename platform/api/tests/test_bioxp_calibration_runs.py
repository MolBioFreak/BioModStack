"""Relay contract tests; robot replies are synthetic, not physical qualification."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from bioxp_calibration_route_bridge import relay_calibration
from services.bioxp.connection import BioXpConnectionService

RUN = {"run_id": "cal-run-1", "before": {"positions": [], "liquid_calibration": {}, "revision_id": None},
       "after": {"positions": [], "liquid_calibration": {}, "revision_id": "partial-2"},
       "decision": None, "body_completed": False, "saved_revision_id": "partial-2",
       "active_revision_id": "partial-2", "measurements": [{"plate": "TC", "measured_raw_z": 0,
       "saved_revision_id": "partial-1"}], "error": "scan failed", "finalization_error": "park failed"}

@pytest.mark.parametrize("decision", ["accept", "restore"])
def test_run_decision_fixed_path_typed_body_and_lossless_reply(decision):
    result = relay_calibration("post", {"expected_connection_generation": 77, "decision": decision},
                               run_id=RUN["run_id"], run_payload={**RUN, "decision": decision})
    assert result["status"] == 200
    assert result["data"] == {**RUN, "decision": decision}
    assert result["robot_requests"] == [{"method": "POST", "path": "/motion/oem/calibration_settings/runs/cal-run-1/decision",
                                          "body": {"decision": decision}}]
    assert result["leases"][0]["require_fresh"] is False

@pytest.mark.parametrize("decision", [None, True, "reject", "ACCEPT", 1])
def test_invalid_decision_never_reaches_robot(decision):
    result = relay_calibration("post", {"expected_connection_generation": 77, "decision": decision}, run_id=RUN["run_id"], run_payload=RUN)
    assert result["status"] == 422
    assert result["robot_requests"] == []

@pytest.mark.parametrize("body,status", [
    ({"decision": "accept", "expected_connection_generation": 78}, 409),
    ({"decision": "accept", "expected_connection_generation": True}, 422),
    ({"decision": "accept", "expected_connection_generation": 77, "force": True}, 422),
])
def test_generation_and_closed_request(body, status):
    result = relay_calibration("post", body, run_id=RUN["run_id"], run_payload=RUN)
    assert result["status"] == status
    assert result["robot_requests"] == []


def test_read_only_without_mutation_permission_and_failure_not_success():
    result = relay_calibration(run_id=RUN["run_id"], run_payload=RUN, mutations=False)
    assert result["status"] == 200 and result["data"] == RUN
    assert result["robot_requests"][0]["path"] == "/motion/oem/calibration_settings/runs/cal-run-1"
    for kwargs, status in [({"mutations": False}, 503), ({"error_status": 409}, 409), ({"error_status": 500}, 500)]:
        failed = relay_calibration("post", {"expected_connection_generation": 77, "decision": "restore"},
                                   run_id=RUN["run_id"], run_payload=RUN, **kwargs)
        assert failed["status"] == status
        assert "decision" not in failed["data"]


def test_passive_run_read_has_generation_lease_no_fresh_status_gate_or_cache():
    leases, calls = [], []
    @asynccontextmanager
    async def lease(**kwargs):
        leases.append(kwargs)
        yield object()
    async def request(client, route, **kwargs):
        calls.append((route, kwargs))
        return RUN
    owner = SimpleNamespace(active_query_lease=lease, _request_client=request, _v2_query_revision=0)
    async def scenario():
        for _ in range(2):
            assert await BioXpConnectionService.request_active_v2_query(owner, "calibration_run", expected_generation=77,
                path_params={"run_id": RUN["run_id"]}) == RUN
    asyncio.run(scenario())
    assert leases == [{"expected_generation": 77, "require_fresh": False}] * 2
    assert len(calls) == 2
    assert all(route == "calibration_run" and args["path_params"] == {"run_id": RUN["run_id"]} for route, args in calls)
