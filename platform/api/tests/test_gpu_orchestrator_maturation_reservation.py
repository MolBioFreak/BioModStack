from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.gpu_orchestrator import (  # noqa: E402
    GPUState,
    JobInfo,
    _pending_job_reservation_mb,
    pack_jobs_to_gpus,
)


def _maturation_job(idx: int) -> JobInfo:
    return JobInfo(
        id=f"job-{idx}",
        name=f"PPIFlow {idx}",
        model_type="maturation_child",
        vram_estimate_mb=18725,
        sequence_length=300,
        priority=0,
        pinned_gpu=None,
        pinned_gpus=[0],
        created_at=datetime(2026, 1, 1, 0, 0, idx),
        batch_id="batch-1",
        scheduler_reservation_mb=None,
    )


def test_maturation_child_reserves_formulaic_fraction_not_startup_sliver() -> None:
    job = _maturation_job(1)

    assert _pending_job_reservation_mb(job, {}) == 14044


def test_maturation_child_pack_does_not_fit_four_x8_children_on_one_5090() -> None:
    jobs = [_maturation_job(i) for i in range(1, 5)]
    jobs = [
        job.__class__(
            **{
                **job.__dict__,
                "scheduler_reservation_mb": _pending_job_reservation_mb(job, {}),
            }
        )
        for job in jobs
    ]
    gpu0 = GPUState(
        index=0,
        name="RTX 5090",
        memory_used_mb=513,
        memory_total_mb=32607,
        memory_free_mb=32094,
        utilization=0,
        temperature=40,
    )
    config = {
        "global": {
            "busy_threshold": 1.0,
            "cooldown_ms": 3000,
            "target_vram_fill": 0.9,
            "capacity_weight": 9.0,
            "emptiness_weight": 0.5,
        },
        "overrides": {
            "0": {
                "force_available": False,
                "quick_enable": False,
                "threshold": 0.9107246910172663,
                "disabled": False,
                "priority_tier": 8,
                "vram_safety_margin_mb": 0,
                "max_concurrent_jobs": 4,
            }
        },
    }

    assignments = pack_jobs_to_gpus(
        jobs,
        [gpu0],
        target_fill=0.9,
        config=config,
        running_jobs_per_gpu={},
        gpu_last_launch_at={},
    )

    assert len(assignments) == 2
    assert [gpu for _job, gpu in assignments] == [0, 0]
