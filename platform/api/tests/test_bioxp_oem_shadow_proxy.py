import pytest

from routers import bioxp


@pytest.mark.asyncio
async def test_oem_shadow_readback_routes_are_expected_and_proxied():
    assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES["/motion/oem/shadow_readback"] is True
    assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES["/motion/oem/shadow_readback/capture"] is True
    assert bioxp.BMS_PROXIED_ROUTES["/motion/oem/shadow_readback"] is True
    assert bioxp.BMS_PROXIED_ROUTES["/motion/oem/shadow_readback/capture"] is True


@pytest.mark.asyncio
async def test_oem_shadow_readback_get_is_thin_readonly_proxy(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "motion_commanded": False, "current_mutation_commanded": False, "switch_mask_mutation_commanded": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.oem_shadow_readback("x,g")

    assert payload["ok"] is True
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["live_homing"] == "blocked"
    assert payload["motion_commanded"] is False
    assert calls == [{"method": "GET", "path": "/motion/oem/shadow_readback", "json_data": None, "params": {"axes": "x,g"}, "timeout": 20.0}]


@pytest.mark.asyncio
async def test_oem_shadow_readback_capture_is_thin_readonly_proxy(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": False, "failed_closed": True, "motion_commanded": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.oem_shadow_readback_capture({"axes": "x,y,z,g,door"})

    assert payload["ok"] is False
    assert payload["failed_closed"] is True
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["live_homing"] == "blocked"
    assert calls == [{"method": "POST", "path": "/motion/oem/shadow_readback/capture", "json_data": {"axes": "x,y,z,g,door"}, "params": None, "timeout": 30.0}]
