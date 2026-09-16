from pathlib import Path
import sys

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_startup_applies_budget_only_when_idle(tmp_path, monkeypatch, active):
    import biomodstack_local_resources as resources
    from experiment_models import ExperimentBase, ExperimentResourceAdmissionPolicy as Policy, ExperimentResourceAdmission as Admission
    from services import ngs_molbio_n5 as n5

    monkeypatch.setattr(n5, "applied_local_policy", lambda: resources.LocalCapacity(26, 24 * 1024**3))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(ExperimentBase.metadata.create_all)
        async with factory() as session:
            session.add(Policy(policy_id="managed-workflows", policy_version=n5.ADMISSION_POLICY_VERSION,
                               cpu_thread_limit=24, dram_byte_limit=96 * 1024**3, lock_generation=0))
            session.add(Admission(admission_id="history", workspace_id="w", domain_experiment_id="d", plan_id="p",
                                  preparation_id="prep", state="released", cpu_threads=20, dram_bytes=32 * 1024**3,
                                  policy_source="project-scheduler", policy_version=n5.ADMISSION_POLICY_VERSION,
                                  owner="test", recovery_evidence_json='{"historical_usage":"unverified"}'))
            if active:
                session.add(Admission(admission_id="active", workspace_id="w", domain_experiment_id="d", plan_id="p",
                                      preparation_id="prep", state="queued", cpu_threads=20, dram_bytes=32 * 1024**3,
                                      policy_source="project-scheduler", policy_version=n5.ADMISSION_POLICY_VERSION, owner="test"))
            await session.commit()
        async with factory() as session:
            if active:
                with pytest.raises(n5.ResourceAdmissionDenied, match="active"):
                    await n5.reconcile_startup_admissions(session)
                await session.rollback()
            else:
                assert await n5.reconcile_startup_admissions(session) == 0
                await session.commit()
        async with factory() as session:
            row = await session.get(Policy, "managed-workflows")
            assert (row.cpu_thread_limit, row.dram_byte_limit) == ((24, 96 * 1024**3) if active else (26, 24 * 1024**3))
            history = await session.get(Admission, "history")
            assert history.state == "released"
            assert history.recovery_evidence_json == '{"historical_usage":"unverified"}'
            if active:
                row = await session.get(Admission, "active")
                assert row.state == "queued" and row.cpu_threads == 20
    finally:
        await engine.dispose()
