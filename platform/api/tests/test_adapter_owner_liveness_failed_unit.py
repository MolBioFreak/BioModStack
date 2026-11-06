from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import gpu_orchestrator as orchestrator
from services.execution_ownership import UnitProperties

JOB_ID = "f894002e-e51d-4e60-a2c2-246691e7672d"
UNIT = f"biomodstack-development-job-{JOB_ID}-attempt-1.service"


def _properties(active_state: str) -> UnitProperties:
    return UnitProperties(
        active_state=active_state, sub_state="dead" if active_state != "active" else "running",
        control_group="", main_pid="0", exec_main_status="137", result="oom-kill",
        slice_name="biomodstack-workflows-development.slice", invocation_id="inv-1",
    )


@pytest.mark.parametrize(
    ("active_state", "cgroup_empty", "expected"),
    [
        ("failed", True, orchestrator._AdapterTransientOwnerLiveness.INACTIVE),
        ("inactive", True, orchestrator._AdapterTransientOwnerLiveness.INACTIVE),
        ("active", False, orchestrator._AdapterTransientOwnerLiveness.ACTIVE),
        ("failed", False, orchestrator._AdapterTransientOwnerLiveness.UNKNOWN),
    ],
)
def test_killed_unit_with_empty_cgroup_is_terminal(monkeypatch, active_state, cgroup_empty, expected) -> None:
    receipt = {"lane": "development", "unit": UNIT, "invocation_id": "inv-1"}
    monkeypatch.setattr(orchestrator, "latest_started_execution_attempt", lambda _params: receipt)
    monkeypatch.setattr(orchestrator, "show_unit_properties", lambda _unit, _lane: _properties(active_state))
    monkeypatch.setattr(orchestrator, "unit_has_empty_cgroup", lambda _props: cgroup_empty)
    job = SimpleNamespace(id=JOB_ID, params={})
    assert asyncio.run(orchestrator._adapter_transient_owner_liveness(job, lane="development")) is expected
