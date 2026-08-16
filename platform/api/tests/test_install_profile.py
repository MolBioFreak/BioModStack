from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]
for root in (REPO_ROOT, API_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import biomodstack_runtime_profile as runtime_profile
import paths as api_paths


@pytest.fixture(autouse=True)
def clear_inherited_runtime_environment(monkeypatch) -> None:
    """Install-profile tests must not inherit the operator's active runtime."""
    for name in (
        "BMS_DATA",
        "BMS_INPUTS",
        "BMS_DB_PATH",
        "BMS_CONTAINER_DIR",
        "BMS_WEIGHTS",
        "BMS_COLABFOLD_DB",
        "BMS_MSA_CACHE",
        "BMS_SABDAB_CACHE",
        "BMS_WORK",
        "BMS_STATE_DIR",
        "BMS_CONTAINER_STATE_PATH",
        "BMS_INPUTS_CONTAINER_PATH",
        "BMS_DB_CONTAINER_PATH",
        "BMS_API_HOST_PORT",
        "BMS_DEV_API_HOST_PORT",
        "BMS_DEV_WEB_HOST_PORT",
        "BMS_WEB_HOST_PORT",
        "BMS_API_IMAGE",
        "BMS_WEB_IMAGE",
        "BMS_HOST_AGENT_IMAGE",
        "BMS_CPU_POWER_IMAGE",
        "CORS_ORIGINS",
        "BMS_CORE_RUNTIME_MODE",
        "BMS_FEATURE_BIOXP",
        "BMS_FEATURE_MOLECULAR_DYNAMICS",
        "BMS_WORKFLOW_ADAPTER_URL",
        "COMPOSE_PROJECT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


EXPECTED_CORS_ORIGINS = ",".join(
    [
        "http://127.0.0.1",
        "http://127.0.0.1:18082",
        "http://127.0.0.1:18080",
        "http://localhost",
        "https://localhost",
        "http://localhost:18082",
        "http://localhost:18080",
        "https://localhost:18082",
        "https://127.0.0.1",
    ]
)


def test_authoritative_port_registry_owns_the_approved_fixed_listener_neighborhood() -> None:
    assert runtime_profile.BMS_PORT_REGISTRY == {
        "production_api": 18000,
        "development_workflow_adapter": 18001,
        "production_workflow_adapter": 18101,
        "development_api": 18002,
        "production_web": 18080,
        "production_tailnet_proxy": 18081,
        "development_web": 18082,
        "stats_web": 18180,
        "stats_api": 18181,
        "cpu_collector": 18797,
        "mk1d_host_agent": 18798,
    }
    assert len(set(runtime_profile.BMS_PORT_REGISTRY.values())) == len(runtime_profile.BMS_PORT_REGISTRY)
    assert all(18000 <= port <= 18999 for port in runtime_profile.BMS_PORT_REGISTRY.values())


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
            "dev_web_host_port": 18882,
            "api_host_port": 18000,
            "web_host_port": 18880,
            "api_image": "biomodstack/api:release-deadbee",
            "web_image": "biomodstack/web:release-deadbee",
            "host_agent_image": "biomodstack/host-agent:release-deadbee",
            "cpu_power_image": "biomodstack/cpu-power:release-deadbee",
        }
    )

    resolved_data_root = (home_dir / "BioModStackData").resolve()
    assert saved["data_root"] == str(resolved_data_root)

    env_sh = runtime_profile.get_compat_env_path()
    env_text = env_sh.read_text(encoding="utf-8")
    assert f'export BMS_DATA="${{BMS_DATA:-{resolved_data_root}}}"' in env_text
    assert f'export BMS_STATE_DIR="${{BMS_STATE_DIR:-{resolved_data_root}}}"' in env_text
    assert 'export BMS_DEV_WEB_HOST_PORT="${BMS_DEV_WEB_HOST_PORT:-18882}"' in env_text
    assert 'export BMS_API_HOST_PORT="${BMS_API_HOST_PORT:-18000}"' in env_text
    assert 'export BMS_WEB_HOST_PORT="${BMS_WEB_HOST_PORT:-18880}"' in env_text
    assert f'export CORS_ORIGINS="${{CORS_ORIGINS:-{EXPECTED_CORS_ORIGINS}}}"' in env_text
    assert 'export BMS_WORKFLOW_ADAPTER_LANE="${BMS_WORKFLOW_ADAPTER_LANE:-production}"' in env_text
    assert 'export BMS_WORKFLOW_ADAPTER_URL="${BMS_WORKFLOW_ADAPTER_URL:-http://127.0.0.1:18101}"' in env_text

    core_runtime_env = runtime_profile.get_core_runtime_env_path()
    core_runtime_text = core_runtime_env.read_text(encoding="utf-8")
    assert f"BMS_STATE_DIR={resolved_data_root}" in core_runtime_text
    assert f"BMS_DATA={resolved_data_root}" in core_runtime_text
    assert f"BMS_INPUTS={resolved_data_root / 'inputs'}" in core_runtime_text
    assert f"BMS_DB_PATH={resolved_data_root / 'biomodstack.db'}" in core_runtime_text
    assert f"BMS_CONTAINER_DIR={resolved_data_root / 'apptainer'}" in core_runtime_text
    assert f"BMS_WEIGHTS={resolved_data_root / 'weights'}" in core_runtime_text
    assert f"BMS_COLABFOLD_DB={resolved_data_root / 'colabfold_db'}" in core_runtime_text
    assert f"BMS_MSA_CACHE={resolved_data_root / 'msa_cache'}" in core_runtime_text
    assert f"BMS_SABDAB_CACHE={resolved_data_root / 'sabdab_cache'}" in core_runtime_text
    assert f"BMS_WORK={resolved_data_root / 'work'}" in core_runtime_text
    assert "BMS_CONTAINER_STATE_PATH=/var/lib/biomodstack-custom" in core_runtime_text
    assert "BMS_INPUTS_CONTAINER_PATH=/var/lib/biomodstack-custom/inputs" in core_runtime_text
    assert "BMS_DB_CONTAINER_PATH=/var/lib/biomodstack-custom/biomodstack.db" in core_runtime_text
    assert "BMS_DEV_WEB_HOST_PORT=18882" in core_runtime_text
    assert f"CORS_ORIGINS={EXPECTED_CORS_ORIGINS}" in core_runtime_text
    assert "BMS_WORKFLOW_ADAPTER_LANE=production" in core_runtime_text
    assert "BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18101" in core_runtime_text
    assert "BMS_API_IMAGE=biomodstack/api:release-deadbee" in core_runtime_text
    assert "BMS_WEB_IMAGE=biomodstack/web:release-deadbee" in core_runtime_text
    assert "BMS_HOST_AGENT_IMAGE=biomodstack/host-agent:release-deadbee" in core_runtime_text
    assert "BMS_CPU_POWER_IMAGE=biomodstack/cpu-power:release-deadbee" in core_runtime_text
    assert core_runtime_env.stat().st_mode & 0o777 == 0o600


def test_resaving_install_profile_preserves_immutable_image_selectors(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    runtime_profile.save_install_profile(
        {
            "data_root": "~/BioModStackData",
            "api_image": "biomodstack/api:release-a229e2f",
            "web_image": "biomodstack/web:release-a229e2f",
        }
    )
    updated = runtime_profile.load_install_profile()
    updated["dev_api_host_port"] = 18002
    runtime_profile.save_install_profile(updated)

    persisted = runtime_profile.load_install_profile()
    assert persisted["api_image"] == "biomodstack/api:release-a229e2f"
    assert persisted["web_image"] == "biomodstack/web:release-a229e2f"
    core_runtime_text = runtime_profile.get_core_runtime_env_path().read_text(encoding="utf-8")
    assert "BMS_API_IMAGE=biomodstack/api:release-a229e2f" in core_runtime_text
    assert "BMS_WEB_IMAGE=biomodstack/web:release-a229e2f" in core_runtime_text


def test_export_preserves_deployment_only_core_runtime_settings(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    core_env = runtime_profile.get_core_runtime_env_path()
    core_env.parent.mkdir(parents=True)
    core_env.write_text(
        "BMS_API_IMAGE=biomodstack/api:release-a229e2f\n"
        "BMS_BIOXP_MUTATIONS_ENABLED=1\n"
        "BMS_CM_TRUSTED_PROXY_SECRET=do-not-drop\n",
        encoding="utf-8",
    )

    runtime_profile.save_install_profile({"data_root": "~/BioModStackData", "dev_api_host_port": 18002})

    core_runtime_text = core_env.read_text(encoding="utf-8")
    assert "BMS_API_IMAGE=biomodstack/api:release-a229e2f" in core_runtime_text
    assert "BMS_BIOXP_MUTATIONS_ENABLED=1" in core_runtime_text
    assert "BMS_CM_TRUSTED_PROXY_SECRET=do-not-drop" in core_runtime_text
    assert "BMS_DEV_API_HOST_PORT=18002" in core_runtime_text


def test_resolve_runtime_paths_defaults_include_cordova_and_loopback_cors_origins(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    project_root = tmp_path / "repo"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    project_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    resolved = runtime_profile.resolve_runtime_paths(project_root=project_root, profile={})

    assert resolved["cors_origins"] == EXPECTED_CORS_ORIGINS.split(",")
    assert resolved["api_host_port"] == 18000
    assert resolved["dev_api_host_port"] == 18002
    assert resolved["dev_web_host_port"] == 18082
    assert resolved["web_host_port"] == 18080
    assert resolved["workflow_adapter_url"] == "http://127.0.0.1:18101"
    assert resolved["dev_data_root"] == str((home_dir / ".biomodstack-dev").resolve())
    assert resolved["dev_db_path"] == str((home_dir / ".biomodstack-dev" / "biomodstack.db").resolve())


def test_install_profile_rejects_runtime_port_collisions_before_writing(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    with pytest.raises(ValueError, match="must be distinct"):
        runtime_profile.save_install_profile({"api_host_port": 18000, "dev_api_host_port": 18000})
    with pytest.raises(ValueError, match="reserved BioModStack auxiliary port"):
        runtime_profile.save_install_profile({"web_host_port": 18001})
    assert not runtime_profile.get_install_profile_path().exists()


def test_install_profile_rejects_bms_listener_ports_outside_governed_neighborhood(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    with pytest.raises(ValueError, match="governed BioModStack port neighborhood"):
        runtime_profile.save_install_profile({"dev_web_host_port": 19000})

    assert not runtime_profile.get_install_profile_path().exists()


def test_resolve_runtime_paths_migrates_only_exact_recognized_legacy_listener_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BMS_DEV_API_HOST_PORT", raising=False)
    monkeypatch.delenv("BMS_DEV_WEB_HOST_PORT", raising=False)
    resolved = runtime_profile.resolve_runtime_paths(
        project_root=tmp_path / "repo",
        profile={
            "api_host_port": 8000,
            "dev_api_host_port": 8002,
            "dev_web_host_port": 5173,
            "workflow_adapter_url": "http://127.0.0.1:8001",
            "cors_origins": ["http://127.0.0.1:5173", "https://localhost:5173"],
        },
    )

    assert resolved["api_host_port"] == 18000
    assert resolved["dev_api_host_port"] == 18002
    assert resolved["dev_web_host_port"] == 18082
    assert resolved["workflow_adapter_url"] == "http://127.0.0.1:18101"
    assert resolved["cors_origins"] == ["http://127.0.0.1:18082", "https://localhost:18082"]


def test_install_profile_rejects_mutable_container_api_port_before_writing(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    with pytest.raises(ValueError, match="fixed.*18000"):
        runtime_profile.save_install_profile({"api_host_port": 9000})

    assert not runtime_profile.get_install_profile_path().exists()


def test_install_profile_persists_only_supported_feature_flags(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    for env_name in (
        "BMS_FEATURE_BIOXP",
        "BMS_FEATURE_MOLECULAR_DYNAMICS",
    ):
        monkeypatch.delenv(env_name, raising=False)

    saved = runtime_profile.save_install_profile(
        {
            "data_root": "~/BioModStackData",
            "features": {
                "bioxp": False,
                "sta" + "ts_tools": True,
                "as" + "say_db": False,
                "molecular_dynamics": True,
            },
        }
    )

    assert saved["features"] == {
        "bioxp": False,
        "molecular_dynamics": True,
    }
    snapshot = runtime_profile.install_profile_snapshot()
    assert snapshot["resolved"]["features"] == {
        "bioxp": False,
        "molecular_dynamics": True,
    }

    env_text = runtime_profile.get_compat_env_path().read_text(encoding="utf-8")
    assert 'export BMS_FEATURE_BIOXP="${BMS_FEATURE_BIOXP:-0}"' in env_text
    assert 'export BMS_FEATURE_MOLECULAR_DYNAMICS="${BMS_FEATURE_MOLECULAR_DYNAMICS:-1}"' in env_text
    assert "BMS_FEATURE_STA" + "TS_TOOLS" not in env_text
    assert "BMS_FEATURE_AS" + "SAY_DB" not in env_text

    core_runtime_text = runtime_profile.get_core_runtime_env_path().read_text(encoding="utf-8")
    assert "BMS_FEATURE_BIOXP=0" in core_runtime_text
    assert "BMS_FEATURE_MOLECULAR_DYNAMICS=1" in core_runtime_text
    assert "BMS_FEATURE_STA" + "TS_TOOLS" not in core_runtime_text
    assert "BMS_FEATURE_AS" + "SAY_DB" not in core_runtime_text


def test_feature_env_override_wins_over_install_profile(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    config_home = home_dir / ".config"
    home_dir.mkdir()
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    for env_name in (
        "BMS_FEATURE_BIOXP",
        "BMS_FEATURE_MOLECULAR_DYNAMICS",
    ):
        monkeypatch.delenv(env_name, raising=False)
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

    assert resolved["features"] == {
        "bioxp": True,
        "molecular_dynamics": False,
    }
    assert runtime_profile.install_feature_enabled("bioxp", profile={"features": {"bioxp": False}}) is True


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


def test_resolve_runtime_paths_puts_work_and_results_under_install_data_root(tmp_path: Path, monkeypatch) -> None:
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


def test_heuristic_data_root_keeps_model_and_cache_paths_with_data_root(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(runtime_profile, "_candidate_data_roots", lambda: [nvme_root, home_dir / ".biomodstack"])
    for name in (
        "BMS_DATA",
        "BMS_WEIGHTS",
        "BMS_COLABFOLD_DB",
        "BMS_MSA_CACHE",
        "BMS_SABDAB_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)

    resolved = runtime_profile.resolve_runtime_paths(project_root=project_root, profile={})

    assert resolved["data_root"] == str(nvme_root.resolve())
    assert resolved["weights_root"] == str(nvme_root.resolve() / "weights")
    assert resolved["colabfold_db"] == str(nvme_root.resolve() / "colabfold_db")
    assert resolved["msa_cache_dir"] == str(nvme_root.resolve() / "msa_cache")
    assert resolved["sabdab_cache_dir"] == str(nvme_root.resolve() / "sabdab_cache")
