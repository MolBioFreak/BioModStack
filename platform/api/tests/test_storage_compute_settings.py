from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import biomodstack_runtime_profile as profile


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def test_independent_storage_setup_and_launcher_round_trip(tmp_path):
    from routers.system import InstallProfilePayload
    keys = {"inputs_dir", "results_dir", "db_path", "work_dir", "analysis_cache_dir",
            "colabfold_db", "msa_cache_dir", "sabdab_cache_dir", "dev_results_dir"}
    assert keys <= set(InstallProfilePayload.model_fields)
    values = {key: str(tmp_path / (key + " 20%")) for key in keys}
    payload = InstallProfilePayload(**values)
    profile.save_install_profile(payload.model_dump(exclude_none=True))
    resolved = profile.resolve_runtime_paths(profile=profile.load_install_profile(), environ={})
    assert values.items() <= resolved.items()
    assert all(not Path(value).exists() for value in values.values())
