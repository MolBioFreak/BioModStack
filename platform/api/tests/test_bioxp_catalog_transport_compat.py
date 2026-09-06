"""Robot F05/F06 provenance additions remain strict and lossless."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import (
    OperatorDashboardXTmclFrame,
    OperatorDashboardXTmclSkippedFrame,
    OperatorDashboardXTmclProvenance,
)
from test_bioxp_operator_controls import _exact_tmcl_provenance


def frame():
    return {
        "classification": "tmcl_response", "arbitration_id": 5, "dlc": 8,
        "data": [5, 100, 6, 0, 0, 0, 123, 0],
        "raw": [126, 0, 5, 8, 5, 100, 6, 0, 0, 0, 123, 0, 239, 126],
        "received_at": 100.3, "receive_sequence": 18,
        "receive_owner": "router-owner", "owner_generation": 2,
    }


def attempt(ordinal=1):
    return {
        "attempt_ordinal": ordinal, "tx_timestamp": 100.1,
        "tx_write_completed_at": 100.2, "wait_signaled": ordinal == 2,
        "outcome": "completion" if ordinal == 2 else "timeout",
        "response_present": ordinal == 2,
        "receive_sequence": 18 if ordinal == 2 else None,
        "response_attempt_attribution": "same_call_ambiguous" if ordinal == 2 else "single_write",
    }


@pytest.mark.parametrize("model", [OperatorDashboardXTmclFrame, OperatorDashboardXTmclSkippedFrame])
def test_frame_receive_owner_fields_survive(model):
    payload = frame()
    result = model.model_validate(payload).model_dump(mode="json", exclude_unset=True)
    assert result == payload


def test_same_call_attempts_survive_without_claiming_response_ownership():
    payload = _exact_tmcl_provenance()
    payload["frames"] = [frame()]
    payload["attempts"] = [attempt(1), attempt(2)]
    result = OperatorDashboardXTmclProvenance.model_validate(payload).model_dump(mode="json")
    assert result["attempts"] == payload["attempts"]
    assert result["frames"] == payload["frames"]


@pytest.mark.parametrize("field,value", [
    ("attempt_ordinal", True), ("attempt_ordinal", 0),
    ("wait_signaled", "true"), ("receive_sequence", "18"),
    ("response_attempt_attribution", "definitely_second_write"),
    ("unknown_authority", True),
])
def test_malformed_attempts_remain_rejected(field, value):
    payload = _exact_tmcl_provenance()
    row = attempt()
    row[field] = value
    payload["attempts"] = [row]
    with pytest.raises(ValidationError):
        OperatorDashboardXTmclProvenance.model_validate(payload)


@pytest.mark.parametrize("field,value", [
    ("receive_sequence", True), ("receive_owner", 17),
    ("owner_generation", "2"), ("physical_effect_verified", True),
])
def test_malformed_frame_authority_remains_rejected(field, value):
    payload = deepcopy(frame())
    payload[field] = value
    with pytest.raises(ValidationError):
        OperatorDashboardXTmclFrame.model_validate(payload)
