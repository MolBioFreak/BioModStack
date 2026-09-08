"""Real helper/transport/exec regressions; fixture bytes are not scientific SIFs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from services.remote_execution import bundle, cache
from tools import bms_artifact_cache as tool, bms_remote_worker as worker
from test_remote_bundle_runtime_gaps import package
from test_remote_cache_integration import local_transport


def image_item(data):
    return dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), kind='runtime_image')


def publish(tmp_path, data=b'fixture runtime' * 1000):
    root = tmp_path / 'worker/cache/artifacts/v1'
    store = tool.Cache(root)
    source = root / 'incoming/upload'
    source.write_bytes(data)
    item = image_item(data)
    store.ingest(item, source)
    return store, source, item


def test_image_publication_isolated_and_downstream_publish_reuses_inode(tmp_path):
    store, source, item = publish(tmp_path)
    obj = store.image_path(item)
    before = obj.stat()
    assert before.st_nlink == 1 and before.st_mode & 0o777 == 0o400
    assert obj.parent.stat().st_mode & 0o777 == 0o500
    assert before.st_ino != source.stat().st_ino
    source.write_bytes(b'mutable upload changed')
    assert store.probe(item)['state'] == 'cache_hit'
    same = tool.runtime_images().publish_image(obj, store.image_store, item['sha256'])
    assert same == obj and same.stat().st_ino == before.st_ino
    assert not list(Path(store.root / 'objects').rglob(item['sha256']))
    assert not list(store.image_store.rglob('.publish-*'))


@pytest.mark.parametrize('corruption', ['bytes', 'mode', 'hardlink', 'missing', 'symlink'])
def test_runtime_corruption_never_repaired(tmp_path, corruption):
    store, source, item = publish(tmp_path)
    obj = store.image_path(item)
    if corruption == 'bytes':
        obj.chmod(0o600)
        obj.write_bytes(b'x' * item['size_bytes'])
        obj.chmod(0o400)
    elif corruption == 'mode':
        obj.chmod(0o600)
    elif corruption == 'hardlink':
        os.link(obj, tmp_path / 'mutable-alias')
    else:
        obj.parent.chmod(0o700)
        obj.unlink()
        if corruption == 'symlink':
            obj.symlink_to(source)
        obj.parent.chmod(0o500)
    for action in (lambda: store.probe(item), lambda: store.ingest(item, source),
                   lambda: store.verify_runtime(item)):
        with pytest.raises((RuntimeError, OSError, ValueError)):
            action()


@pytest.mark.parametrize('bad', ['outside', 'traversal', 'parent_link', 'wrong_destination'])
def test_runtime_reference_paths_fail_closed(tmp_path, bad):
    store, source, item = publish(tmp_path)
    runtime = tmp_path / 'worker/attempts' / str(uuid.uuid4()) / 'materialized/runtime'
    alias = runtime / 'containers/name.sif'
    if bad == 'outside':
        alias = tmp_path / 'outside/name.sif'
    elif bad == 'traversal':
        alias = runtime / '../name.sif'
    elif bad == 'parent_link':
        runtime.mkdir(parents=True)
        outside = tmp_path / 'outside'
        outside.mkdir()
        (runtime / 'containers').symlink_to(outside)
    with pytest.raises((OSError, ValueError)):
        store.materialize_entry(dict(artifact=item, destination=str(store.image_path(item)) + ('x' if bad == 'wrong_destination' else ''),
                                     aliases=[str(alias)], runtime_root=str(runtime)), tmp_path / 'worker')
    assert not (tmp_path / 'outside/name.sif').exists()


@pytest.mark.asyncio
async def test_repeated_attempt_alias_explicit_dedup_and_execution_boundary(package, local_transport, tmp_path, monkeypatch):
    roots, release, job, target, command = package
    # Two controller names with identical bytes (including a managed alias).
    source = roots['containers'] / 'protenix.sif'
    explicit = roots['containers'] / 'shared/explicit.sif'
    explicit.parent.mkdir()
    source.rename(explicit)
    source.symlink_to('shared/explicit.sif')
    command += ['--protenix_container_path', str(explicit), '--literal', '$(touch NOT_EXECUTED); with spaces']
    # Archive the real boundary/helper, not a fabricated protocol response.
    repo = Path(__file__).resolve().parents[3]
    modules = ['platform/api/tools/bms_artifact_cache.py', 'scripts/lib/shared_runtime_images.py']
    if (repo / 'scripts/lib/runtime_image_lifecycle.py').is_file():
        modules.append('scripts/lib/runtime_image_lifecycle.py')
    for relative in modules:
        destination = roots['repo'] / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / relative, destination)
    archive_run = subprocess.run
    def archive(argv, **kwargs):
        if argv[:2] == ['git', 'archive']:
            import tarfile
            from types import SimpleNamespace
            with tarfile.open(fileobj=kwargs['stdout'], mode='w') as tar:
                tar.add(roots['repo'], arcname='.')
            return SimpleNamespace(returncode=0)
        return archive_run(argv, **kwargs)
    monkeypatch.setattr(bundle.subprocess, 'run', archive)
    # A real executable argv recorder stands in for Nextflow, not Apptainer/science.
    executable = Path(target.remote_root) / 'runner/nextflow'
    executable.parent.mkdir(parents=True)
    executable.write_text(f'#!{sys.executable}\nimport json,sys; print(json.dumps(sys.argv[1:]))\n')
    executable.chmod(0o700)
    calls, uploads = local_transport
    monkeypatch.setattr(cache, 'get_code_root', lambda: roots['repo'])
    monkeypatch.setattr(cache, 'current_source_identity', lambda *_: ('a' * 40, 'b' * 40))
    await cache.prewarm_cache(connection=target, job=job, command=command, source_revision='a' * 40,
                             source_tree='b' * 40, operation_id=str(uuid.uuid4()),
                             progress=cache._noop, check_fence=cache._noop)
    assert not (Path(target.remote_root) / 'attempts').exists()
    attempts = []
    for _ in range(2):
        prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
        attempts.append(prepared)
        assert len(prepared.runtime_images) == 1
        image = prepared.runtime_images[0]
        assert len(image.aliases) == 2
        assert not any(record.relative_path.endswith('.sif') for record in prepared.envelope.files)
        await cache.stage_cached_bundle(connection=target, bundle=prepared)
        for transfer in (*bundle.uncached_runtime_transfers(prepared), *prepared.input_transfers):
            destination = Path(transfer.remote_destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if transfer.source.is_dir():
                shutil.copytree(transfer.source, destination, symlinks=True)
            else:
                shutil.copyfile(transfer.source, destination)
                shutil.copymode(transfer.source, destination)
        attempt = Path(prepared.remote_attempt_dir)
        shutil.copytree(prepared.local_attempt_dir, attempt, dirs_exist_ok=True)
        (attempt / 'bundle/source').symlink_to(prepared.remote_source_dir)
        (attempt / 'bundle/runtime').symlink_to(prepared.remote_runtime_dir)
        worker.verify_bundle(attempt)
        result = subprocess.run(prepared.envelope.command, capture_output=True, text=True, check=True)
        argv = json.loads(result.stdout)
        assert argv[argv.index('--literal') + 1] == '$(touch NOT_EXECUTED); with spaces'
        assert argv[argv.index('--protenix_container_path') + 1] == image.remote_destination
        for alias in image.aliases:
            assert Path(alias).is_symlink()
            assert Path(alias).resolve() == Path(image.remote_destination)
        assert not any(p.is_file() and not p.is_symlink() for p in Path(prepared.remote_runtime_dir).rglob('*.sif'))
    assert sum(path.endswith('explicit.sif') for path in uploads) == 1
    images = list((Path(target.remote_root) / 'cache/runtime-images/objects').rglob('runtime.sif'))
    assert len(images) == 1 and images[0].stat().st_nlink == 1
    assert not list((Path(target.remote_root) / 'cache/artifacts/v1/objects').rglob(image.sha256))
    assert not list((Path(target.remote_root) / 'cache/artifacts/v1/incoming').rglob('*sif'))
    assert all(not p.is_file() for p in (Path(target.remote_root) / 'cache/artifacts/v1/incoming').rglob('*'))
    # Source mutation cannot change the worker's published image.
    explicit.write_bytes(b'changed controller')
    assert hashlib.sha256(images[0].read_bytes()).hexdigest() == image.sha256
    # Corruption between staging and execution prevents the real child from running.
    images[0].chmod(0o600)
    images[0].write_bytes(b'corrupt')
    images[0].chmod(0o400)
    failed = subprocess.run(prepared.envelope.command, capture_output=True, text=True)
    assert failed.returncode == 1 and not failed.stdout
    count = len(uploads)
    with pytest.raises(subprocess.CalledProcessError):
        # Restore controller identity so this is a corrupt hit, not a new digest.
        explicit.write_bytes(b'image-not-executed')
        retry = bundle.prepare_remote_bundle(job=job, target=target, command=command)
        await cache.stage_cached_bundle(connection=target, bundle=retry)
    assert len(uploads) == count


def _publish_process(root, item, source):
    tool.Cache(root).ingest(item, source)


def test_concurrent_runtime_publication_has_one_object(tmp_path):
    import multiprocessing
    root = tmp_path / 'worker/cache/artifacts/v1'
    tool.Cache(root)
    source = root / 'incoming/upload'
    source.write_bytes(b'x' * (2 * 1024 * 1024))
    item = image_item(source.read_bytes())
    ctx = multiprocessing.get_context('fork')
    children = [ctx.Process(target=_publish_process, args=(root, item, source)) for _ in range(4)]
    for child in children:
        child.start()
    for child in children:
        child.join(20)
        assert child.exitcode == 0
    store = tool.Cache(root)
    assert store.probe(item)['state'] == 'cache_hit'
    assert len(list(store.image_store.rglob('runtime.sif'))) == 1
    assert not list(store.image_store.rglob('.publish-*'))


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['partial', 'fenced'])
async def test_failed_runtime_upload_is_cleaned(tmp_path, local_transport, monkeypatch, failure):
    from types import SimpleNamespace
    source = tmp_path / 'source.sif'
    source.write_bytes(b'full image')
    item = image_item(source.read_bytes())
    entry = bundle.CacheTransferArtifact(source, 'containers/name.sif', item['sha256'],
                                          item['size_bytes'], 0o400, 'image')
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    transferred = False
    async def upload(connection, source, destination, **kwargs):
        nonlocal transferred
        Path(destination).write_bytes(b'partial' if failure == 'partial' else source.read_bytes())
        transferred = True
    async def fence():
        if transferred and failure == 'fenced':
            raise RuntimeError('cancelled')
    monkeypatch.setattr(cache, 'rsync_to_remote', upload)
    with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
        await cache._cache_artifacts(connection=connection, artifacts=[entry],
                                     operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=fence)
    incoming = Path(connection.remote_root) / 'cache/artifacts/v1/incoming'
    assert not any(p.is_file() for p in incoming.rglob('*'))
    store = tool.Cache(Path(connection.remote_root) / 'cache/artifacts/v1')
    assert store.probe(item)['state'] == 'missing'


def test_execution_rejects_manifest_and_alias_mutation(tmp_path):
    store, source, item = publish(tmp_path)
    runtime = tmp_path / 'worker/attempts' / str(uuid.uuid4()) / 'materialized/runtime'
    alias = runtime / 'containers/name.sif'
    store.runtime_alias(item, alias, runtime)
    manifest = runtime / '.bms-runtime-images.json'
    manifest.write_text(json.dumps(dict(schema='bms.runtime-image-references.v1', runtime_root=str(runtime),
                                       images=[dict(item, aliases=[str(alias)])])))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    argv = [sys.executable, str(Path(tool.__file__)), '--root', str(store.root),
            '--execute-runtime', str(manifest), '--manifest-sha256', digest, '--',
            sys.executable, '-c', 'print("child executed")']
    assert subprocess.run(argv, capture_output=True, text=True, check=True).stdout == 'child executed\n'
    alias.unlink()
    alias.symlink_to(source)
    failed = subprocess.run(argv, capture_output=True)
    assert failed.returncode == 1 and not failed.stdout
    alias.unlink()
    store.runtime_alias(item, alias, runtime)
    manifest.write_text('{}')
    failed = subprocess.run(argv, capture_output=True)
    assert failed.returncode == 1 and not failed.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit_selector', [False, True])
async def test_dorado_typed_selector_passes_actual_strict_sif_gate(package, local_transport, monkeypatch, explicit_selector):
    import importlib.util
    roots, release, job, target, command = package
    image = roots['containers'] / 'dorado.sif'
    image.write_bytes(b'dorado image identity fixture')
    command, _ = bundle.compile_remote_dependencies('protenix', 'predict', command)
    job.model_id = 'ont_basecall_dna'
    monkeypatch.delenv('BMS_NGS_RUNTIME_SIF', raising=False)
    if explicit_selector:
        command += ['--dorado_runtime_sif', str(image)]
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
    await cache.stage_cached_bundle(connection=target, bundle=prepared)
    argv = prepared.envelope.command
    selected = Path(argv[argv.index('--dorado_runtime_sif') + 1])
    assert not selected.is_symlink()
    assert selected == Path(prepared.runtime_images[0].remote_destination)
    assert not any(transfer.source.name == 'dorado.sif' for transfer in prepared.input_transfers)
    # Exercise the real strict gate, stopping before Apptainer/scientific execution.
    repo = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('remote_dorado_preflight', repo / 'scripts/dorado_p4_preflight.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    lock_path = repo / 'config/ngs/dorado_v1.3.1.lock.json'
    lock = native.load_lock(lock_path)
    lock['dorado']['sif_sha256'] = prepared.runtime_images[0].sha256
    monkeypatch.setattr(native, 'load_lock', lambda _: lock)
    monkeypatch.setattr(native, '_pod5_files', lambda *_: [])
    monkeypatch.setattr(native, '_read_pod5_inventory', lambda *_: ({}, set()))
    monkeypatch.setattr(native, '_validate_chemistry', lambda *_: None)
    class PassedStrictImageGate(Exception):
        pass
    def stop_before_apptainer(argv, **kwargs):
        assert argv[:3] == ['apptainer', 'exec', str(selected)]
        raise PassedStrictImageGate()
    monkeypatch.setattr(native.subprocess, 'run', stop_before_apptainer)
    kwargs = dict(lock_path=lock_path, pod5_root=roots['inputs'], molecule='dna', quality='hac',
                  mode='simplex', model_root=roots['weights'], verify_assets=True)
    with pytest.raises(PassedStrictImageGate):
        native.build_preflight(runtime_sif=selected, **kwargs)
    alias = Path(prepared.runtime_images[0].aliases[0])
    with pytest.raises(ValueError, match='SIF identity mismatch'):
        native.build_preflight(runtime_sif=alias, **kwargs)


def test_frustrampnn_strict_alias_is_blocked_at_admission(package, tmp_path):
    from services.frustrampnn import runtime as strict
    roots, release, job, target, command = package
    source = roots['containers'] / 'frustrampnn.sif'
    source.write_bytes(b'not an executed scientific image')
    alias = tmp_path / 'frustrampnn-alias.sif'
    alias.symlink_to(source)
    with pytest.raises(strict.RuntimeValidationError, match='without following symlinks'):
        strict.open_regular_no_follow(alias, label='FrustraMPNN container')
    job.model_id = 'frustrampnn'
    with pytest.raises(bundle.RemoteBundleError, match='canonical-image selector'):
        bundle.prepare_remote_bundle(job=job, target=target, command=command)
