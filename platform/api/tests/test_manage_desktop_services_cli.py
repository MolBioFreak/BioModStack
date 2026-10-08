from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_module():
    script_path = REPO_ROOT / "scripts" / "manage_desktop_services.py"
    spec = importlib.util.spec_from_file_location("manage_desktop_services", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_status_json_prints_runtime_descriptor(monkeypatch, capsys) -> None:
    module = load_module()
    payload = {
        "runtime_mode": "dev",
        "frontend_url": "http://127.0.0.1:5173/",
        "supported_launch_surfaces": ["browser", "electron", "none"],
    }
    monkeypatch.setattr(module, "runtime_descriptor", lambda runtime_mode=None: payload)
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "status", "--runtime", "dev", "--json"])

    assert module.main() == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_status_plain_text_output_keeps_existing_human_readable_contract(monkeypatch, capsys) -> None:
    module = load_module()
    monkeypatch.setattr(module, "status_lines", lambda runtime_mode=None: ["API: active", "Frontend: active"])
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "status", "--runtime", "dev"])

    assert module.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "API: active",
        "Frontend: active",
        f"Frontend log: {module.FRONTEND_LOG}",
    ]


def test_status_plain_text_output_does_not_duplicate_log_lines_from_status_lines(monkeypatch, capsys) -> None:
    module = load_module()
    lines = [
        "API: active",
        "Frontend: active",
        f"API log: {module.API_LOG}",
        f"Frontend log: {module.FRONTEND_LOG}",
    ]
    monkeypatch.setattr(module, "status_lines", lambda runtime_mode=None: lines)
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "status", "--runtime", "dev"])

    assert module.main() == 0
    assert capsys.readouterr().out.splitlines() == lines


def test_status_plain_text_output_does_not_duplicate_container_log_lines_from_status_lines(monkeypatch, capsys) -> None:
    module = load_module()
    lines = [
        "Runtime: active",
        "API: ready",
        "Frontend: ready",
        f"Runtime log: {module.CORE_RUNTIME_LOG}",
    ]
    monkeypatch.setattr(module, "status_lines", lambda runtime_mode=None: lines)
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "status", "--runtime", "container"])

    assert module.main() == 0
    assert capsys.readouterr().out.splitlines() == lines


def test_json_flag_is_rejected_for_non_status_actions(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setattr(module, "start_all", lambda runtime_mode=None: (_ for _ in ()).throw(AssertionError("start_all should not run")))
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "start", "--json"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert excinfo.value.code == 2


def test_select_tailnet_requires_explicit_environment_before_side_effects(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setattr(
        module,
        "select_tailnet_environment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("selector must not run")),
    )
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "select-tailnet"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert excinfo.value.code == 2


def test_select_tailnet_prints_structured_provenance(monkeypatch, capsys) -> None:
    module = load_module()
    report = {
        "selected_environment": "development",
        "serve_root_proxy": "http://127.0.0.1:5173",
        "project_revision": "a" * 40,
    }
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        module,
        "select_tailnet_environment",
        lambda environment, project_root=None: calls.append((environment, project_root)) or report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["manage_desktop_services.py", "select-tailnet", "--environment", "development", "--json"],
    )

    assert module.main() == 0
    assert json.loads(capsys.readouterr().out) == report
    assert calls == [("development", module.REPO_ROOT)]


def test_start_action_reports_missing_dependency_as_error(monkeypatch, capsys) -> None:
    module = load_module()
    monkeypatch.setattr(module, "start_all", lambda runtime_mode=None: (_ for _ in ()).throw(FileNotFoundError("systemctl")))
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "start", "--runtime", "dev"])

    assert module.main() == 1
    assert "ERROR: systemctl" in capsys.readouterr().err


def test_notify_failures_do_not_mask_service_errors(monkeypatch, capsys) -> None:
    module = load_module()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("notify-send")))
    monkeypatch.setattr(module, "start_all", lambda runtime_mode=None: (_ for _ in ()).throw(module.ServiceManagerError("boom")))
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "start", "--runtime", "dev", "--notify"])

    assert module.main() == 1
    assert "ERROR: boom" in capsys.readouterr().err


def test_start_defaults_to_container_runtime_when_flag_omitted(monkeypatch, capsys) -> None:
    module = load_module()
    started: list[str] = []
    monkeypatch.delenv("BMS_RUNTIME_MODE", raising=False)
    monkeypatch.setattr(module, "start_all", lambda runtime_mode=None: started.append(runtime_mode or "missing"))
    monkeypatch.setattr(module, "status_lines", lambda runtime_mode=None: [f"runtime={runtime_mode}"])
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "start"])

    assert module.main() == 0
    assert started == ["container"]
    assert capsys.readouterr().out.splitlines() == ["runtime=container"]


def test_start_api_dispatches_to_api_only_service_action(monkeypatch, capsys) -> None:
    module = load_module()
    started: list[str] = []
    monkeypatch.setattr(module, "start_api", lambda runtime_mode=None: started.append(runtime_mode or "missing"))
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "start-api", "--runtime", "dev"])

    assert module.main() == 0
    assert started == ["dev"]
    assert capsys.readouterr().out.splitlines() == [f"API log: {module.API_LOG}"]


def test_stop_api_dispatches_to_api_only_service_action(monkeypatch, capsys) -> None:
    module = load_module()
    stopped: list[str] = []
    monkeypatch.setattr(module, "stop_api", lambda runtime_mode=None: stopped.append(runtime_mode or "missing"))
    monkeypatch.setattr(sys, "argv", ["manage_desktop_services.py", "stop-api", "--runtime", "container"])

    assert module.main() == 0
    assert stopped == ["container"]
    assert capsys.readouterr().out.splitlines() == ["Stopped BioModStack API"]
