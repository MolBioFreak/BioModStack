from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_runtime_profile as profile  # noqa: E402
import biomodstack_services as services  # noqa: E402


def _config(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    for key in profile.HOST_SELECTOR_KEYS:
        monkeypatch.delenv(key, raising=False)
    return config / "biomodstack"


def test_recorded_selectors_fill_only_unset_values(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path, monkeypatch)
    profile.record_host_selectors({
        "BMS_PYTHON_ROOT": "/opt/bns/python", "BMS_CONTAINER_RUNTIME": "podman",
        "BMS_API_IMAGE": "not-a-selector", "BMS_ONT_CONTAINER_RUNTIME": "",
    })
    environ = {"BMS_CONTAINER_RUNTIME": "docker"}
    applied = profile.apply_host_selectors(environ)
    assert applied == {"BMS_PYTHON_ROOT": "/opt/bns/python"}
    assert environ == {"BMS_CONTAINER_RUNTIME": "docker", "BMS_PYTHON_ROOT": "/opt/bns/python"}
    text = profile.get_host_selectors_path().read_text(encoding="utf-8")
    assert "BMS_API_IMAGE" not in text and "BMS_ONT_CONTAINER_RUNTIME" not in text
    assert profile.get_host_selectors_path().stat().st_mode & 0o777 == 0o600


def test_development_only_install_defaults_to_dev_lane(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, monkeypatch)
    assert services.resolve_runtime_mode() == services.CONTAINER_RUNTIME_MODE
    config.mkdir(parents=True)
    (config / "install_profile.json").write_text(json.dumps({"core_runtime_mode": False}))
    assert services.resolve_runtime_mode() == services.DEV_RUNTIME_MODE
    monkeypatch.setenv("BMS_RUNTIME_MODE", "container")
    assert services.resolve_runtime_mode() == services.CONTAINER_RUNTIME_MODE


def test_units_regenerated_from_a_plain_shell_keep_recorded_container_client(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path, monkeypatch)
    profile.record_host_selectors({"BMS_CONTAINER_RUNTIME": "podman", "BMS_ONT_CONTAINER_RUNTIME": "podman"})
    units = services.install_user_units(
        project_root=tmp_path / "repo", systemd_dir=tmp_path / "units", runtime_mode="dev",
    )
    api = next(path for path in units if path.name == services.API_SERVICE).read_text(encoding="utf-8")
    assert "Environment=BMS_ONT_CONTAINER_RUNTIME=podman" in api
    assert "Environment=BMS_CONTAINER_RUNTIME=podman" in api
