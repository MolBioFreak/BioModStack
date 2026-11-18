from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import database
from database import Base, Job
from services import msa_server, nextflow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "cancelled", "expected"),
    [
        (0, True, ("cancelled", "cancelled")),
        (1, True, ("cancelled", "cancelled")),
        ("raise", True, ("cancelled", "cancelled")),
        (0, False, ("completed", "completed")),
        (1, False, ("failed", "failed")),
    ],
)
async def test_msa_batch_runner_does_not_overwrite_a_job_cancelled_during_the_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome, cancelled: bool, expected,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'msa-batch-cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    output_dir = tmp_path / "msa_batch"
    output_dir.mkdir()
    (output_dir / "msa_manifest.json").write_text(json.dumps({"sequences": []}), encoding="utf-8")

    class _BatchProcess:
        returncode = outcome

        async def communicate(self):
            if cancelled:
                async with factory() as session:
                    await session.execute(update(Job).where(Job.id == "msa-job").values(
                        status="cancelled", queue_status="cancelled", error_message="Cancelled by user"))
                    await session.commit()
            if outcome == "raise":
                raise RuntimeError("batch runner lost its process")
            return b"", None

    async def _spawn(*_args, **_kwargs):
        return _BatchProcess()

    monkeypatch.setattr(database, "async_session", factory)
    monkeypatch.setattr(nextflow, "transient_workflow_runner_mode", lambda: True)
    monkeypatch.setattr(nextflow, "_build_msa_batch_command", lambda **_kwargs: ["batch_msa"])
    monkeypatch.setattr(msa_server, "touch_query_activity", lambda _metadata=None: {})
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    try:
        async with factory() as session:
            session.add(Job(id="msa-job", name="msa", model_id="msa_batch", mode="msa_generation",
                            status="queued", queue_status="queued", params={},
                            output_dir=str(output_dir), created_at=datetime.utcnow()))
            await session.commit()

        await nextflow.launch_msa_batch_job("msa-job", {}, str(output_dir))

        async with factory() as session:
            job = await session.get(Job, "msa-job")
        assert (job.status, job.queue_status) == expected
        assert job.started_at is not None
        if cancelled:
            assert job.error_message == "Cancelled by user"
            assert job.completed_at is None
            assert job.msa_manifest_path is None
    finally:
        await engine.dispose()
