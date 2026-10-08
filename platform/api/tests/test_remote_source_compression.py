"""Lossless source transport through real archive/cache/worker owners, offline."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from services.remote_execution import bundle, cache
from tools import bms_artifact_cache as artifact_cache, bms_remote_worker as worker
from test_remote_bundle_runtime_gaps import package
from test_remote_cache_integration import local_transport


REAL_RUN = subprocess.run
REPO = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_real_source_launch_prewarm_lossless_and_cache_reuse(package, local_transport, monkeypatch, tmp_path):
    roots, release, job, target, command = package
    fake_run = subprocess.run
    archive_commands = []

    def run(argv, **kwargs):
        if argv[:2] == ['git', 'archive']:
            archive_commands.append(argv)
            # Only the fixture's synthetic revision/root are substituted. Exercise
            # the production archive format/options against the full committed tree.
            return REAL_RUN([*argv[:-1], 'HEAD'], **{**kwargs, 'cwd': REPO})
        return fake_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, 'run', run)
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                            native_invocation=job.native_invocation)
    monkeypatch.setattr(cache, 'current_source_identity', bundle.current_source_identity)
    prewarm_dir = tmp_path / 'prewarm'
    prewarm_dir.mkdir()
    prewarmed = cache._prewarm_plan(job, command, job.execution_source_revision,
        job.execution_source_tree, prewarm_dir, native_invocation=job.native_invocation)
    launch_source, = [a for a in bundle.cache_transfer_artifacts(prepared) if a.role == 'source']
    warm_source, = [a for a in prewarmed if a.role == 'source']
    assert archive_commands == [['git', 'archive', '--format=tar.gz', '-6', job.execution_source_revision]] * 2
    compressed = launch_source.source.read_bytes()
    raw = REAL_RUN(['git', 'archive', '--format=tar', 'HEAD'], cwd=REPO,
                   check=True, capture_output=True).stdout
    assert gzip.decompress(compressed) == raw
    assert compressed == warm_source.source.read_bytes()
    assert launch_source.sha256 == warm_source.sha256 == prepared.envelope.source_archive_sha256
    assert launch_source.sha256 == hashlib.sha256(compressed).hexdigest()
    assert launch_source.size_bytes == warm_source.size_bytes == len(compressed) < len(raw)
    assert prepared.envelope.source_revision == job.execution_source_revision
    assert prepared.envelope.source_tree == job.execution_source_tree
    # Prewarm the actual source projection, then stage via the real helper protocol.
    await cache._cache_artifacts(connection=target, artifacts=[warm_source],
        operation_id=prepared.attempt_id, progress=cache._noop,
        check_fence=cache._noop, materialize=False)
    calls, uploads = local_transport
    await cache.stage_cached_bundle(connection=target, bundle=prepared)
    assert sum(a['sha256'] == launch_source.sha256 for c in calls
               if c['action'] == 'ingest_many' for a in c['artifacts']) == 1
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        expected = {m.name for m in archive.getmembers() if m.isfile()}
        source = Path(prepared.remote_source_dir)
        assert {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()} == expected | {'.bms-source.tar.gz'}
        for member in archive.getmembers():
            if member.isfile():
                local = source / member.name
                assert local.read_bytes() == archive.extractfile(member).read()
                assert local.stat().st_mode & 0o777 == member.mode & 0o777


@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE),
    ('/absolute', tarfile.REGTYPE), ('link', tarfile.SYMTYPE), ('link', tarfile.LNKTYPE)])
def test_archive_safety_preserved(tmp_path, compressed, name, kind):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = '../escape'
        archive.addfile(member)
    payload = gzip.compress(data.getvalue(), mtime=0) if compressed else data.getvalue()
    path = tmp_path / 'source.archive'
    path.write_bytes(payload)
    with pytest.raises(bundle.RemoteBundleError):
        bundle._safe_extract(path, tmp_path / 'controller')
    store = artifact_cache.Cache(tmp_path / 'cache')
    item = {'sha256': hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload)}
    incoming = tmp_path / 'cache/incoming/upload'
    incoming.write_bytes(payload)
    store.ingest(item, incoming)
    with pytest.raises(ValueError, match='unsafe_archive_member'):
        store.extract_source(item, tmp_path / 'worker')
    assert not (tmp_path / 'escape').exists()


@pytest.mark.parametrize('suffix', ['.tar', '.tar.gz'])
def test_worker_archive_authority_and_legacy_reads(tmp_path, suffix):
    source = tmp_path / 'bundle/source'
    source.mkdir(parents=True)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w'):
        pass
    payload = gzip.compress(data.getvalue(), mtime=0) if suffix.endswith('gz') else data.getvalue()
    path = source / ('.bms-source' + suffix)
    path.write_bytes(payload)
    record = bundle._record_file(path, 'source/' + path.name, 'source').model_dump()
    envelope = dict(schema='bms.remote-execution.v1', command=['not-executed'],
        files=[record], source_archive_sha256=record['sha256'],
        working_directory=str(source), output_directory=str(tmp_path / 'results'))
    def save():
        (tmp_path / worker.ENVELOPE_FILE).write_text(json.dumps(envelope))
    save()
    assert worker.verify_bundle(tmp_path) == envelope
    envelope['source_archive_sha256'] = '0' * 64
    save()
    with pytest.raises(RuntimeError, match='source archive identity'):
        worker.verify_bundle(tmp_path)
    envelope['source_archive_sha256'] = record['sha256']
    other = source / ('.bms-source.tar' if suffix.endswith('gz') else '.bms-source.tar.gz')
    other.write_bytes(payload)
    envelope['files'].append(bundle._record_file(other, 'source/' + other.name, 'source').model_dump())
    save()
    with pytest.raises(RuntimeError, match='source archive identity'):
        worker.verify_bundle(tmp_path)
    envelope['files'] = [record]
    save()
    path.write_bytes(b'x' * len(payload))
    with pytest.raises(RuntimeError, match='bundle file hash mismatch'):
        worker.verify_bundle(tmp_path)


def test_compressed_cache_digest_checked_before_extract(tmp_path):
    payload = gzip.compress(b'not even a tar', mtime=0)
    source = tmp_path / 'upload'
    source.write_bytes(payload)
    item = {'sha256': hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload)}
    store = artifact_cache.Cache(tmp_path / 'cache')
    incoming = tmp_path / 'cache/incoming/upload'
    incoming.write_bytes(payload)
    store.ingest(item, incoming)
    obj = tmp_path / 'cache/objects/sha256' / item['sha256'][:2] / item['sha256']
    obj.chmod(0o600)
    obj.write_bytes(b'x' * len(payload))
    with pytest.raises(ValueError, match='corrupt_source_archive'):
        store.extract_source(item, tmp_path / 'extracted')
    assert not (tmp_path / 'extracted').exists()
