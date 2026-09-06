from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import paths
from biomodstack_runtime_profile import get_install_profile_path, resolve_runtime_paths


@pytest.fixture
def installed_profile(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("BMS_") or name == "DATABASE_URL":
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    profile = {
        "data_root": str(tmp_path / "operator data"),
        "results_dir": str(tmp_path / "separate results"),
        "inputs_dir": str(tmp_path / "separate inputs"),
    }
    profile_path = get_install_profile_path()
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    return profile


def test_results_helper_honors_actual_install_profile(installed_profile):
    expected = Path(installed_profile["results_dir"])
    assert paths.get_results_dir() == expected
    assert paths.get_results_dir() == Path(resolve_runtime_paths()["results_dir"])
    assert paths.resolve_allowed_path("bms_results/job/model.cif") == expected / "job/model.cif"
    assert paths.to_allowed_relative(expected / "job/model.cif") == "bms_results/job/model.cif"
    assert paths.get_inputs_dir() == Path(installed_profile["inputs_dir"])
    with pytest.raises(ValueError, match="escapes"):
        paths.resolve_allowed_path("bms_results/../outside")


@pytest.mark.parametrize("override", ["BMS_RESULTS_DIR", "BMS_RESULTS_ROOT"])
def test_results_environment_overrides_profile(installed_profile, monkeypatch, tmp_path, override):
    expected = tmp_path / "environment results"
    monkeypatch.setenv(override, str(expected))
    assert paths.get_results_dir() == expected


def test_results_dir_environment_precedes_legacy_alias(installed_profile, monkeypatch, tmp_path):
    monkeypatch.setenv("BMS_RESULTS_ROOT", str(tmp_path / "legacy"))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(tmp_path / "primary"))
    assert paths.get_results_dir() == tmp_path / "primary"


def test_results_default_remains_below_data_root(installed_profile):
    installed_profile.pop("results_dir")
    get_install_profile_path().write_text(json.dumps(installed_profile), encoding="utf-8")
    assert paths.get_results_dir() == Path(installed_profile["data_root"]) / "bms_results"
