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
from component_runtime import NativeInvocation, SourceIdentity, canonical_bytes
from dataclasses import replace
from tools import bms_artifact_cache as tool, bms_remote_worker as worker
from test_remote_bundle_runtime_gaps import package
from test_remote_cache_integration import local_transport


def attach_selected_plan(invocation, roots, *, fixture_metadata=None):
    """Runtime-reader fixtures still carry the real selected metadata contract."""
    from services.nextflow import build_selected_execution_plan
    plan = build_selected_execution_plan(model_id=invocation.model_id, mode=invocation.mode,
        entrypoint=invocation.entrypoint, requested=invocation.requested_json,
        effective=invocation.effective_json, native_parameters=invocation.native_parameters,
        source_identity=invocation.source_identity)
    if fixture_metadata is not None:
        # Explicit lower-layer runtime transport scope; never native-science evidence.
        plan = replace(plan, metadata=fixture_metadata)
    repo = Path(__file__).resolve().parents[3]
    for dependency in plan.dependencies:
        if dependency.kind == 'support_tool':
            assert dependency.relative_path is not None
            destination = roots['repo'] / dependency.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / dependency.relative_path, destination)
    return replace(invocation, execution_plan=plan)


@pytest.mark.parametrize('dynamic', [False, True])
def test_selected_native_runtime_binds_local_context_only_when_needed(package, monkeypatch, dynamic):
    """Exercise the real compiler/context boundary, not a scientific run."""
    from services import nextflow
    roots, _, job, _, _ = package
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: SourceIdentity('a'*40, 'b'*40))
    job.provenance = {}
    job.params = dict(sequence='AAAA', protenix_use_msa=False,
                      msa_provider='colabfold_api', run_frustrampnn=dynamic, gpu_id=0)
    invocation = nextflow.compile_job_nextflow_invocation(job, dict(job.params), job.output_dir)
    environment = {'BMS_WORK': str(roots['data'] / 'work')}
    original = list(invocation.command)
    command = nextflow._component_launch_command(invocation, job, original, environment,
                                                attempt=1, output_dir=job.output_dir)
    if not dynamic:
        assert command == original
        assert 'BMS_COMPONENT_CONTEXT' not in environment
        return
    context_path = Path(environment['BMS_COMPONENT_CONTEXT'])
    context = json.loads(context_path.read_text())
    assert command[-2:] == ['--context', str(context_path)]
    assert environment['APPTAINERENV_BMS_COMPONENT_CONTEXT'] == str(context_path)
    assert context['root_command'] == original
    assert context['source_identity'] == {'revision': 'a'*40, 'tree': 'b'*40}
    assert context['execution_plan'] == invocation.execution_plan.to_dict()
    assert context['parent']['params'] == invocation.native_parameters
    assert context['target_id'] == 'local'
    assert context['parent']['output_dir'] == str(job.output_dir)
    assert context['resources']['gpu_id'] == job.assigned_gpu


@pytest.mark.asyncio
async def test_actual_local_compiler_image_pin_replays_retained_bytes_and_rejects_swaps(package, monkeypatch):
    """Real compiler/lease boundary; published fixture bytes are not science."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from services import nextflow
    import paths
    from services.remote_execution.images import IMAGE_SELECTORS, image_environment_key
    from lib.shared_runtime_images import publish_image
    from lib.runtime_image_lifecycle import commit_release, transaction, load_state
    roots, _, job, _, _ = package
    root = roots['containers'] / '.image-store'
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(root))
    monkeypatch.setattr(paths, 'get_container_dir', lambda: roots['containers'])
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: SourceIdentity('a'*40, 'b'*40))
    job.provenance = {}
    job.params = dict(sequence='AAAA', protenix_use_msa=False,
                      msa_provider='colabfold_api', run_frustrampnn=False, gpu_id=0)
    invocation = nextflow.compile_job_nextflow_invocation(job, dict(job.params), job.output_dir)
    registered, expected = {}, {}
    for dependency in invocation.execution_plan.dependencies:
        if dependency.kind != 'image':
            continue
        name = Path(dependency.relative_path).name
        source = roots['containers'] / name
        if not source.exists():
            source.write_bytes(('explicit test image: ' + name).encode())
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        image = publish_image(source, root, digest)
        selector = IMAGE_SELECTORS.get(name, (None, 'BMS_RUNTIME_IMAGE_' +
            image_environment_key(name).removeprefix('BMS_SELECTED_IMAGE_')))[1]
        registered[selector] = {'path': str(image), 'sha256': digest}
        expected[name] = image
        source.unlink()
    with transaction(root):
        commit_release(root, 'production', registered)
    invocation = nextflow.compile_job_nextflow_invocation(job, dict(job.params), job.output_dir)
    session = SimpleNamespace(commit=AsyncMock())
    first = await nextflow._pin_local_invocation_images(session, job, invocation)
    receipt = job.provenance['runtime_image_references'][invocation.execution_plan.plan_sha256]
    assert receipt['legacy_images'] == []
    assert receipt['lease_token'] in load_state(root)['leases']
    for name, image in expected.items():
        assert first[image_environment_key(name)] == str(image)
        assert not (roots['containers'] / name).exists()
    # A later installer promotion must not silently change a retry's bytes.
    replacement = roots['containers'] / 'later-test-image'
    replacement.write_bytes(b'explicit later fixture image')
    digest = hashlib.sha256(replacement.read_bytes()).hexdigest()
    newer = publish_image(replacement, root, digest)
    replacement.unlink()
    registered['BMS_PROTENIX_CONTAINER_PATH'] = {'path': str(newer), 'sha256': digest}
    with transaction(root):
        commit_release(root, 'production', registered)
    second = await nextflow._pin_local_invocation_images(session, job, invocation)
    assert second == first
    assert session.commit.await_count == 2
    assert job.provenance['runtime_image_references'][invocation.execution_plan.plan_sha256]['lease_token'] == receipt['lease_token']
    # Even another valid retained image cannot be substituted in provenance.
    receipt['environment'][image_environment_key('protenix.sif')] = str(newer)
    job.provenance['runtime_image_references'][invocation.execution_plan.plan_sha256] = receipt
    with pytest.raises(ValueError, match='does not match execution identity'):
        await nextflow._pin_local_invocation_images(session, job, invocation)
    assert session.commit.await_count == 2


@pytest.mark.parametrize('link_kind', ['leaf', 'ancestor'])
def test_runtime_image_projection_rejects_symlinks_before_resolving(package, monkeypatch, link_kind):
    from component_runtime import NativeInvocation
    from services.remote_execution import bundle
    roots, _, job, _, _ = package
    real = roots['containers'].parent/'selected-image'
    real.mkdir()
    (real/'image.sif').write_bytes(b'PATH-GUARD FIXTURE, NOT A SCIENTIFIC IMAGE')
    if link_kind == 'leaf':
        selected = roots['containers']/'linked.sif'
        selected.symlink_to(real/'image.sif')
    else:
        alias = roots['containers']/'linked-root'
        alias.symlink_to(real, target_is_directory=True)
        selected = alias/'image.sif'
    invocation = NativeInvocation.capture(command=['nextflow', 'run', 'structure_prediction.nf'],
        model_id=job.model_id, mode=job.mode, entrypoint='structure_prediction.nf',
        requested=job.params, effective=job.params, native_parameters=job.params)
    invocation = attach_selected_plan(replace(invocation, source_identity=SourceIdentity('a'*40, 'b'*40)), roots)
    monkeypatch.setattr(bundle, 'resolve_image', lambda *args: selected)
    with pytest.raises(bundle.RemoteBundleError, match='no-follow'):
        bundle._runtime_assets(job.model_id, job.mode, job.params, native_invocation=invocation,
                               only_kinds=frozenset({'image'}))


def test_native_metadata_digest_matches_final_cache_and_bundle_inventory(tmp_path):
    from types import SimpleNamespace
    from model_registry import native_checkpoint_dependencies
    from services.remote_execution import bundle
    files = []
    for family in ('score_model', 'confidence_model'):
        path = tmp_path/'diffdock/workdir/v1.1'/family/'model_parameters.yml'
        path.parent.mkdir(parents=True)
        path.write_text('esm_embeddings_model: precomputed\n')
        files.append(path)
    dependencies, blockers = native_checkpoint_dependencies('RunDiffDock', {'weights_root': str(tmp_path)})
    assert not blockers
    plan = SimpleNamespace(dependencies=dependencies)
    def actual_inventory():
        return {record.relative_path: record.sha256 for path in files
                for record in bundle._records_for_source(path, 'weights/' + path.relative_to(tmp_path).as_posix(), 'runtime')}
    bundle.verify_selected_runtime_hashes(plan, actual_inventory())
    files[0].write_text('esm_embeddings_model: esm2_t6_8M_UR50D\n')
    with pytest.raises(bundle.RemoteBundleError, match='metadata changed or is missing'):
        bundle.verify_selected_runtime_hashes(plan, actual_inventory())
    with pytest.raises(bundle.RemoteBundleError, match='metadata changed or is missing'):
        bundle.verify_selected_runtime_hashes(plan, {})


def test_shared_plan_metadata_root_is_server_owned(monkeypatch, tmp_path):
    import model_registry
    import paths
    from services import nextflow
    monkeypatch.setattr(paths, 'get_weights_root', lambda: tmp_path/'managed')
    seen = []
    owner = model_registry.selected_execution_metadata
    def capture(model, mode, params, entrypoint):
        seen.append(dict(params))
        return owner(model, mode, params, entrypoint)
    monkeypatch.setattr(model_registry, 'selected_execution_metadata', capture)
    requested = {'sequence': 'AAAA', 'protenix_use_msa': False, 'weights_root': '/untrusted/runtime'}
    plan = nextflow.build_selected_execution_plan(model_id='protenix', mode='predict',
        entrypoint='structure_prediction.nf', requested=requested, effective=requested,
        native_parameters=requested, source_identity=SourceIdentity('a'*40, 'b'*40))
    assert seen[0]['weights_root'] == str(tmp_path/'managed')
    assert json.loads(plan.requested_json) == requested
    assert json.loads(plan.effective_json) == requested


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


def test_attempt_reference_is_pinned_before_alias_and_survives_recovery(tmp_path, monkeypatch):
    store, source, item = publish(tmp_path)
    runtime = tmp_path / 'worker/attempts' / str(uuid.uuid4()) / 'materialized/runtime'
    alias = runtime / 'containers/name.sif'
    authority = tool.runtime_lifecycle()
    real_symlink = os.symlink
    owner = 'attempt:' + runtime.parts[-3] + ':image:' + item['sha256']
    def expose(target, name, **kwargs):
        leases = authority.load_state(store.image_store)['leases']
        assert any(row['owner'] == owner and item['sha256'] in row['identities'] for row in leases.values())
        return real_symlink(target, name, **kwargs)
    monkeypatch.setattr(os, 'symlink', expose)
    store.runtime_alias(item, alias, runtime)
    state = authority.load_state(store.image_store)
    recovered = tool.Cache(store.root)
    recovered.runtime_alias(item, alias, runtime, check=True)
    recovered.runtime_alias(item, alias, runtime)  # repeated materialization is exact/idempotent
    assert authority.load_state(store.image_store) == state
    assert {row['owner'] for row in state['leases'].values()} == {owner, 'cache-artifact:' + item['sha256']}
    assert alias.resolve() == store.image_path(item)
    assert store.image_path(item).stat().st_nlink == 1


def test_managed_activation_retains_prior_image_leases_before_exposure(tmp_path, monkeypatch):
    from tools import bms_managed_runtime as managed
    store, source, item = publish(tmp_path)
    root = tmp_path / 'worker/runtime/managed-v1'
    manifest = dict(selection=dict(kind='image', model_id='fixture'), source_revision='a' * 40,
                    source_tree='b' * 40, artifacts=[dict(item, name='containers/name.sif', mode=0o400)])
    real_publish = managed.publish
    prior_state = tool.runtime_lifecycle().load_state(store.image_store)
    with pytest.raises(ValueError, match='managed_image_reference_missing_or_changed'):
        managed.retained_image_reference(root, manifest, tool)
    assert tool.runtime_lifecycle().load_state(store.image_store) == prior_state
    def expose(path, value, cache):
        reference = managed.retained_image_reference(root, manifest, tool)
        assert item['sha256'] in reference['identities']
        return real_publish(path, value, cache)
    monkeypatch.setattr(managed, 'publish', expose)
    first = managed.activate(root, manifest, managed.boot_id(), tool)
    token = first['image_reference']['lease_token']
    authority = tool.runtime_images()
    verify = authority.verify_image
    scans = []
    def counted_verify(path, digest):
        scans.append(digest)
        return verify(path, digest)
    with monkeypatch.context() as count:
        count.setattr(authority, 'verify_image', counted_verify)
        count.setattr(tool, 'runtime_images', lambda: authority)
        assert managed.observe(root, manifest, tool)['state'] == 'verified'
    assert scans == [item['sha256']]
    manifest = dict(manifest, source_tree='c' * 40)
    second = managed.activate(root, manifest, managed.boot_id(), tool)
    state = tool.runtime_lifecycle().load_state(store.image_store)
    assert token in state['leases'] and second['image_reference']['lease_token'] in state['leases']
    assert token != second['image_reference']['lease_token']
    assert len(list(store.image_store.rglob('runtime.sif'))) == 1


def test_activation_adopts_preintegration_retained_manifest(tmp_path):
    from tools import bms_managed_runtime as managed
    store, source, item = publish(tmp_path)
    root = tmp_path / 'worker/runtime/managed-v1'
    old = dict(selection=dict(kind='image', model_id='fixture'), source_revision='a' * 40,
               source_tree='b' * 40, artifacts=[dict(item, name='containers/name.sif', mode=0o400)])
    old_path = managed.release_path(root, old)
    old_path.mkdir(parents=True)
    managed.publish(old_path / 'manifest.json', old, tool)
    new = dict(old, source_tree='c' * 40)
    managed.activate(root, new, managed.boot_id(), tool)
    reference = managed.retained_image_reference(root, old, tool)
    assert item['sha256'] in reference['identities']
    assert managed.read_bytes(old_path / 'manifest.json', tool) == managed.canonical(old)


def test_generic_selected_release_local_binding_without_conventional_image(tmp_path, monkeypatch):
    from services.remote_execution import images
    from lib import runtime_image_lifecycle as authority
    from lib.shared_runtime_images import publish_image
    containers = tmp_path / 'containers'
    root = containers / '.image-store'
    source = tmp_path / 'upload'
    source.write_bytes(b'boltz fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    path = publish_image(source, root, digest)
    with authority.transaction(root):
        authority.commit_release(root, 'production', {'BMS_RUNTIME_IMAGE_BOLTZ2_SIF': dict(sha256=digest, path=str(path))})
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(root))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'production')
    selected = images.resolve_image('boltz2.sif', containers)
    assert selected == path and not (containers / 'boltz2.sif').exists()
    receipt = images.bind_local_image_references({'boltz2.sif': selected}, containers, owner='local-attempt:fixture')
    assert receipt['environment']['BMS_SELECTED_IMAGE_BOLTZ2_SIF'] == str(path)
    assert receipt['legacy_images'] == []
    assert authority.load_state(root)['leases'][receipt['lease_token']]['identities'] == receipt['identities']
    assert images.bind_local_image_references({'boltz2.sif': selected}, containers, owner='local-attempt:fixture') == receipt


@pytest.mark.parametrize('name,selector', [
    ('protenix.sif', 'BMS_PROTENIX_CONTAINER_PATH'),
    ('confornets-canonical.sif', 'BMS_CM_CONFORNETS_CONTAINER_PATH'),
    ('frustrampnn.sif', 'BMS_FRUSTRAMPNN_SIF'),
    ('dorado.sif', 'BMS_NGS_RUNTIME_SIF'),
])
def test_local_binding_emits_native_explicit_image_selectors(tmp_path, monkeypatch, name, selector):
    from services.remote_execution import images
    from lib import runtime_image_lifecycle as authority
    containers = tmp_path / 'containers'
    root = containers / '.image-store'
    source = tmp_path / 'upload'
    source.write_bytes(b'selector fixture, no scientific execution')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    path = tool.runtime_images().publish_image(source, root, digest)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(root))
    receipt = images.bind_local_image_references({name: path}, containers, owner='local-attempt:selector')
    assert receipt['environment'][selector] == str(path)
    assert receipt['environment'][images.image_environment_key(name)] == str(path)
    assert authority.load_state(root)['leases'][receipt['lease_token']]['owner'] == receipt['owner']
    assert images.bind_local_image_references({name: path}, containers, owner=receipt['owner']) == receipt
    assert path.stat().st_nlink == 1


@pytest.mark.asyncio
async def test_worker_helper_packages_required_lifecycle_peer(tmp_path, local_transport):
    from types import SimpleNamespace
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker'))
    installed = Path(await cache._install_helper(connection, cache._noop))
    repo = Path(__file__).resolve().parents[3]
    for name in ('runtime_image_lifecycle.py', 'shared_runtime_images.py'):
        assert installed.with_name(name).read_bytes() == (repo / 'scripts/lib' / name).read_bytes()
    # Import the installed peer from an unrelated working directory, not checkout sys.path.
    result = subprocess.run([sys.executable, '-I', '-c',
        'import importlib.util,sys; '
        's=importlib.util.spec_from_file_location("cache_peer",sys.argv[1]); '
        'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
        'print(m.runtime_lifecycle().__file__)', str(installed)],
        cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == str(installed.with_name('runtime_image_lifecycle.py'))


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
async def test_actual_job_compiler_no_original_prewarm_provision_and_repeated_execution(package, local_transport, tmp_path, monkeypatch):
    roots, release, job, target, command = package
    from services import nextflow
    from lib.shared_runtime_images import publish_image
    from lib.runtime_image_lifecycle import commit_release, transaction
    source = roots['containers'] / 'protenix.sif'
    root = roots['containers'] / '.image-store'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    explicit = publish_image(source, root, digest)
    with transaction(root):
        commit_release(root, 'production', {'BMS_PROTENIX_CONTAINER_PATH':
                       {'path': str(explicit), 'sha256': digest}})
    source.unlink()
    monkeypatch.setenv('BMS_PROTENIX_CONTAINER_PATH', str(explicit))
    # Actual persisted Job compiler, not a hand-written selector-bearing argv.
    job.params = dict(sequence='AAAA', protenix_use_msa=False,
                      msa_provider='colabfold_api', run_frustrampnn=False,
                      literal='$(touch NOT_EXECUTED); with spaces')
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: SourceIdentity('a'*40, 'b'*40))
    invocations = []
    command = nextflow.build_job_nextflow_command(job, dict(job.params), job.output_dir,
                                                  native_invocations=invocations)
    assert len(invocations) == 1
    job.native_invocation = attach_selected_plan(invocations[0], roots)
    assert tuple(command) == job.native_invocation.command
    assert '--protenix_container_path' not in command
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
    executable.write_text(f'#!{sys.executable}\n'
        'import json,os,sys\nfrom pathlib import Path\n'
        'image=Path(os.environ["BMS_PROTENIX_CONTAINER_PATH"])\n'
        'print(json.dumps(dict(argv=sys.argv[1:], image=str(image), inode=image.stat().st_ino)))\n')
    executable.chmod(0o700)
    calls, uploads = local_transport
    monkeypatch.setattr(cache, 'get_code_root', lambda: roots['repo'])
    monkeypatch.setattr(cache, 'current_source_identity', lambda *_: ('a' * 40, 'b' * 40))
    from types import SimpleNamespace
    entries = cache.independent_plan(SimpleNamespace(kind='image', model_id='protenix'))
    assert len(entries) == 1 and entries[0].source == explicit
    await cache.provision_cache(connection=target, entries=entries, operation_id=str(uuid.uuid4()),
                               progress=cache._noop, check_fence=cache._noop)
    await cache.prewarm_cache(connection=target, job=job, command=command,
                             native_invocation=job.native_invocation, source_revision='a' * 40,
                             source_tree='b' * 40, operation_id=str(uuid.uuid4()),
                             progress=cache._noop, check_fence=cache._noop)
    assert not (Path(target.remote_root) / 'attempts').exists()
    invocation_inodes = []
    attempts = []
    for _ in range(2):
        prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                                native_invocation=job.native_invocation)
        attempts.append(prepared)
        assert len(prepared.runtime_images) == 1
        image = prepared.runtime_images[0]
        assert len(image.aliases) == 1
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
        (attempt / 'bundle').mkdir(exist_ok=True)
        (attempt / 'bundle/source').symlink_to(prepared.remote_source_dir)
        (attempt / 'bundle/runtime').symlink_to(prepared.remote_runtime_dir)
        worker.verify_bundle(attempt)
        result = subprocess.run(prepared.envelope.command, env={**os.environ, **prepared.envelope.environment},
                                capture_output=True, text=True, check=True)
        observed = json.loads(result.stdout)
        argv = observed['argv']
        invocation_inodes.append(observed['inode'])
        assert observed['image'] == image.remote_destination
        assert prepared.envelope.environment['BMS_PROTENIX_CONTAINER_PATH'] == image.remote_destination
        assert argv[argv.index('--literal') + 1] == '$(touch NOT_EXECUTED); with spaces'
        assert argv[argv.index('--protenix_container_path') + 1] == image.remote_destination
        for alias in image.aliases:
            assert Path(alias).is_symlink()
            assert Path(alias).resolve() == Path(image.remote_destination)
        assert not any(p.is_file() and not p.is_symlink() for p in Path(prepared.remote_runtime_dir).rglob('*.sif'))
    assert sum(path == str(explicit) for path in uploads) == 1
    images = list((Path(target.remote_root) / 'cache/runtime-images/objects').rglob('runtime.sif'))
    assert len(images) == 1 and images[0].stat().st_nlink == 1
    assert invocation_inodes == [images[0].stat().st_ino] * 2
    assert not (roots['containers'] / 'protenix.sif').exists()
    assert not list((Path(target.remote_root) / 'cache/artifacts/v1/objects').rglob(image.sha256))
    assert not list((Path(target.remote_root) / 'cache/artifacts/v1/incoming').rglob('*sif'))
    assert all(not p.is_file() for p in (Path(target.remote_root) / 'cache/artifacts/v1/incoming').rglob('*'))
    assert explicit.stat().st_nlink == 1
    assert hashlib.sha256(images[0].read_bytes()).hexdigest() == image.sha256
    # Corruption between staging and execution prevents the real child from running.
    images[0].chmod(0o600)
    images[0].write_bytes(b'corrupt')
    images[0].chmod(0o400)
    failed = subprocess.run(prepared.envelope.command, env={**os.environ, **prepared.envelope.environment},
                            capture_output=True, text=True)
    assert failed.returncode == 1 and not failed.stdout
    count = len(uploads)
    with pytest.raises(subprocess.CalledProcessError):
        # A corrupt worker hit must not upload/repair from the valid controller.
        retry = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                                native_invocation=job.native_invocation)
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
async def test_uncertain_runtime_upload_is_retained_until_quiescence(tmp_path, local_transport, monkeypatch, failure):
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
    # No worker-quiescence receipt exists: retain rather than race a late writer.
    assert any(p.is_file() for p in incoming.rglob('*'))
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
    command, native = bundle.compile_remote_dependencies('protenix', 'predict', command,
                                                        native_invocation=job.native_invocation)
    from component_runtime import SelectedDependency
    from test_remote_bundle_runtime_gaps import bundle_metadata_fixture
    cpu_metadata = bundle_metadata_fixture()
    fixture_metadata = replace(cpu_metadata, dependencies=cpu_metadata.dependencies + (
        SelectedDependency('fixture:dorado', 'image', 'dorado.sif',
            'scripts/dorado_p4_preflight.py', selector='dorado_runtime_sif'),))
    job.model_id = 'ont_basecall_dna'
    monkeypatch.delenv('BMS_NGS_RUNTIME_SIF', raising=False)
    if explicit_selector:
        command += ['--dorado_runtime_sif', str(image)]
        native['dorado_runtime_sif'] = str(image)
    # Lower-layer strict runtime-selector fixture, not ONT scientific compilation.
    job.native_invocation = replace(NativeInvocation.capture(
        model_id=job.model_id, mode=job.mode, command=command, requested=native,
        effective=native, native_parameters=native, entrypoint='main.nf'),
        source_identity=job.native_invocation.source_identity)
    job.native_invocation = attach_selected_plan(job.native_invocation, roots, fixture_metadata=fixture_metadata)
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                                native_invocation=job.native_invocation)
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


@pytest.mark.asyncio
@pytest.mark.parametrize('controller_alias', [False, True])
async def test_frustrampnn_shared_canonical_reader(
        package, local_transport, tmp_path, monkeypatch, controller_alias):
    from services.frustrampnn import runtime as strict
    from dataclasses import replace
    roots, release, job, target, command = package
    source = roots['containers'] / 'frustrampnn.sif'
    # Same bytes under two model names: transport identity, not inference evidence.
    data = (roots['containers'] / 'protenix.sif').read_bytes()
    source.write_bytes(data)
    if controller_alias:
        from lib.shared_runtime_images import publish_image
        store = roots['containers'] / '.image-store'
        backing = publish_image(source, store, hashlib.sha256(data).hexdigest())
        source.unlink()
        monkeypatch.setenv('BMS_FRUSTRAMPNN_SIF', str(backing))
        monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(store))
    else:
        backing = source
    monkeypatch.setattr(strict, 'FRUSTRAMPNN_RUNTIME_IDENTITY', replace(
        strict.FRUSTRAMPNN_RUNTIME_IDENTITY, configured_sif_path=str(backing),
        sif_sha256=hashlib.sha256(data).hexdigest()))
    command += ['--run_frustrampnn', 'true']
    # Explicit runtime-reader fixture retains one coherent typed handoff.
    from component_runtime import SelectedDependency
    fixture_metadata = replace(job.native_invocation.execution_plan.metadata,
        dependencies=job.native_invocation.execution_plan.dependencies + (
            SelectedDependency('fixture:frustra', 'image', 'frustrampnn.sif', __file__),))
    native = {**job.native_invocation.native_parameters, 'run_frustrampnn': True}
    job.native_invocation = replace(job.native_invocation, command=tuple(command), execution_plan=None,
        requested_json=canonical_bytes(native), effective_json=canonical_bytes(native),
        native_parameters_json=canonical_bytes(native))
    job.native_invocation = attach_selected_plan(job.native_invocation, roots, fixture_metadata=fixture_metadata)
    monkeypatch.setattr(cache, 'get_code_root', lambda: roots['repo'])
    monkeypatch.setattr(cache, 'current_source_identity', lambda *_: ('a' * 40, 'b' * 40))
    await cache.prewarm_cache(connection=target, job=job, command=command,
                             native_invocation=job.native_invocation, source_revision='a' * 40,
                             source_tree='b' * 40, operation_id=str(uuid.uuid4()),
                             progress=cache._noop, check_fence=cache._noop)
    before = len(local_transport[1])
    prepared_attempts = []
    for _ in range(2):
        prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                                native_invocation=job.native_invocation)
        assert not [r for r in prepared.envelope.files if r.relative_path.endswith('.sif')]
        assert len(prepared.runtime_images) == 1
        await cache.provision_cache(connection=target, entries=prepared.runtime_images,
            operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=cache._noop)
        await cache.stage_cached_bundle(connection=target, bundle=prepared)
        prepared_attempts.append(prepared)
    assert not any(path.endswith('.sif') for path in local_transport[1][before:])
    inode = None
    for prepared in prepared_attempts:
        image = prepared.runtime_images[0]
        canonical = Path(prepared.envelope.environment['BMS_FRUSTRAMPNN_SIF'])
        assert str(canonical) == image.remote_destination
        alias = Path(prepared.remote_runtime_dir) / 'containers/frustrampnn.sif'
        assert alias.is_symlink() and alias.resolve() == canonical
        assert canonical.is_file() and not canonical.is_symlink()
        identity = replace(strict.FRUSTRAMPNN_RUNTIME_IDENTITY,
                           configured_sif_path=str(canonical), sif_sha256=image.sha256)
        with monkeypatch.context() as env:
            for key, value in prepared.envelope.environment.items():
                env.setenv(key, value)
            env.setattr(strict, 'get_container_path', lambda name: alias.parent / name)
            env.setattr(strict, 'get_container_dir', lambda: alias.parent)
            selected = strict.validate_configured_container_path(alias, identity=identity)
            assert selected == str(canonical)
            with strict.open_verified_container(selected, identity.sif_sha256) as pinned:
                current = os.fstat(pinned.fd).st_ino
                assert inode is None or inode == current
                inode = current
                assert os.pread(pinned.fd, len(data), 0) == data
        with pytest.raises(strict.RuntimeValidationError, match='without following symlinks'):
            strict.open_verified_container(alias, image.sha256)
    objects = Path(target.remote_root) / 'cache/runtime-images/objects'
    assert len(list(objects.rglob('runtime.sif'))) == 1
    assert not list((Path(target.remote_root) / 'cache/artifacts/v1/objects').rglob(image.sha256))
