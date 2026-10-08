from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers.queue import _resolve_display_gpu_ids


def _job(*, params=None, pinned_gpu=None, assigned_gpu=None, mode="design", current_stage="run_boltz"):
    return SimpleNamespace(
        params=params or {},
        pinned_gpu=pinned_gpu,
        assigned_gpu=assigned_gpu,
        mode=mode,
        current_stage=current_stage,
    )


def test_resolve_display_gpu_ids_prefers_boltz_cp_multi_gpu_launch_ids() -> None:
    job = _job(
        params={
            "bcp_gpu_ids": "0,1,2,3",
            "pinned_gpus": [0, 1, 2, 3],
        },
        pinned_gpu=None,
        assigned_gpu=0,
    )

    assert _resolve_display_gpu_ids(job) == [0, 1, 2, 3]


def test_resolve_display_gpu_ids_falls_back_to_explicit_pinned_gpu_list() -> None:
    job = _job(
        params={
            "pinned_gpus": [2, "3", 3, "bad"],
        },
        pinned_gpu=None,
        assigned_gpu=None,
    )

    assert _resolve_display_gpu_ids(job) == [2, 3]


def test_resolve_display_gpu_ids_uses_anchor_gpu_only_when_no_multi_gpu_config_exists() -> None:
    job = _job(params={}, pinned_gpu=2, assigned_gpu=2)

    assert _resolve_display_gpu_ids(job) == [2]


def test_resolve_display_gpu_ids_skips_non_gpu_tail_stage_anchor_assignments() -> None:
    job = _job(
        params={},
        pinned_gpu=None,
        assigned_gpu=2,
        mode="maturation_child",
        current_stage="filterbymaturation",
    )

    assert _resolve_display_gpu_ids(job) is None
