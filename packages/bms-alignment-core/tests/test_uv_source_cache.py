"""Exercise non-editable frozen sync with isolated source, consumer and uv cache.

Requires uv and registry access for the isolated build dependencies. Never uses
or clears the deployment cache. The control reproduces uv's default stale wheel.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.environ.get("BMS_TEST_UV_SOURCE_CACHE") != "1",
                    reason="set BMS_TEST_UV_SOURCE_CACHE=1 for isolated registry-backed uv sync")
@pytest.mark.parametrize("remove_module", [False, True], ids=["edit-and-add", "removal"])
@pytest.mark.parametrize("source_cache_key", [False, True], ids=["stale-control", "source-aware"])
def test_frozen_sync_tracks_python_sources(tmp_path, source_cache_key, remove_module):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the actual frozen-sync regression")
    source = Path(__file__).resolve().parents[1]
    package = tmp_path / "package"
    shutil.copytree(source, package, ignore=shutil.ignore_patterns(
        "__pycache__", "*.egg-info", "build", "dist", ".pytest_cache", ".venv",
    ))
    metadata = package / "pyproject.toml"
    if not source_cache_key:
        metadata.write_text(metadata.read_text().replace(
            '[tool.uv]\ncache-keys = [{ file = "pyproject.toml" }, { file = "bms_alignment/**/*.py" }]\n\n', ''
        ))
    original_metadata = metadata.read_bytes()
    module = package / "bms_alignment" / "cache_probe.py"
    added = package / "bms_alignment" / "cache_added.py"
    module.write_text("VALUE = 'before'\n")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "pyproject.toml").write_text('''[project]
name = "alignment-cache-consumer"
version = "0.0.0"
requires-python = ">=3.10"
dependencies = ["bms-alignment-core"]
[tool.uv.sources]
bms-alignment-core = { path = "../package", editable = false }
''')
    env = {k: v for k, v in os.environ.items() if not k.startswith("UV_") and k not in {
        "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
    }}
    env.update(UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_PYTHON_DOWNLOADS="never",
               UV_PROJECT_ENVIRONMENT=str(consumer / ".venv"))

    def run(*args):
        result = subprocess.run(args, cwd=consumer, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        print(result.stdout)
        assert result.returncode == 0, result.stdout
        return result.stdout

    def sync():
        run(uv, "sync", "--frozen", "--python", sys.executable)
        assert metadata.read_bytes() == original_metadata
        assert (consumer / "uv.lock").read_bytes() == lock

    def installed():
        return json.loads(run(str(consumer / ".venv/bin/python"), "-c", '''
import importlib, importlib.util, json
import bms_alignment.cache_probe as probe
spec = importlib.util.find_spec('bms_alignment.cache_added')
added = importlib.import_module('bms_alignment.cache_added').ADDED if spec else False
print(json.dumps({'value': probe.VALUE, 'added': added, 'path': probe.__file__}))
'''))

    run(uv, "lock", "--python", sys.executable)
    lock = (consumer / "uv.lock").read_bytes()
    sync()
    initial = installed()
    assert initial['value'] == 'before' and not initial['added']
    assert str(consumer / '.venv') in initial['path']
    time.sleep(1.1)  # Account for filesystem timestamp granularity.
    module.write_text("VALUE = 'after-source-edit'\n")
    added.write_text("ADDED = True\n")
    sync()
    changed = installed()
    assert changed['value'] == ('after-source-edit' if source_cache_key else 'before')
    assert changed['added'] is source_cache_key
    if not remove_module:
        return
    time.sleep(1.1)
    added.unlink()
    sync()
    removed = installed()
    assert removed['value'] == changed['value']
    if source_cache_key and removed['added']:
        assert (package / 'build/lib/bms_alignment/cache_added.py').is_file()
        pytest.xfail('uv rebuilds on removal, but setuptools retains deleted modules in build/lib')
    assert not removed['added']
