from __future__ import annotations

import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import workflow_adapter


def test_blank_adapter_url_disables_adapter_for_direct_transient_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", "development")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_URL", "")
    monkeypatch.setenv("BMS_CORE_RUNTIME_MODE", "0")

    assert workflow_adapter.workflow_adapter_base_url() is None
    assert workflow_adapter.workflow_adapter_enabled() is False
    assert workflow_adapter.workflow_launch_mode() == "native"