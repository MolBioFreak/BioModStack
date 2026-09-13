"""Physical support members, authenticated aliases and existing actor coverage."""
import copy
import hashlib
import os
from pathlib import Path

import pytest

from services.remote_execution import critical_runtime as cr
from tools import bms_managed_runtime as managed, bms_artifact_cache as cache
from test_managed_runtime_safety import critical_package, install_critical_fixture


def test_physical_members_do_not_expand_directory_or_binary_aliases(tmp_path):
    root = tmp_path / 'support'
    lib = root / 'venv/lib/python3.12/site-packages'
    lib.mkdir(parents=True)
    (lib / 'example.py').write_text('value=1\n')
    (root / 'venv/lib64').symlink_to('lib')
    (lib / '__pycache__').mkdir()
    (lib / '__pycache__/example.cpython-312.pyc').write_bytes(b'regenerable')
    (lib / 'sourceless.pyc').write_bytes(b'required-bytecode-only-module')
    (root / 'venv/.venv').symlink_to('/not-a-worker-dependency')
    names = [n for n, _ in cr._leaves(root)]
    assert names == ['venv/lib/python3.12/site-packages/example.py',
                     'venv/lib/python3.12/site-packages/sourceless.pyc']
    links = list(cr._links(root))
    assert len(links) == 1 and links[0]['name'] == 'support-python/venv/lib64'
    assert links[0]['target'] == 'lib'
    assert (lib / '__pycache__/example.cpython-312.pyc').exists()


@pytest.mark.parametrize('target', ['/etc', '.', 'absent'])
def test_support_alias_escape_cycle_and_missing_target_rejected(tmp_path, target):
    (tmp_path / 'alias').symlink_to(target)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        list(cr._support_members(tmp_path))


@pytest.fixture
def linked_package(critical_package, tmp_path):
    _, _, requirements, worker = critical_package
    support = (Path(os.environ['BMS_CM_API_RUNTIME_DIR']) / 'current').resolve()
    real = support / 'base/bin/python'
    real.parent.mkdir(parents=True)
    (support / 'venv/bin/python').replace(real)
    (support / 'venv/bin/python').symlink_to('../../base/bin/python')
    (support / 'venv/lib64').symlink_to('lib')
    staging = tmp_path / 'linked-staging'
    manifest, artifacts = cr.project_runtime(str(worker), staging)
    return manifest, artifacts, requirements, worker


def test_real_projection_and_actor_preserve_aliases_without_object_copies(linked_package, monkeypatch):
    manifest, artifacts, _, worker = linked_package
    rows = {r['name']: r for r in manifest['artifacts']}
    assert rows['support-python/venv/bin/python']['kind'] == 'runtime_link'
    assert rows['support-python/venv/lib64']['kind'] == 'runtime_link'
    assert all('/lib64/' not in r['name'] for r in manifest['artifacts'])
    assert all(rows[a.remote_destination].get('kind') != 'runtime_link' for a in artifacts)
    m, storage, root = install_critical_fixture(linked_package, monkeypatch)
    assert m.install(root, manifest, m.boot_id(), storage)['release']['state'] == 'verified'
    release = m.release_path(root, manifest)
    assert (release / 'support-python/venv/lib64').is_symlink()
    python = release / 'support-python/venv/bin/python'
    assert python.is_symlink() and python.resolve() == release / 'support-python/base/bin/python'
    binding = cr.runtime_binding(str(worker), manifest)
    assert binding['sha256']['python'] == hashlib.sha256(python.read_bytes()).hexdigest()
    assert m.install(root, manifest, m.boot_id(), storage)['admission']['additional_copy_bytes'] == 0
    python.unlink()
    python.symlink_to('/bin/sh')
    assert m.observe(root, manifest, storage)['state'] == 'corrupt'
    with pytest.raises(ValueError, match='active_generation_damaged'):
        m.install(root, manifest, m.boot_id(), storage)


@pytest.mark.parametrize('target', ['/etc/passwd', '../../../../escape', '.', 'missing', '../../..'])
def test_link_manifest_rejects_escape_cycle_or_undeclared_target(linked_package, target):
    manifest = copy.deepcopy(linked_package[0])
    row = next(r for r in manifest['artifacts'] if r['name'] == 'support-python/venv/lib64')
    row.update(target=target, size_bytes=len(target.encode()), sha256=hashlib.sha256(target.encode()).hexdigest())
    with pytest.raises(ValueError, match='invalid_runtime_link'):
        managed.validate_manifest(manifest, cache)


def test_link_digest_is_not_trusted_without_exact_target_text(linked_package):
    manifest = copy.deepcopy(linked_package[0])
    next(r for r in manifest['artifacts'] if r.get('kind') == 'runtime_link')['sha256'] = 'a' * 64
    with pytest.raises(ValueError, match='invalid_runtime_link'):
        managed.validate_manifest(manifest, cache)
