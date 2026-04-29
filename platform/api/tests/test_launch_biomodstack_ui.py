from __future__ import annotations

import importlib.util
import os
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
            "browser_url": "http://127.0.0.1:18080/bms/",
            "launch_preferences": {"default_surface": "browser", "auto_open_hosted_web_on_start": True},
        },
    )
    monkeypatch.setattr(module.webbrowser, "open_new_tab", lambda url: opened.append(url))

    module.launch_ui(runtime_mode="container", surface="none")

    assert opened == []


def test_electron_surface_fails_clearly_until_shell_exists(monkeypatch) -> None:
    module = load_module()
    started: list[str] = []
    monkeypatch.setattr(module, "electron_shell_installed", lambda: False)
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
    monkeypatch.setattr(module, "electron_shell_installed", lambda: False)
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
            "browser_url": "http://127.0.0.1:18080/bms/",
            "launch_preferences": {"default_surface": "electron", "auto_open_hosted_web_on_start": True},
        },
    )
    monkeypatch.setattr(module.webbrowser, "open_new_tab", lambda url: opened.append(url))

    module.launch_ui(runtime_mode="container")

    assert started == ["container"]
    assert opened == ["http://127.0.0.1:18080/bms/"]


def test_explicit_electron_surface_launches_shell_when_installed(monkeypatch) -> None:
    module = load_module()
    opened: list[str] = []
    started: list[str] = []
    launched: list[dict[str, object]] = []
    monkeypatch.setattr(module, "electron_shell_installed", lambda: True)
    monkeypatch.setattr(module, "start_all", lambda project_root=None, runtime_mode=None: started.append(runtime_mode or "dev"))
    monkeypatch.setattr(
        module,
        "runtime_descriptor",
        lambda project_root=None, runtime_mode=None: {
            "runtime_mode": runtime_mode or "container",
            "frontend_origin": "http://127.0.0.1:18080",
            "browser_url": "http://127.0.0.1:18080/bms/",
            "router_basename": "/bms/",
            "launch_preferences": {"default_surface": "browser", "auto_open_hosted_web_on_start": True},
        },
    )
    monkeypatch.setattr(module, "launch_electron_shell", lambda descriptor: launched.append(descriptor))
    monkeypatch.setattr(module.webbrowser, "open_new_tab", lambda url: opened.append(url))

    descriptor = module.launch_ui(runtime_mode="container", surface="electron")

    assert descriptor["browser_url"] == "http://127.0.0.1:18080/bms/"
    assert started == ["container"]
    assert opened == []
    assert launched == [descriptor]


def test_resolve_pnpm_executable_falls_back_to_nvm_when_not_on_path(tmp_path) -> None:
    module = load_module()
    home_dir = tmp_path / "home"
    pnpm_bin_dir = home_dir / ".nvm" / "versions" / "node" / "v20.19.4" / "bin"
    pnpm_bin_dir.mkdir(parents=True)
    pnpm_path = pnpm_bin_dir / "pnpm"
    pnpm_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    resolved_pnpm, resolved_env = module.resolve_pnpm_executable(
        env={"PATH": "/usr/bin:/bin"},
        home=home_dir,
    )

    assert resolved_pnpm == str(pnpm_path)
    assert resolved_env["PATH"].split(os.pathsep)[0] == str(pnpm_bin_dir)



def test_launch_electron_shell_exports_runtime_context_to_the_shell(monkeypatch, tmp_path) -> None:
    module = load_module()
    shell_dir = tmp_path / "desktop-electron"
    shell_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "ELECTRON_SHELL_DIR", shell_dir)
    monkeypatch.setattr(module, "electron_shell_installed", lambda: True)

    resolved_env = {"PATH": "/tmp/node-bin:/usr/bin"}
    monkeypatch.setattr(
        module,
        "resolve_pnpm_executable",
        lambda env=None, home=None: (
            "/tmp/node-bin/pnpm",
            {**(env or {}), **resolved_env},
        ),
    )

    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        captured["start_new_session"] = kwargs.get("start_new_session")
        return object()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)

    module.launch_electron_shell(
        {
            "runtime_mode": "container",
            "frontend_origin": "http://127.0.0.1:18080",
            "router_basename": "/bms/",
        }
    )

    env = captured["env"]
    assert captured["command"] == ["/tmp/node-bin/pnpm", "start"]
    assert captured["cwd"] == shell_dir
    assert captured["start_new_session"] is True
    assert env["PATH"] == resolved_env["PATH"]
    assert env["BMS_HOME"] == str(module.REPO_ROOT)
    assert env["BMS_RUNTIME_MODE"] == "container"
    assert env["BMS_FRONTEND_ORIGIN"] == "http://127.0.0.1:18080"
    assert env["BMS_ROUTER_BASENAME"] == "/bms/"


def test_electron_shell_installed_requires_platform_binary(monkeypatch, tmp_path) -> None:
    module = load_module()
    shell_dir = tmp_path / "desktop-electron"
    electron_dist = shell_dir / "node_modules" / "electron" / "dist"
    electron_dist.mkdir(parents=True)
    (shell_dir / "package.json").write_text("{}")
    monkeypatch.setattr(module, "ELECTRON_SHELL_DIR", shell_dir)
    monkeypatch.setattr(module.sys, "platform", "linux")

    assert module.electron_shell_installed() is False

    (electron_dist / "electron").write_text("")
    assert module.electron_shell_installed() is True


def test_stored_electron_preference_launches_shell_when_installed(monkeypatch) -> None:
    module = load_module()
    opened: list[str] = []
    started: list[str] = []
    launched: list[dict[str, object]] = []
    monkeypatch.setattr(module, "electron_shell_installed", lambda: True)
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
            "runtime_mode": runtime_mode or "container",
            "frontend_origin": "http://127.0.0.1:18080",
            "browser_url": "http://127.0.0.1:18080/bms/",
            "router_basename": "/bms/",
            "launch_preferences": {"default_surface": "electron", "auto_open_hosted_web_on_start": True},
        },
    )
    monkeypatch.setattr(module, "launch_electron_shell", lambda descriptor: launched.append(descriptor))
    monkeypatch.setattr(module.webbrowser, "open_new_tab", lambda url: opened.append(url))

    descriptor = module.launch_ui(runtime_mode="container")

    assert descriptor["browser_url"] == "http://127.0.0.1:18080/bms/"
    assert started == ["container"]
    assert opened == []
    assert launched == [descriptor]


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
