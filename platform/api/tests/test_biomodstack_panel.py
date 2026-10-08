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


def test_panel_start_button_uses_explicit_runtime_target(monkeypatch) -> None:
    module = load_module(monkeypatch)
    captured = {}
    panel = SimpleNamespace(
        _script_env=lambda: {"TEST_ENV": "1"},
        _refresh_status=lambda: True,
        _selected_runtime_target=lambda: "both",
    )

    monkeypatch.setattr(module, "show_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.GLib, "timeout_add_seconds", lambda seconds, callback: captured.setdefault("timeout", (seconds, callback)))
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: captured.setdefault("spawn", (command, kwargs.get("env"))))

    module.BioModStackPanel._on_start_all(panel, None)

    assert captured["spawn"] == (
        ["bash", str(module.START_SCRIPT), "start-target", "--target", "both"],
        {"TEST_ENV": "1"},
    )
    assert captured["timeout"][0] == 5


def test_panel_apply_runtime_ports_persists_dev_and_prod_ports(monkeypatch) -> None:
    module = load_module(monkeypatch)
    saved: list[tuple[int, int]] = []
    notifications: list[tuple[str, str]] = []

    class Entry:
        def __init__(self, text: str) -> None:
            self.text = text

        def get_text(self) -> str:
            return self.text

        def set_text(self, value: str) -> None:
            self.text = value

    class Row:
        def __init__(self) -> None:
            self.subtitle = ""

        def set_subtitle(self, value: str) -> None:
            self.subtitle = value

    row = Row()
    panel = module.BioModStackPanel.__new__(module.BioModStackPanel)
    panel.dev_port_entry = Entry("5180")
    panel.prod_port_entry = Entry("19090")
    panel.runtime_ports_row = row

    def fake_save_runtime_port_settings(dev_web_host_port=None, prod_web_host_port=None, project_root=None):
        saved.append((dev_web_host_port, prod_web_host_port))
        return {
            "dev_web_host_port": dev_web_host_port,
            "prod_web_host_port": prod_web_host_port,
            "dev_url": f"http://127.0.0.1:{dev_web_host_port}/",
            "prod_url": f"http://127.0.0.1:{prod_web_host_port}/bms/",
        }

    monkeypatch.setattr(module, "save_runtime_port_settings", fake_save_runtime_port_settings)
    monkeypatch.setattr(module, "show_notification", lambda title, message: notifications.append((title, message)))

    module.BioModStackPanel._on_apply_runtime_ports(panel, None)

    assert saved == [(5180, 19090)]
    assert row.subtitle == "Dev http://127.0.0.1:5180/  |  Stable http://127.0.0.1:19090/bms/"
    assert notifications == [("Runtime Ports Saved", "Restart the selected surfaces, then reload Electron/browser to apply the new ports.")]
