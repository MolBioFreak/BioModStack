from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
API_LAUNCHER = REPO_ROOT / "scripts" / "run_biomodstack_api.sh"


def test_dev_api_launcher_does_not_inherit_container_adapter_routing(tmp_path: Path) -> None:
    """The native dev API must collect host telemetry directly.

    The compatibility env file also configures the container runtime.  Sourcing it
    must not make a systemd-owned dev API proxy GPU telemetry through the optional
    container workflow adapter.
    """

    home = tmp_path / "home"
    project = tmp_path / "repo"
    api_dir = project / "platform" / "api"
    fake_bin = tmp_path / "bin"
    capture_path = tmp_path / "captured.json"
    home.joinpath(".biomodstack").mkdir(parents=True)
    home.joinpath(".config", "biomodstack").mkdir(parents=True)
    api_dir.mkdir(parents=True)
    fake_bin.mkdir()

    home.joinpath(".biomodstack", "env.sh").write_text(
        "export BMS_CORE_RUNTIME_MODE=1\n"
        "export BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:8001\n"
        "export BMS_DATA=/mnt/BioModStack\n"
        "export BMS_DB_PATH=/mnt/BioModStack/biomodstack.db\n",
        encoding="utf-8",
    )
    home.joinpath(".config", "biomodstack", "core-runtime.env").write_text(
        "BMS_ANALYTICAL_DB_PASSWORD=test-only-password\n",
        encoding="utf-8",
    )
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "pathlib.Path(os.environ['BMS_CAPTURE_PATH']).write_text(json.dumps({\n"
        "  'runtime_mode': os.environ.get('BMS_RUNTIME_MODE'),\n"
        "  'core_runtime_mode': os.environ.get('BMS_CORE_RUNTIME_MODE'),\n"
        "  'workflow_adapter_url': os.environ.get('BMS_WORKFLOW_ADAPTER_URL'),\n"
        "  'data': os.environ.get('BMS_DATA'),\n"
        "  'db_path': os.environ.get('BMS_DB_PATH'),\n"
        "  'analytical_db_password': os.environ.get('BMS_ANALYTICAL_DB_PASSWORD'),\n"
        "  'analytical_db_port': os.environ.get('BMS_ANALYTICAL_DB_PORT'),\n"
        "}))\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.pop("BMS_ANALYTICAL_DB_PASSWORD", None)
    env.pop("BMS_ANALYTICAL_DB_PORT", None)
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "BMS_HOME": str(project),
            "BMS_RUNTIME_MODE": "dev",
            "BMS_API_MODE": "dev",
            "BMS_API_RELOAD": "0",
            "BMS_CPU_POWER_STRICT": "0",
            "BMS_DATA": str(tmp_path / "dev-state"),
            "BMS_DB_PATH": str(tmp_path / "dev-state" / "biomodstack.db"),
            "BMS_CAPTURE_PATH": str(capture_path),
        }
    )
    completed = subprocess.run(
        [str(API_LAUNCHER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured == {
        "runtime_mode": "dev",
        "core_runtime_mode": "0",
        "workflow_adapter_url": None,
        "data": str(tmp_path / "dev-state"),
        "db_path": str(tmp_path / "dev-state" / "biomodstack.db"),
        "analytical_db_password": "test-only-password",
        "analytical_db_port": "55432",
    }
