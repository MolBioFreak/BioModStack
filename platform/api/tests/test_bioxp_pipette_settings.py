from bioxp_pipette_settings_route_bridge import relay_pipette
import pytest


def test_manual_tip_set_fixed_path_and_readback():
    before = relay_pipette(resource="calibration")
    result = relay_pipette("post", "set", {"expected_connection_generation": 77, "tray": 4})
    after = relay_pipette(resource="calibration", saved=True)
    assert result["status"] == 200
    assert result["robot_requests"] == [{"method": "POST", "path": "/motion/oem/calibration_settings/manual_tip_set", "body": {"tray": 4}}]
    assert result["data"]["measured_z"] == 0
    assert after["robot_requests"][0]["path"] == "/motion/oem/calibration_settings"
    assert after["data"]["active_positions"] == before["data"]["active_positions"]
    assert after["data"]["pending_restart"] is True


@pytest.mark.parametrize("tray", [0, 5, True, "1", None])
def test_manual_tip_set_rejects_wrong_tray_before_transport(tray):
    result = relay_pipette("post", "set", {"expected_connection_generation": 77, "tray": tray})
    assert result["status"] == 422
    assert result["robot_requests"] == []


def test_flags_relay_preserves_explicit_false_and_unchanged_fields():
    before = relay_pipette()
    saved = relay_pipette("patch", request={"expected_connection_generation": 77, "LogPressure": False, "CheckForStaticTipLoss": True})
    after = relay_pipette(saved=True)
    assert before["robot_requests"][0]["path"] == "/liquid/oem/operation_parameters"
    assert saved["status"] == 200
    assert saved["robot_requests"] == [{"method": "PATCH", "path": "/liquid/oem/operation_parameters",
        "body": {"LogPressure": False, "CheckForStaticTipLoss": True}}]
    assert after["data"]["operation_parameters"]["LogPressure"] is False
    assert after["data"]["operation_parameters"]["CheckSnapTips"] is True


@pytest.mark.parametrize("change", [{}, {"LogPressure": None}, {"LogPressure": 0}, {"LogPressure": "false"}, {"Mode": "Other"}])
def test_flags_reject_untyped_or_unrelated_changes(change):
    result = relay_pipette("patch", request={"expected_connection_generation": 77, **change})
    assert result["status"] == 422
    assert result["robot_requests"] == []


def test_generation_and_robot_failure_are_not_success():
    assert relay_pipette("post", "set", {"expected_connection_generation": 78, "tray": 1})["robot_requests"] == []
    assert relay_pipette("patch", request={"expected_connection_generation": 77, "LogPressure": False}, error_status=500)["status"] == 500
