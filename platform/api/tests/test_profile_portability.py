"""Profile saves must never inherit a calling runtime's lane."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import biomodstack_runtime_profile as profile


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def test_partial_save_preserves_supported_and_unrelated_values(tmp_path):
    path = profile.get_install_profile_path()
    path.parent.mkdir(parents=True)
    original = {"data_root": str(tmp_path / "prod"), "dev_data_root": str(tmp_path / "dev"),
                "dev_results_dir": str(tmp_path / "dev results"), "results_dir": str(tmp_path / "results"),
                "api_image": "image@sha256:abc", "extension_setting": {"enabled": True},
                "features": {"bioxp": False, "molecular_dynamics": True}}
    path.write_text(json.dumps(original))
    profile.save_install_profile({"weights_root": str(tmp_path / "weights"), "features": {"bioxp": True}})
    saved = json.loads(path.read_text())
    for key in original.keys() - {"features"}:
        assert saved[key] == original[key]
    assert saved["features"] == {"bioxp": True, "molecular_dynamics": True}


def test_export_ignores_dev_environment_without_mutating_it(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_DATA", str(tmp_path / "ambient dev"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:////ambient/dev.db")
    monkeypatch.setenv("BMS_FEATURE_BIOXP", "1")
    profile.save_install_profile({"data_root": str(tmp_path / "production"), "features": {"bioxp": False}})
    exported = profile.get_core_runtime_env_path().read_text()
    assert f"BMS_DATA={tmp_path / 'production'}\n" in exported
    assert f"BMS_DB_PATH={tmp_path / 'production' / 'biomodstack.db'}\n" in exported
    assert "BMS_FEATURE_BIOXP=0\n" in exported
    assert profile.resolve_runtime_paths()["data_root"] == str(tmp_path / "ambient dev")


def test_api_payload_roundtrips_all_supported_profile_fields():
    from routers.system import InstallProfilePayload
    fields = set(profile._PATH_FIELDS + profile._CONFIG_FIELDS + profile._INT_FIELDS)
    fields |= {"development_project_root", "production_project_root"}
    assert fields <= set(InstallProfilePayload.model_fields)


@pytest.mark.parametrize("value", [True, 2.5])
def test_api_rejects_non_integer_cpu_budget(value):
    from routers.system import InstallProfilePayload
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        InstallProfilePayload(local_cpu_threads=value)
