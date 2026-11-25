"""M2 acceptance tests: optional modules (R19) and failure isolation (R34)."""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _reload_main():
    sys.modules.pop("main", None)
    importlib.invalidate_caches()
    return importlib.import_module("main")


def test_m2_feature_flags_default_on_and_render_in_profile_and_compose(monkeypatch) -> None:
    import biomodstack_runtime_profile as profiles

    for env_name in (
        "BMS_FEATURE_NGS_ONT",
        "BMS_FEATURE_STATS",
        "BMS_FEATURE_PROTEIN_COMPUTE",
    ):
        monkeypatch.delenv(env_name, raising=False)

    assert profiles.install_feature_enabled("ngs_ont", profile={}) is True
    assert profiles.install_feature_enabled("stats", profile={}) is True
    assert profiles.install_feature_enabled("protein_compute", profile={}) is True

    resolved = profiles.resolve_runtime_paths(profile={}, environ={})
    resolved.update(local_cpu_threads=4, local_memory_bytes=8 * 1024**3)
    core_env = "\n".join(profiles._core_runtime_env_lines(resolved))
    compat_env = "\n".join(profiles._compat_env_lines(resolved))
    for key in ("BMS_FEATURE_NGS_ONT", "BMS_FEATURE_STATS", "BMS_FEATURE_PROTEIN_COMPUTE"):
        assert f"{key}=1" in core_env
        assert f'export {key}="${{{key}:-1}}"' in compat_env

    compose_text = (REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8")
    assert "BMS_FEATURE_NGS_ONT: ${BMS_FEATURE_NGS_ONT:-1}" in compose_text
    assert "BMS_FEATURE_STATS: ${BMS_FEATURE_STATS:-1}" in compose_text
    assert "BMS_FEATURE_PROTEIN_COMPUTE: ${BMS_FEATURE_PROTEIN_COMPUTE:-1}" in compose_text


def test_ngs_disabled_unmounts_routes_and_never_opens_ngs_store(monkeypatch) -> None:
    import molbio_ngs_database

    molbio_ngs_database.reset_molbio_ngs_state()
    engine_calls: list[str] = []
    monkeypatch.setattr(
        molbio_ngs_database,
        "create_molbio_ngs_engine",
        lambda *a, **kw: engine_calls.append("open") or (_ for _ in ()).throw(AssertionError("NGS store opened")),
    )
    monkeypatch.setenv("BMS_FEATURE_NGS_ONT", "0")

    bms_main = _reload_main()
    paths = set(bms_main.app.openapi()["paths"])

    assert not any(p.startswith(("/api/ont", "/api/sequence-qc", "/api/ngs", "/api/molbio-ngs")) for p in paths)
    assert "/api/sequences/" in paths
    assert "/api/projects" in paths or any(p.startswith("/api/projects") for p in paths)
    assert engine_calls == []

    # Health check must not call molbio_ngs_health when ngs_ont is disabled.
    monkeypatch.setattr(bms_main, "molbio_health", lambda **kw: asyncio.sleep(0, result={"status": "healthy", "ready": True, "restriction_digest": {"ready": True}}))
    monkeypatch.setattr(bms_main, "molbio_ngs_health", lambda **kw: (_ for _ in ()).throw(AssertionError("molbio_ngs_health called")))

    health_payload = asyncio.run(bms_main.health_check(deep=False))
    assert health_payload["molbio_ngs"]["status"] == "disabled"
    ngs_check = health_payload["readiness"]["checks"]["molbio_ngs_database"]
    assert ngs_check["required"] is False
    assert ngs_check["ready"] is True
    assert ngs_check["status"] == "disabled"
    assert engine_calls == []


def test_ngs_enabled_but_broken_returns_503_without_crashing_health(monkeypatch) -> None:
    import molbio_ngs_database

    molbio_ngs_database.reset_molbio_ngs_state()
    monkeypatch.setenv("BMS_FEATURE_NGS_ONT", "1")
    bms_main = _reload_main()
    molbio_ngs_database.set_molbio_ngs_startup_error("corrupted ngs sqlite file")
    try:
        async def _try_query() -> None:
            gen = molbio_ngs_database.get_molbio_ngs_session()
            session = await gen.__anext__()
            try:
                await session.execute("SELECT 1")
            finally:
                await gen.aclose()

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(_try_query())
        assert exc_info.value.status_code == 503
        assert "corrupted ngs sqlite file" in str(exc_info.value.detail)

        monkeypatch.setattr(bms_main, "molbio_health", lambda **kw: asyncio.sleep(0, result={"status": "healthy", "ready": True, "restriction_digest": {"ready": True}}))
        health_payload = asyncio.run(bms_main.health_check(deep=False))
        assert health_payload["molbio_ngs"]["status"] == "unavailable"
        assert health_payload["readiness"]["checks"]["molbio_ngs_database"]["ready"] is False
    finally:
        molbio_ngs_database.reset_molbio_ngs_state()


def test_bioxp_startup_failure_returns_503_with_reason() -> None:
    from routers.bioxp.dependencies import get_bioxp_runtime

    request = cast(
        Any,
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(bioxp_runtime=None, bioxp_startup_error="sqlite disk image malformed")
            )
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        get_bioxp_runtime(request)
    assert exc_info.value.status_code == 503
    assert "sqlite disk image malformed" in str(exc_info.value.detail)


def test_protein_compute_disabled_keeps_core_molecular_and_job_routes(monkeypatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_PROTEIN_COMPUTE", "0")
    monkeypatch.setenv("BMS_FEATURE_NGS_ONT", "1")
    bms_main = _reload_main()
    paths = set(bms_main.app.openapi()["paths"])

    assert not any(p.startswith(("/api/frustrampnn", "/api/boltzgen", "/api/jobs/boltz-api", "/api/msa", "/api/blind-pose")) for p in paths)
    assert any(p.startswith("/api/jobs") for p in paths)
    assert any(p.startswith("/api/sequences") for p in paths)
    assert any(p.startswith("/api/designs") for p in paths)
    assert bms_main.app.state.mounted_features["protein_compute"] is False


def test_stats_disabled_blocks_stats_toolkit_endpoint_and_keeps_readback(monkeypatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_STATS", "0")
    bms_main = _reload_main()
    assert bms_main.app.state.mounted_features["stats"] is False
    paths = set(bms_main.app.openapi()["paths"])
    assert any(p.startswith("/api/designs") for p in paths)

    from routers.system import get_stats_toolkit_status
    request = cast(Any, SimpleNamespace(app=bms_main.app))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_stats_toolkit_status(request))
    assert exc_info.value.status_code == 404


def test_workflow_adapter_offline_to_online_starts_orchestrator_once_and_cancels_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_NGS_ONT", "1")
    import worker as bms_worker
    bms_worker._orchestrator = None

    probes = iter([(False, "unavailable"), (False, "unavailable"), (True, "http_200")])
    started: list[str] = []

    async def fake_readiness(_url: str):
        return next(probes, (True, "http_200"))

    class _FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def start(self):
            started.append("started")

    monkeypatch.setattr(bms_worker, "workflow_adapter_base_url", lambda: "http://127.0.0.1:18001")
    monkeypatch.setattr(bms_worker, "http_readiness", fake_readiness)
    monkeypatch.setattr(bms_worker, "GPUOrchestrator", _FakeOrchestrator)

    async def _scenario():
        await bms_worker._wait_and_start_workflow_orchestrator(poll_interval_seconds=0.01)
        # Calling start helper again must be idempotent.
        await bms_worker._start_workflow_orchestrator()

    asyncio.run(_scenario())
    assert started == ["started"]
    assert bms_worker._orchestrator is not None

    # Also verify cancellation exits cleanly without starting an orchestrator.
    bms_worker._orchestrator = None
    started.clear()
    monkeypatch.setattr(bms_worker, "http_readiness", lambda _url: asyncio.sleep(0, result=(False, "unavailable")))

    async def _cancel_scenario():
        task = asyncio.create_task(bms_worker._wait_and_start_workflow_orchestrator(poll_interval_seconds=0.05))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_cancel_scenario())
    assert started == []
    assert bms_worker._orchestrator is None
