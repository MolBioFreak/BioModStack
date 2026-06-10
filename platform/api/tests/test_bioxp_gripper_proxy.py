import pytest

from routers import bioxp


@pytest.mark.asyncio
async def test_gripper_routes_are_expected_and_proxied():
    for path in (
        "/motion/gripper/status",
        "/motion/gripper/restore_idle_current",
        "/motion/gripper/clear",
        "/motion/gripper/home",
    ):
        assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES[path] is True
        assert bioxp.BMS_PROXIED_ROUTES[path] is True


@pytest.mark.asyncio
async def test_gripper_status_is_thin_proxy(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "physical_motion": False, "blockers": ["both_effective_limits_active"]}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.gripper_status()

    assert payload["ok"] is True
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["physical_motion"] is False
    assert calls == [{"method": "GET", "path": "/motion/gripper/status", "json_data": None, "params": None, "timeout": 20.0}]


@pytest.mark.asyncio
async def test_gripper_clear_preserves_robot_payload_and_uses_headroom(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": False, "error": "gripper_switch_conflict", "motion_commanded": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.gripper_clear({"operator_ack": "GRIPPER_CLEAR", "reason": "commissioning", "timeout_s": 15})

    assert payload["ok"] is False
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["motion_commanded"] is False
    assert calls == [{"method": "POST", "path": "/motion/gripper/clear", "json_data": {"operator_ack": "GRIPPER_CLEAR", "reason": "commissioning", "timeout_s": 15}, "params": None, "timeout": 35.0}]


@pytest.mark.asyncio
async def test_gripper_home_and_restore_idle_are_thin_proxy(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "motion_commanded": path.endswith("/home")}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    restore = await bioxp.gripper_restore_idle_current({"reason": "safe idle"})
    home = await bioxp.gripper_home({"operator_ack": "GRIPPER_HOME", "reason": "commissioning", "timeout_s": 20})

    assert restore["bms_role"] == "thin_proxy_only"
    assert home["bms_role"] == "thin_proxy_only"
    assert calls == [
        {"method": "POST", "path": "/motion/gripper/restore_idle_current", "json_data": {"reason": "safe idle"}, "params": None, "timeout": 20.0},
        {"method": "POST", "path": "/motion/gripper/home", "json_data": {"operator_ack": "GRIPPER_HOME", "reason": "commissioning", "timeout_s": 20}, "params": None, "timeout": 40.0},
    ]
