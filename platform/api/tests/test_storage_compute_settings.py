from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import biomodstack_services as manager
import biomodstack_runtime_profile as profile
import biomodstack_local_resources as resources
from test_biomodstack_panel import load_module


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(resources, "detect_local_capacity", lambda: resources.LocalCapacity(32, 128 * 1024**3))
    def query_only(command, **kwargs):
        assert kwargs["timeout"] <= 3
        if command[0] == "systemctl":
            assert command[1:3] == ["--user", "show"]
            return SimpleNamespace(returncode=0, stdout="ActiveState=active\nCPUQuotaPerSecUSec=24s\nMemoryMax=103079215104\n", stderr="")
        assert command[0] == "nvidia-smi"
        return SimpleNamespace(returncode=0, stdout="Example GPU, 580.0\n", stderr="")
    monkeypatch.setattr(manager.subprocess, "run", query_only)


def test_storage_snapshot_and_save_do_not_apply_or_relocate(tmp_path):
    assert hasattr(manager, "storage_compute_settings")
    data = tmp_path / "installed data"
    data.mkdir()
    sentinel = data / "existing-result"
    sentinel.write_text("untouched")
    profile.save_install_profile({"data_root": str(data), "local_cpu_threads": 8, "local_memory_gib": 32})
    before = profile.get_install_profile_path().read_bytes()
    snapshot = manager.storage_compute_settings()
    assert snapshot["roots"]["data_root"] == str(data)
    assert snapshot["configured_cpu_threads"] == 8
    assert snapshot["default_cpu_threads"] == 26
    assert snapshot["detected_memory_gib"] == 128
    assert "Example GPU" in snapshot["cuda_status"]
    assert "24s" in snapshot["applied_limits_status"]
    assert "103079215104" in snapshot["applied_limits_status"]
    assert profile.get_install_profile_path().read_bytes() == before
    message = manager.save_storage_compute_settings({"local_cpu_threads": 12})
    assert "restart" in message.lower()
    assert sentinel.read_text() == "untouched"
    assert profile.load_install_profile()["data_root"] == str(data)


def test_stale_machine_budget_remains_editable(tmp_path):
    import json
    target = profile.get_install_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"data_root": str(tmp_path / "data"), "local_cpu_threads": 64, "local_memory_gib": 32}))
    snapshot = manager.storage_compute_settings()
    assert snapshot["configured_cpu_threads"] == 64
    assert snapshot["default_cpu_threads"] == 26
    assert snapshot["validation_error"]
    assert profile.load_install_profile()["local_cpu_threads"] == 64
    manager.save_storage_compute_settings({"local_cpu_threads": 26})
    assert not manager.storage_compute_settings()["validation_error"]


def test_independent_storage_setup_and_launcher_round_trip(tmp_path):
    from routers.system import InstallProfilePayload
    keys = {"inputs_dir", "results_dir", "db_path", "work_dir", "analysis_cache_dir",
            "colabfold_db", "msa_cache_dir", "sabdab_cache_dir", "dev_results_dir"}
    assert keys <= set(InstallProfilePayload.model_fields)
    values = {key: str(tmp_path / (key + " 20%")) for key in keys}
    payload = InstallProfilePayload(**values)
    manager.save_storage_compute_settings(payload.model_dump(exclude_none=True))
    assert values.items() <= manager.storage_compute_settings()["roots"].items()
    assert all(not Path(value).exists() for value in values.values())


class Entry:
    def __init__(self, text): self.text = text
    def get_text(self): return self.text
    def set_text(self, text): self.text = str(text)


class Label:
    def set_text(self, text): self.text = text


def test_panel_redetect_preserves_manual_entries_and_save_feedback(monkeypatch, tmp_path):
    module = load_module(monkeypatch)
    cls = module.BioModStackPanel
    assert hasattr(cls, "_on_redetect_storage_compute")
    app = cls.__new__(cls)
    app.storage_entries = {"local_cpu_threads": Entry("8"), "local_memory_gib": Entry("32"),
                           "data_root": Entry(str(tmp_path / "data"))}
    app.storage_initial = {key: value.get_text() for key, value in app.storage_entries.items()}
    app.storage_hardware_label = Label()
    app.storage_feedback = Label()
    app._on_redetect_storage_compute(None)
    assert app.storage_entries["local_cpu_threads"].get_text() == "8"
    assert not profile.get_install_profile_path().exists()
    app.storage_entries["local_cpu_threads"].set_text("12")
    app._on_save_storage_compute(None)
    assert profile.load_install_profile()["local_cpu_threads"] == 12
    assert "restart" in app.storage_feedback.text.lower()
    app.storage_entries["local_cpu_threads"].set_text("99")
    app._on_save_storage_compute(None)
    assert "32" in app.storage_feedback.text
    assert profile.load_install_profile()["local_cpu_threads"] == 12
