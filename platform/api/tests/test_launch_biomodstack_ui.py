from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module():
    script_path = REPO_ROOT / "scripts" / "launch_biomodstack_ui.py"
    spec = importlib.util.spec_from_file_location("launch_biomodstack_ui", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_explicit_surface_overrides_preferences() -> None:
    module = load_module()
    prefs = {"default_surface": "browser", "auto_open_hosted_web_on_start": False}

    assert module.resolve_surface_choice("electron", prefs) == "electron"
    assert module.resolve_surface_choice(None, prefs) == "browser"


def test_none_surface_does_not_open_browser(monkeypatch) -> None:
    module = load_module()
    opened: list[str] = []
    monkeypatch.setattr(module, "start_all", lambda project_root=None, runtime_mode=None: None)
    monkeypatch.setattr(
        module,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "browser_url": "http://127.0.0.1:5173/bms/",
            "launch_preferences": {"default_surface": "browser", "auto_open_hosted_web_on_start": True},
        },
    )
    monkeypatch.setattr(module.webbrowser, "open_new_tab", lambda url: opened.append(url))

    module.launch_ui(runtime_mode="container", surface="none")

    assert opened == []


def test_electron_surface_fails_clearly_until_shell_exists(monkeypatch) -> None:
    module = load_module()
    started: list[str] = []
    monkeypatch.setattr(module, "start_all", lambda project_root=None, runtime_mode=None: started.append(runtime_mode or "dev"))
    monkeypatch.setattr(
        module,
        "load_launch_preferences",
        lambda: {"default_surface": "browser", "auto_open_hosted_web_on_start": True},
    )

    try:
        module.launch_ui(runtime_mode="container", surface="electron")
    except module.ServiceManagerError as exc:
        assert "Electron launch surface requested" in str(exc)
    else:
        raise AssertionError("expected ServiceManagerError")

    assert started == []


def test_stored_electron_preference_falls_back_to_browser_until_shell_exists(monkeypatch) -> None:
    module = load_module()
    opened: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(module, "start_all", lambda project_root=None, runtime_mode=None: started.append(runtime_mode or "dev"))
    monkeypatch.setattr(
        module,
        "load_launch_preferences",
        lambda: {"default_surface": "electron", "auto_open_hosted_web_on_start": True},
    )
    monkeypatch.setattr(
        module,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "browser_url": "http://127.0.0.1:5173/bms/",
            "launch_preferences": {"default_surface": "electron", "auto_open_hosted_web_on_start": True},
        },
    )
    monkeypatch.setattr(module.webbrowser, "open_new_tab", lambda url: opened.append(url))

    module.launch_ui(runtime_mode="container")

    assert started == ["container"]
    assert opened == ["http://127.0.0.1:5173/bms/"]


def test_main_reports_missing_dependency_as_error(monkeypatch, capsys) -> None:
    module = load_module()
    monkeypatch.setattr(module, "launch_ui", lambda runtime_mode="dev", surface=None: (_ for _ in ()).throw(FileNotFoundError("systemctl")))
    monkeypatch.setattr(sys, "argv", ["launch_biomodstack_ui.py", "--runtime", "dev", "--surface", "none"])

    assert module.main() == 1
    assert "ERROR: systemctl" in capsys.readouterr().err


def test_main_omits_runtime_to_allow_environment_default(monkeypatch) -> None:
    module = load_module()
    seen: list[str | None] = []
    monkeypatch.setattr(module, "launch_ui", lambda runtime_mode=None, surface=None: seen.append(runtime_mode) or {})
    monkeypatch.setattr(sys, "argv", ["launch_biomodstack_ui.py", "--surface", "none"])

    assert module.main() == 0
    assert seen == [None]
