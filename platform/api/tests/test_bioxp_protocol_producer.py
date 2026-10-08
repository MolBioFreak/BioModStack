"""Validate BMS consumers against exported real robot host-path results.

The producer runs with explicit native doubles; this is wire/host acceptance only.
"""
import json
import os
from pathlib import Path

import pytest
from services.bioxp.protocol_models import ProtocolJob, ProtocolSubmission


@pytest.fixture(scope="module")
def producer():
    source = os.environ.get("BIOXP_WORKFLOW_EXPORT_SOURCE")
    if not source:
        pytest.skip("requires the connected robot producer export")
    payload = json.loads(Path(source).read_text())
    assert payload["fixture_only"] is True and payload["physical_acceptance"] is False
    return payload


@pytest.mark.parametrize("phase", ["accepted", "running", "completed", "fresh_process"])
def test_real_robot_workflow_payload_is_consumed_without_relabeling(producer, phase):
    original = producer[phase]
    parsed = ProtocolJob.model_validate(original).model_dump(mode="json")
    assert parsed["command"] == original["command"]
    assert parsed["job_id"] == original["job_id"]
    assert parsed["execution"]["runtime_state"]["workflow"] == original["execution"]["runtime_state"]["workflow"]
    assert parsed["execution"]["runtime_state"]["completed"] == original["execution"]["runtime_state"]["completed"]
    assert parsed["execution"]["runtime_state"]["action_results"] == original["execution"]["runtime_state"]["action_results"]
    assert parsed["execution"]["runtime_state"]["source_model"] == original["execution"]["runtime_state"]["source_model"]


def test_real_robot_submission_retains_selected_document_and_original_key(producer):
    request = {**producer["request"], "expected_connection_generation": 17}
    parsed = ProtocolSubmission.model_validate(request).model_dump(mode="json", exclude_none=True)
    assert parsed["document"] == request["document"]
    assert parsed["idempotency_key"] == request["idempotency_key"]
    assert parsed["live_execution"] == request["live_execution"]


@pytest.mark.parametrize("outcome", ["delay-gate", "delay-completed", "aborted", "stopped"])
def test_real_robot_control_outcome_retains_phase_and_native_custody(outcome):
    source = os.environ.get("BIOXP_WORKFLOW_CONTROL_EXPORT_SOURCE")
    if not source:
        pytest.skip("requires connected robot control exports")
    payload = json.loads(Path(source + '.' + outcome + '.json').read_text())
    assert payload["fixture_only"] is True
    original = payload["job"]
    parsed = ProtocolJob.model_validate(original).model_dump(mode="json")
    assert parsed["command"] == original["command"]
    assert parsed["execution"]["runtime_state"]["workflow"] == original["execution"]["runtime_state"]["workflow"]
    assert parsed["execution"]["runtime_state"]["action_results"] == original["execution"]["runtime_state"]["action_results"]
