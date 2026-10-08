from __future__ import annotations

import importlib

import pytest


@pytest.mark.asyncio
async def test_native_readiness_does_not_require_workflow_adapter(monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "0")
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)
    monkeypatch.setenv("BMS_FEATURE_ASSAY_DB", "0")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://frontend.test/bms/")
    monkeypatch.setattr(readiness, "core_database_readiness", lambda: _async_result(True, "ready"))
    monkeypatch.setattr(readiness, "http_readiness", lambda _url: _async_result(True, "ready"))

    result = await readiness.collect_runtime_readiness(
        molbio={"status": "healthy", "ready": True},
    )

    assert result["mode"] == "native"
    assert result["checks"]["workflow_adapter"] == {
        "required": False,
        "ready": True,
        "status": "not_required",
    }
    assert result["checks"]["workflow_launch"]["allowed"] is True
    assert result["ready"] is True


@pytest.mark.asyncio
async def test_container_readiness_requires_reachable_adapter(monkeypatch) -> None:
    readiness = importlib.import_module("readiness")
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "http://adapter.test")
    monkeypatch.setenv("BMS_FEATURE_ASSAY_DB", "0")
    monkeypatch.setenv("BMS_FRONTEND_HEALTH_URL", "http://frontend.test/bms/")
    monkeypatch.setattr(readiness, "core_database_readiness", lambda: _async_result(True, "ready"))

    async def fake_http(url: str):
        if "adapter.test" in url:
            return False, "unreachable"
        return True, "ready"

    monkeypatch.setattr(readiness, "http_readiness", fake_http)

    result = await readiness.collect_runtime_readiness(
        molbio={"status": "healthy", "ready": True},
    )

    assert result["checks"]["workflow_adapter"]["required"] is True
    assert result["checks"]["workflow_adapter"]["ready"] is False
    assert result["checks"]["workflow_launch"]["allowed"] is True
    assert result["ready"] is False


@pytest.mark.asyncio
async def test_version_endpoint_reports_build_identity(monkeypatch) -> None:
    monkeypatch.setenv("BMS_BUILD_SHA", "fedcba9876543210fedcba9876543210fedcba98")
    monkeypatch.setenv("BMS_BUILD_ID", "phase5-test")
    monkeypatch.setenv("BMS_BUILD_TIME", "2026-07-18T04:00:00Z")
    main = importlib.import_module("main")

    response = await main.api_version()

    assert response["service"] == "biomodstack-api"
    assert response["build"]["revision"] == "fedcba9876543210fedcba9876543210fedcba98"


async def _async_result(ready: bool, status: str):
    return ready, status
