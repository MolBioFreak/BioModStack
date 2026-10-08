import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_bioxp_router_module():
    repo_api = Path(__file__).resolve().parents[1]
    paths_module = types.ModuleType("paths")
    paths_module.get_data_root = lambda: Path("/tmp/biomodstack-test-data")
    services_module = types.ModuleType("services")
    services_module.bioxp_interlink = types.SimpleNamespace(
        get_settings=lambda: None,
        save_settings=lambda settings: None,
        get_state=lambda: {},
        lifecycle_action=lambda *args, **kwargs: {},
        probe_runtime=lambda *args, **kwargs: {},
    )
    sys.modules.setdefault("paths", paths_module)
    sys.modules.setdefault("services", services_module)
    sys.modules.setdefault("services.bioxp_interlink", services_module.bioxp_interlink)
    spec = importlib.util.spec_from_file_location("bioxp_router_under_test", repo_api / "routers" / "bioxp.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


bioxp = _load_bioxp_router_module()


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
async def test_gripper_status_harmonizes_gap10_with_oem_home_semantics(monkeypatch):
    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        return {
            "ok": True,
            "axis": "g",
            "position": {"position": 0, "ok": True},
            "speed": {"speed": 0, "ok": True},
            "switches": {
                "left_state": 1,
                "right_state": 1,
                "left_active": True,
                "right_active": True,
                "left_disabled": False,
                "right_disabled": False,
                "both_effective_limits_active": True,
            },
            "current": {"run_current_param6": 10, "standby_current_param7": 10, "idle_safe": True},
            "oem_home_predicate": {"query_home_active": True, "position_lt_50": True, "oem_confirmed_home": True},
            "blockers": [],
            "physical_motion": False,
        }

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.gripper_status()

    interpretation = payload["bms_oem_interpretation"]
    assert interpretation["oem_home_confirmed"] is True
    assert interpretation["oem_confirmation_rule"] == "queryHome(MotorGrip) OR getG()<50"
    assert interpretation["gap10_role"] == "unresolved_raw_asserted_not_physical_limit_proof"
    assert interpretation["physical_double_limit_proven"] is False
    assert interpretation["motion_test_state"] == "ready_for_controlled_clear_with_gap10_diagnostic"
    assert any("no longer hard-blocks" in note for note in interpretation["notes"])
    assert any("GAP10/right raw asserted while OEM home is confirmed" in note for note in interpretation["notes"])


@pytest.mark.asyncio
async def test_gripper_clear_adds_readiness_defaults_and_uses_headroom(monkeypatch):
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": False, "error": "gripper_switch_conflict", "motion_commanded": False}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.gripper_clear({"operator_ack": "GRIPPER_CLEAR", "reason": "commissioning", "timeout_s": 15})

    assert payload["ok"] is False
    assert payload["bms_role"] == "thin_proxy_only"
    assert payload["motion_commanded"] is False
    assert payload["bms_oem_interpretation"]["action_test_readiness"] == "robot_blocked_or_failed_before_proof"
    assert calls == [{"method": "POST", "path": "/motion/gripper/clear", "json_data": {"operator_ack": "GRIPPER_CLEAR", "reason": "commissioning", "timeout_s": 15, "capture_bundle": True, "require_motion_evidence": True, "restore_idle_current": True}, "params": None, "timeout": 35.0}]


@pytest.mark.asyncio
async def test_gripper_clear_ack_without_motion_is_not_clear_proof(monkeypatch):
    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        return {"ok": True, "ack": {"ok": True}, "seen_nonzero": False, "ambiguous_no_motion": True, "motion_commanded": True}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)
    payload = await bioxp.gripper_clear({"operator_ack": "GRIPPER_CLEAR", "reason": "commissioning"})

    assert payload["ok"] is True
    interpretation = payload["bms_oem_interpretation"]
    assert interpretation["action_test_readiness"] == "ambiguous_no_motion_not_clear_proof"
    assert interpretation["physical_clear_proven"] is False
    assert "ACK-only gripper clear is not physical proof" in interpretation["notes"]


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
        {"method": "POST", "path": "/motion/gripper/home", "json_data": {"operator_ack": "GRIPPER_HOME", "reason": "commissioning", "timeout_s": 20, "capture_bundle": True, "require_motion_evidence": True, "restore_idle_current": True}, "params": None, "timeout": 40.0},
    ]


@pytest.mark.asyncio
async def test_generic_gripper_axis_motion_routes_reject_g_axis(monkeypatch):
    """G is not in MOTION_GUARDED_AXES so generic routes reject it with 400, not a special handler."""
    calls = []

    async def fake_proxy(method, path, json_data=None, params=None, timeout=65.0):
        calls.append({"method": method, "path": path, "json_data": json_data, "params": params, "timeout": timeout})
        return {"ok": True, "path": path}

    class RequestStub:
        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy)

    route_calls = [
        (bioxp.move_axis_relative, {"axis": "g", "steps": 30}),
        (bioxp.move_axis_absolute, {"axis": "g", "position_steps": 0}),
        (bioxp.move_axis_zero, {"axis": "g"}),
        (bioxp.home_axis, {"axis": "g", "capture_bundle": True, "operator_note": "blocked generic G home"}),
        (bioxp.bioxp_operation_micro_move_proof, {"operator_ack": True, "axis": "g", "steps": 30}),
    ]

    for route, payload in route_calls:
        with pytest.raises(Exception) as exc_info:
            await route(RequestStub(payload))
        # g is not a guarded axis → 400, not 409
        assert getattr(exc_info.value, "status_code", None) == 400
        assert "axis must be one of" in str(getattr(exc_info.value, "detail", exc_info.value))

    assert calls == []
