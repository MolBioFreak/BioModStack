"""Real relocated imports and explicit exclusion of unrelated host-prefix data."""
import json
import os
from pathlib import Path
import subprocess

import python_runtime_fixture as runtime


def test_relocated_runtime_imports_complete_stdlib(tmp_path):
    base = tmp_path / 'python'
    binary = runtime.copy_python_base(base)
    env = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
    probe = ('import ctypes,encodings,json,pathlib,sqlite3,ssl,sys; '
             'print(json.dumps({"base":sys.base_prefix,"stdlib":encodings.__file__}))')
    result = subprocess.run([str(binary), '-I', '-c', probe], env=env,
                            capture_output=True, text=True, check=True, timeout=30)
    observed = json.loads(result.stdout)
    assert Path(observed['base']) == base
    assert Path(observed['stdlib']).is_relative_to(base)
    assert {entry.name for entry in base.iterdir()} == {'bin', 'lib'}
    assert not list(base.rglob('site-packages'))
    assert not list(base.rglob('dist-packages'))


def test_os_prefix_is_never_traversed(tmp_path, monkeypatch):
    prefix = tmp_path / 'usr'
    stdlib = prefix / 'lib/python-fixture'
    stdlib.mkdir(parents=True)
    (stdlib / 'stdlib.py').write_text('# complete test source')
    for name in ('site-packages', 'dist-packages', '__pycache__'):
        (stdlib / name).mkdir()
        (stdlib / name / 'unrelated').write_bytes(b'not copied')
    (prefix / 'share').mkdir()
    (prefix / 'share/huge-unrelated-file').write_bytes(b'not Python')
    binary = prefix / 'bin/python-fixture'
    binary.parent.mkdir()
    binary.write_bytes(b'layout-only fixture, never executed')
    monkeypatch.setattr(runtime.sys, '_base_executable', str(binary))
    monkeypatch.setattr(runtime.sys, 'base_prefix', str(prefix))
    monkeypatch.setattr(runtime.sysconfig, 'get_path', lambda key: str(stdlib))
    monkeypatch.setattr(runtime.sysconfig, 'get_config_var', lambda key: None)
    base = tmp_path / 'copy'
    runtime.copy_python_base(base)
    assert not (base / 'share').exists()
    assert list(base.rglob('stdlib.py'))
    assert not list(base.rglob('unrelated'))
