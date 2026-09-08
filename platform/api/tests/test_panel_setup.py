"""Launcher adapters: use installer authority; never collect credentials."""
import json
from types import SimpleNamespace

import pytest
from test_biomodstack_panel import load_module


@pytest.mark.parametrize("action", ["python-plan", "python-bootstrap", "python-verify",
    "frontend-plan", "frontend-bootstrap", "frontend-verify"])
def test_prerequisites_use_existing_cli(monkeypatch, action):
    module = load_module(monkeypatch)
    assert module.build_setup_command(action) == ["bash", str(module.START_SCRIPT), action, "--json"]


@pytest.mark.parametrize("action", ["discover", "plan"])
def test_setup_is_explicitly_development(monkeypatch, action):
    module = load_module(monkeypatch)
    command = module.build_setup_command(action, models="protenix, boltz2,protenix")
    assert command[4:] == ["--runtime", "dev", "--model", "protenix", "--model", "boltz2"]


@pytest.mark.parametrize("action", ["configure", "configure-preview"])
def test_configure_requires_absolute_document_and_preserves_argv(monkeypatch, action):
    module = load_module(monkeypatch)
    with pytest.raises(ValueError):
        module.build_setup_command(action, document="relative.json")
    assert module.build_setup_command(action, document="/tmp/a ; b.json")[-2:] == ["--document", "/tmp/a ; b.json"]


@pytest.mark.parametrize("action", ["provision", "resume", "start", "sudo", None])
def test_unsafe_or_unreviewed_actions_not_exposed(monkeypatch, action):
    module = load_module(monkeypatch)
    with pytest.raises(ValueError):
        module.build_setup_command(action)


def test_recovery_and_scientific_checks_require_explicit_selection(monkeypatch):
    module = load_module(monkeypatch)
    for action in ("recover", "provision-plan", "verify"):
        with pytest.raises(ValueError):
            module.build_setup_command(action)
    assert module.build_setup_command("recover", operation="reviewed-op")[-2:] == ["--operation-id", "reviewed-op"]
    assert module.build_setup_command("verify", models="protenix")[-2:] == ["--model", "protenix"]
    assert set(module.SETUP_MUTATIONS) == {"python-bootstrap", "frontend-bootstrap", "configure", "recover"}


def test_blocked_json_and_stderr_remain_visible(monkeypatch):
    module = load_module(monkeypatch)
    captured = {}
    row = SimpleNamespace(set_subtitle=lambda text: captured.update(subtitle=text))
    buffer = SimpleNamespace(set_text=lambda text: captured.update(output=text))
    panel = SimpleNamespace(setup_status_row=row, setup_output=SimpleNamespace(get_buffer=lambda: buffer),
        _update_dev_updates_control=lambda: None, _refresh_status_once=lambda: None)
    monkeypatch.setattr(module, "show_notification", lambda *args: None)
    report = json.dumps({"status": "blocked", "errors": [{"code": "missing_prerequisite"}]})
    result = SimpleNamespace(returncode=3, stdout=report, stderr="additional diagnostics")
    module.BioModStackPanel._finish_service_action(panel, "Setup: python-verify", result, None)
    assert "blocked (exit 3)" in captured["subtitle"]
    assert report in captured["output"]
    assert "additional diagnostics" in captured["output"]


def test_password_surface_is_removed(monkeypatch):
    module = load_module(monkeypatch)
    source = module.PROJECT_ROOT.joinpath("biomodstack_panel.py").read_text()
    for retired in ("Admin Password", "PasswordEntry", "cached_sudo_password", "BMS_SUDO_PASSWORD", "_build_privilege_section"):
        assert retired not in source
