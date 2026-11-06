"""Actual cache/install/worker owners, tiny local bytes; no science or providers."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

from services.remote_execution import bundle, critical_runtime
from tools import bms_artifact_cache as cache, bms_remote_worker as worker
from test_managed_runtime_safety import critical_package, install_critical_fixture
from test_remote_cache_integration import local_transport


def ingest(storage, payload):
    row = dict(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    path = Path(storage.root) / 'incoming' / row['sha256']
    path.write_bytes(payload)
    storage.ingest(row, path)
    return row


def packed(revision):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, payload, mode in [('lib.py', b'unchanged library\n', 0o644),
                                    ('bin/tool', b'#!/bin/sh\nexit 0\n', 0o755),
                                    ('revision.txt', revision, 0o644),
                                    ('containers/transport/proot/resource', b'resource', 0o644)]:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(payload), mode
            archive.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def test_cache_immutable_modes_cold_warm_concurrent_and_private(tmp_path, monkeypatch):
    storage = cache.Cache(tmp_path / 'cache')
    item = ingest(storage, b'payload')
    calls = []
    original = storage._publish_copy
    def copied(*args):
        calls.append((args[3]['sha256'], args[4]))
        return original(*args)
    monkeypatch.setattr(storage, '_publish_copy', copied)
    views = tmp_path / 'views'
    def materialize(index):
        return storage.materialize(item, views / str(index), views, 0o555)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(materialize, range(2)))
    materialize(2)
    assert calls == []  # Private allocator, not a new immutable mode projection.
    assert len({(views / str(i)).stat().st_ino for i in range(3)}) == 3
    cas = tmp_path / 'cache/objects/sha256' / item['sha256'][:2] / item['sha256']
    storage.materialize(item, views / 'read', views, 0o444)
    assert (views / 'read').stat().st_ino != cas.stat().st_ino
    assert cas.stat().st_mode & 0o777 == 0o444
    storage.materialize(item, views / 'private', views, 0o644)
    (views / 'private').write_bytes(b'changed')
    assert cas.read_bytes() == (views / '0').read_bytes() == b'payload'


def test_source_members_once_across_attempts_and_revisions(tmp_path, monkeypatch):
    storage = cache.Cache(tmp_path / 'cache')
    one, two = [ingest(storage, packed(revision)) for revision in (b'one', b'two')]
    copies = []
    original = os.replace
    def published(source, destination, **kwargs):
        result = original(source, destination, **kwargs)
        # Count actual member CAS adoption, not the removed second copy pass.
        if str(source).startswith('.source-member-'):
            path = Path(os.readlink('/proc/self/fd/' + str(kwargs['dst_dir_fd']))) / destination
            copies.append((str(destination), path.stat().st_mode & 0o777))
        return result
    monkeypatch.setattr(os, 'replace', published)
    a, b, c = [tmp_path / name for name in ('a', 'b', 'c')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda dest: storage.extract_source(one, dest, immutable=True), (a, b)))
    first = list(copies)
    assert len(first) == 4  # Four CAS bodies; attempts use the private allocator.
    storage.extract_source(one, b, immutable=True)
    assert copies == first
    storage.extract_source(two, c, immutable=True)
    assert copies[len(first):] == [(hashlib.sha256(b'two').hexdigest(), 0o444)]
    for name in ('lib.py', 'bin/tool', 'containers/transport/proot/resource'):
        assert len({(p / name).stat().st_ino for p in (a, b, c)}) == 3
    assert (a / 'bin/tool').stat().st_mode & 0o777 == 0o555
    (a / '.nextflow').mkdir()
    (a / '.nextflow/log').write_text('private log')
    assert not (b / '.nextflow').exists()
    assert (a / 'revision.txt').read_bytes() == b'one'
    assert (c / 'revision.txt').read_bytes() == b'two'


def test_cancelled_source_stage_has_no_completion_and_recovers(tmp_path, monkeypatch):
    storage = cache.Cache(tmp_path / 'cache')
    item = ingest(storage, packed(b'one'))
    def cancel(item, state, **kwargs):
        if state == 'publishing':
            raise KeyboardInterrupt('cancelled')
    monkeypatch.setattr(storage, 'emit', cancel)
    with pytest.raises(KeyboardInterrupt):
        storage.extract_source(item, tmp_path / 'attempt', immutable=True)
    assert not list((tmp_path / 'cache/archives').glob('source-*'))
    assert not list((tmp_path / 'cache').rglob('.partial-*'))
    assert not list((tmp_path / 'cache/archives').glob('.source-member-*'))
    monkeypatch.setattr(storage, 'emit', lambda *a, **kw: None)
    storage.extract_source(item, tmp_path / 'retry', immutable=True)
    assert (tmp_path / 'retry/lib.py').read_bytes() == b'unchanged library\n'


def test_streaming_controller_inventory_matches_worker_modes(tmp_path, monkeypatch):
    storage = cache.Cache(tmp_path / 'cache')
    payload = packed(b'one')
    item = ingest(storage, payload)
    attempt = tmp_path / 'attempt'
    source = attempt / 'materialized/source'
    source.mkdir(parents=True)
    archive = source / '.bms-source.tar.gz'
    storage.materialize(item, archive, source, 0o444)
    monkeypatch.setattr(tarfile.TarFile, 'extractall', lambda *a, **kw: pytest.fail('hash-only extraction'))
    records = bundle._source_archive_records(archive, item['sha256'])
    storage.extract_source(item, source, immutable=True)
    (attempt / 'bundle').mkdir()
    (attempt / 'bundle/source').symlink_to(source, target_is_directory=True)
    envelope = dict(schema='bms.remote-execution.v1', command=['not-executed'],
                    files=[r.model_dump() for r in records], source_archive_sha256=item['sha256'],
                    working_directory=str(source), output_directory=str(attempt / 'results'))
    (attempt / worker.ENVELOPE_FILE).write_text(json.dumps(envelope))
    assert worker.verify_bundle(attempt) == envelope
    assert [r.mode for r in records if r.relative_path == 'source/bin/tool'] == [0o555]
    assert (source / 'lib.py').stat().st_nlink == 1
    assert not (source / '__pycache__').exists()


def test_relocation_shares_immutable_leaves_never_chmods_or_rewrites_donor(tmp_path, monkeypatch):
    source = tmp_path / 'payload'
    (source / 'venv/bin').mkdir(parents=True)
    (source / 'base').mkdir()
    library = source / 'venv/library.so'
    library.write_bytes(b'native library')
    config = source / 'venv/pyvenv.cfg'
    config.write_text('home = ' + str(source / 'base') + '\n')
    script = source / 'venv/bin/tool'
    script.write_text('#!' + str(source / 'venv/bin/python') + '\nprint(1)\n')
    for path in (library, config, script):
        path.chmod(0o555 if path == script else 0o444)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (library, config, script)}
    monkeypatch.setattr(bundle.shutil, 'copy2', lambda *a, **kw: pytest.fail('immutable relocation copy'))
    a = bundle._relocate_python_runtime(source, tmp_path / 'a', '/worker/releases/a')
    b = bundle._relocate_python_runtime(source, tmp_path / 'b', '/worker/releases/b')
    assert library.stat().st_ino == (a / 'venv/library.so').stat().st_ino == (b / 'venv/library.so').stat().st_ino
    assert config.stat().st_ino != (a / 'venv/pyvenv.cfg').stat().st_ino
    assert (a / 'venv/pyvenv.cfg').read_bytes() != (b / 'venv/pyvenv.cfg').read_bytes()
    assert (a / 'venv/bin/tool').stat().st_ino != script.stat().st_ino
    assert before == {p: (p.read_bytes(), p.stat().st_mode) for p in before}


def test_installed_critical_source_only_release_shares_actual_payload(critical_package, monkeypatch, tmp_path):
    first, _, requirements, host = critical_package
    managed, module, install_root = install_critical_fixture(critical_package, monkeypatch)
    managed.install(install_root, first, managed.boot_id(), module)
    source = (Path(os.environ['BMS_CM_API_RUNTIME_DIR']) / 'current').resolve()
    for path in source.rglob('*'):
        if path.is_file() and not path.is_symlink():
            path.chmod(path.stat().st_mode & 0o555)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda root: ('c'*40, 'd'*40))
    monkeypatch.setattr(bundle.shutil, 'copy2', lambda *a, **kw: pytest.fail('immutable body recopied'))
    second, transfers = critical_runtime.project_runtime(str(host), tmp_path / 'second')
    assert first['critical']['installation_id'] != second['critical']['installation_id']
    storage = module.Cache(host / 'cache/artifacts/v1')
    copied = []
    original = module.Cache._publish_copy
    def copy_body(self, *args):
        copied.append(args[3]['sha256'])
        return original(self, *args)
    monkeypatch.setattr(module.Cache, '_publish_copy', copy_body)
    for entry in transfers:
        if storage.probe(dict(sha256=entry.sha256, size_bytes=entry.size_bytes))['state'] != 'cache_hit':
            ingest(storage, entry.source.read_bytes())
    def install(manifest):
        # Existing admission is nonblocking; contention is retried by its caller.
        import time
        for _ in range(100):
            try:
                return managed.install(install_root, manifest, managed.boot_id(), module)
            except BlockingIOError:
                time.sleep(0.01)
        pytest.fail('admission lock did not converge')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(install, (first, second)))
    assert all(r['release']['state'] == 'verified' for r in results)
    old, new = [managed.release_path(install_root, m) for m in (first, second)]
    for name in ('support-python/venv/bin/python', 'support-python/venv/lib/python3.11/site-packages/example.py',
                 'support-python/base/lib/python3.11/encodings/__init__.py'):
        assert (old / name).stat().st_ino == (new / name).stat().st_ino
        digest = hashlib.sha256((old / name).read_bytes()).hexdigest()
        assert digest not in copied
    assert (old / 'support-python/venv/pyvenv.cfg').read_bytes() != (new / 'support-python/venv/pyvenv.cfg').read_bytes()
    managed.install(install_root, second, managed.boot_id(), module)
    assert managed.observe(install_root, second, module)['state'] == 'verified'


def test_local_completed_receipt_selects_concrete_payload_and_reuses_hashes(critical_package, monkeypatch, tmp_path):
    _, _, _, host = critical_package
    selected = (Path(os.environ['BMS_CM_API_RUNTIME_DIR']) / 'current').resolve()
    members = []
    for name, path, target in critical_runtime._support_members(selected):
        if target is None:
            path.chmod(path.stat().st_mode & 0o555)
            members.append(dict(path=name, kind='file', size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), mode=path.stat().st_mode & 0o777))
    receipt = dict(payload_root=str(selected), payload_id='fixture-selected', members=members,
        published_member_manifest_sha256=hashlib.sha256(json.dumps(members,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    receipt_path = selected / 'support-payload.json'
    receipt_path.write_text(json.dumps(receipt))
    receipt_path.chmod(0o444)
    monkeypatch.setenv('BMS_SUPPORT_PAYLOAD_RECEIPT', str(receipt_path))
    # current can remain frozen on a different retained selection.
    current = Path(os.environ['BMS_CM_API_RUNTIME_DIR']) / 'current'
    current.unlink()
    current.symlink_to(tmp_path / 'retained-unselected')
    original = critical_runtime._digest
    def digest(path):
        if 'support-python' in str(path) or path.is_relative_to(selected) or '/relocated/' in str(path):
            pytest.fail('receipt-bound support leaf rehashed')
        return original(path)
    monkeypatch.setattr(critical_runtime, '_digest', digest)
    manifest, artifacts = critical_runtime.project_runtime(str(host), tmp_path / 'with-receipt')
    assert critical_runtime.selected_support_root() == selected
    support = [entry for entry in artifacts if entry.remote_destination.startswith('support-python/')]
    assert support and not any(entry.remote_destination.endswith('support-payload.json') for entry in support)
    library = next(entry for entry in support if entry.remote_destination.endswith('example.py'))
    assert library.source.stat().st_ino == (selected / 'venv/lib/python3.11/site-packages/example.py').stat().st_ino
    assert manifest['critical']['schema'] == 'bms.critical-runtime.v2'


@pytest.mark.asyncio
async def test_controller_real_cache_helper_then_managed_code_only_releases(critical_package, local_transport, monkeypatch, tmp_path):
    import asyncio
    import uuid
    from types import SimpleNamespace
    from services.remote_execution import cache as controller
    from test_managed_runtime_safety import load
    first, artifacts, requirements, host = critical_package
    managed, module = load('bms_managed_runtime'), load('bms_artifact_cache')
    monkeypatch.setattr(managed, 'observed_compatibility', lambda: dict(requirements))
    # Scientific compatibility probes are inert fixture outputs, not native qualification.
    monkeypatch.setattr(managed, 'qualify_container', lambda *a: dict(backend='apptainer', cuda='BMS_CUDA_OK', nextflow='BMS_NEXTFLOW_INTERPRETERS_OK'))
    connection = SimpleNamespace(remote_root=str(host))
    async def stage(entries):
        await controller._cache_artifacts(connection=connection, artifacts=entries,
            operation_id=str(uuid.uuid4()), progress=controller._noop,
            check_fence=controller._noop, materialize=False)
    await stage(artifacts)
    root = host / 'managed-assets/v1'
    managed.install(root, first, managed.boot_id(), module)
    original = managed.release_path(root, first)
    support = (Path(os.environ['BMS_CM_API_RUNTIME_DIR']) / 'current').resolve()
    for path in support.rglob('*'):
        if path.is_file() and not path.is_symlink():
            path.chmod(path.stat().st_mode & 0o555)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda _: ('c'*40, 'd'*40))
    second, entries = critical_runtime.project_runtime(str(host), tmp_path / 'next-source')
    # Concurrent controller deliveries run the actual serialized helper protocol.
    await asyncio.gather(stage(entries), stage(entries))
    managed.install(root, second, managed.boot_id(), module)
    newer = managed.release_path(root, second)
    for name in ('venv/bin/python', 'venv/lib/python3.11/site-packages/example.py',
                 'base/lib/python3.11/encodings/__init__.py'):
        assert (original / 'support-python' / name).stat().st_ino == (newer / 'support-python' / name).stat().st_ino
    retained = managed.observe(root, first, module)
    assert retained['state'] == 'unverified'  # retained bytes, no longer the active release
    assert all(row['state'] == 'verified' for row in retained['artifacts'])
    assert managed.observe(root, second, module)['state'] == 'verified'
    calls, uploads = local_transport
    assert calls and uploads
    assert all('bms-cache-batch-' in source or Path(source).is_file() for source in uploads)
    assert not any('support-python' in source for source in uploads)
