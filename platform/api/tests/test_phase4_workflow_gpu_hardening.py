from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import gpu as gpu_router
from routers.gpu import SchedulerGPUOverride, SchedulerGlobalConfig
from services import gpu_config
from services.gpu_orchestrator import (
    GPUState,
    HEAVY_MODELS,
    JobInfo,
    VRAM_PROFILES,
    _gpu_safety_margin_mb,
    build_queue_scheduler_diagnostics,
    pack_jobs_to_gpus,
)
from services.nextflow import WORKFLOW_ENTRYPOINTS


CANONICAL_NGS_WORKFLOWS = {
    "ont_basecall_dna",
    "ont_basecall_rna",
    "ont_plasmid_qc",
    "ont_construct_screening",
    "ont_methylation_analysis",
    "ont_fastq_qc",
    "wf_clone_validation",
}
PROTECTED_HIGH_PRESSURE_MODELS = {
    "maturation_child",
    "ppiflow",
    "esmfold",
    "esmfold2",
    "esmfold2_experimental",
}


def test_scheduler_state_honors_current_state_dir_contract_at_process_start(tmp_path: Path) -> None:
    state_dir = tmp_path / "profile-data" / "scheduler-state"
    env = {**os.environ, "BMS_SCHEDULER_STATE_DIR": str(state_dir)}
    env.pop("BMS_GPU_CONFIG_PATH", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from services.gpu_config import GPU_CONFIG_PATH; print(GPU_CONFIG_PATH)",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == str((state_dir / "gpu_config.json").resolve())


def test_scheduler_source_does_not_restore_stale_gpu_config_path_contract() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "gpu_config.py").read_text(encoding="utf-8")

    assert "BMS_SCHEDULER_STATE_DIR" in source
    assert "BMS_GPU_CONFIG_PATH" not in source


def test_scheduler_defaults_keep_headroom_and_bound_high_pressure_models() -> None:
    config = gpu_config.DEFAULT_SCHEDULER_CONFIG

    assert config["global"]["vram_safety_margin_mb"] >= 1024
    assert _gpu_safety_margin_mb(0, config) == config["global"]["vram_safety_margin_mb"]
    assert PROTECTED_HIGH_PRESSURE_MODELS <= gpu_config.PROTECTED_CONCURRENCY_LIMITS
    assert all(config["concurrency_limits"][model] >= 1 for model in PROTECTED_HIGH_PRESSURE_MODELS)
    assert config["concurrency_limits"]["esmfold"] == 1
    assert config["concurrency_limits"]["esmfold2"] == 1
    assert config["concurrency_limits"]["esmfold2_experimental"] == 1
    assert VRAM_PROFILES["esmfold"]["base"] >= 18_000
    assert VRAM_PROFILES["esmfold2"]["base"] >= 22_000
    assert VRAM_PROFILES["esmfold2_experimental"]["base"] >= 22_000
    assert {"esmfold", "esmfold2", "esmfold2_experimental"} <= HEAVY_MODELS
    assert SchedulerGlobalConfig().vram_safety_margin_mb == config["global"]["vram_safety_margin_mb"]
    assert SchedulerGPUOverride().vram_safety_margin_mb is None


def _queued_job(job_id: str, model_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        name=job_id,
        model_id=model_id,
        mode="predict",
        params={},
        vram_estimate_mb=None,
        sequence_length=300,
        priority=0,
        pinned_gpu=None,
        created_at=datetime(2026, 1, 1),
        batch_id=None,
    )


def test_running_heavy_job_blocks_second_heavy_job_at_mandatory_cap(monkeypatch) -> None:
    queued = _queued_job("queued-esm", "esmfold2")
    running = _queued_job("running-esm", "esmfold2")
    running.queue_status = "running"
    running.assigned_gpu = 0
    running.started_at = datetime.utcnow()
    running.nextflow_run_id = None
    gpu = GPUState(0, "test heavy GPU", 1000, 32768, 31768, 0, 40)
    monkeypatch.setattr(
        "services.gpu_orchestrator.GPU_CAPABILITIES",
        {0: {"supports_heavy": True, "supports_protenix": True}},
    )

    diagnostics = build_queue_scheduler_diagnostics(
        [queued], [running], [gpu], gpu_config.DEFAULT_SCHEDULER_CONFIG
    )

    assert diagnostics[queued.id]["scheduler_ready"] is False
    assert diagnostics[queued.id]["scheduler_blockers"] == ["model concurrency limit reached (1/1)"]


def test_global_safety_margin_changes_gpu_packing_admission(monkeypatch) -> None:
    job = JobInfo(
        id="packing-job",
        name="packing-job",
        model_type="default",
        vram_estimate_mb=8000,
        sequence_length=300,
        priority=0,
        pinned_gpu=None,
        created_at=datetime(2026, 1, 1),
        scheduler_reservation_mb=8000,
    )
    gpu = GPUState(0, "test GPU", 0, 10000, 10000, 0, 40)
    monkeypatch.setattr(
        "services.gpu_orchestrator.GPU_CAPABILITIES",
        {0: {"supports_heavy": True, "supports_protenix": True}},
    )
    base_global = {"busy_threshold": 1.0, "cooldown_ms": 0, "target_vram_fill": 0.9}
    low_margin = {"global": {**base_global, "vram_safety_margin_mb": 500}, "overrides": {}}
    high_margin = {"global": {**base_global, "vram_safety_margin_mb": 1500}, "overrides": {}}

    assert len(pack_jobs_to_gpus([job], [gpu], 0.9, low_margin)) == 1
    assert pack_jobs_to_gpus([job], [gpu], 0.9, high_margin) == []


@pytest.mark.asyncio
async def test_protected_concurrency_limit_rejects_null_removal(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_router,
        "mutate_scheduler_config",
        lambda _mutator: pytest.fail("protected removal must not mutate scheduler state"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await gpu_router.set_concurrency_limit(
            gpu_router.ConcurrencyLimitRequest(model_type="esmfold", limit=None)
        )

    assert exc_info.value.status_code == 400
    assert "mandatory safety cap" in exc_info.value.detail


@pytest.mark.asyncio
async def test_protected_concurrency_limit_rejects_delete(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_router,
        "mutate_scheduler_config",
        lambda _mutator: pytest.fail("protected deletion must not mutate scheduler state"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await gpu_router.delete_concurrency_limit("esmfold2_experimental")

    assert exc_info.value.status_code == 400
    assert "mandatory safety cap" in exc_info.value.detail


def test_standalone_ngs_entrypoints_resolve_repository_root_without_workflow_rewrites() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    config_source = (repo_root / "nextflow.config").read_text(encoding="utf-8")

    assert "new File(new File(projectDir.toString()).parent, 'scripts').exists()" in config_source
    assert "parentFile.parent" in config_source
    for workflow_id in CANONICAL_NGS_WORKFLOWS:
        relative = WORKFLOW_ENTRYPOINTS[workflow_id]
        assert (repo_root / relative).is_file(), (workflow_id, relative)


def test_frontend_scheduler_contract_preserves_global_and_inherited_safety_margin() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_source = (repo_root / "platform/frontend/src/lib/api.ts").read_text(encoding="utf-8")
    resources_source = (
        repo_root / "platform/frontend/src/components/dashboard/SystemResources.tsx"
    ).read_text(encoding="utf-8")

    assert "vram_safety_margin_mb: number;" in api_source
    assert "vram_safety_margin_mb: number;" in resources_source
    assert "vram_safety_margin_mb: config?.global?.vram_safety_margin_mb" in resources_source
    assert "existing.vram_safety_margin_mb ?? config.global.vram_safety_margin_mb" in resources_source
    assert "vram_safety_margin_mb ?? 0" not in resources_source


def test_build_context_and_docs_use_current_scheduler_and_log_limit_names() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    dockerignore = (repo_root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    api_readme = (repo_root / "platform/api/README.md").read_text(encoding="utf-8")

    assert {".gpu_config.json", ".gpu_*.json", ".gpu_*.lock"} <= set(dockerignore)
    assert "BMS_SCHEDULER_STATE_DIR" in api_readme
    assert "BMS_GPU_CONFIG_PATH" not in api_readme
    for name in (
        "BMS_NEXTFLOW_LOG_MAX_BYTES",
        "BMS_NEXTFLOW_LOG_READ_BYTES",
        "BMS_NEXTFLOW_RETAINED_LOG_MAX_BYTES",
        "BMS_NEXTFLOW_RETAINED_LOG_MAX_LINES",
    ):
        assert name in api_readme
