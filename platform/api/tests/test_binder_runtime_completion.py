"""Non-science return-processing regressions; no worker or model execution."""
import hashlib
import io
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from services.remote_execution import executor as ex, result_generation as gen
from services.remote_execution.contracts import RemoteResultManifest
from test_remote_result_generation import job_at, package
from test_remote_manual_result_pull import success


def manifest_at(root, names):
    job = job_at(root)
    status = success()
    artifacts = [dict(relative_path=name, size_bytes=7,
                      sha256=hashlib.sha256(b'payload').hexdigest(), role='result') for name in names]
    manifest = RemoteResultManifest(job_id=job.id, attempt_id=job.remote_attempt_id,
        source_revision=job.execution_source_revision, source_tree=job.execution_source_tree,
        execution_envelope_sha256=job.execution_bundle_sha256, artifacts=artifacts,
        exit_code=status.exit_code, completed_at=status.completed_at)
    encoded = manifest.model_dump_json().encode()
    digest = hashlib.sha256(encoded).hexdigest()
    incoming = gen.staging_path(job, digest)
    incoming.mkdir(parents=True)
    (incoming / 'result-manifest.json').write_bytes(encoded)
    return job, status.model_copy(update={'result_manifest_sha256': digest}), incoming, encoded


@pytest.mark.asyncio
async def test_manifest_reader_bounds_read_before_rejecting(tmp_path, monkeypatch):
    """Exercise the exact remote Python reader, instrumenting only file I/O."""
    reads = []
    cap = 31
    monkeypatch.setattr(ex, 'MAX_RESULT_MANIFEST_BYTES', cap)

    class File(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    class Source:
        def open(self, mode):
            assert mode == 'rb'
            return File(b'x' * 1000)

        def read_bytes(self):
            reads.append(-1)
            return b'x' * 1000

    async def local_reader(connection, argv, **kwargs):
        import pathlib
        with monkeypatch.context() as patcher:
            patcher.setattr(pathlib, 'Path', lambda _: Source())
            patcher.setattr(sys, 'argv', ['-c', *argv[3:]])
            with pytest.raises(RuntimeError, match='manifest too large'):
                exec(compile(argv[2], '<remote manifest reader>', 'exec'), {})
        raise ex.RemoteExecutionError('manifest too large')

    monkeypatch.setattr(ex, 'run_remote', local_reader)
    with pytest.raises(ex.RemoteExecutionError, match='manifest too large'):
        await ex._fetch_result_manifest(None, '/results', tmp_path / 'incoming', None, None)
    assert reads == [cap + 1]


@pytest.mark.asyncio
async def test_return_retry_temp_lookup_is_linear_and_preserves_payloads(tmp_path, monkeypatch):
    names = [f'states/{n:04d}.name+β.cif' for n in range(96)]
    names += ['nested/same.cif', 'other/same.cif', '.hidden.cif', 'line\nbreak.cif']
    job, status, incoming, encoded = manifest_at(tmp_path, names)
    for name in names:
        path = incoming / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'payload')
        path.with_name('.' + path.name + '.Ab09Zx').write_bytes(b'partial')
    calls = []
    fullmatch = re.fullmatch

    def counted(*args, **kwargs):
        calls.append(1)
        return fullmatch(*args, **kwargs)

    async def fetch(*args, **kwargs):
        return SimpleNamespace(stdout=encoded.decode())

    monkeypatch.setattr(ex, 'run_remote', fetch)
    monkeypatch.setattr(re, 'fullmatch', counted)
    result = await ex._fetch_result_manifest(None, '/results', incoming, job, status)
    assert len(result.artifacts) == len(names)
    assert all((incoming / name).read_bytes() == b'payload' for name in names)
    assert not any(path.name.endswith('.Ab09Zx') for path in incoming.rglob('*'))
    assert len(calls) <= len(names)
    assert ex._verify_result_package(incoming, job, status) == result


@pytest.mark.asyncio
@pytest.mark.parametrize('temporary', ['.sample.cif.short', '.sample.cif.Ab09Z_',
                                      '.other.cif.Ab09Zx', 'other/.sample.cif.Ab09Zx',
                                      '..Ab09Zx', '...Ab09Zx'])
async def test_retry_keeps_exact_declared_sibling_matching(tmp_path, monkeypatch, temporary):
    job, status, incoming, encoded = manifest_at(tmp_path, ['sample.cif'])
    path = incoming / temporary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'not owned')

    async def fetch(*args, **kwargs):
        return SimpleNamespace(stdout=encoded.decode())

    monkeypatch.setattr(ex, 'run_remote', fetch)
    with pytest.raises(ex.RemoteExecutionError, match='undeclared files'):
        await ex._fetch_result_manifest(None, '/results', incoming, job, status)
    assert path.read_bytes() == b'not owned'


def test_publication_walks_once_and_syncs_files_before_directories(tmp_path, monkeypatch):
    job = job_at(tmp_path)
    _, incoming, _ = package(job)
    nested = incoming / 'campaign/arm/state'
    nested.mkdir(parents=True)
    (nested / 'native.cif').write_bytes(b'opaque native fixture')
    expected_files = {p for p in incoming.rglob('*') if p.is_file()}
    expected_dirs = {incoming, *(p for p in incoming.rglob('*') if p.is_dir())}
    walks = []
    events = []
    rglob = Path.rglob
    fsync = gen.os.fsync


    def counted(path, pattern, *args, **kwargs):
        if path == incoming:
            walks.append(1)
        return rglob(path, pattern, *args, **kwargs)

    def synced(fd):
        events.append(Path(gen.os.readlink(f'/proc/self/fd/{fd}')))
        fsync(fd)

    monkeypatch.setattr(Path, 'rglob', counted)
    monkeypatch.setattr(gen.os, 'fsync', synced)
    gen.publish(job, incoming)
    assert len(walks) == 1
    assert expected_files <= set(events)
    assert expected_dirs <= set(events)
    assert max(events.index(p) for p in expected_files) < min(events.index(p) for p in expected_dirs)
    assert events.index(nested) < events.index(nested.parent) < events.index(incoming)
    assert (Path(job.output_dir) / 'campaign/arm/state/native.cif').read_bytes() == b'opaque native fixture'
    assert gen.recover(job)


@pytest.mark.asyncio
async def test_real_manifest_reader_preserves_exact_limit_bytes(tmp_path, monkeypatch):
    job, status, incoming, encoded = manifest_at(tmp_path, ['sample.cif'])
    source = tmp_path / 'worker-results'
    source.mkdir()
    (source / 'result-manifest.json').write_bytes(encoded)
    monkeypatch.setattr(ex, 'MAX_RESULT_MANIFEST_BYTES', len(encoded))

    async def local_reader(connection, argv, **kwargs):
        return subprocess.run(argv, capture_output=True, text=True, check=True)

    monkeypatch.setattr(ex, 'run_remote', local_reader)
    result = await ex._fetch_result_manifest(None, str(source), incoming, job, status)
    assert len(result.artifacts) == 1
    assert (incoming / 'result-manifest.json').read_bytes() == encoded
