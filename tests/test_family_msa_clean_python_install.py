"""Opt-in offline clean-root API dependency installation; no deployed state."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_api_installs_alignment_core_from_clean_source(tmp_path):
    cache = os.environ.get('BMS_TEST_UV_OFFLINE_CACHE')
    if not cache:
        pytest.skip('set BMS_TEST_UV_OFFLINE_CACHE to a populated uv cache')
    api = tmp_path / 'platform/api'
    api.mkdir(parents=True)
    for name in ('pyproject.toml', 'uv.lock', 'README.md'):
        shutil.copy2(ROOT / 'platform/api' / name, api / name)
    shutil.copytree(ROOT / 'packages/bms-alignment-core', tmp_path / 'packages/bms-alignment-core',
                    ignore=shutil.ignore_patterns('__pycache__', '.venv', '.pytest_cache'))
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path / 'home'),
           'UV_CACHE_DIR': cache, 'UV_PROJECT_ENVIRONMENT': str(tmp_path / 'venv')}
    subprocess.run(['uv', 'sync', '--frozen', '--offline', '--no-dev', '--no-config'],
                   cwd=api, env=env, check=True, timeout=300)
    result = subprocess.run([str(tmp_path / 'venv/bin/python'), '-I', '-c',
        'import bms_alignment; print(bms_alignment.__file__)'], cwd=tmp_path,
        env=env, check=True, capture_output=True, text=True)
    assert Path(result.stdout.strip()).is_relative_to(tmp_path / 'venv')
