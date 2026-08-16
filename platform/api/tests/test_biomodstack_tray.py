from __future__ import annotations

import importlib.util
import sys
import types
import urllib.request
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module(monkeypatch):
    fake_pystray = types.ModuleType("pystray")
    fake_pystray.MenuItem = lambda *args, **kwargs: (args, kwargs)
    fake_pystray.Menu = lambda *args, **kwargs: (args, kwargs)
    fake_pystray.Icon = object
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)

    fake_pil = types.ModuleType("PIL")
    fake_image = types.ModuleType("PIL.Image")
    fake_image.Image = object
    fake_image.open = lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError())
    fake_image.Resampling = SimpleNamespace(LANCZOS=1)
    fake_image_draw = types.ModuleType("PIL.ImageDraw")
    fake_image_draw.Draw = lambda image: SimpleNamespace(ellipse=lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_image)
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", fake_image_draw)

    script_path = REPO_ROOT / "biomodstack_tray.py"
    spec = importlib.util.spec_from_file_location("biomodstack_tray", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_tray_frontend_status_accepts_http_200(monkeypatch) -> None:
    module = load_module(monkeypatch)
    monkeypatch.setattr(module, "operator_runtime_mode", lambda project_root=None: module.DEV_RUNTIME_MODE)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=2: _FakeResponse())

    assert module.check_frontend_status() is True



def test_tray_status_probes_never_fall_back_to_process_or_service_manager(monkeypatch) -> None:
    module = load_module(monkeypatch)

    def legacy_probe_must_not_run(*args, **kwargs):
        raise AssertionError("status must not use a service-manager or process fallback")

    monkeypatch.setattr(urllib.request, "urlopen", legacy_probe_must_not_run)
    monkeypatch.setattr(module, "operator_runtime_mode", lambda project_root=None: module.DEV_RUNTIME_MODE)
    monkeypatch.setattr(module, "service_is_active", legacy_probe_must_not_run, raising=False)
    monkeypatch.setattr(module.subprocess, "run", legacy_probe_must_not_run)

    assert module.check_api_status() is False
    assert module.check_frontend_status() is False


def test_tray_status_checks_both_surfaces_for_one_selected_runtime(monkeypatch) -> None:
    module = load_module(monkeypatch)
    selected_runtime = module.DEV_RUNTIME_MODE
    checked_runtimes: list[str] = []
    monkeypatch.setattr(module, "operator_runtime_mode", lambda project_root=None: selected_runtime)
    monkeypatch.setattr(module, "check_api_status", lambda runtime_mode: checked_runtimes.append(runtime_mode) or True)
    monkeypatch.setattr(module, "check_frontend_status", lambda runtime_mode: checked_runtimes.append(runtime_mode) or True)

    assert module.BioModStackTray.get_status(object()) == "healthy"
    assert checked_runtimes == [selected_runtime, selected_runtime]


def test_tray_icon_is_non_green_when_runtime_is_down(monkeypatch) -> None:
    module = load_module(monkeypatch)
    fills: list[str] = []
    monkeypatch.setattr(module.Image, "new", lambda *args, **kwargs: object(), raising=False)
    monkeypatch.setattr(
        module.ImageDraw,
        "Draw",
        lambda image: SimpleNamespace(ellipse=lambda *args, **kwargs: fills.append(kwargs["fill"])),
    )

    module.create_status_icon("down")

    assert fills == ["#ef4444"]
    assert "#22c55e" not in fills


def test_tray_open_ui_passes_detected_runtime_to_launcher(monkeypatch) -> None:
    module = load_module(monkeypatch)
    captured = {}

    monkeypatch.setattr(module, "operator_runtime_mode", lambda project_root=None: module.DEV_RUNTIME_MODE)
    monkeypatch.setattr(module, "show_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "build_launch_ui_command",
        lambda **kwargs: captured.setdefault("command", [kwargs["runtime_mode"], str(kwargs["project_root"])]),
    )
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: captured.setdefault("popen", command))

    module.open_ui()

    assert captured["command"][0] == module.DEV_RUNTIME_MODE
    assert captured["popen"] == captured["command"]



def test_tray_restart_all_services_passes_detected_runtime_to_wrapper(monkeypatch) -> None:
    module = load_module(monkeypatch)
    captured = {}

    monkeypatch.setattr(module, "operator_runtime_mode", lambda project_root=None: module.DEV_RUNTIME_MODE)
    monkeypatch.setattr(module, "show_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: captured.setdefault("command", command))

    module.restart_all_services()

    assert captured["command"] == [str(module.START_SCRIPT), "restart", "--runtime", module.DEV_RUNTIME_MODE]
