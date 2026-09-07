"""Shared lifecycle boundaries exercised without science, network or reservations."""
import hashlib

import pytest

from component_runtime import ComponentBoundary, GroupingLedger, ResultReference
from test_component_runtime import plan


@pytest.fixture
def boundary(tmp_path):
    return ComponentBoundary(GroupingLedger(tmp_path / "ledger.sqlite", attempt_id="attempt", plan=plan()))


def test_submit_is_called_once_on_ambiguous_transport_failure(boundary):
    calls = []
    def submit():
        calls.append("possibly-started")
        raise OSError("connection lost after send")
    with pytest.raises(OSError, match="after send"):
        boundary.submit(submit)
    assert calls == ["possibly-started"]


@pytest.mark.parametrize("phase", ["before", "during"])
def test_submission_cancellation_does_not_claim_quiescence(boundary, phase):
    calls = []
    def submit():
        calls.append("submitted")
        boundary.ledger.request_cancel()
    if phase == "before":
        boundary.ledger.request_cancel()
    with pytest.raises(RuntimeError, match="quiescence is not yet established"):
        boundary.submit(submit)
    assert calls == ([] if phase == "before" else ["submitted"])


def test_wait_observes_until_adapter_validates_completion(boundary):
    observations = iter([{"state": "running"}, {"state": "completed"}])
    sleeps = []
    assert boundary.wait(lambda: next(observations), lambda row: row["state"] == "completed",
                         poll_interval=2, sleep=sleeps.append) == {"state": "completed"}
    assert sleeps == [2]
    # Neither observation nor submit creates a scientific result or resource claim.
    with pytest.raises(RuntimeError, match="incomplete"):
        boundary.ledger.join()


def test_wait_cancellation_during_observation_cannot_become_success(boundary):
    def observe():
        boundary.ledger.request_cancel()
        return True
    with pytest.raises(RuntimeError, match="quiescence"):
        boundary.wait(observe, bool, poll_interval=0)


def test_wait_timeout_is_bounded_without_resubmission(boundary):
    now = [0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    with pytest.raises(RuntimeError, match="timed out"):
        boundary.wait(lambda: False, bool, poll_interval=10, timeout=3,
                      clock=lambda: now[0], sleep=sleep)
    assert sleeps == [3]


def test_result_validation_precedes_sealing_and_replay_rechecks_bytes(boundary, tmp_path):
    refs = []
    for ordinal in range(len(boundary.ledger.plan.groups)):
        payload = str(ordinal).encode()
        path = tmp_path / f"{ordinal}.json"
        path.write_bytes(b"corrupt")
        reference = ResultReference(boundary.ledger.plan.component_id(ordinal), path.name,
                                    hashlib.sha256(payload).hexdigest(), len(payload), "native")
        with pytest.raises(ValueError, match="byte binding"):
            boundary.result(reference, tmp_path)
        with pytest.raises(RuntimeError, match="incomplete"):
            boundary.join(tmp_path)
        path.write_bytes(payload)
        assert boundary.result(reference, tmp_path) == path
        refs.append(path)
    assert boundary.join(tmp_path) == tuple(refs)
    refs[0].write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="byte binding"):
        boundary.join(tmp_path)
