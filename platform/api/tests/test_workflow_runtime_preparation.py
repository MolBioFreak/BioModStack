"""Inert local transport: actual CAS/layout installation, no model execution."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import uuid

import pytest

from services.remote_execution import cache, bundle
from services.remote_execution.bundle import CacheTransferArtifact
from tools import bms_artifact_cache as worker
from test_remote_cache_integration import local_transport

REAL_WEIGHT_LAYOUTS = cache.workflow_pack_weight_layouts


@pytest.fixture
def prepared(tmp_path, monkeypatch, local_transport):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'workflow.nf').write_text('// inert source fixture\n')
    for args in [('init',), ('add', '.'), ('-c', 'user.name=Fixture', '-c',
            'user.email=fixture@example.invalid', 'commit', '-m', 'inert')]:
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
    data = tmp_path / 'data'
    monkeypatch.setattr(cache, 'get_code_root', lambda: repo)
    monkeypatch.setattr(cache, 'get_data_root', lambda: data)
    monkeypatch.setattr(bundle, 'get_data_root', lambda: data)
    monkeypatch.setattr(cache.hf_assets, 'weights_archive', lambda: None)
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: None)
    entries = []
    for name in ['boltz/model', 'protenix/base', 'protenix/template', 'esmfold2/fast', 'esmfold2/full']:
        path = tmp_path / 'assets' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        entries.append(CacheTransferArtifact(path, 'weights/' + name,
            hashlib.sha256(name.encode()).hexdigest(), len(name), 0o644, 'runtime'))
    groups = {'boltz': (entries[0],), 'fold_cp': (entries[0],),
              'ordinary': (entries[1],), 'template': tuple(entries[1:3]),
              'combined': tuple(entries[:2]), 'fast': (entries[3],), 'full': (entries[4],)}
    monkeypatch.setattr(cache, 'workflow_pack_weight_layouts', lambda *args: groups)
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    kwargs = dict(connection=connection, entries=entries, operation_id=str(uuid.uuid4()),
        progress=cache._noop, check_fence=cache._noop,
        selection=SimpleNamespace(kind='workflow_pack', workflow_id='structure_prediction'),
        backend=None, source_identity=cache.current_source_identity(repo))
    yield SimpleNamespace(kwargs=kwargs, groups=groups, calls=local_transport[0],
                          uploads=local_transport[1], repo=repo)
    for path in tmp_path.rglob('*'):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)


def rows(group):
    return [dict(name=e.remote_destination.removeprefix('weights/'), sha256=e.sha256,
                 size_bytes=e.size_bytes, mode=e.mode) for e in group]


@pytest.mark.asyncio
async def test_native_subset_layouts_installed_once_and_warm_no_transfer(prepared):
    p = prepared
    result = await cache.provision_cache(**p.kwargs)
    assert result['preparation'] == dict(source='cached', weight_layouts=6,
        images='deferred_backend_unknown', backend=None)
    root = Path(p.kwargs['connection'].remote_root) / 'cache/artifacts/v1'
    store = worker.Cache(root)
    digests = set()
    for group in p.groups.values():
        digest, _, _ = worker.weight_layout(rows(group))
        digests.add(digest)
        observed = store.weights(rows(group))
        assert observed['state'] == 'ready'
        for entry in group:
            obj = root / 'objects/sha256' / entry.sha256[:2] / entry.sha256
            leaf = Path(observed['root']) / entry.remote_destination.removeprefix('weights/')
            assert leaf.stat().st_ino == obj.stat().st_ino
    assert {path.name for path in (root / 'weights').iterdir()} == digests
    assert len([c for c in p.calls if c['action'] == 'weights_install']) == 6
    uploads = len(p.uploads)
    p.calls.clear()
    await cache.provision_cache(**dict(p.kwargs, operation_id=str(uuid.uuid4())))
    assert len(p.uploads) == uploads
    assert not any(c['action'] in {'ingest', 'ingest_many', 'weights_install', 'extract_source'} for c in p.calls)
    assert len(list((p.repo.parent / 'data/remote-execution/source-archives').glob('*.tar.gz'))) == 1


@pytest.mark.asyncio
async def test_archive_owner_called_once_with_bound_weights_not_mutated(prepared, monkeypatch):
    p = prepared
    calls = []
    async def archive(**kwargs):
        calls.append(kwargs)
        assert all(e.role == 'weights' for e in kwargs['artifacts'])
        assert len(worker.weight_layout_reference(kwargs['layout'])) == len(p.kwargs['entries'])
        return None
    monkeypatch.setattr(cache, '_install_weight_archive', archive)
    await cache.provision_cache(**p.kwargs)
    await cache.provision_cache(**p.kwargs)
    assert len(calls) == 1
    assert all(e.role == 'runtime' for e in p.kwargs['entries'])


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', ['apptainer', 'udocker'])
@pytest.mark.parametrize('damage', [None, 'sha256', 'backend', 'state'])
async def test_image_preparation_identity_and_operation(prepared, monkeypatch, backend, damage):
    p = prepared
    image = p.repo.parent / 'inert.sif'
    image.write_bytes(b'inert image, never executed')
    entry = CacheTransferArtifact(image, 'containers/inert.sif',
        hashlib.sha256(image.read_bytes()).hexdigest(), image.stat().st_size, 0o444, 'image')
    original = cache.run_remote
    requests = []
    async def remote(connection, argv, input_bytes=None, **kwargs):
        request = json.loads(input_bytes) if '-c' not in argv and input_bytes else {}
        if request.get('action') == 'prepare_runtime_image':
            requests.append(request)
            response = dict(request['artifact'], backend=backend, state='ready',
                            rootfs='/shared/rootfs' if backend == 'udocker' else None)
            if damage:
                response[damage] = 'wrong'
            return SimpleNamespace(stdout=json.dumps(response))
        return await original(connection, argv, input_bytes=input_bytes, **kwargs)
    monkeypatch.setattr(cache, 'run_remote', remote)
    kwargs = dict(p.kwargs, entries=[*p.kwargs['entries'], entry, entry], backend=backend)
    if damage:
        with pytest.raises(ValueError, match='image preparation identity mismatch'):
            await cache.provision_cache(**kwargs)
    else:
        result = await cache.provision_cache(**kwargs)
        assert result['preparation']['images'] == 'ready'
    assert len(requests) == 1
    assert requests[0]['operation_id'] == p.kwargs['operation_id']


@pytest.mark.asyncio
async def test_changed_fence_after_helper_response_cannot_publish(prepared, monkeypatch):
    original = cache.run_remote
    changed = False
    async def remote(*args, **kwargs):
        nonlocal changed
        result = await original(*args, **kwargs)
        payload = kwargs.get('input_bytes')
        if payload and '-c' not in args[1] and json.loads(payload).get('action') == 'weights_probe':
            changed = True
        return result
    async def fence():
        if changed:
            raise RuntimeError('changed fence')
    monkeypatch.setattr(cache, 'run_remote', remote)
    with pytest.raises((RuntimeError, ExceptionGroup)) as error:
        await cache.provision_cache(**dict(prepared.kwargs, check_fence=fence))
    failures = error.value.exceptions if isinstance(error.value, ExceptionGroup) else [error.value]
    assert all(str(exc) == 'changed fence' for exc in failures)
    assert not prepared.uploads
    assert not any(c['action'] == 'weights_install' for c in prepared.calls)


@pytest.mark.asyncio
async def test_cancel_drains_archive_thread_before_scratch_cleanup(prepared, monkeypatch):
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    paths = []
    original = cache._workflow_source_archive
    def archive(directory, identity):
        paths.append(directory)
        entered.set()
        assert release.wait(10)
        try:
            assert directory.is_dir()
            return original(directory, identity)
        finally:
            stopped.set()
    monkeypatch.setattr(cache, '_workflow_source_archive', archive)
    task = asyncio.create_task(cache.provision_cache(**prepared.kwargs))
    while not entered.is_set():
        await asyncio.sleep(.01)
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()
    assert paths[0].is_dir() and not stopped.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set() and not paths[0].exists()
    assert not prepared.uploads


@pytest.mark.asyncio
async def test_real_archive_unpack_once_and_only_missing_bytes_relay(prepared, monkeypatch):
    import io
    import tarfile
    p = prepared
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode='w') as archive:
        for entry in p.kwargs['entries'][:-1]:
            archive.add(entry.source, arcname=entry.remote_destination)
    payload = packed.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(cache.hf_assets, 'weights_archive', lambda: (digest, len(payload)))
    monkeypatch.setattr(cache.hf_assets, 'configuration', lambda: object())
    async def sources(entries, **kwargs):
        return {(False, digest): {'fixture': True}}
    monkeypatch.setattr(cache.hf_assets, 'prepare_sources', sources)
    original = cache.run_remote
    downloads = []
    async def remote(connection, argv, input_bytes=None, **kwargs):
        request = json.loads(input_bytes) if '-c' not in argv and input_bytes else {}
        if request.get('action') == 'acquire_hf':
            downloads.append(request)
            incoming = Path(connection.remote_root) / 'cache/artifacts/v1/incoming'
            path = incoming / request['operation_id'] / request['batch_id'] / digest
            path.write_bytes(payload)
            return SimpleNamespace(stdout=json.dumps(dict(state='downloaded',
                sha256=digest, size_bytes=len(payload))))
        return await original(connection, argv, input_bytes=input_bytes, **kwargs)
    monkeypatch.setattr(cache, 'run_remote', remote)
    await cache.provision_cache(**p.kwargs)
    relayed = {r['sha256'] for c in p.calls if c['action'] == 'ingest_many' for r in c['artifacts']}
    assert p.kwargs['entries'][-1].sha256 in relayed
    assert not relayed.intersection(e.sha256 for e in p.kwargs['entries'][:-1])
    await cache.provision_cache(**p.kwargs)
    assert len(downloads) == 1
    assert len([c for c in p.calls if c['action'] == 'unpack_weights_archive']) == 1
    store = worker.Cache(Path(p.kwargs['connection'].remote_root) / 'cache/artifacts/v1')
    assert all(store.weights(rows(group))['state'] == 'ready' for group in p.groups.values())


@pytest.mark.asyncio
async def test_link_layout_matches_job_mode_without_link_byte_transfer(prepared):
    p = prepared
    target = 'model'
    entry = p.kwargs['entries'][0]
    alias = CacheTransferArtifact(entry.source.parent / 'alias', 'weights/boltz/alias',
        hashlib.sha256(target.encode()).hexdigest(), len(target), 0o644, 'runtime', link_target=target)
    p.kwargs['entries'].append(alias)
    p.groups['boltz'] = p.groups['fold_cp'] = (entry, alias)
    await cache.provision_cache(**p.kwargs)
    expected = rows((entry,)) + [dict(name='boltz/alias', sha256=alias.sha256,
        size_bytes=alias.size_bytes, mode=0o777, target=target)]
    store = worker.Cache(Path(p.kwargs['connection'].remote_root) / 'cache/artifacts/v1')
    result = store.weights(expected)
    assert result['state'] == 'ready'
    assert (Path(result['root']) / 'boltz/alias').read_bytes() == entry.source.read_bytes()
    assert not any(alias.sha256 == row['sha256'] for c in p.calls if c['action'] == 'ingest_many'
                   for row in c['artifacts'])


@pytest.mark.asyncio
async def test_cancel_during_remote_derivation_has_no_ready_return(prepared, monkeypatch):
    p = prepared
    started = asyncio.Event()
    cancelled = asyncio.Event()
    entry = p.kwargs['entries'][0]
    from dataclasses import replace
    image = replace(entry, role='image', remote_destination='containers/inert.sif')
    original = cache.run_remote
    async def remote(connection, argv, input_bytes=None, **kwargs):
        request = json.loads(input_bytes) if '-c' not in argv and input_bytes else {}
        if request.get('action') == 'prepare_runtime_image':
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        return await original(connection, argv, input_bytes=input_bytes, **kwargs)
    monkeypatch.setattr(cache, 'run_remote', remote)
    task = asyncio.create_task(cache.provision_cache(**dict(p.kwargs,
        entries=[*p.kwargs['entries'], image], backend='udocker')))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_real_structure_registry_consumer_layouts_ready(prepared, monkeypatch):
    from model_registry import workflow_pack_weight_groups
    p = prepared
    groups = workflow_pack_weight_groups('structure_prediction')
    members = set(member for group in groups.values() for member in group)
    entries = []
    for name in sorted(members):
        # Directory bindings carry one inert member; exact file bindings retain
        # native consumer names. No scientific compiler or model is invoked.
        if name in {'boltz', 'esmfold2', 'protenix/mmcif'}:
            name += '/fixture.bin'
        path = p.repo.parent / 'native-fixtures' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        entries.append(CacheTransferArtifact(path, 'weights/' + name,
            hashlib.sha256(name.encode()).hexdigest(), len(name), 0o644, 'runtime'))
    monkeypatch.setattr(cache, 'workflow_pack_weight_layouts', REAL_WEIGHT_LAYOUTS)
    result = await cache.provision_cache(**dict(p.kwargs, entries=entries))
    store = worker.Cache(Path(p.kwargs['connection'].remote_root) / 'cache/artifacts/v1')
    projected = REAL_WEIGHT_LAYOUTS(p.kwargs['selection'], entries)
    assert set(projected) == set(groups)
    digests = set()
    for group in projected.values():
        assert group
        assert store.weights(rows(group))['state'] == 'ready'
        digests.add(worker.weight_layout(rows(group))[0])
    assert result['preparation']['weight_layouts'] == len(digests)
    assert len([c for c in p.calls if c['action'] == 'weights_install']) == len(digests)


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', [None, 'apptainer'])
async def test_real_inert_image_unknown_backend_or_apptainer(prepared, backend):
    from dataclasses import replace
    p = prepared
    image = replace(p.kwargs['entries'][0], role='image', remote_destination='containers/inert.sif')
    result = await cache.provision_cache(**dict(p.kwargs,
        entries=[*p.kwargs['entries'], image], backend=backend))
    assert result['preparation']['images'] == ('ready' if backend else 'deferred_backend_unknown')
    requests = [c for c in p.calls if c['action'] == 'prepare_runtime_image']
    assert len(requests) == (1 if backend else 0)
    root = Path(p.kwargs['connection'].remote_root)
    assert not list(root.rglob('.rootfs-*'))


@pytest.mark.asyncio
async def test_wrong_weight_placement_response_cannot_publish(prepared, monkeypatch):
    original = cache.run_remote
    async def remote(*args, **kwargs):
        result = await original(*args, **kwargs)
        payload = kwargs.get('input_bytes')
        if payload and '-c' not in args[1] and json.loads(payload).get('action') == 'weights_probe':
            response = json.loads(result.stdout)
            response['root'] = '/wrong/shared/layout'
            return SimpleNamespace(stdout=json.dumps(response))
        return result
    monkeypatch.setattr(cache, 'run_remote', remote)
    with pytest.raises((ValueError, ExceptionGroup)) as error:
        await cache.provision_cache(**prepared.kwargs)
    failures = error.value.exceptions if isinstance(error.value, ExceptionGroup) else [error.value]
    assert all(str(exc) == 'Shared weight placement identity mismatch' for exc in failures)
    assert not prepared.uploads


@pytest.mark.asyncio
async def test_changed_source_does_not_transfer(prepared):
    with pytest.raises(ValueError, match='source identity changed'):
        await cache.provision_cache(**dict(prepared.kwargs, source_identity=('a' * 40, 'b' * 40)))
    assert not prepared.uploads
