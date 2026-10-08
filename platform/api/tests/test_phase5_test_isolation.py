from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_phase5_runtime_fingerprint_uses_stable_container_state() -> None:
    script = (ROOT / "scripts" / "check_phase_5.sh").read_text(encoding="utf-8")

    assert "docker ps --format '{{.ID}} {{.Names}} {{.Status}}'" not in script
    assert ".State.Health.Status" in script
    assert "ss -H -ltnp 'sport = :8002'" not in script
    assert "ss -H -ltn 'sport = :8002'" in script
    assert "run_api_pytest" in script
    assert "env -i" in script
    assert 'XDG_CONFIG_HOME="$TEST_HOME/.config"' in script


@pytest.mark.parametrize(
    "command",
    [
        ["systemctl", "--user", "status", "biomodstack-api.service"],
        ["docker", "ps"],
        ["podman", "ps"],
        ["bash", "-c", "docker compose ps"],
    ],
)
def test_default_suite_blocks_real_runtime_control_commands(command: list[str]) -> None:
    with pytest.raises(RuntimeError, match="runtime integration command blocked"):
        subprocess.run(command, check=False)


def test_default_suite_still_allows_safe_local_child_processes() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "print('isolated-child-ok')"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "isolated-child-ok"
