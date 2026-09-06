import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def policy_module():
    # An assertion (rather than collection failure) documents the missing owner.
    import importlib.util
    assert importlib.util.find_spec("biomodstack_local_resources") is not None
    return importlib.import_module("biomodstack_local_resources")


@pytest.fixture
def resources(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("BMS_LOCAL_CPU_THREADS", raising=False)
    monkeypatch.delenv("BMS_LOCAL_MEMORY_BYTES", raising=False)
    m = policy_module()
    monkeypatch.setattr(m, "detect_local_capacity", lambda: m.LocalCapacity(32, 128 * 1024**3))
    m.applied_local_policy.cache_clear()
    yield m
    m.applied_local_policy.cache_clear()


def test_default_policy_uses_total_ram_and_logical_threads(monkeypatch):
    resources = policy_module()
    monkeypatch.setattr(resources, "detect_local_capacity", lambda: resources.LocalCapacity(32, 128 * 1024**3))
    p = resources.configured_local_policy({})
    assert p.cpu_threads == 26
    assert p.memory_bytes == 96 * 1024**3


def test_manual_policy_survives_redetection_and_only_restart_applies(resources):
    import biomodstack_runtime_profile as profile
    before = resources.applied_local_policy()
    profile.save_install_profile({"local_cpu_threads": 12, "local_memory_gib": 40})
    assert resources.configured_local_policy().cpu_threads == 12
    assert resources.configured_local_policy().memory_bytes == 40 * 1024**3
    assert resources.applied_local_policy() == before
    resources.applied_local_policy.cache_clear()
    assert resources.applied_local_policy().cpu_threads == 12


@pytest.mark.parametrize("values", [{"local_cpu_threads": 33}, {"local_cpu_threads": 0},
    {"local_cpu_threads": 2.5}, {"local_cpu_threads": True}, {"local_memory_gib": 129},
    {"local_memory_gib": float("nan")}, {"local_memory_gib": 0}])
def test_invalid_override_never_writes(resources, values):
    import biomodstack_runtime_profile as profile
    with pytest.raises(ValueError):
        profile.save_install_profile(values)
    assert not profile.get_install_profile_path().exists()


def test_detection_failure_never_invents_capacity(monkeypatch):
    resources = policy_module()
    monkeypatch.setattr(resources.os, "cpu_count", lambda: None)
    with pytest.raises(ValueError, match="detect"):
        resources.detect_local_capacity()


def test_generated_root_and_new_job_share_policy(resources):
    import biomodstack_services as manager
    from services import execution_ownership as owner
    root = manager.render_workflow_root_slice()
    assert "CPUQuota=2600%" in root
    assert f"MemoryMax={96 * 1024**3}" in root
    command = owner.build_systemd_run_command(lane="development", job_id="test", attempt=1, command=["true"], cpu_threads=26)
    assert "--property=CPUQuota=2600%" in command
    assert f"--property=MemoryMax={96 * 1024**3}" in command


def test_historical_or_remote_evidence_not_capped_by_controller(resources):
    from services.resource_usage_evidence import build_resource_admission_handoff
    handoff = build_resource_admission_handoff(admission_id="a", run_attempt_id="r", canonical_job_id="j",
        preparation_id="p", cpu_threads=128, dram_bytes=512 * 1024**3, gpu_index=None, gpu_uuid=None,
        policy_source="project-scheduler", policy_version="bms.resource-admission-policy.v1", owner="test",
        lease_token="lease", source_revision="a" * 40, source_tree="b" * 40)
    assert handoff["cpu_threads"] == 128


def test_admission_uses_local_policy(resources):
    from services.ngs_molbio_n5 import _resource_request, ResourceAdmissionDenied
    assert _resource_request({"resources": {"cpu_threads": 26}})["cpu_threads"] == 26
    with pytest.raises(ResourceAdmissionDenied):
        _resource_request({"resources": {"cpu_threads": 27}})


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_persisted_policy_changes_only_when_idle(resources, tmp_path, active):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from experiment_models import ExperimentBase, ExperimentResourceAdmissionPolicy as Policy, ExperimentResourceAdmission as Admission
    from services import ngs_molbio_n5 as n5
    assert hasattr(n5, "_locked_local_admission_policy")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admissions.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ExperimentBase.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Policy(policy_id="managed-workflows", policy_version=n5.ADMISSION_POLICY_VERSION,
            cpu_thread_limit=24, dram_byte_limit=96 * 1024**3, lock_generation=0))
        if active:
            session.add(Admission(admission_id="a", workspace_id="w", domain_experiment_id="d", plan_id="p",
                preparation_id="prep", state="admitted", cpu_threads=20, dram_bytes=32 * 1024**3,
                policy_source="project-scheduler", policy_version=n5.ADMISSION_POLICY_VERSION, owner="test"))
        await session.commit()
        if active:
            with pytest.raises(n5.ResourceAdmissionDenied, match="active"):
                await n5._locked_local_admission_policy(session)
            assert (await session.get(Policy, "managed-workflows")).cpu_thread_limit == 24
            assert (await session.get(Admission, "a")).cpu_threads == 20
        else:
            row = await n5._locked_local_admission_policy(session)
            await session.commit()
            assert row.cpu_thread_limit == 26
            assert row.dram_byte_limit == 96 * 1024**3
    await engine.dispose()


@pytest.mark.asyncio
async def test_nextflow_local_share_uses_policy_but_remote_is_untouched(resources, monkeypatch):
    from services import nextflow
    from types import SimpleNamespace
    monkeypatch.setattr(nextflow, "read_scheduler_config", lambda: {"global": {"auto_cpu_threads": False, "cpu_threads_per_job": 64}})
    assert await nextflow._resolve_dynamic_gpu_cpu_share(None, SimpleNamespace(execution_target_id=None), {}) == 26
    assert await nextflow._resolve_dynamic_gpu_cpu_share(None, SimpleNamespace(execution_target_id="remote-1"), {"cpus_per_gpu": 64}) is None
    assert nextflow._dynamic_gpu_cpu_pool_threads() == 26


def test_applied_container_policy_uses_exported_budget_not_profile(resources, monkeypatch):
    monkeypatch.setenv("BMS_LOCAL_CPU_THREADS", "10")
    monkeypatch.setenv("BMS_LOCAL_MEMORY_BYTES", str(30 * 1024**3))
    assert resources.applied_local_policy().cpu_threads == 10
    assert resources.applied_local_policy().memory_bytes == 30 * 1024**3


def test_profile_exports_local_budget_without_ambient_contamination(resources, monkeypatch):
    import biomodstack_runtime_profile as profile
    monkeypatch.setenv("BMS_LOCAL_CPU_THREADS", "5")
    monkeypatch.setenv("BMS_LOCAL_MEMORY_BYTES", str(10 * 1024**3))
    profile.save_install_profile({"local_cpu_threads": 12, "local_memory_gib": 40})
    export = profile.get_core_runtime_env_path().read_text()
    assert "BMS_LOCAL_CPU_THREADS=12\n" in export
    assert f"BMS_LOCAL_MEMORY_BYTES={40 * 1024**3}\n" in export


def test_development_units_export_shared_images_and_local_policy(resources, tmp_path):
    import biomodstack_services as manager
    import biomodstack_runtime_profile as profile
    profile.save_install_profile({"container_dir": str(tmp_path / "shared-images"),
                                  "local_cpu_threads": 12, "local_memory_gib": 40})
    units = manager.render_user_units(Path(__file__).resolve().parents[3], runtime_mode="dev")
    for name in (manager.API_SERVICE, manager.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE):
        assert f"Environment=BMS_CONTAINER_DIR={tmp_path / 'shared-images'}" in units[name]
        assert "Environment=BMS_LOCAL_CPU_THREADS=12" in units[name]
        assert f"Environment=BMS_LOCAL_MEMORY_BYTES={40 * 1024**3}" in units[name]
