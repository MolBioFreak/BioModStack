"""Offline warm-path cost and integrity regressions."""
import hashlib
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
    assert bundle._staged_source_archive(repo, data, revision, second) == digest
    assert len(archives) == 1
    assert first.joinpath('.bms-source.tar.gz').read_bytes() == second.joinpath('.bms-source.tar.gz').read_bytes()
    assert first.joinpath('workflow.nf').stat().st_ino != second.joinpath('workflow.nf').stat().st_ino
    first.joinpath('workflow.nf').write_text('changed')
    assert second.joinpath('workflow.nf').read_text() != 'changed'
    cached = data / 'remote-execution/source-archives' / (revision + '.tar.gz')
    cached.write_bytes(b'corrupt')
    third = tmp_path / 'attempt3'
    assert bundle._staged_source_archive(repo, data, revision, third) == digest
    assert len(archives) == 2
    assert third.joinpath('workflow.nf').read_text() == second.joinpath('workflow.nf').read_text()


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
