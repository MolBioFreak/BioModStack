from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from routers import gpu as gpu_router
from services import gpu_config
from services.gpu_orchestrator import (
    GPUState,
    HEAVY_MODELS,
    JobInfo,
    VRAM_PROFILES,
    _gpu_safety_margin_mb,
    pack_jobs_to_gpus,
)


def _point_config_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "scheduler" / "gpu_config.json"
    monkeypatch.setattr(gpu_config, "GPU_CONFIG_PATH", path)
    monkeypatch.setattr(gpu_config, "GPU_CONFIG_LOCK_PATH", path.with_suffix(".lock"))
    monkeypatch.setattr(gpu_config, "LEGACY_GPU_CONFIG_PATH", tmp_path / "missing-legacy.json")
    return path


@pytest.mark.parametrize(
    "persisted_limits",
    [
        {},
        {"esmfold2": "auto"},
        {"esmfold2": 99},
        {"esmfold2": None},
        {"esmfold2_experimental": "auto"},
    ],
)
def test_persisted_scheduler_config_always_applies_mandatory_effective_caps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    persisted_limits: dict[str, object],
) -> None:
    path = _point_config_at(monkeypatch, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"concurrency_limits": persisted_limits}), encoding="utf-8")

    limits = gpu_config.read_scheduler_config()["concurrency_limits"]

    assert limits["maturation_child"] <= 2
    assert limits["ppiflow"] <= 2
    assert limits["esmfold2"] == 1
    assert limits["esmfold2_experimental"] == 1
    assert "esmfold" not in limits


def test_global_margin_inheritance_and_explicit_zero_override_change_admission() -> None:
    job = JobInfo(
        id="job",
        name="job",
        model_type="default",
        vram_estimate_mb=8000,
        sequence_length=300,
        priority=0,
        pinned_gpu=None,
        created_at=datetime(2026, 1, 1),
        scheduler_reservation_mb=8000,
    )
    gpu = GPUState(0, "test", 0, 10000, 10000, 0, 40)
    base = {"busy_threshold": 1.0, "cooldown_ms": 0, "target_vram_fill": 0.9}

    low = {"global": {**base, "vram_safety_margin_mb": 500}, "overrides": {}}
    high = {"global": {**base, "vram_safety_margin_mb": 2048}, "overrides": {}}
    explicit_zero = {
        "global": {**base, "vram_safety_margin_mb": 2048},
        "overrides": {"0": {"vram_safety_margin_mb": 0}},
    }

    assert len(pack_jobs_to_gpus([job], [gpu], 0.9, low)) == 1
    assert pack_jobs_to_gpus([job], [gpu], 0.9, high) == []
    assert _gpu_safety_margin_mb(0, high) == 2048
    assert _gpu_safety_margin_mb(0, explicit_zero) == 0
    assert len(pack_jobs_to_gpus([job], [gpu], 0.9, explicit_zero)) == 1


def test_canonical_esmfold2_has_heavy_22gb_reservation() -> None:
    assert VRAM_PROFILES["esmfold2"]["base"] >= 22000
    assert VRAM_PROFILES["esmfold2_experimental"]["base"] >= 22000
    assert {"esmfold2", "esmfold2_experimental"} <= HEAVY_MODELS
    assert "esmfold" not in HEAVY_MODELS


@pytest.mark.asyncio
@pytest.mark.parametrize("model_type", ["esmfold2", "esmfold2_experimental"])
async def test_protected_limit_rejects_null_delete_and_over_cap(
    monkeypatch: pytest.MonkeyPatch,
    model_type: str,
) -> None:
    monkeypatch.setattr(
        gpu_router,
        "mutate_scheduler_config",
        lambda _mutator: pytest.fail("invalid protected update must not mutate config"),
    )

    for request in (
        gpu_router.ConcurrencyLimitRequest(model_type=model_type, limit=None),
        gpu_router.ConcurrencyLimitRequest(model_type=model_type, limit=2),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await gpu_router.set_concurrency_limit(request)
        assert exc_info.value.status_code == 400
        assert "mandatory safety cap" in str(exc_info.value.detail)

    with pytest.raises(HTTPException) as exc_info:
        await gpu_router.delete_concurrency_limit(model_type)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_protected_auto_update_remains_mandatory(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(mutator):
        config = gpu_config.get_default_config()
        mutator(config)
        return gpu_config.normalize_scheduler_config(config)

    monkeypatch.setattr(gpu_router, "mutate_scheduler_config", mutate)
    response = await gpu_router.set_concurrency_limit(
        gpu_router.ConcurrencyLimitRequest(model_type="esmfold2", limit="auto")
    )
    assert response["concurrency_limits"]["esmfold2"] == 1


def test_frontend_preserves_inherited_margin_when_saving_unrelated_fields() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    api_source = (repo_root / "platform/frontend/src/lib/api.ts").read_text(encoding="utf-8")
    resources = (
        repo_root / "platform/frontend/src/components/dashboard/SystemResources.tsx"
    ).read_text(encoding="utf-8")

    assert "vram_safety_margin_mb: number;" in api_source
    assert "vram_safety_margin_mb?: number | null;" in api_source
    assert "override.vram_safety_margin_mb ?? config.global.vram_safety_margin_mb" in resources
    assert "existing.vram_safety_margin_mb ?? null" in resources
    assert "existing.vram_safety_margin_mb ?? 0" not in resources


def test_docker_context_excludes_only_scheduler_state_patterns() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    entries = (repo_root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".gpu_config.json" in entries
    assert ".gpu_*.json" in entries
    assert ".gpu_*.lock" in entries
