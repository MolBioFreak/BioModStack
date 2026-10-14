from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTROLLER_PATH = REPO_ROOT / "scripts" / "biomodstack_core_runtime_controller.py"


def load_controller() -> ModuleType:
    spec = importlib.util.spec_from_file_location("biomodstack_core_runtime_controller", CONTROLLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module








def test_minknow_token_marker_reads_only_stat_metadata(monkeypatch) -> None:
    controller = load_controller()

    class TokenPath:
        def __init__(self) -> None:
            self.stat_calls = 0

        def stat(self):
            self.stat_calls += 1
            return SimpleNamespace(st_dev=11, st_ino=22)

        def read_text(self, *args, **kwargs):
            raise AssertionError("token contents must not be read")

        def read_bytes(self, *args, **kwargs):
            raise AssertionError("token contents must not be read")

    token_path = TokenPath()
    monkeypatch.setattr(controller, "MINKNOW_LOCAL_AUTH_TOKEN_PATH", token_path)

    assert controller.minknow_local_auth_token_marker() == (11, 22)
    assert token_path.stat_calls == 1


def test_minknow_token_rotation_requires_governed_manual_reconnect(monkeypatch, tmp_path: Path) -> None:
    controller = load_controller()
    monkeypatch.setenv("BMS_RUNTIME_SUPERVISOR_STATE_DIR", str(tmp_path))
    commands: list[list[str]] = []

    def fake_run(args, **kwargs):
        commands.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(controller, "run_command", fake_run)

    assert controller.recover_minknow_token_rotation((1, 101), (1, 202)) is False
    assert commands == []


def test_supervise_detects_token_rotation_during_initial_compose_launch(monkeypatch, tmp_path: Path) -> None:
    controller = load_controller()
    monkeypatch.setenv("BMS_RUNTIME_SUPERVISOR_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(controller, "STARTUP_GRACE_SECONDS", 60)
    monkeypatch.setattr(controller, "POLL_SECONDS", 0)
    monkeypatch.setattr(controller, "run_preflight", lambda: {"ok": True})
    monkeypatch.setattr(controller, "managed_services", lambda: ("bms-host-agent",))
    monkeypatch.setattr(
        controller,
        "compose_ps",
        lambda: {"bms-host-agent": {"State": "running", "Health": "healthy"}},
    )
    marker = {"value": (7, 101)}
    monkeypatch.setattr(controller, "minknow_local_auth_token_marker", lambda: marker["value"])
    commands: list[list[str]] = []

    def fake_run(args, **kwargs):
        commands.append(list(args))
        if list(args) == controller.compose_command("up", "-d"):
            marker["value"] = (7, 202)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def stop_after_first_poll(_seconds: float) -> None:
        setattr(controller, "_STOP_REQUESTED", True)

    monkeypatch.setattr(controller, "run_command", fake_run)
    monkeypatch.setattr(controller.time, "sleep", stop_after_first_poll)
    setattr(controller, "_STOP_REQUESTED", False)

    assert controller.supervise() == 0
    assert commands == [controller.compose_command("up", "-d")]


def test_supervise_skips_token_probe_and_recreation_when_local_token_is_disabled(monkeypatch, tmp_path: Path) -> None:
    controller = load_controller()
    monkeypatch.setenv("BMS_RUNTIME_SUPERVISOR_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MINKNOW_API_USE_LOCAL_TOKEN", "false")
    monkeypatch.setattr(controller, "STARTUP_GRACE_SECONDS", 60)
    monkeypatch.setattr(controller, "POLL_SECONDS", 0)
    monkeypatch.setattr(controller, "run_preflight", lambda: {"ok": True})
    monkeypatch.setattr(controller, "managed_services", lambda: ("bms-host-agent",))
    monkeypatch.setattr(
        controller,
        "compose_ps",
        lambda: {"bms-host-agent": {"State": "running", "Health": "healthy"}},
    )
    monkeypatch.setattr(
        controller,
        "minknow_local_auth_token_marker",
        lambda: pytest.fail("local token marker must not be probed when disabled"),
    )
    commands: list[list[str]] = []

    def fake_run(args, **kwargs):
        commands.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def stop_after_first_poll(_seconds: float) -> None:
        setattr(controller, "_STOP_REQUESTED", True)

    monkeypatch.setattr(controller, "run_command", fake_run)
    monkeypatch.setattr(controller.time, "sleep", stop_after_first_poll)
    setattr(controller, "_STOP_REQUESTED", False)

    assert controller.supervise() == 0
    assert commands == [controller.compose_command("up", "-d")]
