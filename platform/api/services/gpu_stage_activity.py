"""
Helpers for reasoning about whether a running job should still count as actively
occupying its assigned GPU.
"""

from __future__ import annotations

from typing import Any


_MATURATION_CHILD_CPU_TAIL_STAGES = {
    "filterbymaturation",
    "scorepartialflowimprovement",
    "prepmaturationredesign",
}


def stage_uses_assigned_gpu(mode: str | None, current_stage: str | None) -> bool:
    mode_token = str(mode or "").strip().lower()
    stage_token = str(current_stage or "").strip().lower()
    if not stage_token:
        return True
    if mode_token == "maturation_child" and stage_token in _MATURATION_CHILD_CPU_TAIL_STAGES:
        return False
    return True


def job_uses_assigned_gpu(job: Any) -> bool:
    return (
        getattr(job, "assigned_gpu", None) is not None
        and stage_uses_assigned_gpu(getattr(job, "mode", None), getattr(job, "current_stage", None))
    )
