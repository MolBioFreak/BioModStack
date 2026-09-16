"""Native launch command wiring; tiny interpreters here are test-only adapters.

Real prerequisite install/import/receipt and service readiness acceptance are
separate. These tests prove no unmanaged uv fallback or skipped migration.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "source with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "platform/api").mkdir(parents=True)
    for name in ("run_biomodstack_api.sh", "run_biomodstack_mobile_update_publisher.sh",
                 "run_biomodstack_telemetry.sh", "python_runtime_guard.sh", "configuration_read_guard.sh"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    (root / "biomodstack_configuration.py").write_text(
        "def configuration_identity(): return None\ndef assert_configuration_readable(): pass\n")
    (root / "biomodstack_python_prerequisites.py").write_text(
        "import os\ndef resolve_python_environment(root):\n"
        "    if os.environ.get('TEST_INVALID'): raise RuntimeError('managed inventory changed')\n"
        "    return {'python': os.environ['TEST_INTERPRETER']}\n")
    interpreter = tmp_path / "external dependencies/environment/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(f"#!{sys.executable}\n" +
        "import json,os,sys\n"
        "with open(os.environ['TEST_COMMAND_LOG'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if 'run_migrations.py' in sys.argv and os.environ.get('TEST_MIGRATION_FAILURE'): sys.exit(13)\n")
    interpreter.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text("#!/bin/sh\nprintf 'UV FALLBACK IS FORBIDDEN' >&2\nexit 99\n")
    (fake_bin / "uv").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BMS_", "UV_", "PYTHON", "XDG_"))}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
               PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", os.defpath),
               BMS_HOME=str(root), BMS_RUNTIME_MODE="dev", BMS_API_MODE="dev",
               BMS_CPU_POWER_STRICT="0", PYTHONDONTWRITEBYTECODE="1",
               TEST_INTERPRETER=str(interpreter), TEST_COMMAND_LOG=str(tmp_path / "commands.jsonl"))
    return root, env


def launch(fixture, name):
    root, env = fixture
    p = subprocess.run(["bash", str(root / "scripts" / name)], env=env, text=True, capture_output=True, timeout=20)
    log = Path(env["TEST_COMMAND_LOG"])
    return p, [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("script,module", [
    ("run_biomodstack_api.sh", "main:app"),
    ("run_biomodstack_mobile_update_publisher.sh", "mobile_update_publisher_app:app"),
    ("run_biomodstack_telemetry.sh", "tools.telemetry_collector"),
])
def test_managed_native_launch_uses_same_external_interpreter(fixture, script, module):
    p, commands = launch(fixture, script)
    assert p.returncode == 0, p.stderr
    assert module in commands[-1]
    assert commands[-1][:2] == ["-m", "tools.telemetry_collector" if "telemetry" in script else "uvicorn"]
    if script == "run_biomodstack_api.sh":
        assert commands[0] == ["run_migrations.py"]
    assert "UV FALLBACK" not in p.stderr


@pytest.mark.parametrize("script", ["run_biomodstack_api.sh", "run_biomodstack_mobile_update_publisher.sh", "run_biomodstack_telemetry.sh"])
def test_invalid_managed_environment_never_downgrades(fixture, script):
    fixture[1]["TEST_INVALID"] = "1"
    p, commands = launch(fixture, script)
    assert p.returncode == 78
    assert "managed inventory changed" in p.stderr
    assert not commands
    assert "UV FALLBACK" not in p.stderr


def test_migration_failure_prevents_managed_api_server(fixture):
    fixture[1]["TEST_MIGRATION_FAILURE"] = "1"
    p, commands = launch(fixture, "run_biomodstack_api.sh")
    assert p.returncode == 13
    assert commands == [["run_migrations.py"]]
