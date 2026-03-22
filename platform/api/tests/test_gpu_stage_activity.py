from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.gpu_stage_activity import job_uses_assigned_gpu, stage_uses_assigned_gpu


def test_maturation_child_cpu_tail_stage_does_not_count_as_gpu_active() -> None:
    assert stage_uses_assigned_gpu("maturation_child", "filterbymaturation") is False
    assert stage_uses_assigned_gpu("maturation_child", "scorepartialflowimprovement") is False
    assert stage_uses_assigned_gpu("maturation_child", "prepmaturationredesign") is False


def test_maturation_child_gpu_stage_still_counts_as_gpu_active() -> None:
    assert stage_uses_assigned_gpu("maturation_child", "runpartialflow") is True
    assert stage_uses_assigned_gpu("maturation_child", "anarcii") is True


def test_job_without_assigned_gpu_is_never_gpu_active() -> None:
    job = SimpleNamespace(mode="maturation_child", current_stage="runpartialflow", assigned_gpu=None)
    assert job_uses_assigned_gpu(job) is False
