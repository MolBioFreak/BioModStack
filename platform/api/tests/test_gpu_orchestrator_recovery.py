from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


API_ROOT = Path(__file__).resolve().parents[1]

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job
from services.gpu_orchestrator import (
    _commit_reconciled_job_mutations,
    _has_terminal_nextflow_history,
    _recover_rfantibody_parent_after_child_wait,
    _reconcile_terminal_history_without_process,
)


def test_recover_rfantibody_parent_after_child_wait_opens_post_rf_gate(tmp_path: Path) -> None:
    for mode in ("antibody_denovo_pipeline", "antibody_refinement_pipeline"):
        parent_output = tmp_path / mode / "parent"
        child_output = tmp_path / mode / "child"
        child_rfa_dir = child_output / "run" / "rfantibody" / "output"
        child_rfa_dir.mkdir(parents=True)
        (child_rfa_dir / "001_backbone.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
        (child_rfa_dir / "001_backbone.trb").write_text("{}", encoding="utf-8")

        job = SimpleNamespace(
            mode=mode,
            output_dir=str(parent_output),
            params={
                "interactive_gating": True,
                "interactive_gate_stage": "post_rfantibody",
                "framework_type": "custom",
                "antibody_chains": "H",
                "structure_validator": "boltz2",
            },
            completed_stages=[],
            stage_outputs={},
            awaiting_input=False,
            awaiting_stage=None,
            awaiting_payload={},
            status="running",
            queue_status="running",
            current_stage="waitforchildren",
            stage_progress="1/4",
            error_message="stale launcher",
            completed_at=None,
            assigned_gpu=0,
        )

        recovered = _recover_rfantibody_parent_after_child_wait(
            job,
            {
                "output_dirs": [str(child_output)],
                "completed": 1,
                "total": 1,
            },
        )

        assert recovered is not None
        assert recovered["opened_gate"] is True
        raw_dir = parent_output / "collected" / "rfantibody_raw"
        assert (raw_dir / "job0_001_backbone.pdb").exists()
        assert (raw_dir / "job0_001_backbone.trb").exists()

        manifest = json.loads((parent_output / "collected" / "rfantibody" / "collection_manifest.json").read_text())
        assert manifest["count"] == 1
        assert manifest["recovered_after_child_wait"] is True

        assert job.completed_stages == ["rfantibody"]
        assert job.stage_outputs["rfantibody"] == [str(raw_dir)]
        assert job.awaiting_input is True
        assert job.awaiting_stage == "post_rfantibody"
        assert job.status == "awaiting_input"
        assert job.queue_status == "completed"
        assert job.current_stage == "post_rfantibody"
        assert job.awaiting_payload["candidate_count"] == 1
        assert Path(parent_output / "gates" / "gate_post_rfantibody.json").exists()


def test_reconcile_terminal_history_without_process_marks_err_history_as_failed() -> None:
    job = SimpleNamespace(
        name="CP resume run",
        status="running",
        queue_status="running",
        error_message=(
            "Reconciled as failed: no active process and no terminal .nextflow/history status "
            "(expected OK/ERR)"
        ),
        completed_at=None,
        awaiting_input=False,
        awaiting_stage=None,
        current_stage="boltz2",
        stage_progress="1/1",
        nextflow_run_id="3153244",
        stage_work_dir="/tmp/workdir",
        output_dir="/tmp/output",
    )

    reconciled = _reconcile_terminal_history_without_process(
        job,
        history_status="ERR",
        gate_present=False,
        age_seconds=301,
        stale_fail_after_seconds=300,
    )

    assert reconciled is True
    assert job.status == "failed"
    assert job.queue_status == "failed"
    assert job.error_message == "Reconciled as failed: terminal .nextflow/history status ERR"
    assert job.completed_at is not None


def test_terminal_nextflow_history_overrides_gpu_activity_liveness_hint() -> None:
    assert _has_terminal_nextflow_history(("OK", "36.6s")) is True
    assert _has_terminal_nextflow_history(("ERR", "4.2s")) is True
    assert _has_terminal_nextflow_history(None) is False


@pytest.mark.asyncio
async def test_worker_recovery_publish_does_not_overwrite_concurrent_cancellation(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as seed:
        seed.add(Job(
            id="recovery-race", name="recovery-race", model_id="boltz2", mode="predict",
            params={}, status="running", queue_status="running", awaiting_input=False,
            awaiting_payload={}, retry_count=0, max_retries=2,
        ))
        await seed.commit()

    async with factory() as worker:
        stale_job = (await worker.execute(select(Job).where(Job.id == "recovery-race"))).scalar_one()
        _reconcile_terminal_history_without_process(
            stale_job, history_status="ERR", gate_present=False,
            age_seconds=301, stale_fail_after_seconds=300,
        )
        async with factory() as cancellation:
            await cancellation.execute(
                update(Job).where(Job.id == "recovery-race").values(
                    status="cancelled", queue_status="cancelled", awaiting_input=False,
                )
            )
            await cancellation.commit()

        assert await _commit_reconciled_job_mutations(worker) == 0

    async with factory() as verify:
        final = (await verify.execute(select(Job).where(Job.id == "recovery-race"))).scalar_one()
        assert (final.status, final.queue_status) == ("cancelled", "cancelled")
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operator_values", "expected"),
    [
        ({"status": "cancelled", "queue_status": "cancelled", "awaiting_input": False}, ("cancelled", "cancelled", False)),
        ({"status": "awaiting_input", "queue_status": "completed", "awaiting_input": True}, ("awaiting_input", "completed", True)),
    ],
)
async def test_worker_loop_boundary_publishes_recovery_before_next_job_db_work(
    tmp_path: Path, operator_values: dict[str, object], expected: tuple[str, str, bool]
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'loop-boundary.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as seed:
        for job_id in ("stale-a", "next-b"):
            seed.add(Job(
                id=job_id, name=job_id, model_id="boltz2", mode="predict", params={},
                status="running", queue_status="running", awaiting_input=False,
                awaiting_payload={}, retry_count=0, max_retries=2,
            ))
        await seed.commit()

    async with factory() as worker:
        stale = (await worker.execute(select(Job).where(Job.id == "stale-a"))).scalar_one()
        _reconcile_terminal_history_without_process(
            stale, history_status="ERR", gate_present=False,
            age_seconds=301, stale_fail_after_seconds=300,
        )
        async with factory() as operator:
            await operator.execute(update(Job).where(Job.id == "stale-a").values(**operator_values))
            await operator.commit()

        # This is the completion loop's next-job boundary.  A subsequent finalizer
        # starts with DB work (refresh/select); it must not autoflush stale-a first.
        assert await _commit_reconciled_job_mutations(worker) == 0
        assert (await worker.execute(select(Job).where(Job.id == "next-b"))).scalar_one().id == "next-b"

    async with factory() as verify:
        final = (await verify.execute(select(Job).where(Job.id == "stale-a"))).scalar_one()
        assert (final.status, final.queue_status, final.awaiting_input) == expected
    await engine.dispose()
