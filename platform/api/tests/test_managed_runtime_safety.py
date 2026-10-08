"""Offline fault injection for weight copies and shared image activation."""
import errno
import hashlib
import importlib.util
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('kind', ['model', 'image', 'workflow', 'critical_runtime'])
def test_bounded_native_check_never_executes_or_certifies(kind, monkeypatch):
    managed = load('bms_managed_runtime')
    def forbidden(*args, **kwargs):
        raise AssertionError('bounded native coverage must not run a compatibility/GPU probe')
    monkeypatch.setattr(managed, 'observed_compatibility', forbidden)
    import subprocess
    monkeypatch.setattr(subprocess, 'run', forbidden)
    release = dict(selection=dict(kind=kind, model_id='fixture'), state='verified')
    result = managed.bounded_native_check(release)
    assert result['state'] == ('not_applicable' if kind == 'critical_runtime' else 'unverified')
    assert result['scope'] == 'native_runtime_preflight_only'
    if kind != 'critical_runtime':
        assert 'selected_image_native_preflight_binding' in result['missing_authorities']
        release['state'] = 'missing'
        assert managed.bounded_native_check(release)['state'] == 'blocked'


def test_workflow_manifest_accepts_only_sanitized_selection():
    managed, cache = load('bms_managed_runtime'), load('bms_artifact_cache')
    manifest = dict(selection=dict(kind='workflow', model_id='a' * 64),
                    source_revision='b' * 40, source_tree='c' * 40,
                    artifacts=[dict(name='weights/model.bin', sha256='d' * 64, size_bytes=1, mode=0o444)])
    assert len(managed.validate_manifest(manifest, cache)) == 64
    for selection in (dict(kind='workflow', model_id='model'),
                      dict(kind='workflow', model_id='A' * 64),
                      dict(kind='workflow', model_id='a' * 64, workflow_request={'sequence': 'PRIVATE'})):
        with pytest.raises(ValueError, match='invalid_selection'):
            managed.validate_manifest(manifest | {'selection': selection}, cache)
    for kind in ('model', 'image'):
        assert len(managed.validate_manifest(manifest | {'selection': dict(kind=kind, model_id='model')}, cache)) == 64


@pytest.fixture
def critical_package(tmp_path, monkeypatch, request):
    """Actual host producer and relocation; fake executable bytes, no science."""
    from services.remote_execution import critical_runtime as cr, bundle
    from services import nextflow
    version = getattr(getattr(request.node, 'callspec', None), 'params', {}).get('version', '25.10.1')
    host = tmp_path / 'host'
    support = host / 'runtime/releases/one'
    files = {'venv/bin/python': '#!/bin/sh\nprintf "BMS_SUPPORT_OK\\n"\n',
             'base/lib/python3.11/encodings/__init__.py': '# stdlib',
             'venv/lib/python3.11/site-packages/example.py': '# library'}
    for name, data in files.items():
        path = support / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
        path.chmod(0o755 if name.endswith('/python') else 0o644)
    (support / 'venv/pyvenv.cfg').write_text('home = ' + str(support / 'base') + '\n')
    (host / 'runtime/current').symlink_to(support)
    launcher = host / 'nextflow'
    launcher.write_text('#!/bin/sh\nset -eu\ntest "$NXF_OFFLINE" = true\n'
        'test -s "$NXF_HOME/framework/$NXF_VER/nextflow-$NXF_VER-one.jar"\n'
        'printf "nextflow version %s\\n" "$NXF_VER"\n')
    launcher.chmod(0o755)
    jar = host / f'nxf/framework/{version}/nextflow-{version}-one.jar'
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b'fixture JAR')
    monkeypatch.setenv('BMS_CM_API_RUNTIME_DIR', str(host / 'runtime'))
    monkeypatch.setenv('NXF_HOME', str(host / 'nxf'))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: str(launcher))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_version', lambda: version)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda root: ('a'*40, 'b'*40))
    requirements = dict(system='Linux', machine='x86_64', java='fixture Java',
                        apptainer='fixture Apptainer', driver='fixture driver')
    worker = tmp_path / 'worker'
    staging = tmp_path / 'staging'
    staging.mkdir()
    manifest, artifacts = cr.project_runtime(str(worker), staging)
    return manifest, artifacts, requirements, worker


def install_critical_fixture(package, monkeypatch):
    manifest, artifacts, requirements, worker = package
    cache, managed = load('bms_artifact_cache'), load('bms_managed_runtime')
    storage = cache.Cache(worker / 'cache/artifacts/v1')
    for entry in artifacts:
        incoming = Path(storage.root) / 'incoming' / entry.sha256
        incoming.write_bytes(entry.source.read_bytes())
        storage.ingest(dict(sha256=entry.sha256, size_bytes=entry.size_bytes), incoming)
    monkeypatch.setattr(managed, 'observed_compatibility', lambda: dict(requirements))
    return managed, cache, worker / 'managed-assets/v1'


def test_critical_projection_install_and_repeat_readback(critical_package, monkeypatch):
    manifest, artifacts, requirements, worker = critical_package
    m, cache, root = install_critical_fixture(critical_package, monkeypatch)
    assert m.observe(root, manifest, cache)['state'] == 'missing'
    assert not (root / 'active/critical_runtime-worker.json').exists()
    installed = m.install(root, manifest, m.boot_id(), cache)
    assert installed['release']['state'] == 'verified'
    assert installed['release']['critical']['observed'] == requirements
    assert m.install(root, manifest, m.boot_id(), cache)['admission']['additional_copy_bytes'] == 0
    release = m.release_path(root, manifest)
    assert str(release / 'support-python/base') in (release / 'support-python/venv/pyvenv.cfg').read_text()
    assert len(artifacts) == len(manifest['artifacts'])


@pytest.mark.parametrize('java,apptainer,driver', [
    ('openjdk version "17.0.15"', 'apptainer version 1.3.0', '550.54.14'),
    ('openjdk version "21.0.7"', 'apptainer version 1.4.2', '570.133.20'),
])
def test_critical_native_compatibility_accepts_different_tool_versions(
        critical_package, monkeypatch, java, apptainer, driver):
    from services.remote_execution import managed_inventory as mi
    manifest, _, observed, _ = critical_package
    m, cache, root = install_critical_fixture(critical_package, monkeypatch)
    actual = observed | dict(java=java, apptainer=apptainer, driver=driver)
    monkeypatch.setattr(m, 'observed_compatibility', lambda: actual)
    release = m.install(root, manifest, m.boot_id(), cache)['release']
    assert release['state'] == 'verified'
    assert release['critical']['observed'] == actual
    assert release['critical']['requirements'] == m.CRITICAL_REQUIREMENTS
    assert actual != release['critical']['requirements']
    mi.validate_observation(mi.ManagedInventory(observed_at='2026-09-10T00:00:00Z',
        boot_id=m.boot_id(), releases=[release]), [manifest])


@pytest.mark.parametrize('tool', ['java', 'apptainer', 'driver'])
def test_critical_missing_native_capability_remains_incompatible(critical_package, monkeypatch, tool):
    manifest, _, observed, _ = critical_package
    m, cache, root = install_critical_fixture(critical_package, monkeypatch)
    monkeypatch.setattr(m, 'observed_compatibility', lambda: observed | {tool: ''})
    with pytest.raises(ValueError, match='critical_runtime_incompatible'):
        m.install(root, manifest, m.boot_id(), cache)
    assert not (root / 'active/critical_runtime-worker.json').exists()


def test_critical_reboot_keeps_bytes_but_requires_native_reactivation(critical_package, monkeypatch):
    manifest, _, _, _ = critical_package
    m, cache, root = install_critical_fixture(critical_package, monkeypatch)
    m.install(root, manifest, m.boot_id(), cache)
    marker = root / 'active/critical_runtime-worker.json'
    prior = marker.read_bytes()
    monkeypatch.setattr(m, 'boot_id', lambda: '00000000-0000-4000-8000-000000000001')
    observed = m.observe(root, manifest, cache)
    assert observed['state'] == 'unverified'
    assert all(row['state'] == 'verified' for row in observed['artifacts'])
    assert marker.read_bytes() == prior


@pytest.mark.parametrize('member', ['jar', 'python'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_critical_member_damage_never_ready(critical_package, monkeypatch, member, damage):
    manifest, _, _, _ = critical_package
    m, cache, root = install_critical_fixture(critical_package, monkeypatch)
    m.install(root, manifest, m.boot_id(), cache)
    marker = root / 'active/critical_runtime-worker.json'
    prior = marker.read_bytes()
    path = m.release_path(root, manifest) / manifest['critical']['entrypoints'][member]
    if damage == 'missing':
        path.unlink()
    else:
        path.chmod(0o600)
        path.write_bytes(b'corrupt')
    assert m.observe(root, manifest, cache)['state'] in {'partial', 'corrupt', 'incompatible'}
    with pytest.raises(ValueError, match='active_generation_damaged'):
        m.install(root, manifest, m.boot_id(), cache)
    assert marker.read_bytes() == prior


def test_critical_incompatible_interrupted_preserves_prior(critical_package, monkeypatch):
    import copy
    manifest, _, requirements, _ = critical_package
    m, cache, root = install_critical_fixture(critical_package, monkeypatch)
    m.install(root, manifest, m.boot_id(), cache)
    marker = root / 'active/critical_runtime-worker.json'
    prior = marker.read_bytes()
    successor = copy.deepcopy(manifest)
    successor['critical']['installation_id'] = 'e'*64
    monkeypatch.setattr(m, 'observed_compatibility', lambda: requirements | {'machine': 'different'})
    with pytest.raises(ValueError, match='critical_runtime_incompatible'):
        m.install(root, successor, m.boot_id(), cache)
    assert marker.read_bytes() == prior
    monkeypatch.setattr(m, 'observed_compatibility', lambda: dict(requirements))
    publish = m.publish
    def interrupt(path, value, helper):
        if path == marker:
            raise OSError(errno.ENOSPC, 'injected')
        return publish(path, value, helper)
    monkeypatch.setattr(m, 'publish', interrupt)
    with pytest.raises(OSError):
        m.install(root, successor, m.boot_id(), cache)
    assert marker.read_bytes() == prior
    assert m.observe(root, successor, cache)['state'] == 'unverified'
    assert m.observe(root, manifest, cache)['state'] == 'verified'


def test_critical_incomplete_manifest_rejected_before_copy(critical_package, monkeypatch):
    manifest, _, _, _ = critical_package
    m, cache, root = install_critical_fixture(critical_package, monkeypatch)
    manifest['artifacts'] = [r for r in manifest['artifacts'] if r['name'] != manifest['critical']['entrypoints']['jar']]
    with pytest.raises(ValueError, match='incomplete_critical_manifest'):
        m.install(root, manifest, m.boot_id(), cache)
    assert not root.exists()


@pytest.fixture
def tree(tmp_path):
    cache = load('bms_artifact_cache')
    managed = load('bms_managed_runtime')
    root = tmp_path / 'worker/managed-assets/v1'
    storage = cache.Cache(tmp_path / 'worker/cache/artifacts/v1')
    def manifest(data=b'old image', names=('weights/test.sif',)):
        digest = hashlib.sha256(data).hexdigest()
        source = Path(storage.root) / 'incoming' / digest
        source.write_bytes(data)
        row = dict(sha256=digest, size_bytes=len(data))
        storage.ingest(row, source)
        return dict(selection=dict(kind='model', model_id='test'), source_revision='a'*40,
                    source_tree='b'*40,
                    artifacts=[dict(row, name=name, mode=0o644) for name in names])
    prior = manifest()
    managed.install(root, prior, managed.boot_id(), cache)
    marker = root / 'active/model-test.json'
    return managed, cache, root, manifest, prior, marker


def test_shared_image_activation_reuses_authoritative_object(tmp_path, monkeypatch):
    cache, m = load('bms_artifact_cache'), load('bms_managed_runtime')
    root = tmp_path / 'worker/managed-assets/v1'
    storage = cache.Cache(tmp_path / 'worker/cache/artifacts/v1')
    data = b'shared SIF'
    row = dict(name='containers/test.sif', sha256=hashlib.sha256(data).hexdigest(),
               size_bytes=len(data), mode=0o644, kind='runtime_image')
    manifest = dict(selection=dict(kind='image', model_id='test'), source_revision='a'*40,
                    source_tree='b'*40, artifacts=[row])
    source = Path(storage.root) / 'incoming/image'
    source.write_bytes(data)
    storage.ingest(row, source)
    image = storage.verify_runtime(row)
    before = image.stat()
    def forbidden(*args, **kwargs):
        raise AssertionError('managed image activation must not copy a SIF')
    monkeypatch.setattr(cache.Cache, '_publish_copy', forbidden)
    for selection in ('image', 'model'):
        selected = manifest | {'selection': dict(kind=selection, model_id='test')}
        result = m.install(root, selected, m.boot_id(), cache)
        assert result['release']['state'] == 'verified'
        assert result['admission']['additional_copy_bytes'] == 0
    after = image.stat()
    assert all(getattr(after, key) == getattr(before, key) for key in
               ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
    assert list(root.rglob('*.sif')) == []
    assert list((Path(storage.root) / 'objects').rglob('*')) == [Path(storage.root) / 'objects/sha256']
    # A legacy manifest must not bless even correctly hashed old copied bytes.
    legacy = manifest | {'artifacts': [{k: v for k, v in row.items() if k != 'kind'}]}
    with pytest.raises(ValueError, match='legacy_or_invalid_image_storage'):
        m.install(root, legacy, m.boot_id(), cache)
    image.chmod(0o600)
    image.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='active_generation_damaged'):
        m.install(root, manifest, m.boot_id(), cache)
    assert m.observe(root, manifest, cache)['state'] == 'corrupt'
    assert image.read_bytes() == b'corrupt'


def test_image_observation_is_read_only_when_store_absent(tmp_path):
    cache, m = load('bms_artifact_cache'), load('bms_managed_runtime')
    root = tmp_path / 'worker/managed-assets/v1'
    manifest = dict(selection=dict(kind='image', model_id='test'), source_revision='a'*40,
                    source_tree='b'*40, artifacts=[dict(name='containers/test.sif', sha256='c'*64,
                        size_bytes=1, mode=0o644, kind='runtime_image')])
    assert m.observe(root, manifest, cache)['state'] == 'missing'
    assert not list(tmp_path.iterdir())


def test_missing_copy_accounting_and_reuse(tree, monkeypatch):
    m, cache, root, make, prior, marker = tree
    successor = make(b'next', ('weights/a.sif', 'weights/b.sif'))
    result = m.install(root, successor, m.boot_id(), cache)
    assert result['admission']['additional_copy_bytes'] >= 2 * len(b'next')
    assert result['admission']['reservation'] is False
    def forbidden(*args, **kwargs):
        raise AssertionError('verified installed leaves must not be copied again')
    monkeypatch.setattr(cache.Cache, '_publish_copy', forbidden)
    result = m.install(root, successor, m.boot_id(), cache)
    assert result['admission']['additional_copy_bytes'] == 0
    assert (root / 'releases' / m.validate_manifest(prior, cache) / 'weights/test.sif').read_bytes() == b'old image'


def test_low_space_rejects_before_first_copy(tree, monkeypatch):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new')
    before = marker.read_bytes()
    monkeypatch.setattr(m.os, 'fstatvfs', lambda fd: SimpleNamespace(f_frsize=4096, f_bsize=4096, f_bavail=1))
    with pytest.raises(OSError) as exc:
        m.install(root, successor, m.boot_id(), cache)
    assert exc.value.errno == errno.ENOSPC
    assert marker.read_bytes() == before
    assert not (root / 'releases' / m.validate_manifest(successor, cache)).exists()


@pytest.mark.parametrize('failure', ['write', 'file_fsync', 'manifest_replace', 'active_replace'])
def test_real_syscall_faults_retain_prior_identity(tree, monkeypatch, failure):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new image' * 100)
    before = marker.read_bytes()
    write, fsync, replace = os.write, os.fsync, os.replace
    def fail_write(fd, data):
        if not Path('/proc/self/fd/' + str(fd)).resolve().name.startswith('.partial-'):
            return write(fd, data)
        write(fd, data[:3])  # actually create partial bytes before ENOSPC
        raise OSError(errno.ENOSPC, 'injected')
    def fail_fsync(fd):
        if Path('/proc/self/fd/' + str(fd)).resolve().name.startswith('.partial-'):
            raise OSError(errno.ENOSPC, 'injected')
        return fsync(fd)
    def fail_replace(src, dst, **kwargs):
        if dst == ('manifest.json' if failure == 'manifest_replace' else marker.name):
            raise OSError(errno.ENOSPC, 'injected')
        return replace(src, dst, **kwargs)
    with monkeypatch.context() as patch:
        if failure == 'write': patch.setattr(os, 'write', fail_write)
        elif failure == 'file_fsync': patch.setattr(os, 'fsync', fail_fsync)
        else: patch.setattr(os, 'replace', fail_replace)
        with pytest.raises(OSError) as exc:
            m.install(root, successor, m.boot_id(), cache)
        assert exc.value.errno == errno.ENOSPC
    assert marker.read_bytes() == before
    assert m.observe(root, prior, cache)['state'] == 'verified'
    assert not list(root.rglob('.partial-*'))
    assert not list(root.rglob('.publish-*'))
    assert m.install(root, successor, m.boot_id(), cache)['release']['state'] == 'verified'


def test_abrupt_process_death_before_publication_and_retry(tree):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new image' * 100)
    before = marker.read_bytes()
    def child():
        original = os.write
        def die(fd, data):
            original(fd, data[:7])
            os._exit(91)  # no finally cleanup, no graceful Python exception
        os.write = die
        m.install(root, successor, m.boot_id(), cache)
    process = multiprocessing.get_context('fork').Process(target=child)
    process.start()
    process.join(10)
    assert process.exitcode == 91
    assert marker.read_bytes() == before
    orphans = list(root.rglob('.partial-*'))
    assert len(orphans) == 1 and orphans[0].read_bytes() == b'new ima'
    assert m.install(root, successor, m.boot_id(), cache)['release']['state'] == 'verified'
    assert orphans[0].exists()  # no opportunistic GC or existing asset deletion
    assert (root / 'releases' / m.validate_manifest(prior, cache)).exists()


def test_admission_lock_real_competing_process_and_release(tree):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new')
    receiver, sender = multiprocessing.Pipe(duplex=False)
    def child():
        try:
            m.install(root, successor, m.boot_id(), cache)
        except BlockingIOError:
            sender.send('busy')
        else:
            sender.send('unexpected success')
    with m.admission_lock(root, cache):
        process = multiprocessing.get_context('fork').Process(target=child)
        process.start()
        assert receiver.poll(10)
        assert receiver.recv() == 'busy'
        process.join(10)
        assert process.exitcode == 0
    assert m.install(root, successor, m.boot_id(), cache)['release']['state'] == 'verified'


@pytest.mark.parametrize('change', ['root', 'lock'])
def test_inode_replacement_fenced(tree, change):
    m, cache, root, make, prior, marker = tree
    with m.admission_lock(root, cache) as (_, fence):
        if change == 'root':
            root.rename(root.with_name('retained'))
            root.mkdir()
        else:
            (root / '.admission.lock').rename(root / '.retained-lock')
            (root / '.admission.lock').touch()
        with pytest.raises(ValueError, match='changed'):
            fence()


def test_symlink_parent_cannot_redirect_copy(tree, tmp_path):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new')
    before = marker.read_bytes()
    release = root / 'releases' / m.validate_manifest(successor, cache)
    release.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (release / 'weights').symlink_to(outside)
    with pytest.raises(OSError):
        m.install(root, successor, m.boot_id(), cache)
    assert list(outside.iterdir()) == []
    assert marker.read_bytes() == before


def test_boot_change_during_copy_prevents_activation(tree, monkeypatch):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new')
    before = marker.read_bytes()
    boot = m.boot_id()
    original = cache.Cache._publish_copy
    def copy_then_reboot(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(m, 'boot_id', lambda: 'changed')
        return result
    monkeypatch.setattr(cache.Cache, '_publish_copy', copy_then_reboot)
    with pytest.raises(ValueError, match='worker_boot_changed'):
        m.install(root, successor, boot, cache)
    assert marker.read_bytes() == before


def test_damaged_active_generation_is_not_rewritten(tree):
    m, cache, root, make, prior, marker = tree
    leaf = root / 'releases' / m.validate_manifest(prior, cache) / 'weights/test.sif'
    leaf.chmod(0o600)
    leaf.write_bytes(b'damage')
    with pytest.raises(ValueError, match='active_generation_damaged'):
        m.install(root, prior, m.boot_id(), cache)
    assert leaf.read_bytes() == b'damage'


def test_after_active_rename_fsync_failure_is_ambiguous_but_complete(tree, monkeypatch):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new')
    original_replace, original_fsync = os.replace, os.fsync
    published = False
    def replace(src, dst, **kw):
        nonlocal published
        result = original_replace(src, dst, **kw)
        if dst == marker.name:
            published = True
        return result
    def fsync(fd):
        if published:
            raise OSError(errno.EIO, 'injected after commit point')
        return original_fsync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        patch.setattr(os, 'fsync', fsync)
        with pytest.raises(OSError):
            m.install(root, successor, m.boot_id(), cache)
    # Do not claim exceptions always mean rollback: rename already committed.
    # New pointer references complete bytes; old bytes remain, no rollback race.
    assert m.observe(root, successor, cache)['state'] == 'verified'
    assert (root / 'releases' / m.validate_manifest(prior, cache)).exists()


def test_mid_copy_headroom_drop_aborts_and_cleans_partial(tree, monkeypatch):
    m, cache, root, make, prior, marker = tree
    successor = make(b'x' * (cache.CHUNK * 2))
    before = marker.read_bytes()
    original_write, original_stat = os.write, os.fstatvfs
    wrote = False
    def write(fd, data):
        nonlocal wrote
        result = original_write(fd, data)
        if Path('/proc/self/fd/' + str(fd)).resolve().name.startswith('.partial-'):
            wrote = True
        return result
    def space(fd):
        if wrote:
            return SimpleNamespace(f_frsize=4096, f_bsize=4096, f_bavail=1)
        return original_stat(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'write', write)
        patch.setattr(os, 'fstatvfs', space)
        with pytest.raises(OSError, match='managed_space_floor_reached'):
            m.install(root, successor, m.boot_id(), cache)
    assert marker.read_bytes() == before
    assert not list(root.rglob('.partial-*'))


def test_parent_renamed_during_copy_cannot_activate(tree, monkeypatch):
    m, cache, root, make, prior, marker = tree
    successor = make(b'new')
    before = marker.read_bytes()
    original = cache.Cache._publish_copy
    def rename_after_copy(storage, source, out, name, row, mode):
        original(storage, source, out, name, row, mode)
        path = root / 'releases' / m.validate_manifest(successor, cache) / 'weights'
        path.rename(path.with_name('retained'))
        path.mkdir()
    monkeypatch.setattr(cache.Cache, '_publish_copy', rename_after_copy)
    with pytest.raises(ValueError, match='managed_directory_changed'):
        m.install(root, successor, m.boot_id(), cache)
    assert marker.read_bytes() == before


def test_preupload_peak_budget_is_conservative_and_not_reserved(tree):
    m, cache, root, make, prior, marker = tree
    with cache.directory(root) as parent:
        budget = m.check_space(parent, prior, prior['artifacts'])
    admitted = m.admit(root, prior, m.boot_id(), cache)['admission']
    assert admitted['additional_copy_bytes'] == 3 * budget['additional_copy_bytes']
    assert admitted['reservation'] is False
    assert admitted['headroom_bytes'] == 1024 * 1024 * 1024


def test_preupload_rejects_cache_on_another_device(tree, monkeypatch):
    m, cache, root, make, prior, marker = tree
    original = os.fstat
    def different_device(fd):
        result = original(fd)
        if Path('/proc/self/fd/' + str(fd)).resolve().name == 'incoming':
            return SimpleNamespace(st_dev=result.st_dev + 1)
        return result
    monkeypatch.setattr(os, 'fstat', different_device)
    with pytest.raises(ValueError, match='managed_filesystem_changed'):
        m.admit(root, prior, m.boot_id(), cache)


def test_manifest_path_prefix_collision_rejected(tree):
    m, cache, root, make, prior, marker = tree
    manifest = make(b'new', ('weights/a', 'weights/a/b'))
    with pytest.raises(ValueError, match='conflicting_artifact_paths'):
        m.install(root, manifest, m.boot_id(), cache)
