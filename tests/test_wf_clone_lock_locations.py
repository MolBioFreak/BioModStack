"""The wf-clone runtime lock carries identities; installation locations are resolved per install."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.lib.component_adapter import wf_clone_container_config
from scripts.validate_wf_clone_runtime import expand_lock_location, main, resolve_lock_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/ngs/wf_clone_validation_v1.8.4.lock.json"


@pytest.fixture
def install(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = tmp_path / "config" / "biomodstack"
    config.mkdir(parents=True)
    data = tmp_path / "srv" / "bms"
    (config / "install_profile.json").write_text(json.dumps({
        "data_root": str(data), "container_dir": str(data / "apptainer"),
        "weights_root": str(data / "weights"), "inputs_dir": str(data / "inputs")}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("BMS_HOST_DATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home, data


def test_tracked_lock_names_no_host_location():
    lock = json.loads(LOCK.read_text())
    assert lock["patched_source"]["path"].startswith("${BMS_SHARED_DATA}/")
    assert lock["containers"]["cache_dir"].startswith("${BMS_RUNTIME_HOME}/")
    assert "/home/" not in LOCK.read_text() and "/mnt/" not in LOCK.read_text()


def test_locations_resolve_to_this_installs_roots(install):
    home, data = install
    assert expand_lock_location("${BMS_SHARED_DATA}/ngs/x") == str(data / "ngs/x")
    assert expand_lock_location("${BMS_RUNTIME_HOME}/img") == str(home / ".local/share/biomodstack/runtime/img")


def test_host_data_and_xdg_override_the_profile(install, monkeypatch, tmp_path):
    monkeypatch.setenv("BMS_HOST_DATA", str(tmp_path / "host"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert expand_lock_location("${BMS_SHARED_DATA}/a") == str(tmp_path / "host/a")
    assert expand_lock_location("${BMS_RUNTIME_HOME}/b") == str(tmp_path / "xdg/biomodstack/runtime/b")


def test_without_a_profile_the_shared_root_is_the_home_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "none"))
    monkeypatch.delenv("BMS_HOST_DATA", raising=False)
    assert expand_lock_location("${BMS_SHARED_DATA}/a") == str(tmp_path / "home/.biomodstack/a")


def test_absolute_and_relative_locations_are_unchanged(tmp_path):
    lock = tmp_path / "locks" / "lock.json"
    assert resolve_lock_path(lock, "/elsewhere/source") == Path("/elsewhere/source")
    assert resolve_lock_path(lock, "../source") == (tmp_path / "source").resolve()
    assert expand_lock_location("${BMS_SHARED_DATA}x") == "${BMS_SHARED_DATA}x"


def test_cli_prints_the_resolved_location(install, capsys, monkeypatch):
    _, data = install
    monkeypatch.setattr("sys.argv", ["validate", "--lock", str(LOCK), "--resolve", "patched_source.path"])
    assert main() == 0
    assert capsys.readouterr().out.strip() == str(data / "ngs/wf-clone-validation/v1.8.4-bms.2")


def test_container_config_binds_images_under_the_resolved_cache(install, monkeypatch):
    home, _ = install
    monkeypatch.setenv("BMS_CONTAINER_BACKEND", "apptainer")
    config = wf_clone_container_config(LOCK, "flye")
    assert str(home / ".local/share/biomodstack/runtime/ont-clone-v1.8.4-bms.2") in config
