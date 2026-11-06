"""BNS service rendering preserves optional non-secret publication selection.

Ported from eb5f23f; trimmed-product execution fixtures are intentionally absent.
No services, container execution or remote transport.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_services as manager
from biomodstack_runtime_profile import asset_publication_environment, get_install_profile_path

KEY = 'BMS_HF_WEIGHT_ARCHIVE'
SELECTOR = 'a' * 64 + ':3606773760'


@pytest.fixture
def installation(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('BMS_') or key in {'DATABASE_URL', 'HF_TOKEN', 'HF_HOME'}:
            monkeypatch.delenv(key)
    for key, leaf in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                      ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache')]:
        monkeypatch.setenv(key, str(tmp_path / leaf))
    profile = get_install_profile_path()
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({'data_root': str(tmp_path / 'data'),
                                  'container_dir': str(tmp_path / 'images')}))
    owner = profile.parent / 'hf-assets.env'
    owner.write_text(f'{KEY}="{SELECTOR}"\nBMS_HF_TOKEN=DO_NOT_FORWARD\nHF_TOKEN=DO_NOT_FORWARD\n')
    return owner


def environment(unit):
    result = {}
    for line in unit.splitlines():
        if line.strip().startswith('Environment='):
            for assignment in shlex.split(line.strip().split('=', 1)[1]):
                key, value = assignment.replace('%%', '%').split('=', 1)
                result[key] = value
    return result


@pytest.mark.parametrize('mode,name', [
    ('dev', manager.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE),
    ('container', manager.PRODUCTION_WORKFLOW_ADAPTER_SERVICE),
])
def test_shared_owner_rendering_and_explicit_precedence(installation, monkeypatch, mode, name):
    for explicit in (None, 'b' * 64 + ':42', ''):
        if explicit is not None:
            monkeypatch.setenv(KEY, explicit)
        units = manager.render_user_units(ROOT, runtime_mode=mode)
        expected = SELECTOR if explicit is None else explicit
        assert environment(units[name])[KEY] == expected
        if mode == 'dev':
            assert environment(units[manager.API_SERVICE])[KEY] == expected
        assert 'DO_NOT_FORWARD' not in '\n'.join(units.values())
        assert str(installation) not in units[name]  # Not a whole credential EnvironmentFile.
        directives = '\n'.join(line.strip() for line in units[name].splitlines()
                               if line.strip().startswith('Environment='))
        probe = installation.parent / 'publication-probe.service'
        probe.write_text('[Service]\n' + directives + '\nExecStart=/bin/true\n')
        checked = subprocess.run(['systemd-analyze', 'verify', str(probe)],
                                 capture_output=True, text=True, timeout=30)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert 'Failed to parse' not in checked.stderr and 'Invalid' not in checked.stderr
    monkeypatch.delenv(KEY)
    installation.write_text(f'{KEY}=' + 'c' * 64 + ':84\n')
    assert environment(manager.render_user_units(ROOT, runtime_mode=mode)[name])[KEY] == 'c' * 64 + ':84'
    installation.unlink()
    assert asset_publication_environment() == {}
    assert KEY not in environment(manager.render_user_units(ROOT, runtime_mode=mode)[name])
