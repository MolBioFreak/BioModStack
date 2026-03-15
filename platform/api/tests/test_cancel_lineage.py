from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.job_control import _job_is_cancelable, _lineage_has_cancelable_jobs, _sort_jobs_for_cancellation


@dataclass
class DummyJob:
    id: str
    status: str
    queue_status: str
    created_at: datetime
    awaiting_input: bool = False
    nextflow_run_id: str | None = None
    completed_at: datetime | None = None


def test_lineage_is_cancelable_when_root_is_cancelled_but_child_is_running() -> None:
    base = datetime(2026, 3, 15, 9, 0, 0)
    root = DummyJob(
        id="root",
        status="cancelled",
        queue_status="cancelled",
        created_at=base,
        completed_at=base + timedelta(minutes=1),
    )
    child = DummyJob(
        id="child",
        status="running",
        queue_status="running",
        created_at=base + timedelta(seconds=5),
        nextflow_run_id="12345",
    )

    assert not _job_is_cancelable(root)
    assert _job_is_cancelable(child)
    assert _lineage_has_cancelable_jobs([root, child])


def test_terminal_completed_job_is_not_cancelable_without_live_process() -> None:
    job = DummyJob(
        id="done",
        status="completed",
        queue_status="completed",
        created_at=datetime(2026, 3, 15, 9, 0, 0),
        completed_at=datetime(2026, 3, 15, 9, 30, 0),
    )

    assert not _job_is_cancelable(job)
    assert not _lineage_has_cancelable_jobs([job])


def test_sort_jobs_for_cancellation_orders_descendants_before_parent() -> None:
    base = datetime(2026, 3, 15, 9, 0, 0)
    parent = DummyJob(
        id="parent",
        status="running",
        queue_status="running",
        created_at=base,
    )
    child = DummyJob(
        id="child",
        status="running",
        queue_status="running",
        created_at=base + timedelta(seconds=1),
    )
    grandchild = DummyJob(
        id="grandchild",
        status="running",
        queue_status="running",
        created_at=base + timedelta(seconds=2),
    )

    ordered = _sort_jobs_for_cancellation(
        [parent, child, grandchild],
        {"parent": 0, "child": 1, "grandchild": 2},
    )

    assert [job.id for job in ordered] == ["grandchild", "child", "parent"]
