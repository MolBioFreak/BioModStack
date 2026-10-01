"""Count real Git/archive/record/cache receiving work without scientific execution."""
from collections import Counter
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from component_runtime import SourceIdentity
from services.remote_execution import bundle
from tools import bms_artifact_cache as artifact_cache, bms_remote_worker as worker
from test_remote_bundle_runtime_gaps import package

REAL_RUN = subprocess.run
REAL_GIT = bundle._git
REAL_IDENTITY = bundle.current_source_identity


@pytest.fixture
def git_package(package, monkeypatch):
    roots, release, job, target, command = package
    repo = roots['repo']
    (repo / 'executable.sh').write_text('#!/bin/sh\nexit 0\n')
    (repo / 'executable.sh').chmod(0o755)
    monkeypatch.setenv('GIT_AUTHOR_DATE', '2026-09-30T12:00:00+00:00')
    monkeypatch.setenv('GIT_COMMITTER_DATE', '2026-09-30T12:00:00+00:00')
    REAL_RUN(['git', 'init', '-q', str(repo)], check=True)
    REAL_RUN(['git', '-C', str(repo), 'add', '.'], check=True)
    REAL_RUN(['git', '-C', str(repo), '-c', 'user.name=Fixture',
              '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'inert'], check=True)
    monkeypatch.setattr(subprocess, 'run', REAL_RUN)
    monkeypatch.setattr(bundle, '_git', REAL_GIT)
    monkeypatch.setattr(bundle, 'current_source_identity', REAL_IDENTITY)
    revision, tree = REAL_IDENTITY(repo)
    job.execution_source_revision, job.execution_source_tree = revision, tree
    identity = SourceIdentity(revision, tree)
    job.native_invocation = replace(job.native_invocation, source_identity=identity,
        execution_plan=replace(job.native_invocation.execution_plan, source_identity=identity))
    return roots, release, job, target, command


def test_real_cold_warm_archive_chain(git_package, monkeypatch, record_property):
    roots, _, job, target, command = git_package
    counts = Counter()
    original_open, original_extract = Path.open, bundle._safe_extract
    class Reader:
        def __init__(self, stream, category):
            self.stream, self.category = stream, category
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def __getattr__(self, name): return getattr(self.stream, name)
        def read(self, size=-1):
            data = self.stream.read(size)
            counts[self.category + '_bytes'] += len(data)
            return data
    def opened(path, mode='r', *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        category = None
        if mode == 'rb':
            if path.parent.name == 'source-archives':
                category = 'generated' if path.name.startswith('.archive-') else 'copy'
            elif path.name == '.bms-source.tar.gz': category = 'record_archive'
            elif 'staging' in path.parts and 'source' in path.parts: category = 'leaf'
        if category:
            counts[category + '_opens'] += 1
            return Reader(stream, category)
        return stream
    def run(argv, **kwargs):
        if argv[:2] == ['git', 'archive']: counts['git_archive'] += 1
        return REAL_RUN(argv, **kwargs)
    def extract(*args, **kwargs):
        counts['extract'] += 1
        return original_extract(*args, **kwargs)
    monkeypatch.setattr(Path, 'open', opened)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(bundle, '_safe_extract', extract)
    record_passes = int(os.environ.get('BMS_TEST_ARCHIVE_RECORD_PASSES', '0'))
    snapshots = []
    prepared_bundles = []
    for cold in (True, False):
        counts.clear()
        prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                                native_invocation=job.native_invocation)
        prepared_bundles.append(prepared)
        source_records = [r for r in prepared.envelope.files if r.role == 'source']
        archive_record, = [r for r in source_records if r.relative_path == 'source/.bms-source.tar.gz']
        source = prepared.source_transfer.source
        size = (source / '.bms-source.tar.gz').stat().st_size
        leaves = [r for r in source_records if r != archive_record]
        assert counts['git_archive'] == counts['generated_opens'] == int(cold)
        assert counts['generated_bytes'] == size * int(cold)
        assert counts['copy_opens'] == counts['extract'] == 1
        assert counts['copy_bytes'] == size
        assert counts['record_archive_opens'] == record_passes
        assert counts['record_archive_bytes'] == size * record_passes
        assert counts['leaf_opens'] == len(leaves)
        assert counts['leaf_bytes'] == sum(r.size_bytes for r in leaves)
        record_property('cold' if cold else 'warm', json.dumps(dict(counts), sort_keys=True))
        assert archive_record.sha256 == prepared.envelope.source_archive_sha256
        snapshots.append([r.model_dump() for r in source_records])
        # Test oracle reads are outside the measured preparation operation.
        for record in source_records:
            path = source / record.relative_path.removeprefix('source/')
            with original_open(path, 'rb') as stream: payload = stream.read()
            assert (record.sha256, record.size_bytes, record.mode) == (
                hashlib.sha256(payload).hexdigest(), len(payload), path.stat().st_mode & 0o777)
    assert snapshots[0] == snapshots[1]
    record_property('source_records', json.dumps(snapshots[0], sort_keys=True))
    first, second = prepared_bundles
    assert first.source_transfer.source != second.source_transfer.source
    # Real CAS publication/extraction and worker receiving verification retain all leaves.
    store = artifact_cache.Cache(roots['data'] / 'fixture-cas')
    artifact, = [a for a in bundle.cache_transfer_artifacts(second) if a.role == 'source']
    item = dict(sha256=artifact.sha256, size_bytes=artifact.size_bytes)
    incoming = store.root / 'incoming' / 'fixture-upload'
    shutil.copyfile(artifact.source, incoming)
    store.ingest(item, incoming)
    attempt = roots['data'] / 'fixture-worker'
    destination = attempt / 'bundle/source'
    store.extract_source(item, destination)
    shutil.copyfile(artifact.source, destination / '.bms-source.tar.gz')
    envelope = second.envelope.model_dump(mode='json', by_alias=True)
    envelope.update(files=snapshots[1], working_directory=str(destination),
                    output_directory=str(attempt / 'results'))
    (attempt / worker.ENVELOPE_FILE).write_text(json.dumps(envelope))
    assert worker.verify_bundle(attempt) == envelope
    leaf = destination / 'main.nf'
    leaf.write_bytes(b'x' * leaf.stat().st_size)
    with pytest.raises(RuntimeError, match='bundle file hash mismatch'):
        worker.verify_bundle(attempt)


def test_corrupt_cached_copy_evicts_before_extract_and_retry_regenerates(git_package, monkeypatch, record_property):
    roots, _, job, _, _ = git_package
    revision = job.execution_source_revision
    repo, data = roots['repo'], roots['data']
    digest = bundle._staged_source_archive(repo, data, revision, data / 'cold-source')
    archive = data / 'remote-execution/source-archives' / (revision + '.tar.gz')
    counts = Counter()
    original_extract = bundle._safe_extract
    def run(argv, **kwargs):
        if argv[:2] == ['git', 'archive']: counts['git_archive'] += 1
        return REAL_RUN(argv, **kwargs)
    def extract(*args, **kwargs):
        counts['extract'] += 1
        return original_extract(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(bundle, '_safe_extract', extract)
    payload = archive.read_bytes()
    archive.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    with pytest.raises(bundle.RemoteBundleError, match='changed during staging'):
        bundle._staged_source_archive(repo, data, revision, data / 'corrupt-source')
    assert counts == Counter()
    assert str(archive) not in bundle._SOURCE_ARCHIVE_DIGESTS
    assert not (data / 'corrupt-source/main.nf').exists()
    assert bundle._staged_source_archive(repo, data, revision, data / 'retry-source') == digest
    assert counts == Counter(git_archive=1, extract=1)
    # Restart simulation retains the existing cold regeneration policy.
    bundle._SOURCE_ARCHIVE_DIGESTS.pop(str(archive))
    assert bundle._staged_source_archive(repo, data, revision, data / 'restart-source') == digest
    assert counts == Counter(git_archive=2, extract=2)
    record_property('corruption_retry_restart', json.dumps(dict(counts), sort_keys=True))


@pytest.mark.parametrize('role', ['source', 'input', 'runtime'])
def test_digest_reuse_is_only_top_level_source_archive(tmp_path, monkeypatch, role):
    root = tmp_path / 'inventory'
    (root / 'nested').mkdir(parents=True)
    names = ['.bms-source.tar.gz', 'nested/.bms-source.tar.gz', 'leaf.txt']
    for name in names:
        (root / name).write_bytes(name.encode())
    hashed = []
    original = bundle._sha256_file
    def hashed_file(path):
        hashed.append(path.relative_to(root).as_posix())
        return original(path)
    monkeypatch.setattr(bundle, '_sha256_file', hashed_file)
    # Same nodes run against the pinned original; only its missing reuse argument differs.
    baseline = int(os.environ.get('BMS_TEST_ARCHIVE_RECORD_PASSES', '0')) == 1
    digest = hashlib.sha256(names[0].encode()).hexdigest()
    kwargs = {} if baseline else {'source_archive_sha256': digest}
    records = bundle._records_for_source(root, role, role, **kwargs)
    assert {r.relative_path for r in records} == {role + '/' + name for name in names}
    expected = set(names) - ({names[0]} if not baseline and role == 'source' else set())
    assert set(hashed) == expected
    for row in records:
        name = row.relative_path.removeprefix(role + '/')
        assert row.sha256 == hashlib.sha256(name.encode()).hexdigest()


@pytest.mark.parametrize('mutation', ['symlink', 'special_mode'])
def test_reused_archive_keeps_metadata_rejection(tmp_path, mutation):
    root = tmp_path / 'inventory'
    root.mkdir()
    archive = root / '.bms-source.tar.gz'
    archive.write_bytes(b'inert')
    if mutation == 'symlink':
        archive.rename(root / 'body')
        archive.symlink_to('body')
    else:
        archive.chmod(0o4644)
    baseline = int(os.environ.get('BMS_TEST_ARCHIVE_RECORD_PASSES', '0')) == 1
    kwargs = {} if baseline else {'source_archive_sha256': hashlib.sha256(b'inert').hexdigest()}
    with pytest.raises(bundle.RemoteBundleError, match='symlink|special mode'):
        bundle._records_for_source(root, 'source', 'source', **kwargs)
