from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]
for root in (REPO_ROOT, API_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import biomodstack_runtime_profile as runtime_profile
import paths as api_paths


def test_save_install_profile_writes_compatibility_exports(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    saved = runtime_profile.save_install_profile(
        {
            "data_root": "~/BioModStackData",
            "container_state_path": "/var/lib/biomodstack-custom",
            "api_host_port": 9000,
            "web_host_port": 5174,
        }
    )

    resolved_data_root = (home_dir / "BioModStackData").resolve()
    assert saved["data_root"] == str(resolved_data_root)

    env_sh = runtime_profile.get_compat_env_path()
    env_text = env_sh.read_text(encoding="utf-8")
    assert f'export BMS_DATA="${{BMS_DATA:-{resolved_data_root}}}"' in env_text
    assert f'export BMS_STATE_DIR="${{BMS_STATE_DIR:-{resolved_data_root}}}"' in env_text
    assert 'export BMS_API_HOST_PORT="${BMS_API_HOST_PORT:-9000}"' in env_text
    assert 'export BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-5174}"' in env_text
    assert 'export BMS_WORKFLOW_ADAPTER_URL="${BMS_WORKFLOW_ADAPTER_URL:-http://127.0.0.1:8001}"' in env_text

    core_runtime_env = runtime_profile.get_core_runtime_env_path()
    core_runtime_text = core_runtime_env.read_text(encoding="utf-8")
    assert f"BMS_STATE_DIR={resolved_data_root}" in core_runtime_text
    assert "BMS_CONTAINER_STATE_PATH=/var/lib/biomodstack-custom" in core_runtime_text
    assert "BMS_INPUTS_CONTAINER_PATH=/var/lib/biomodstack-custom/inputs" in core_runtime_text
    assert "BMS_DB_CONTAINER_PATH=/var/lib/biomodstack-custom/biomodstack.db" in core_runtime_text
    assert "BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:8001" in core_runtime_text


def test_api_paths_prefer_install_profile_when_env_is_missing(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("BMS_DATA", raising=False)
    monkeypatch.delenv("BMS_INPUTS", raising=False)
    monkeypatch.delenv("BMS_DB_PATH", raising=False)

    runtime_profile.save_install_profile(
        {
            "data_root": "~/operator-state",
            "inputs_dir": "~/operator-inputs",
            "db_path": "~/operator-state/shared.db",
        }
    )

    reloaded_paths = importlib.reload(api_paths)

    assert reloaded_paths.get_data_root() == (home_dir / "operator-state").resolve()
    assert reloaded_paths.get_inputs_dir() == (home_dir / "operator-inputs").resolve()
    assert reloaded_paths.get_db_path() == (home_dir / "operator-state" / "shared.db").resolve()