import pytest
from services.bioxp.calibration_models import CalibrationSettingsPatch
from bioxp_calibration_route_bridge import FIXTURE, relay_calibration

REQUEST = {"expected_connection_generation": 77, "positions": [
    {"name": "TECANRACK1", "x": 0, "y": -17, "zLow": 34000, "zDelta": 1000, "inc_factor": 0},
    {"name": "CAMERA_OFFSET", "x": -4}]}

def test_wire_schema_matches_native_owner():
    assert CalibrationSettingsPatch.model_json_schema() == FIXTURE["schema"]

def test_full_native_read_save_readback_preserves_active_and_pending():
    before = relay_calibration()
    saved = relay_calibration("patch", REQUEST)
    after = relay_calibration(saved=True)
    assert before["data"] == FIXTURE["before"]
    assert saved["data"] == after["data"] == FIXTURE["after"]
    assert len(after["data"]["saved_positions"]) == len(FIXTURE["before"]["saved_positions"])
    assert saved["robot_requests"] == [{"method": "PATCH", "path": "/motion/oem/calibration_settings", "body": {"positions": REQUEST["positions"]}}]
    assert saved["leases"][0]["require_fresh"] is False
    assert after["data"]["active_positions"] == before["data"]["active_positions"]
    assert after["data"]["pending_restart"] is True
    assert after["data"]["motion_commanded"] is False
    row = next(r for r in after["data"]["saved_motion_positions"] if r["location_id"] == "TECANRACK1")
    assert row["z_delta"] == 53000

@pytest.mark.parametrize("positions", [[], [{"name": "TECANRACK1"}], [{"name": "NOT_A_STATION", "x": 1}],
    [{"name": "TECANRACK1", "x": None}], [{"name": "TECANRACK1", "x": True}],
    [{"name": "TECANRACK1", "x": 1.5}], [{"name": "TECANRACK1", "x": "0"}],
    [{"name": "TECANRACK1", "x": 2147483648}], [{"name": "TECANRACK1", "zHigh": 0}],
    [{"name": "TECANRACK1", "x": 0}, {"name": "TECANRACK1", "y": 0}]])
def test_invalid_patch_never_reaches_robot(positions):
    result = relay_calibration("patch", {**REQUEST, "positions": positions})
    assert result["status"] == 422
    assert result["robot_requests"] == []

@pytest.mark.parametrize("changes,status", [({"error_status": 500}, 500), ({"mutations": False}, 503)])
def test_failures_are_not_save_success(changes, status):
    result = relay_calibration("patch", REQUEST, **changes)
    assert result["status"] == status
    assert "pending_restart" not in result["data"]

def test_read_is_available_without_mutation_permission():
    result = relay_calibration(mutations=False)
    assert result["status"] == 200
    assert result["robot_requests"][0]["method"] == "GET"


def test_settings_query_uses_existing_generation_lease_without_status_gate_or_cache():
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from services.bioxp.connection import BioXpConnectionService
    leases, calls = [], []
    @asynccontextmanager
    async def lease(**kwargs):
        leases.append(kwargs)
        yield object()
    async def request(client, route, **kwargs):
        calls.append(route)
        return FIXTURE["before"]
    owner = SimpleNamespace(active_query_lease=lease, _request_client=request, _v2_query_revision=0)
    async def scenario():
        for _ in range(2):
            assert await BioXpConnectionService.request_active_v2_query(owner, "calibration_settings", expected_generation=77) == FIXTURE["before"]
    asyncio.run(scenario())
    assert leases == [{"expected_generation": 77, "require_fresh": False}] * 2
    assert calls == ["calibration_settings"] * 2


def test_generation_rejected_without_save():
    result = relay_calibration("patch", {**REQUEST, "expected_connection_generation": 78})
    assert result["status"] == 409
    assert result["robot_requests"] == []
