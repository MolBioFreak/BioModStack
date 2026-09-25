"""Native manual documents through the closed BMS envelope, offline only."""
import pytest
from pydantic import ValidationError

from bioxp_manual_route_bridge import relay_manual_request
from services.bioxp.protocol_models import ProtocolJob, ProtocolSubmission


@pytest.mark.parametrize("kind,params", [
    ("pipette_position", {"operation": "move", "location_id": 4, "well": "C3", "position_flag": 1}),
    ("pipette_position", {"operation": "lower", "location_id": 4}),
    ("pipette_position", {"operation": "lift", "location_id": 4, "height_steps": None}),
    ("pipette_position", {"operation": "lift", "location_id": 2, "height_steps": 0}),
    ("pipette_aspirate", {"channels": [1], "volume_ul": 12.5, "speed": 80}),
    ("pipette_dispense", {"channels": [0, 1, 2, 3], "volume_ul": 12.5, "speed": 65}),
])
@pytest.mark.parametrize("status", ["dispatched", "completed", "failed"])
def test_native_manual_actions_are_lossless_through_strict_consumers(kind, params, status):
    document = {"protocol_id": "bms-manual-pipetting", "version": 1,
        "stages": [{"stage_id": "manual", "actions": [{"action_id": "manual-0-0", "stage_id": "manual",
            "kind": kind, "params": params, "metadata": {"manual_step": 0}}]}],
        "metadata": {"manual_scope": "explicit_steps_only", "well_alignment": "source_machine_tip_location"}}
    request = {"expected_connection_generation": 77, "source_type": "native", "document": document,
        "dry_run": False, "idempotency_key": "manual-fixture-key", "live_execution": {"live_execution_ack": True}}
    parsed = ProtocolSubmission.model_validate(request)
    assert parsed.model_dump(mode="json", exclude_unset=True) == request
    result = relay_manual_request(request, status)
    assert result["status"] == (202 if status == "dispatched" else 200)
    job = ProtocolJob.model_validate(result["data"])
    assert job.protocol.document == document
    assert job.command.status == status
    assert job.execution.runtime_state.action_results[0]["physical_effect_verified"] is False
    assert result["robot_requests"][0]["body"]["document"] == document
    # The native document belongs to the robot; do not add a BMS kind enum or
    # normalize params. The outer execution contract remains closed and strict.
    with pytest.raises(ValidationError):
        ProtocolSubmission.model_validate({**request, "tip_location": 1})
    with pytest.raises(ValidationError):
        ProtocolSubmission.model_validate({**request, "dry_run": "false"})
