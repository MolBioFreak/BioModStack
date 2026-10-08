from __future__ import annotations

import importlib
import json
import threading
import time
from pathlib import Path

from services import gpu_config


def _point_config_at(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    state_path = tmp_path / "state" / "scheduler" / "gpu_config.json"
    legacy_path = tmp_path / "checkout" / ".gpu_config.json"
    monkeypatch.setattr(gpu_config, "GPU_CONFIG_PATH", state_path)
    monkeypatch.setattr(gpu_config, "GPU_CONFIG_LOCK_PATH", state_path.with_suffix(".lock"))
    monkeypatch.setattr(gpu_config, "LEGACY_GPU_CONFIG_PATH", legacy_path)
    return state_path, legacy_path


def test_scheduler_config_path_uses_explicit_persistent_state_dir(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "shared-scheduler-state"
    monkeypatch.setenv("BMS_SCHEDULER_STATE_DIR", str(state_dir))

    reloaded = importlib.reload(gpu_config)

    assert reloaded.GPU_CONFIG_PATH == (state_dir / "gpu_config.json").resolve()
    assert reloaded.GPU_CONFIG_PATH.parent != reloaded.PROJECT_ROOT


def test_read_migrates_legacy_checkout_config_once(monkeypatch, tmp_path: Path) -> None:
    state_path, legacy_path = _point_config_at(monkeypatch, tmp_path)
    legacy_path.parent.mkdir(parents=True)
    legacy_config = gpu_config.get_default_config()
    legacy_config["global"]["cooldown_ms"] = 9876
    legacy_path.write_text(json.dumps(legacy_config), encoding="utf-8")

    migrated = gpu_config.read_scheduler_config()

    assert migrated["global"]["cooldown_ms"] == 9876
    assert json.loads(state_path.read_text(encoding="utf-8"))["global"]["cooldown_ms"] == 9876

    legacy_config["global"]["cooldown_ms"] = 123
    legacy_path.write_text(json.dumps(legacy_config), encoding="utf-8")
    assert gpu_config.read_scheduler_config()["global"]["cooldown_ms"] == 9876


def test_transactional_mutations_preserve_concurrent_updates(monkeypatch, tmp_path: Path) -> None:
    _point_config_at(monkeypatch, tmp_path)
    assert gpu_config.write_scheduler_config(gpu_config.get_default_config())
    barrier = threading.Barrier(2)

    def set_override() -> None:
        barrier.wait()

        def mutate(config: dict) -> None:
            time.sleep(0.05)
            config["overrides"]["7"] = {"disabled": True}

        gpu_config.mutate_scheduler_config(mutate)

    def set_workflow_pin() -> None:
        barrier.wait()

        def mutate(config: dict) -> None:
            time.sleep(0.05)
            config["workflow_pins"]["boltz"] = 3

        gpu_config.mutate_scheduler_config(mutate)

    threads = [threading.Thread(target=set_override), threading.Thread(target=set_workflow_pin)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    persisted = gpu_config.read_scheduler_config()
    assert persisted["overrides"]["7"]["disabled"] is True
    assert persisted["workflow_pins"]["boltz"] == 3
