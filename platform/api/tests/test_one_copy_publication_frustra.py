from __future__ import annotations

import os
import shutil

import pytest

from services.frustrampnn import manifests
from scripts import plan_frustrampnn_groups as groups
from scripts import publish_frustrampnn_bundle as publisher
from scripts import run_frustrampnn_component as component
from test_frustrampnn_manifests import _bundle, _write_json


def bundle(tmp_path):
    source = tmp_path / 'native'
    source.mkdir()
    _bundle(source)
    manifest = manifests.build_result_manifest(source)
    _write_json(source, manifests.MANIFEST_PATH, manifest)
    for path in source.iterdir():
        component._fsync_regular(path)
    return source, manifest


def test_group_fanout_and_parent_share_payload_not_metadata(tmp_path):
    source, manifest = bundle(tmp_path)
    authority = groups.tree_authority(source)
    group = tmp_path / 'group' / 'candidate'
    parent = tmp_path / 'parent'
    parent.mkdir()
    groups.reconcile_tree(source, group, authority, staging_root=tmp_path / '.staging')
    result = publisher.publish(source_bundle=group, allowed_root=parent,
                               destination=parent / 'candidate', marker=parent / 'marker.json')
    for name in ('normalized_input.pdb', 'raw_frustrampnn.csv'):
        paths = [source / name, group / name, parent / 'candidate' / name]
        assert len({(p.stat().st_dev, p.stat().st_ino) for p in paths}) == 1
        assert all(not p.is_symlink() and not p.stat().st_mode & 0o222 for p in paths)
    name = 'workflow_component_request_v1.json'
    assert not (group / name).samefile(source / name)
    assert not (parent / 'candidate' / name).samefile(group / name)
    expected = manifests.validate_result_manifest(source, manifest)
    shutil.rmtree(source)
    shutil.rmtree(group.parent)
    assert manifests.validate_result_manifest(parent / 'candidate', manifest) == expected
    assert result['result'] == 'candidate/workflow_component_result_v1.json'
    assert result['source'] == 'inputs/candidate.pdb'


def test_group_partial_publication_reconciles_without_truncation(tmp_path, monkeypatch):
    source, _ = bundle(tmp_path)
    destination = tmp_path / 'group'
    authority = groups.tree_authority(source)
    real = groups.os.replace
    calls = 0
    def interrupt(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('interrupted')
        return real(src, dst)
    with monkeypatch.context() as context:
        context.setattr(groups.os, 'replace', interrupt)
        with pytest.raises(OSError, match='interrupted'):
            groups.reconcile_tree(source, destination, authority, staging_root=tmp_path / '.staging')
    assert all(authority[name] == value for name, value in groups.tree_authority(destination).items())
    assert not list((tmp_path / '.staging').iterdir())
    groups.reconcile_tree(source, destination, authority, staging_root=tmp_path / '.staging')
    assert groups.tree_authority(destination) == authority


def test_legacy_mutable_group_source_gets_private_snapshot(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    donor = source / 'normalized_input.pdb'
    donor.write_bytes(b'legacy mutable input')
    authority = groups.tree_authority(source)
    destination = tmp_path / 'group'
    groups.reconcile_tree(source, destination, authority, staging_root=tmp_path / '.staging')
    target = destination / donor.name
    assert not donor.samefile(target)
    assert donor.stat().st_mode & 0o222
    assert not target.stat().st_mode & 0o222
    donor.write_bytes(b'changed legacy input')
    assert target.read_bytes() == b'legacy mutable input'


@pytest.mark.parametrize('immutable_input', [True, False])
def test_manifest_shared_read_vs_destination_link_generation(tmp_path, monkeypatch, immutable_input):
    source = tmp_path / 'input.pdb'
    source.write_bytes(b'input')
    source.chmod(0o444)
    real = manifests.os.fstat
    calls = 0
    def link_during_read(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            os.link(source, tmp_path / 'sibling.pdb')
        return real(fd)
    monkeypatch.setattr(manifests.os, 'fstat', link_during_read)
    if immutable_input:
        assert manifests._read_regular(tmp_path, source.name, immutable_input=True) == b'input'
    else:
        with pytest.raises(manifests.ManifestValidationError, match='changed'):
            manifests._read_regular(tmp_path, source.name)


def test_component_shared_input_link_generation(tmp_path, monkeypatch):
    source = tmp_path / 'input.pdb'
    source.write_bytes(b'input')
    source.chmod(0o444)
    real = component.os.pread
    def read_and_link(fd, count, offset):
        data = real(fd, count, offset)
        os.link(source, tmp_path / 'sibling.pdb')
        return data
    monkeypatch.setattr(component.os, 'pread', read_and_link)
    assert component._read_regular(source, label='normalized PDB', max_bytes=100,
                                   immutable_input=True) == b'input'


def test_interrupted_parent_publication_keeps_donor_and_no_partial_parent(tmp_path, monkeypatch):
    source, manifest = bundle(tmp_path)
    expected = manifests.validate_result_manifest(source, manifest)
    parent = tmp_path / 'parent'
    parent.mkdir()
    def interrupted(*args):
        raise OSError('interrupted')
    monkeypatch.setattr(publisher.os, 'rename', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        publisher.publish(source_bundle=source, allowed_root=parent,
                          destination=parent / 'candidate', marker=parent / 'marker.json')
    assert not list(parent.iterdir())
    assert manifests.validate_result_manifest(source, manifest) == expected
