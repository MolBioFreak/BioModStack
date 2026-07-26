from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for root in (API_ROOT, REPO_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _import_main_with_bioxp(enabled: bool):
    sys.modules.pop("main", None)
    importlib.invalidate_caches()
    return importlib.import_module("main")


def test_bioxp_api_routes_are_not_registered_when_feature_disabled(monkeypatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_BIOXP", "0")

    bms_main = _import_main_with_bioxp(False)
    paths = bms_main.app.openapi()["paths"]

    assert "/api/system/features" in paths
    assert not any(path.startswith("/api/bioxp") for path in paths)


def test_bioxp_api_routes_are_registered_when_feature_enabled(monkeypatch) -> None:
    monkeypatch.setenv("BMS_FEATURE_BIOXP", "1")

    bms_main = _import_main_with_bioxp(True)
    paths = bms_main.app.openapi()["paths"]

    bioxp_paths = {path for path in paths if path.startswith("/api/bioxp")}
    assert "/api/bioxp/status" in bioxp_paths
    assert "/api/bioxp/profile" in bioxp_paths
    assert "/api/bioxp/commands" in bioxp_paths
    assert "/api/bioxp/oem-full-lifecycle/contract" in bioxp_paths
    assert len(bioxp_paths) <= 18
    assert not any("interlink" in path or "proxy" in path for path in bioxp_paths)


def test_compose_runtime_passes_feature_flags_to_api_container() -> None:
    compose_text = (REPO_ROOT / "compose.core-runtime.yml").read_text(encoding="utf-8")

    assert "BMS_FEATURE_BIOXP: ${BMS_FEATURE_BIOXP:-1}" in compose_text
    assert "BMS_FEATURE_STA" + "TS_TOOLS" not in compose_text
    assert "BMS_FEATURE_AS" + "SAY_DB" not in compose_text


def test_runtime_feature_response_does_not_advertise_unmounted_bioxp_router() -> None:
    from routers.system import _effective_runtime_features

    request = cast(Any, SimpleNamespace(
        app=SimpleNamespace(
            routes=[SimpleNamespace(path="/api/system/features")],
            openapi=lambda: {"paths": {"/api/system/features": {}}},
        ),
    ))
    effective = _effective_runtime_features(request, {"bioxp": True})

    assert effective["bioxp"] is False


def test_runtime_feature_response_detects_mounted_bioxp_router_from_openapi() -> None:
    from routers.system import _effective_runtime_features

    request = cast(Any, SimpleNamespace(
        app=SimpleNamespace(
            routes=[SimpleNamespace(path="/api/system/features")],
            openapi=lambda: {
                "paths": {
                    "/api/system/features": {},
                    "/api/bioxp/status": {},
                },
            },
        ),
    ))
    effective = _effective_runtime_features(request, {"bioxp": True})

    assert effective["bioxp"] is True
