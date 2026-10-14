"""Bounded bootstrap contract: observe, never install or admit."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_bootstrap as bootstrap


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(('BMS_', 'XDG_')) or key in {'DATABASE_URL', 'PYTHONPATH'}:
            monkeypatch.delenv(key)
    for key, leaf in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                      ('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'),
                      ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')]:
        path = tmp_path / leaf
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv('BMS_DATA', str(tmp_path / 'storage'))
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path / 'storage' / 'inputs'))
    return tmp_path


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in root.rglob('*')}
