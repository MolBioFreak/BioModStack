from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module(monkeypatch):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *args, **kwargs: None

    fake_gtk = types.SimpleNamespace(
        Widget=object,
        Align=SimpleNamespace(CENTER="center"),
        Orientation=SimpleNamespace(HORIZONTAL=0, VERTICAL=1),
        WrapMode=SimpleNamespace(WORD_CHAR=0),
    )
    fake_adw = types.SimpleNamespace(Application=type("Application", (), {}))
    fake_glib = types.SimpleNamespace(timeout_add_seconds=lambda *args, **kwargs: None, idle_add=lambda *args, **kwargs: None)
    fake_gio = types.SimpleNamespace(ApplicationFlags=SimpleNamespace(FLAGS_NONE=0))
    fake_pango = types.SimpleNamespace()

    fake_repository = types.ModuleType("gi.repository")
    fake_repository.Gtk = fake_gtk
    fake_repository.Adw = fake_adw
    fake_repository.GLib = fake_glib
    fake_repository.Gio = fake_gio
    fake_repository.Pango = fake_pango

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repository)
    monkeypatch.setitem(sys.modules, "gi.repository.Gtk", fake_gtk)
    monkeypatch.setitem(sys.modules, "gi.repository.Adw", fake_adw)
    monkeypatch.setitem(sys.modules, "gi.repository.GLib", fake_glib)
    monkeypatch.setitem(sys.modules, "gi.repository.Gio", fake_gio)
    monkeypatch.setitem(sys.modules, "gi.repository.Pango", fake_pango)

    script_path = REPO_ROOT / "biomodstack_panel.py"
    spec = importlib.util.spec_from_file_location("biomodstack_panel", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_panel_open_browser_uses_runtime_aware_frontend_url(monkeypatch) -> None:
    module = load_module(monkeypatch)
    captured = {}
    panel = module.BioModStackPanel.__new__(module.BioModStackPanel)

    monkeypatch.setattr(module, "operator_frontend_url", lambda project_root=None: "http://127.0.0.1:5173/")
    monkeypatch.setattr(module.webbrowser, "open", lambda url: captured.setdefault("url", url))

    module.BioModStackPanel._on_open_browser(panel, None)

    assert captured["url"] == "http://127.0.0.1:5173/"


def test_panel_restart_all_passes_detected_runtime_to_wrapper(monkeypatch) -> None:
    module = load_module(monkeypatch)
    captured = {}
    panel = SimpleNamespace(
        _script_env=lambda: {"TEST_ENV": "1"},
        _refresh_status=lambda: True,
    )

    monkeypatch.setattr(module, "operator_runtime_mode", lambda project_root=None: module.DEV_RUNTIME_MODE)
    monkeypatch.setattr(module, "show_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.GLib, "timeout_add_seconds", lambda seconds, callback: captured.setdefault("timeout", (seconds, callback)))
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: captured.setdefault("spawn", (command, kwargs.get("env"))))

    module.BioModStackPanel._on_restart_all(panel, None)

    assert captured["spawn"] == (
        ["bash", str(module.START_SCRIPT), "restart", "--runtime", module.DEV_RUNTIME_MODE],
        {"TEST_ENV": "1"},
    )
    assert captured["timeout"][0] == 3
