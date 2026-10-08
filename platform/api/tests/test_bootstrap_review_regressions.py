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
    assert result.returncode == 3, result.stderr
    assert not result.stderr
    return json.loads(result.stdout)


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


@pytest.mark.parametrize('source', ['profile', 'environment', 'config_home'])
def test_symlink_loops_return_json(isolated, monkeypatch, source):
    loop = isolated / 'loop'
    loop.symlink_to(loop)
    if source == 'profile':
        profile(json.dumps({'data_root': str(loop)}))
    elif source == 'environment':
        monkeypatch.setenv('BMS_DATA', str(loop))
    else:
        monkeypatch.setenv('XDG_CONFIG_HOME', str(loop))
    value = report(cli('--json', 'plan'))
    assert {'profile_invalid', 'profile_resolution_failed'} & {b['code'] for b in value['blockers']}


def test_symlink_runtimeerror_boundary_on_all_python_versions(isolated, monkeypatch):
    def loop(*args, **kwargs):
        raise RuntimeError('Symlink loop')
    monkeypatch.setattr(bootstrap, 'resolve_runtime_paths', loop)
    value = bootstrap.bootstrap_report('discover', project_root=ROOT)
    assert 'profile_resolution_failed' in {b['code'] for b in value['blockers']}


@pytest.mark.parametrize('mode', ['dev', 'container'])
def test_lane_storage_and_independent_destinations(isolated, monkeypatch, mode):
    prod, dev = isolated / 'prod', isolated / 'dev'
    raw = {'data_root': str(prod), 'dev_data_root': str(dev),
           'dev_results_dir': '/proc/this-cannot-be-written',
           'results_dir': str(isolated / 'separate-results'),
           'db_path': str(isolated / 'separate-db' / 'database.sqlite'),
           'work_dir': str(isolated / 'separate-work'),
           'analysis_cache_dir': str(isolated / 'separate-analysis'),
           'msa_cache_dir': str(isolated / 'separate-msa'),
           'sabdab_cache_dir': str(isolated / 'separate-sabdab'),
           'colabfold_db': str(isolated / 'shared-reference')}
    profile(json.dumps(raw))
    # proc's access hint can be true for root despite unavailable creation;
    # test destination-specific diagnostic wiring without doing write probes.
    access = bootstrap.os.access
    monkeypatch.setattr(bootstrap.os, 'access', lambda path, flags:
                        False if str(path) == '/proc' else access(path, flags))
    value = bootstrap.bootstrap_report('plan', project_root=ROOT, runtime=mode)
    observed = {s['role']: s for s in value['observations']['storage']}
    expected = managed_runtime_storage_paths(resolve_runtime_paths(ROOT, raw), mode)
    assert {key: item['path'] for key, item in observed.items()} == expected
    if mode == 'dev':
        assert observed['dev_results_dir']['path'] == raw['dev_results_dir']
        assert observed['dev_db_path']['path'] == str(dev / 'biomodstack.db')
        assert observed['dev_analysis_cache_dir']['path'] == str(dev / 'analysis_cache')
        assert observed['dev_work_dir']['path'] == str(dev / 'work')
        assert 'storage_not_writable' in {b['code'] for b in value['blockers']}
        assert 'results_dir' not in observed
    else:
        assert not any(key.startswith('dev_') for key in observed)
        assert 'storage_not_writable' not in {b['code'] for b in value['blockers']}
        assert observed['db_path']['observed_at'] == str(isolated)
        assert observed['db_path']['kind'] == 'file'
        for key in ('results_dir', 'db_path', 'work_dir', 'analysis_cache_dir',
                    'msa_cache_dir', 'sabdab_cache_dir', 'colabfold_db'):
            assert observed[key]['path'] == raw[key]


def test_existing_database_observes_parent_not_file(isolated):
    database = isolated / 'existing.sqlite'
    database.write_bytes(b'not opened as a database')
    profile(json.dumps({'db_path': str(database)}))
    value = report(cli('discover', '--json'))
    storage = {s['role']: s for s in value['observations']['storage']}
    assert storage['db_path']['observed_at'] == str(isolated)
    assert not any(b['code'] == 'storage_unavailable' for b in value['blockers'])


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


@pytest.mark.parametrize('action', ['discover', 'plan'])
@pytest.mark.parametrize('entry', ['shell', 'python_action_first', 'python_option_first', 'python_env'])
def test_archived_source_and_fresh_pycache_prefix_no_writes(isolated, archived, action, entry):
    env = dict(os.environ)
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    env['PYTHONPYCACHEPREFIX'] = str(isolated / 'fresh-interpreter-cache')
    # Use the same interpreter via shell PATH, no shell startup hooks.
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    before = snapshot(isolated)
    if entry == 'shell':
        result = subprocess.run([str(archived / 'start_ui.sh'), action, '--json'],
                                env=env, capture_output=True, text=True)
    elif entry == 'python_env':
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        result = cli('--runtime', 'dev', '--json', action, root=archived, env=env, startup=False)
    else:
        args = [action, '--json'] if entry.endswith('action_first') else ['--runtime', 'dev', '--json', action]
        result = cli(*args, root=archived, env=env)
    value = report(result)
    assert value['interpreter_startup']['bytecode_disabled'] is True
    assert snapshot(isolated) == before
    assert not (isolated / 'fresh-interpreter-cache').exists()


def test_direct_python_without_startup_flag_reports_boundary(isolated, archived):
    env = dict(os.environ)
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    env['PYTHONPYCACHEPREFIX'] = str(isolated / 'startup-cache')
    before = snapshot(archived)
    value = report(cli('--json', 'discover', root=archived, env=env, startup=False))
    assert value['interpreter_startup']['bytecode_disabled'] is False
    assert 'excludes interpreter startup' in value['effects_scope']
    assert snapshot(archived) == before
    # Startup may cache stdlib; nothing imported after our first boundary may
    # cache project modules, regardless of action/option ordering.
    assert not list((isolated / 'startup-cache').rglob('*biomodstack*.pyc'))


@pytest.mark.parametrize('models', [(), ('frustrampnn',), ('not-a-model',)])
def test_license_applicability_is_not_invented(isolated, models):
    value = bootstrap.bootstrap_report('plan', project_root=ROOT, models=models)
    assert 'licensed_weights_unresolved' not in {b['code'] for b in value['blockers']}
    if models:
        assert value['observations']['weight_licensing']['applicability'] == 'unknown'
    else:
        assert 'weight_licensing' not in value['observations']
