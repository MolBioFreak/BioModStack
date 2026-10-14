"""Independent-review repros, including interpreter startup outside HOME."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from test_bootstrap_cli import ROOT, isolated, snapshot
import biomodstack_bootstrap as bootstrap
from biomodstack_runtime_profile import (
    managed_runtime_storage_paths, normalize_install_profile,
    resolve_runtime_paths, validate_install_profile_raw,
)


def cli(*args, env=None, root=ROOT, startup=True):
    return subprocess.run([sys.executable, *(['-B'] if startup else []),
                           str(root / 'scripts/manage_desktop_services.py'), *args],
                          capture_output=True, text=True, env=env)


def report(result):
    assert not result.stderr
    value = json.loads(result.stdout)
    assert result.returncode == (3 if value['blockers'] else 0)
    assert value['status'] == ('blocked' if value['blockers'] else 'completed')
    return value


def profile(raw):
    path = bootstrap.get_install_profile_path()
    path.parent.mkdir(exist_ok=True)
    path.write_text(raw)


@pytest.mark.parametrize('raw', [
    '{"features":{"bioxp":true,"misspelled_feature":true}}',
    '{"features":{"bioxp":null}}', '{"features":{"bioxp":2}}',
    '{"data_root":123,"api_host_port":true}', '{"data_root":[]}',
    '{"db_path":false}', '{"container_state_path":123}',
    '{"api_host_port":true}', '{"dev_api_host_port":18002.5}',
    '{"web_host_port":18080.0}', '{"web_host_port":0}',
    '{"web_host_port":65536}', '{"web_host_port":Infinity}',
    '{"web_host_port":1e999}', '{"web_host_port":NaN}',
    '{"local_memory_gib":1e999}', '{"local_memory_gib":NaN}',
    '{"local_memory_gib":' + '9' * 400 + '}',
    '{"cors_origins":[123]}',
])
def test_raw_profile_failures_are_json_blockers(isolated, raw):
    profile(raw)
    value = report(cli('--json', 'discover'))
    assert 'profile_invalid' in {b['code'] for b in value['blockers']}
    assert 'storage' not in value['observations']


def test_strict_boundary_is_opt_in_and_legacy_spellings_survive(isolated):
    raw = {'features': {'BioXP': 'yes', 'molecular-dynamics': 0},
           'dev_api_host_port': '8002', 'data_root': str(isolated / 'data')}
    validate_install_profile_raw(raw)
    normalized = normalize_install_profile(raw)
    assert normalized['features'] == {'bioxp': True, 'molecular_dynamics': False}
    assert normalized['dev_api_host_port'] == 18002
    # Do not silently tighten unrelated legacy consumers.
    assert normalize_install_profile({'api_host_port': True})['api_host_port'] == 1
    assert normalize_install_profile({'features': {'unknown': True}}) == {}










@pytest.fixture
def archived(isolated):
    # Archive the actual current source, not imported/precompiled modules. The
    # bounded import closure is sufficient even with no optional model packages.
    names = ['biomodstack_configuration.py', 'biomodstack_install_document.py',
             'biomodstack_python_prerequisites.py',
             'biomodstack_bootstrap.py', 'biomodstack_runtime_profile.py',
             'biomodstack_local_resources.py', 'scripts/manage_desktop_services.py',
             'start_ui.sh']
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        for name in names:
            archive.add(ROOT / name, arcname=name)
    data.seek(0)
    root = isolated / 'archived-source'
    root.mkdir()
    with tarfile.open(fileobj=data) as archive:
        archive.extractall(root, filter='data')
    return root
