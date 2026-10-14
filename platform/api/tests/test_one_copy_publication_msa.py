from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from biomodstack_msa_handoff import digest
from biomodstack_boltz_msa import validate_boltz_msa
from services import model_msa_handoff as handoff


def generated(tmp_path, *, paired=False):
    cache = tmp_path / 'cache'
    cache.mkdir()
    artifacts = []
    for role in (('unpaired', 'paired') if paired else ('unpaired',)):
        source = cache / (role + '.a3m')
        source.write_bytes(b'>query\nACD\n>hit\nACE\n')
        artifacts.append({'chain_index': 0, 'role': role, 'path': str(source),
                          'sha256': digest(source.read_bytes())})
    root = tmp_path / 'prepared'
    result = {'artifacts': artifacts, 'provider': 'fixture'}
    paths = handoff.native_alignments(result, ['ACD'], root / 'operations' / '0')
    return root, paths[0], result


@pytest.mark.parametrize('paired', [False, True])
def test_generated_alignment_seal_survives_operation_removal(tmp_path, paired, record_property):
    root, source, _ = generated(tmp_path, paired=paired)
    original = source.read_bytes()
    record = handoff._seal_native_alignment({'msa': str(source), 'sequence': 'ACD'}, root)
    target = root / record['path']
    assert target.samefile(source)
    assert not target.is_symlink()
    assert not target.stat().st_mode & 0o222
    assert record['sha256'] == digest(original)
    record_property('retained_names', 2)
    record_property('unique_inodes', 1)
    record_property('payload_bytes', target.stat().st_size)
    record_property('allocated_bytes', target.stat().st_blocks * 512)
    shutil.rmtree(root / 'operations')
    shutil.rmtree(tmp_path / 'cache')
    assert validate_boltz_msa(target, 'ACD') == original


def test_retry_replaces_operation_not_sealed_sibling(tmp_path):
    root, source, result = generated(tmp_path)
    record = handoff._seal_native_alignment({'msa': str(source), 'sequence': 'ACD'}, root)
    target = root / record['path']
    before = target.read_bytes()
    cache = Path(result['artifacts'][0]['path'])
    cache.write_bytes(b'>query\nACD\n>new\nACC\n')
    result['artifacts'][0]['sha256'] = digest(cache.read_bytes())
    handoff.native_alignments(result, ['ACD'], source.parent)
    assert target.read_bytes() == before
    assert not target.samefile(source)
    assert validate_boltz_msa(source, 'ACD') != before


@pytest.mark.parametrize('readonly', [False, True])
def test_supplied_alignment_remains_independent(tmp_path, readonly):
    source = tmp_path / 'supplied.a3m'
    source.write_bytes(b'>query\nACD\n')
    if readonly:
        source.chmod(0o444)
    root = tmp_path / 'prepared'
    record = handoff._seal_native_alignment({'msa': str(source), 'sequence': 'ACD'}, root)
    target = root / record['path']
    assert not target.samefile(source)
    assert bool(source.stat().st_mode & 0o222) is not readonly
    source.unlink()
    assert validate_boltz_msa(target, 'ACD') == b'>query\nACD\n'


def test_interrupted_seal_preserves_existing_target(tmp_path, monkeypatch):
    root, source, _ = generated(tmp_path)
    protein = {'msa': str(source), 'sequence': 'ACD'}
    record = handoff._seal_native_alignment(protein, root)
    target = root / record['path']
    before = target.stat().st_ino
    def interrupted(*args):
        raise OSError('publication interrupted')
    monkeypatch.setattr(handoff.os, 'replace', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        handoff._seal_native_alignment(protein, root)
    assert target.stat().st_ino == before
    assert target.read_bytes() == source.read_bytes()
    assert list(target.parent.iterdir()) == [target]


def test_cross_filesystem_retains_independent_payload(tmp_path, monkeypatch):
    root, source, _ = generated(tmp_path)
    def cross_device(*args):
        raise OSError(errno.EXDEV, 'cross-device fixture')
    monkeypatch.setattr(handoff.os, 'link', cross_device)
    record = handoff._seal_native_alignment({'msa': str(source), 'sequence': 'ACD'}, root)
    target = root / record['path']
    assert not target.samefile(source)
    assert validate_boltz_msa(target, 'ACD') == source.read_bytes()
