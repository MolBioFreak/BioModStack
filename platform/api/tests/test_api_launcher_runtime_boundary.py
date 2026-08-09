from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
API_LAUNCHER = REPO_ROOT / "scripts" / "run_biomodstack_api.sh"
WORKFLOW_ADAPTER_LAUNCHER = REPO_ROOT / "scripts" / "run_biomodstack_workflow_adapter.sh"


def test_workflow_adapter_disables_proxy_header_client_rewriting() -> None:
    launcher = WORKFLOW_ADAPTER_LAUNCHER.read_text(encoding="utf-8")
    assert "uvicorn workflow_adapter_app:app" in launcher
    assert "--no-proxy-headers" in launcher


def test_workflow_adapter_preserves_systemd_tailnet_authority_environment() -> None:
    launcher = WORKFLOW_ADAPTER_LAUNCHER.read_text(encoding="utf-8")
    for key in (
        "BMS_BUILD_SHA",
        "BMS_TAILNET_CONTROL_SOURCE_REVISION",
        "BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS",
        "BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS",
        "BMS_WORKFLOW_ADAPTER_BIND_HOST",
    ):
        assert key in launcher
    assert launcher.count("restore_systemd_authority_environment") >= 2


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
        "export BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18001\n"
        "export BMS_DATA=/mnt/BioModStack\n"
        "export BMS_DB_PATH=/mnt/BioModStack/biomodstack.db\n",
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
        "  'uv_cache_dir': os.environ.get('UV_CACHE_DIR'),\n"
        "}))\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.pop("UV_CACHE_DIR", None)
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
        "workflow_adapter_url": "http://127.0.0.1:18001",
        "data": str(tmp_path / "dev-state"),
        "db_path": str(tmp_path / "dev-state" / "biomodstack.db"),
        "uv_cache_dir": str(home / ".cache" / "biomodstack" / "uv"),
    }


def test_biomodstack_launchers_isolate_uv_cache_from_shared_user_cache() -> None:
    expected = 'export UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/biomodstack/uv}"'

    assert expected in API_LAUNCHER.read_text(encoding="utf-8")
    assert expected in WORKFLOW_ADAPTER_LAUNCHER.read_text(encoding="utf-8")
