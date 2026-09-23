"""Offline warm-path cost and integrity regressions."""
import hashlib
import fcntl
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from services.remote_execution import bundle, cache
from tools import bms_artifact_cache as worker


@pytest.mark.asyncio
async def test_helper_reuses_verified_bytes_and_repairs_corruption(tmp_path, monkeypatch):
    uploads = []

    async def local_run(connection, argv, input_bytes=None, **kwargs):
        if input_bytes is not None:
            uploads.append(len(input_bytes))
        return subprocess.run(argv, input=input_bytes, capture_output=True, check=True)

    monkeypatch.setattr(cache, 'run_remote', local_run)
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    first = Path(await cache._install_helper(connection, cache._noop))
    cold = len(uploads)
    await cache._install_helper(connection, cache._noop)
    assert cold > 0 and len(uploads) == cold
    original = first.read_bytes()
    first.write_bytes(b'corrupt')
    await cache._install_helper(connection, cache._noop)
    assert len(uploads) == cold + 1
    assert first.read_bytes() == original


@pytest.mark.asyncio
async def test_warm_helper_refuses_symlink_ancestor_before_skip(tmp_path, monkeypatch):
    uploads = []

    async def local_run(connection, argv, input_bytes=None, **kwargs):
        if input_bytes is not None:
            uploads.append(1)
        return subprocess.run(argv, input=input_bytes, capture_output=True, check=True)

    monkeypatch.setattr(cache, 'run_remote', local_run)
    real = tmp_path / 'worker'
    await cache._install_helper(SimpleNamespace(remote_root=str(real)), cache._noop)
    cold = len(uploads)
    (tmp_path / 'alias').symlink_to(real, target_is_directory=True)
    with pytest.raises(subprocess.CalledProcessError):
        await cache._install_helper(SimpleNamespace(remote_root=str(tmp_path / 'alias')), cache._noop)
    assert len(uploads) == cold
    await cache._install_helper(SimpleNamespace(remote_root=str(real)), cache._noop)
    assert len(uploads) == cold


def test_source_archive_reuse_private_trees_and_rebuild_corruption(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'workflow.nf').write_text('process RUN { script: "echo ok" }\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'workflow.nf'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'fixture'], check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    real_run = bundle.subprocess.run
    archives = []

    def counted_run(argv, **kwargs):
        if argv[:2] == ['git', 'archive']:
            archives.append(1)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(bundle.subprocess, 'run', counted_run)
    data = tmp_path / 'data'
    first = tmp_path / 'attempt1'
    second = tmp_path / 'attempt2'
    digest = bundle._staged_source_archive(repo, data, revision, first)
    def no_extract(*args):
        raise AssertionError('prewarm must not extract source')
    with monkeypatch.context() as patcher:
        patcher.setattr(bundle, '_safe_extract', no_extract)
        assert bundle._staged_source_archive(repo, data, revision, second, extract=False) == digest
    assert len(archives) == 1
    assert first.joinpath('.bms-source.tar.gz').read_bytes() == second.joinpath('.bms-source.tar.gz').read_bytes()
    assert [p.name for p in second.iterdir()] == ['.bms-source.tar.gz']
    first.joinpath('workflow.nf').write_text('changed')
    assert not second.joinpath('workflow.nf').exists()
    cached = data / 'remote-execution/source-archives' / (revision + '.tar.gz')
    cached.write_bytes(b'corrupt')
    third = tmp_path / 'attempt3'
    assert bundle._staged_source_archive(repo, data, revision, third) == digest
    assert len(archives) == 2
    assert third.joinpath('workflow.nf').read_text() != first.joinpath('workflow.nf').read_text()


def test_source_extraction_does_not_hold_shared_revision_lock(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'workflow.nf').write_text('process RUN { script: "echo ok" }\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'workflow.nf'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'fixture'], check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    data = tmp_path / 'data'
    actual_extract = bundle._safe_extract

    def extract_without_serializing_attempts(archive, destination):
        lock_path = data / 'remote-execution/source-archives' / (revision + '.lock')
        with lock_path.open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        actual_extract(archive, destination)

    monkeypatch.setattr(bundle, '_safe_extract', extract_without_serializing_attempts)
    bundle._staged_source_archive(repo, data, revision, tmp_path / 'attempt1')
    bundle._staged_source_archive(repo, data, revision, tmp_path / 'attempt2')
    assert (tmp_path / 'attempt1/workflow.nf').read_bytes() == (tmp_path / 'attempt2/workflow.nf').read_bytes()


def test_source_archive_retention_skips_in_use_revision(tmp_path):
    revisions = [f'{number:040x}' for number in range(4)]
    archives = [tmp_path / (revision + '.tar.gz') for revision in revisions]
    for number, archive in enumerate(archives):
        archive.write_bytes(b'archive')
        os.utime(archive, ns=(number + 1, number + 1))
    with (tmp_path / (revisions[1] + '.lock')).open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        bundle._prune_source_archives(tmp_path)
        assert not archives[0].exists()
        assert all(path.exists() for path in archives[1:])
    bundle._prune_source_archives(tmp_path)
    assert [path.exists() for path in archives] == [False, False, True, True]


def test_materialize_hashes_copy_once_and_refuses_corrupt_object(tmp_path, monkeypatch):
    store = worker.Cache(tmp_path / 'cache/artifacts/v1')
    payload = b'fixture' * 4096
    item = {'sha256': hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload)}
    source = Path(store.root / 'incoming' / 'source')
    source.write_bytes(payload)
    store.ingest(item, source)
    calls = []
    original = worker.verified

    def count_verify(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, 'verified', count_verify)
    destination = tmp_path / 'attempt/runtime/model.bin'
    store.materialize(item, destination, tmp_path / 'attempt')
    assert not calls  # _publish_copy verifies exact copied bytes itself
    assert destination.read_bytes() == payload
    object_file = Path(store.root / 'objects/sha256' / item['sha256'][:2] / item['sha256'])
    object_file.chmod(0o600)
    object_file.write_bytes(b'wrong' + payload[5:])
    with pytest.raises(ValueError, match='hash_mismatch'):
        store.materialize(item, destination, tmp_path / 'attempt')
    assert destination.read_bytes() == payload
