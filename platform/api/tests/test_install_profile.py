from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]
for root in (REPO_ROOT, API_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import biomodstack_runtime_profile as runtime_profile  # noqa: E402
import paths as api_paths  # noqa: E402


EXPECTED_CORS_ORIGINS = ",".join(
    [
        "http://127.0.0.1",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:18080",
        "http://localhost",
        "https://localhost",
        "http://localhost:5173",
        "http://localhost:18080",
        "https://localhost:5173",
        "https://127.0.0.1",
    ]
)


def test_save_install_profile_writes_compatibility_exports(
    tmp_path: Path, monkeypatch
) -> None:
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
            "dev_web_host_port": 5179,
            "api_host_port": 9000,
            "web_host_port": 5174,
        }
    )

    resolved_data_root = (home_dir / "BioModStackData").resolve()
    assert saved["data_root"] == str(resolved_data_root)

    env_sh = runtime_profile.get_compat_env_path()
    env_text = env_sh.read_text(encoding="utf-8")
    assert f'export BMS_DATA="${{BMS_DATA:-{resolved_data_root}}}"' in env_text
    assert (
        f'export BMS_STATE_DIR="${{BMS_STATE_DIR:-{resolved_data_root}}}"' in env_text
    )
    assert 'export BMS_DEV_WEB_HOST_PORT="${BMS_DEV_WEB_HOST_PORT:-5179}"' in env_text
    assert 'export BMS_API_HOST_PORT="${BMS_API_HOST_PORT:-9000}"' in env_text
    assert 'export BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-5174}"' in env_text
    assert (
        f'export CORS_ORIGINS="${{CORS_ORIGINS:-{EXPECTED_CORS_ORIGINS}}}"' in env_text
    )
    assert (
        'export BMS_WORKFLOW_ADAPTER_URL="${BMS_WORKFLOW_ADAPTER_URL:-http://127.0.0.1:8001}"'
        in env_text
    )

    core_runtime_env = runtime_profile.get_core_runtime_env_path()
    core_runtime_text = core_runtime_env.read_text(encoding="utf-8")
    assert f"BMS_STATE_DIR={resolved_data_root}" in core_runtime_text
    assert f"BMS_DATA={resolved_data_root}" in core_runtime_text
    assert f"BMS_INPUTS={resolved_data_root / 'inputs'}" in core_runtime_text
    assert f"BMS_DB_PATH={resolved_data_root / 'biomodstack.db'}" in core_runtime_text
    assert f"BMS_CONTAINER_DIR={resolved_data_root / 'apptainer'}" in core_runtime_text
    assert f"BMS_WEIGHTS={resolved_data_root / 'weights'}" in core_runtime_text
    assert (
        f"BMS_COLABFOLD_DB={resolved_data_root / 'colabfold_db'}" in core_runtime_text
    )
    assert f"BMS_MSA_CACHE={resolved_data_root / 'msa_cache'}" in core_runtime_text
    assert (
        f"BMS_SABDAB_CACHE={resolved_data_root / 'sabdab_cache'}" in core_runtime_text
    )
    assert f"BMS_WORK={resolved_data_root / 'work'}" in core_runtime_text
    assert "BMS_CONTAINER_STATE_PATH=/var/lib/biomodstack-custom" in core_runtime_text
    assert (
        "BMS_INPUTS_CONTAINER_PATH=/var/lib/biomodstack-custom/inputs"
        in core_runtime_text
    )
    assert (
        "BMS_DB_CONTAINER_PATH=/var/lib/biomodstack-custom/biomodstack.db"
        in core_runtime_text
    )
    assert "BMS_DEV_WEB_HOST_PORT=5179" in core_runtime_text
    assert f"CORS_ORIGINS={EXPECTED_CORS_ORIGINS}" in core_runtime_text
    assert "BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:8001" in core_runtime_text


def test_resolve_runtime_paths_defaults_include_cordova_and_loopback_cors_origins(
    tmp_path: Path, monkeypatch
) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    project_root = tmp_path / "repo"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    project_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    resolved = runtime_profile.resolve_runtime_paths(
        project_root=project_root, profile={}
    )

    assert resolved["cors_origins"] == EXPECTED_CORS_ORIGINS.split(",")
    assert resolved["dev_web_host_port"] == 5173
    assert resolved["web_host_port"] == 18080


def test_install_profile_persists_only_supported_feature_flags(
    tmp_path: Path, monkeypatch
) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    saved = runtime_profile.save_install_profile(
        {
            "data_root": "~/BioModStackData",
            "features": {
                "bioxp": False,
                "sta" + "ts_tools": True,
                "as" + "say_db": False,
            },
        }
    )

    assert saved["features"] == {"bioxp": False}
    snapshot = runtime_profile.install_profile_snapshot()
    assert snapshot["resolved"]["features"] == {"bioxp": False}

    env_text = runtime_profile.get_compat_env_path().read_text(encoding="utf-8")
    assert 'export BMS_FEATURE_BIOXP="${BMS_FEATURE_BIOXP:-0}"' in env_text
    assert "BMS_FEATURE_STA" + "TS_TOOLS" not in env_text
    assert "BMS_FEATURE_AS" + "SAY_DB" not in env_text

    core_runtime_text = runtime_profile.get_core_runtime_env_path().read_text(
        encoding="utf-8"
    )
    assert "BMS_FEATURE_BIOXP=0" in core_runtime_text
    assert "BMS_FEATURE_STA" + "TS_TOOLS" not in core_runtime_text
    assert "BMS_FEATURE_AS" + "SAY_DB" not in core_runtime_text


def test_feature_env_override_wins_over_install_profile(
    tmp_path: Path, monkeypatch
) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("BMS_FEATURE_BIOXP", "1")

    resolved = runtime_profile.resolve_runtime_paths(
        profile={
            "features": {
                "bioxp": False,
                "sta" + "ts_tools": False,
                "as" + "say_db": False,
            }
        }
    )

    assert resolved["features"] == {"bioxp": True}
    assert (
        runtime_profile.install_feature_enabled(
            "bioxp", profile={"features": {"bioxp": False}}
        )
        is True
    )


def test_api_paths_prefer_install_profile_when_env_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
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
    assert (
        reloaded_paths.get_db_path()
        == (home_dir / "operator-state" / "shared.db").resolve()
    )


def test_resolve_runtime_paths_puts_work_and_results_under_install_data_root(
    tmp_path: Path, monkeypatch
) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    project_root = tmp_path / "repo-on-os-drive"
    nvme_root = tmp_path / "BMS-4TB-NVME"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    project_root.mkdir()
    nvme_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    for name in (
        "BMS_DATA",
        "BMS_INPUTS",
        "BMS_DB_PATH",
        "BMS_CONTAINER_DIR",
        "BMS_WEIGHTS",
        "BMS_COLABFOLD_DB",
        "BMS_MSA_CACHE",
        "BMS_SABDAB_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)

    resolved = runtime_profile.resolve_runtime_paths(
        project_root=project_root,
        profile={"data_root": str(nvme_root)},
    )

    assert resolved["data_root"] == str(nvme_root.resolve())
    assert resolved["results_dir"] == str(nvme_root.resolve() / "bms_results")
    assert resolved["work_dir"] == str(nvme_root.resolve() / "work")
    assert not str(resolved["results_dir"]).startswith(str(project_root.resolve()))
    assert not str(resolved["work_dir"]).startswith(str(project_root.resolve()))


def test_heuristic_data_root_keeps_model_and_cache_paths_with_data_root(
    tmp_path: Path, monkeypatch
) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    project_root = tmp_path / "repo-on-os-drive"
    nvme_root = tmp_path / "BMS-4TB-NVME"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    project_root.mkdir()
    (nvme_root / "bms_results").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(
        runtime_profile,
        "_candidate_data_roots",
        lambda: [nvme_root, home_dir / ".biomodstack"],
    )
    for name in (
        "BMS_DATA",
        "BMS_WEIGHTS",
        "BMS_COLABFOLD_DB",
        "BMS_MSA_CACHE",
        "BMS_SABDAB_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)

    resolved = runtime_profile.resolve_runtime_paths(
        project_root=project_root, profile={}
    )

    assert resolved["data_root"] == str(nvme_root.resolve())
    assert resolved["weights_root"] == str(nvme_root.resolve() / "weights")
    assert resolved["colabfold_db"] == str(nvme_root.resolve() / "colabfold_db")
    assert resolved["msa_cache_dir"] == str(nvme_root.resolve() / "msa_cache")
    assert resolved["sabdab_cache_dir"] == str(nvme_root.resolve() / "sabdab_cache")
