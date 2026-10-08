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


@pytest.mark.parametrize('action', ['discover', 'plan'])
@pytest.mark.parametrize('shell', [False, True])
def test_real_cli_read_only_json(isolated, action, shell):
    before = snapshot(isolated)
    command = ([str(ROOT / 'start_ui.sh')] if shell else
               [sys.executable, '-B', str(ROOT / 'scripts/manage_desktop_services.py')])
    result = subprocess.run(command + [action, '--json', '--model', 'frustrampnn'],
                            text=True, capture_output=True, check=False)
    assert result.returncode == 3, result.stderr
    report = json.loads(result.stdout)
    assert report['schema_version'] == 'bms.bootstrap.v1'
    assert report['action'] == action
    assert report['ready'] is False
    assert report['status'] == 'blocked'
    assert report['read_only'] is True
    assert not any(report['effects'].values())
    assert 'acquisition_unavailable' in {b['code'] for b in report['blockers']}
    assert report['observations']['storage'][0]['required_peak_bytes'] is None
    assert snapshot(isolated) == before


def test_registry_authority_reused(isolated):
    from model_registry import model_runtime_dependencies
    report = bootstrap.bootstrap_report('plan', project_root=ROOT, models=('protenix',))
    assert [(d['kind'], d['relative_path']) for d in report['dependencies']] == [
        (r.kind, r.relative_path) for r in model_runtime_dependencies('protenix')]
    assert report['plan']['executable'] is False
    assert all(d['qualification'] == 'not_checked' for d in report['dependencies'])


@pytest.mark.parametrize('raw', ['{broken', '[]', '{"unknown": 1}'])
def test_invalid_profile_is_not_silently_ready(isolated, raw):
    path = bootstrap.get_install_profile_path()
    path.parent.mkdir()
    path.write_text(raw)
    before = snapshot(isolated)
    report = bootstrap.bootstrap_report('discover', project_root=ROOT)
    assert 'profile_invalid' in {b['code'] for b in report['blockers']}
    assert 'storage' not in report['observations']
    assert snapshot(isolated) == before


def test_missing_tools_disk_and_privilege_hints(isolated, monkeypatch):
    monkeypatch.setattr(bootstrap.shutil, 'which', lambda _: None)
    monkeypatch.setattr(bootstrap.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0, total=10))
    monkeypatch.setattr(bootstrap.os, 'access', lambda *_: False)
    report = bootstrap.bootstrap_report('plan', project_root=ROOT)
    assert {'tool_missing', 'disk_full', 'storage_not_writable'} <= {b['code'] for b in report['blockers']}
    assert report['ready'] is False


def test_unknown_model_fails_closed(isolated):
    report = bootstrap.bootstrap_report('plan', project_root=ROOT, models=('not-a-model',))
    assert 'dependency_closure_unavailable' in {b['code'] for b in report['blockers']}
    assert report['dependencies'] == []


def test_human_output_and_no_commands_or_writes(isolated, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('read-only discovery attempted mutation or external command')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    report = bootstrap.bootstrap_report('plan', project_root=ROOT)
    assert 'BLOCKED [acquisition_unavailable]' in bootstrap.render_report(report)
    assert 'bytes free; required peak unknown' in bootstrap.render_report(report)


def test_invalid_runtime_environment_json(isolated, monkeypatch):
    monkeypatch.setenv('BMS_RUNTIME_MODE', 'bogus')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/manage_desktop_services.py'),
                             'discover', '--json'], capture_output=True, text=True)
    assert result.returncode == 3
    assert 'runtime_invalid' in {b['code'] for b in json.loads(result.stdout)['blockers']}
