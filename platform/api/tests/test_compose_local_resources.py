"""Offline Compose resolution of local policy; never starts a container."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_local_resources as resources
import biomodstack_runtime_profile as profile

pytestmark = pytest.mark.runtime_integration
KEYS = ("BMS_LOCAL_CPU_THREADS", "BMS_LOCAL_MEMORY_BYTES")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    # Synthetic capacity is a test fixture, not a hardware default.
    monkeypatch.setattr(resources, "detect_local_capacity", lambda: resources.LocalCapacity(10, 16 * resources.GIB))
    resources.applied_local_policy.cache_clear()
    yield tmp_path
    resources.applied_local_policy.cache_clear()


def resolve(envfile, tmp_path):
    # Whitelist prevents real profile values, credentials and Compose overrides
    # from entering the resolver. Explicit envfile suppresses repository .env.
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "DOCKER_HOST": "unix:///nonexistent-bms-compose-test.sock"}
    result = subprocess.run(
        ["docker", "compose", "--project-directory", str(tmp_path),
         "--env-file", str(envfile), "-f", str(ROOT / "compose.core-runtime.yml"),
         "-p", "bms-local-policy-test", "config", "--format", "json"],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["bms-api"]["environment"]


@pytest.mark.parametrize("threads,memory_gib", [(2, 3), (7, 9)])
def test_generated_profile_values_reach_api(isolated, monkeypatch, threads, memory_gib):
    exported = profile.export_install_profile(
        {"local_cpu_threads": threads, "local_memory_gib": memory_gib,
         "data_root": str(isolated / "data")}, project_root=ROOT,
    )
    env = resolve(Path(exported["core_runtime_env_path"]), isolated)
    assert env.get(KEYS[0]) == str(threads)
    assert env.get(KEYS[1]) == str(memory_gib * resources.GIB)
    for key in KEYS:
        monkeypatch.setenv(key, env[key])
    assert resources.applied_local_policy() == resources.LocalCapacity(threads, memory_gib * resources.GIB)


def test_undefined_values_do_not_inject_blank_or_hardware_defaults(isolated):
    envfile = isolated / "empty.env"
    envfile.write_text(f"BMS_STATE_DIR={isolated / 'data'}\n")
    env = resolve(envfile, isolated)
    # Compose may retain an undefined key as JSON null; it must never become
    # an assigned empty string (which applied_local_policy cannot parse).
    for key in KEYS:
        assert env.get(key) is None
    assert resources.applied_local_policy() == resources.LocalCapacity(8, 12 * resources.GIB)
