import pytest

from routers import bioxp


@pytest.mark.asyncio
async def test_oem_shadow_readback_routes_are_expected_and_proxied():
    assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES["/motion/oem/machine_config"] is True
    assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES["/motion/oem/position_table"] is True
    assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES["/motion/oem/position_table/plan"] is True
    assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES["/motion/oem/shadow_readback"] is True
    assert bioxp.ROBOT_LOCAL_EXPECTED_ROUTES["/motion/oem/shadow_readback/capture"] is True
    assert bioxp.BMS_PROXIED_ROUTES["/motion/oem/machine_config"] is True
    assert bioxp.BMS_PROXIED_ROUTES["/motion/oem/position_table"] is True
    assert bioxp.BMS_PROXIED_ROUTES["/motion/oem/position_table/plan"] is True
    assert bioxp.BMS_PROXIED_ROUTES["/motion/oem/shadow_readback"] is True
    assert bioxp.BMS_PROXIED_ROUTES["/motion/oem/shadow_readback/capture"] is True


@pytest.mark.asyncio
async def test_oem_machine_config_is_thin_readonly_proxy(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "machine_calibrated": True, "opened_usb": False, "physical_motion": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.oem_machine_config()

    assert payload["ok"] is True
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["live_homing"] == "blocked"
    assert payload["usb_motion"] == "no"
    assert payload["opened_usb"] is False
    assert payload["physical_motion"] is False
    assert calls == [{"method": "GET", "path": "/motion/oem/machine_config", "json_data": None, "params": None, "timeout": 12.0}]


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



@pytest.mark.asyncio
async def test_oem_position_table_is_thin_readonly_proxy(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "position_table_count": 29, "opened_usb": False, "physical_motion": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.oem_position_table()

    assert payload["ok"] is True
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["live_homing"] == "blocked"
    assert payload["usb_motion"] == "no"
    assert calls == [{"method": "GET", "path": "/motion/oem/position_table", "json_data": None, "params": None, "timeout": 12.0}]


@pytest.mark.asyncio
async def test_oem_position_table_plan_is_thin_readonly_proxy(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "planned_coordinates": {"x": 1, "y": 2, "z": 3}, "opened_usb": False, "physical_motion": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.oem_position_table_plan("LOC_MS", column=2, row=3, high_pos=False, mode="scriptmoveTo", positionflag=2, tip_location=1)

    assert payload["ok"] is True
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["live_homing"] == "blocked"
    assert payload["usb_motion"] == "no"
    assert calls == [{
        "method": "GET",
        "path": "/motion/oem/position_table/plan",
        "json_data": None,
        "params": {"location_id": "LOC_MS", "column": 2, "row": 3, "high_pos": False, "mode": "scriptmoveTo", "positionflag": 2, "tip_location": 1, "offset_x": 0, "offset_y": 0},
        "timeout": 12.0,
    }]



@pytest.mark.asyncio
async def test_oem_pathing_default_parameters_proxy_is_readonly(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "pseudo_z_home": 500, "motion_commanded": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.oem_pathing_default_parameters(force_high_home=True, tiploaded="TIP")

    assert payload["pseudo_z_home"] == 500
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["usb_motion"] == "no"
    assert calls == [{"method": "GET", "path": "/motion/oem/pathing/default_parameters", "json_data": None, "params": {"force_high_home": True, "tiploaded": "TIP"}, "timeout": 12.0}]


@pytest.mark.asyncio
async def test_oem_pathing_scriptmove_plan_proxy_is_readonly(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "branch": "gripper_confirmed_no_tip_direct_moveTo", "motion_commanded": False, "physical_motion": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.oem_pathing_scriptmove_plan("LOC_MS", current_loc="LOC_OC", column=2, row=3, current_x=1, current_y=2, current_z=3, gripper_confirmed=True)

    assert payload["branch"] == "gripper_confirmed_no_tip_direct_moveTo"
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["usb_motion"] == "no"
    assert calls == [{
        "method": "GET",
        "path": "/motion/oem/pathing/scriptmove_plan",
        "json_data": None,
        "params": {"location_id": "LOC_MS", "column": 2, "row": 3, "positionflag": 0, "current_x": 1, "current_y": 2, "current_z": 3, "tip_loaded": False, "tip_dirty": False, "tip_location": -1, "clean_path": False, "device_type": "", "gripper_confirmed": True, "run_in_parallel": True, "current_loc": "LOC_OC"},
        "timeout": 12.0,
    }]
