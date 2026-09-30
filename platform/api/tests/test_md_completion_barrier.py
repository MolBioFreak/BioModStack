from __future__ import annotations

from pathlib import Path

import services.md.completion as completion_module
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job, MdAttemptSegment, MdReplicaRun, MdRun
from services.md.state import create_md_run, create_replica_attempt


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_md_completion_service_is_the_named_terminal_authority() -> None:
    service = (REPO_ROOT / "platform/api/services/md/completion.py").read_text(encoding="utf-8")
    results = (REPO_ROOT / "platform/api/services/md/results.py").read_text(encoding="utf-8")
    assert "def validate_and_finalize_md_job" in service
    assert "apply_completion_barrier(job, _snapshot=snapshot)" in service
    assert "md_run_v1.schema.json" in results
    assert "md_analysis_v1.schema.json" in results
    assert "replica_manifest_set_sha256" in results
    assert "MD_COMPLETION_CONFLICT" in results


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_evidence", [False, True])
async def test_md_terminal_authority_closes_durable_run_state_in_the_callers_transaction(
    monkeypatch, tmp_path, missing_evidence,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'completion.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            parent = Job(id="job-1", name="MD", status="running", model_id="molecular_dynamics",
                         mode="simulate", params={})
            replica_child = Job(id="replica", name="replica", status="running", model_id="molecular_dynamics",
                mode="replica", params={}, parent_job_id=parent.id, child_stage="md_replica",
                provenance={"component_projection": {"root_job_id": parent.id, "state": "completed",
                    "result": {"references": [] if missing_evidence else [{"role": "manifest"}]}}})
            analysis_child = Job(id="analysis", name="analysis", status="completed", model_id="molecular_dynamics",
                mode="analyze", params={}, parent_job_id=parent.id, child_stage="md_analysis")
            session.add_all([parent, replica_child, analysis_child])
            await session.flush()
            run = await create_md_run(session, job=parent, normalized_request={
                "schema": "bms.md.job.v2", "chemistry": {"profile_id": "amber_ff19sb_opc_protein_v1",
                    "profile_sha256": "a" * 64, "assurance": "curated_profile"},
            })
            replica, segment = await create_replica_attempt(session, job_id=parent.id,
                replica_index=0, attempt=0, engine="gromacs", execution_plan_sha256="b" * 64,
                compatibility_key="c" * 64, child_job_id=replica_child.id)
            replica.state = segment.state = "running"
            run.phase, run.state_version, run.controls_blocked = "finalizing", 7, True
            replica_id, segment_id = replica.id, segment.id
            await session.commit()

            # The completion-barrier boundary now returns exact native children,
            # not merely a state string. Exercise their real persisted lineage.
            snapshot = {"state": "completed", "replica_child_ids": ["replica"],
                        "analysis_child_ids": ["analysis"]}
            monkeypatch.setattr(completion_module, "_prepare_completion",
                                lambda record: (snapshot, None, None))
            async def no_artifacts(_job, _session, *, _inventory, _frame_endpoints) -> None:
                return None
            monkeypatch.setattr(completion_module, "_ingest_durable_artifacts", no_artifacts)

            if missing_evidence:
                with pytest.raises(completion_module.MDResultError, match="validated component evidence"):
                    await completion_module.validate_and_finalize_md_job(parent, session)
                assert run.phase == "finalizing" and replica.state == segment.state == "running"
                return

            assert await completion_module.validate_and_finalize_md_job(parent, session) == snapshot
            await session.flush()
            async with sessions() as observer:
                stored = await observer.get(MdRun, parent.id)
                assert stored.phase == "finalizing" and stored.state_version == 7
            assert (run.phase, run.verification_status, run.state_version, run.controls_blocked) == (
                "completed", "verified", 8, False,
            )
            assert replica.state == segment.state == "completed" and replica.active is False
            assert replica.completed_at is not None and segment.completed_at is not None
            await session.commit()
            # Replay must not increment state version or overwrite terminal time.
            completed_at = segment.completed_at
            await completion_module.validate_and_finalize_md_job(parent, session)
            assert run.state_version == 8 and segment.completed_at == completed_at
            await session.commit()
        async with sessions() as observer:
            assert (await observer.get(MdRun, "job-1")).phase == "completed"
            assert (await observer.get(MdReplicaRun, replica_id)).state == "completed"
            assert (await observer.get(MdAttemptSegment, segment_id)).state == "completed"
    finally:
        await engine.dispose()
