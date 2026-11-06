"""Generated service -> actual launcher config phase -> runner -> shared selection.

Tiny private payloads only: no services, container execution or remote transport.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import biomodstack_services as manager
from biomodstack_runtime_profile import asset_publication_environment, get_install_profile_path
from component_runtime import SourceIdentity, canonical_bytes
from routers.workflow_adapter import _runner_environment
from services.execution_ownership import AdapterIdentity
from services.nextflow import build_selected_execution_plan
from services.remote_execution import bundle, cache, hf_assets
import paths

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


@pytest.mark.parametrize('binding', ['published', 'absent', 'mismatch', 'leaf', 'ancestor'])
def test_generated_launcher_runner_selects_one_shared_body(installation, tmp_path, monkeypatch, binding):
    units = manager.render_user_units(ROOT, runtime_mode='dev')
    generated = environment(units[manager.DEVELOPMENT_WORKFLOW_ADAPTER_SERVICE])
    # Compatibility/profile defaults must not replace explicit generated authority.
    compat = Path(os.environ['HOME']) / '.biomodstack/env.sh'
    compat.parent.mkdir(parents=True)
    compat.write_text(f'export {KEY}=stale-compatibility\n')
    (installation.parent / 'core-runtime.env').write_text(f'{KEY}=stale-profile\n')
    launcher = ROOT / 'scripts/run_biomodstack_workflow_adapter.sh'
    prefix = launcher.read_text().split('# Keep BioModStack dependency resolution', 1)[0]
    received = subprocess.run(['bash', '-c', prefix + '\nenv -0', str(launcher)],
        env={**os.environ, **generated}, capture_output=True, check=True).stdout
    adapter_env = dict(item.decode().split('=', 1) for item in received.split(b'\0') if item)
    assert adapter_env[KEY] == SELECTOR
    assert 'BMS_HF_TOKEN' not in adapter_env and 'HF_TOKEN' not in adapter_env
    for key in tuple(os.environ):
        monkeypatch.delenv(key)
    for key, value in adapter_env.items():
        monkeypatch.setenv(key, value)
    identity = AdapterIdentity('development', tmp_path / 'state', tmp_path / 'db',
                               tmp_path / 'work', tmp_path / 'results')
    runner = _runner_environment(identity=identity, unit_name='fixture.service',
                                  owner_nonce='fixture', resource_handoff=None)
    assert runner[KEY] == SELECTOR
    assert 'BMS_HF_TOKEN' not in runner and 'HF_TOKEN' not in runner
    for key in tuple(os.environ):
        monkeypatch.delenv(key)
    for key, value in runner.items():
        monkeypatch.setenv(key, str(value))
    containers = tmp_path / 'images'
    containers.mkdir()
    weights = tmp_path / 'weights'
    weights.mkdir()
    for owner in (paths, bundle):
        monkeypatch.setattr(owner, 'get_container_dir', lambda: containers)
        monkeypatch.setattr(owner, 'get_weights_root', lambda: weights)
        monkeypatch.setattr(owner, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(cache, 'get_data_root', lambda: tmp_path)
    image = containers / 'boltz2-version.sif'
    image.write_bytes(b'INERT PRIVATE SHARED IMAGE')
    alias = containers / 'boltz2.sif'
    alias.symlink_to(image.name)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    row = dict(name='containers/boltz2.sif', source=str(image), sha256=digest,
               size_bytes=image.stat().st_size, mode=0o644)
    if binding == 'mismatch':
        row['source'] = str(containers / 'other.sif')
    elif binding in {'leaf', 'ancestor'}:
        link = containers / 'published-link'
        link.symlink_to(image if binding == 'leaf' else containers,
                        target_is_directory=binding == 'ancestor')
        row['source'] = str(link if binding == 'leaf' else link / image.name)
    index = hf_assets.publication_index_path()
    assert index is not None
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps(dict(schema=hf_assets.NAMED_INDEX_SCHEMA,
        archive=dict(sha256='a' * 64, size_bytes=3606773760), digest_sizes={},
        dependencies=['containers/boltz2.sif'], artifacts=[row])))
    index.chmod(0o444)
    if binding == 'absent':
        monkeypatch.delenv(KEY)
    params = dict(sequence='AAAA', boltz_use_msa=False, run_frustrampnn=False)
    plan = build_selected_execution_plan(model_id='boltz2', mode='predict',
        entrypoint='structure_prediction.nf', requested=canonical_bytes(params),
        effective=canonical_bytes(params), native_parameters=params,
        source_identity=SourceIdentity('a' * 40, 'b' * 40))
    def selected(_=None):
        return bundle._runtime_assets('boltz2', 'predict', params,
            selected_plan=plan, only_kinds=frozenset({'image'}))
    if binding != 'published':
        with pytest.raises(bundle.RemoteBundleError, match='no-follow'):
            selected()
        return
    before = {str(p): (p.lstat().st_ino, p.lstat().st_size, p.lstat().st_blocks)
              for p in tmp_path.rglob('*') if p.is_file()}
    cold, warm = selected(), selected()
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = list(pool.map(selected, range(2)))
    assert cold == warm == concurrent[0] == concurrent[1] == [(image, 'containers/boltz2.sif')]
    assert all(path.stat().st_ino == image.stat().st_ino for result in concurrent for path, _ in result)
    records = bundle._runtime_records(image, 'containers/boltz2.sif')
    assert [(r.sha256, r.size_bytes) for r in records] == [(digest, image.stat().st_size)]
    # Merely supplying named-publication metadata must not select the giant pack.
    assert cache._weights_archive_artifact(records) is None
    assert cache._weights_archive_artifact([SimpleNamespace(sha256='f' * 64, size_bytes=17)]) is None
    after = {str(p): (p.lstat().st_ino, p.lstat().st_size, p.lstat().st_blocks)
             for p in tmp_path.rglob('*') if p.is_file()}
    assert before == after
    assert alias.is_symlink() and alias.readlink() == Path(image.name)
