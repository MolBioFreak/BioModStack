import pytest

from services import ont_local_images


@pytest.mark.parametrize("env,installed,expected", [
    ({}, {"docker", "podman"}, "docker"),
    ({}, {"podman"}, "podman"),
    ({}, set(), "docker"),
    ({"BMS_CONTAINER_RUNTIME": "podman"}, {"docker", "podman"}, "podman"),
    ({"BMS_ONT_CONTAINER_RUNTIME": "docker", "BMS_CONTAINER_RUNTIME": "podman"}, {"docker", "podman"}, "docker"),
])
def test_one_runtime_choice_for_every_ont_signal_tool(monkeypatch, env, installed, expected):
    for name in ("BMS_ONT_CONTAINER_RUNTIME", "BMS_CONTAINER_RUNTIME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ont_local_images.shutil, "which", lambda name: f"/usr/bin/{name}" if name in installed else None)
    assert ont_local_images.configured_container_runtime() == expected


def test_signal_services_read_the_runtime_from_one_place():
    from pathlib import Path
    services = Path(ont_local_images.__file__).parent
    for name in ("ont_signal_worker.py", "ont_raw_signal.py", "ont_pod5_restore.py"):
        text = (services / name).read_text()
        assert "configured_container_runtime()" in text
        assert 'CONTAINER_RUNTIME", "podman")' not in text and '"docker").strip()' not in text
