from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import main as bms_api_main


@pytest.mark.asyncio
async def test_analytical_init_failure_is_logged_not_raised(monkeypatch, caplog) -> None:
    async def fail_init() -> None:
        raise RuntimeError("postgres refused connection")

    monkeypatch.setenv("BMS_ANALYTICAL_INIT_ON_STARTUP", "1")
    monkeypatch.setattr(bms_api_main, "init_analytical_store", fail_init, raising=False)

    with caplog.at_level(logging.WARNING):
        await bms_api_main._init_analytical_store_optional()

    assert bms_api_main.ANALYTICAL_STARTUP_STATUS == {
        "attempted": True,
        "ok": False,
        "message": "postgres refused connection",
    }
    assert "BMS DB service unavailable for analytical init" in caplog.text


@pytest.mark.asyncio
async def test_analytical_init_skipped_when_startup_flag_is_off(monkeypatch) -> None:
    called = False

    async def init_should_not_run() -> None:
        nonlocal called
        called = True

    monkeypatch.setenv("BMS_ANALYTICAL_INIT_ON_STARTUP", "0")
    monkeypatch.setattr(bms_api_main, "init_analytical_store", init_should_not_run, raising=False)

    await bms_api_main._init_analytical_store_optional()

    assert called is False
    assert bms_api_main.ANALYTICAL_STARTUP_STATUS == {
        "attempted": False,
        "ok": None,
        "message": "not requested",
    }
