"""A workflow that stopped at an opened review gate finished normally; it is not a runner failure."""
from types import SimpleNamespace

from workflow_job_runner import _terminal_state


def test_open_review_gate_counts_as_completed_even_while_status_is_running():
    job = SimpleNamespace(status="running", awaiting_input=True, params={}, error_message=None)
    assert _terminal_state(job)[0] == "completed"


def test_running_job_without_a_gate_is_still_interrupted():
    job = SimpleNamespace(status="running", awaiting_input=False, params={}, error_message=None)
    assert _terminal_state(job)[0] == "interrupted_owner"
