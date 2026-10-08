from __future__ import annotations

import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from routers import bioxp


PREPARE_TO_RUN_JOB_READINESS_ROUTE = "/oem/runtime/readiness/prepare-to-run-job/dry-run"


@pytest.mark.asyncio
async def test_bioxp_capabilities_expose_prepare_to_run_job_readiness_as_bms_proxy_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bioxp, "_GLOBAL_LINKAGE_URL", "http://robot:8123")

    response = await bioxp.bioxp_capabilities()

    assert response["robot_local_expected_routes"][PREPARE_TO_RUN_JOB_READINESS_ROUTE] is True
    assert response["bms_proxy_routes"][PREPARE_TO_RUN_JOB_READINESS_ROUTE] is True
    assert response["default_operator_routes"][PREPARE_TO_RUN_JOB_READINESS_ROUTE] is True
    assert response["bms_role"] == "thin_operator_surface"
    assert any("named" in note and "BMS" in note for note in response["notes"])


@pytest.mark.asyncio
async def test_operations_capabilities_report_prepare_to_run_job_readiness_without_raw_port_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bioxp, "_GLOBAL_LINKAGE_URL", "http://robot:8123")

    async def fake_proxy_request(method: str, path: str, **_: object) -> dict:
        assert method == "GET"
        assert path == "/openapi.json"
        return {"paths": {PREPARE_TO_RUN_JOB_READINESS_ROUTE: {}}}

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy_request)

    response = await bioxp.bioxp_operations_capabilities()

    operation = response["operations"]["prepare_to_run_job_readiness"]
    assert operation["available"] is True
    assert operation["risk"] == "low"
    assert operation["operator_ack_required"] is False
    assert operation["required_routes"][PREPARE_TO_RUN_JOB_READINESS_ROUTE] is True
    assert "robot-local FastAPI owns hardware/runtime" in response["safety_boundary"]


@pytest.mark.asyncio
async def test_prepare_to_run_job_readiness_proxy_forwards_to_robot_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict | None, dict | None, float]] = []
    robot_payload = {
        "ok": True,
        "schema_version": "bioxp.oem_runtime.prepare_to_run_job_readiness.v1",
        "command": "PrepareToRunJob",
        "mode": "dry_run",
        "motion_commanded": False,
        "hardware_touched": False,
        "truth_level": "source_anchored_plan_only_no_motion",
    }

    async def fake_proxy_request(method: str, path: str, json_data=None, params=None, timeout: float = 65.0):
        calls.append((method, path, json_data, params, timeout))
        return robot_payload

    class RequestStub:
        async def json(self) -> dict:
            return {
                "mode": "dry_run",
                "source": "bms-pytest-red-test",
                "params": {"no_motion": True, "deck_inspection": True},
            }

    monkeypatch.setattr(bioxp, "proxy_request", fake_proxy_request)

    response = await bioxp.oem_runtime_prepare_to_run_job_readiness_dry_run(RequestStub())

    assert response == robot_payload
    assert calls == [
        (
            "POST",
            PREPARE_TO_RUN_JOB_READINESS_ROUTE,
            {
                "mode": "dry_run",
                "source": "bms-pytest-red-test",
                "params": {"no_motion": True, "deck_inspection": True},
            },
            None,
            90.0,
        )
    ]
