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


def test_tray_frontend_status_checks_http_before_dev_service_fallbacks(monkeypatch) -> None:
    module = load_module(monkeypatch)
    monkeypatch.setattr(module, "service_is_active", lambda *args, **kwargs: False)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=2: _FakeResponse())

    assert module.check_frontend_status() is True



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
