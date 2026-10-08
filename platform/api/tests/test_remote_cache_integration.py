"""Offline real helper protocol exercised through a local transport double."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import uuid

import pytest

from services.remote_execution import cache, bundle as bundle_module
from component_runtime import NativeInvocation, SourceIdentity
from dataclasses import replace


def cache_only_plan_fixture(invocation):
    """Typed asset-free view with a real CPU policy for transport-only tests."""
    from component_runtime import SelectedExecutionMetadata, SelectedExecutionPlan, canonical_bytes
    from test_remote_bundle_runtime_gaps import bundle_resource_components_fixture
    metadata = SelectedExecutionMetadata('fixture', 'explicit cache/input test boundary',
        bundle_resource_components_fixture(), (), (), (), (), b'{}', None, None, ())
    plan = SelectedExecutionPlan(invocation.source_identity, Path(invocation.entrypoint).stem,
        invocation.model_id, invocation.mode, invocation.entrypoint, invocation.requested_json,
        invocation.effective_json, canonical_bytes(invocation.native_parameters), metadata)
    return replace(invocation, execution_plan=plan)


def cache_invocation_fixture():
    """Explicit source/cache projection fixture; not a scientific compiler proof."""
    return cache_only_plan_fixture(replace(NativeInvocation.capture(model_id='example', mode='predict',
        command=['nextflow', 'run', 'workflow.nf'], requested={}, effective={},
        native_parameters={}, entrypoint='workflow.nf'),
        source_identity=SourceIdentity('a'*40, 'b'*40)))

from test_remote_bundle_runtime_gaps import package, bundle_resource_components_fixture
from services.remote_execution.bundle import CacheTransferArtifact, TransferPlan, cache_transfer_artifacts, uncached_runtime_transfers


def record(path, data, role='runtime', link_target=None):
    return SimpleNamespace(relative_path=path, sha256=hashlib.sha256(data).hexdigest(),
                           size_bytes=len(data), mode=0o644, role=role, link_target=link_target)


@pytest.fixture
def local_transport(monkeypatch):
    calls, uploads = [], []
    async def run(connection, argv, input_bytes=None, **kwargs):
        if input_bytes and argv[0] == 'python3' and '-c' not in argv:
            calls.append(json.loads(input_bytes))
        return subprocess.run(argv, input=input_bytes, capture_output=True, check=True)
    async def rsync(connection, source, destination, **kwargs):
        uploads.append(str(source))
        shutil.copyfile(source, destination)
    monkeypatch.setattr(cache, 'run_remote', run)
    monkeypatch.setattr(cache, 'rsync_to_remote', rsync)
    return calls, uploads


def make_bundle(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    archive = source / '.bms-source.tar'
    with tarfile.open(archive, 'w') as tar:
        info = tarfile.TarInfo('workflow.nf')
        info.size = len(b'workflow')
        tar.addfile(info, io.BytesIO(b'workflow'))
    weights = tmp_path / 'weights'
    weights.mkdir()
    (weights / 'model').write_bytes(b'model')
    (weights / 'alias').symlink_to('model')
    remote = tmp_path / 'worker'
    attempt_id = str(uuid.uuid4())
    generation = remote / 'attempts' / attempt_id / 'materialized'
    runtime = str(generation / 'runtime')
    files = [record('source/.bms-source.tar', archive.read_bytes(), 'source'),
             record('source/workflow.nf', b'workflow', 'source'),
             record('runtime/weights/model', b'model'),
             record('runtime/weights/alias', b'model', link_target='model'),
             record('runtime/support-python/secret', b'excluded'),
             record('inputs/secret', b'excluded', 'input'),
             record('results/secret', b'excluded', 'result')]
    bundle = SimpleNamespace(attempt_id=attempt_id, envelope=SimpleNamespace(files=files),
                             source_transfer=TransferPlan(source, str(generation / 'source')),
                             remote_source_dir=str(generation / 'source'), remote_runtime_dir=runtime,
                             runtime_transfers=(TransferPlan(weights, runtime + '/weights'),
                                                TransferPlan(tmp_path / 'support', runtime + '/support-python')))
    return SimpleNamespace(remote_root=str(remote)), bundle


def next_attempt(connection, bundle):
    attempt_id = str(uuid.uuid4())
    generation = f'{connection.remote_root}/attempts/{attempt_id}/materialized'
    return SimpleNamespace(**(vars(bundle) | dict(
        attempt_id=attempt_id, remote_source_dir=generation + '/source',
        remote_runtime_dir=generation + '/runtime',
        source_transfer=TransferPlan(bundle.source_transfer.source, generation + '/source'),
        runtime_transfers=tuple(TransferPlan(t.source, t.remote_destination.replace(
            bundle.remote_runtime_dir, generation + '/runtime')) for t in bundle.runtime_transfers))))


@pytest.mark.asyncio
async def test_prewarm_launch_share_verified_cache_and_links(tmp_path, monkeypatch, local_transport):
    connection, bundle = make_bundle(tmp_path)
    artifacts = cache_transfer_artifacts(bundle)
    assert len(artifacts) == 2
    assert [p.remote_destination for p in uncached_runtime_transfers(bundle)] == [bundle.remote_runtime_dir + '/support-python']
    invocation = cache_invocation_fixture()
    def prewarm_plan(*args, native_invocation):
        assert native_invocation is invocation
        assert args[1] == list(invocation.command)
        return artifacts
    monkeypatch.setattr(cache, '_prewarm_plan', prewarm_plan)
    calls, uploads = local_transport
    await cache.prewarm_cache(connection=connection, job=None, command=list(invocation.command),
                              native_invocation=invocation, source_revision='a'*40,
                              source_tree='b'*40, operation_id=str(uuid.uuid4()),
                              progress=cache._noop, check_fence=cache._noop)
    assert len(uploads) == 2
    assert not Path(bundle.remote_runtime_dir).exists()
    await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert len(uploads) == 2
    assert (Path(bundle.remote_source_dir) / 'workflow.nf').read_bytes() == b'workflow'
    model = Path(bundle.remote_runtime_dir) / 'weights/model'
    alias = model.with_name('alias')
    assert alias.is_symlink() and alias.read_bytes() == b'model'
    alias.write_bytes(b'job mutation')
    with pytest.raises(subprocess.CalledProcessError):
        await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert model.read_bytes() == b'job mutation' and len(uploads) == 2
    bundle = next_attempt(connection, bundle)
    await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    model = Path(bundle.remote_runtime_dir) / 'weights/model'
    assert model.read_bytes() == b'model' and len(uploads) == 2
    # Corruption forces a verified replacement, not blind reuse.
    item = artifacts[1]
    obj = Path(connection.remote_root) / 'cache/artifacts/v1/objects/sha256' / item.sha256[:2] / item.sha256
    obj.chmod(0o600)
    obj.write_bytes(b'xxxxx')
    bundle = next_attempt(connection, bundle)
    model = Path(bundle.remote_runtime_dir) / 'weights/model'
    await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert len(uploads) == 3 and model.read_bytes() == b'model'
    assert not any('secret' in str(request) for request in calls)


@pytest.mark.asyncio
async def test_warm_probe_and_materialize_use_bounded_batches(tmp_path, monkeypatch):
    calls, events = [], []
    async def progress(event):
        events.append(event)
    async def run(connection, argv, input_bytes=None, **kwargs):
        if '-c' in argv:
            return SimpleNamespace(stdout=b'')
        request = json.loads(input_bytes)
        calls.append(request)
        return SimpleNamespace(stdout=json.dumps({'artifacts': [dict(a, state='cache_hit') for a in request.get('artifacts', [])]}))
    async def no_upload(*args, **kwargs):
        pytest.fail('warm artifacts must not upload')
    monkeypatch.setattr(cache, 'run_remote', run)
    monkeypatch.setattr(cache, 'rsync_to_remote', no_upload)
    artifacts = [CacheTransferArtifact(tmp_path / str(i), '/worker/runtime/' + str(i),
                                       hashlib.sha256(str(i).encode()).hexdigest(), 1, 0o644, 'runtime') for i in range(257)]
    await cache._cache_artifacts(connection=SimpleNamespace(remote_root='/worker'), artifacts=artifacts,
                                 operation_id=str(uuid.uuid4()), progress=progress,
                                 check_fence=cache._noop, materialize=True)
    assert [len(r['artifacts']) for r in calls if r['action'] == 'probe'] == [128, 128, 1]
    assert [len(r['entries']) for r in calls if r['action'] == 'materialize_many'] == [128, 128, 1]
    assert len(calls) == 6
    assert events == ([{'phase': 'checking', 'artifact': None,
                        'message': 'Verifying cached artifact batch'}] * 3
                      + [{'phase': 'verifying', 'artifact': None,
                          'message': 'Materializing verified artifact batch'}] * 3)


@pytest.mark.parametrize('identity_matches', [True, False])
def test_prewarm_plan_pins_source_and_excludes_support(tmp_path, monkeypatch, identity_matches):
    connection, bundle = make_bundle(tmp_path)
    revision, tree = 'a' * 40, 'b' * 40
    monkeypatch.setattr(cache, 'get_code_root', lambda: tmp_path)
    monkeypatch.setattr(cache, 'current_source_identity', lambda repo: (revision, tree if identity_matches else 'c' * 40))
    monkeypatch.setattr(cache, '_runtime_assets', lambda *args, **kwargs: [(tmp_path / 'weights', 'weights'),
                                                              (tmp_path / 'missing-support', 'support-python')])
    calls = []
    def archive(argv, **kwargs):
        calls.append(argv)
        kwargs['stdout'].write((bundle.source_transfer.source / '.bms-source.tar').read_bytes())
    monkeypatch.setattr(cache.subprocess, 'run', archive)
    directory = tmp_path / 'prewarm'
    directory.mkdir()
    job = SimpleNamespace(model_id='example', mode='predict')
    invocation = cache_invocation_fixture()
    if not identity_matches:
        with pytest.raises(ValueError, match='source identity'):
            cache._prewarm_plan(job, list(invocation.command), revision, tree, directory,
                                native_invocation=invocation)
        assert calls == []
        return
    planned = cache._prewarm_plan(job, list(invocation.command), revision, tree, directory,
                                native_invocation=invocation)
    assert calls == [['git', 'archive', '--format=tar', revision]]
    launched = cache_transfer_artifacts(bundle)
    assert {(a.sha256, a.size_bytes) for a in planned} == {(a.sha256, a.size_bytes) for a in launched}
    assert all('support-python' not in a.remote_destination for a in planned)


@pytest.mark.asyncio
async def test_real_bundle_generations_exclude_stale_files(package, local_transport):
    roots, release, job, target, command = package
    from component_runtime import SelectedDependency, SelectedExecutionMetadata, SelectedExecutionPlan
    # Explicit lower-layer fixture assets, not a second scientific planner.
    invocation = job.native_invocation
    metadata = SelectedExecutionMetadata(
        availability='fixture', settings_authority=__file__, static_components=bundle_resource_components_fixture(),
        dynamic_templates=(), dependencies=(
            SelectedDependency('fixture:image', 'image', 'protenix.sif', __file__),
            SelectedDependency('fixture:weights', 'weights', 'protenix', __file__),
            SelectedDependency('fixture:support', 'support_python', None, __file__)),
        artifact_roles=(), external_services=(), result_contract_json=b'{}',
        admission_authority=None, retrieval_authority=None, blockers=(), closure_reviewed=True)
    job.native_invocation = replace(invocation, execution_plan=SelectedExecutionPlan(
        source_identity=invocation.source_identity, workflow='fixture',
        model_id=invocation.model_id, mode=invocation.mode, entrypoint=invocation.entrypoint,
        requested_json=invocation.requested_json, effective_json=invocation.effective_json,
        native_parameters_json=invocation.native_parameters_json, metadata=metadata))
    first = bundle_module.prepare_remote_bundle(job=job, target=target, command=command,
                                                  native_invocation=job.native_invocation)
    await cache.stage_cached_bundle(connection=target, bundle=first)
    preserved = [Path(first.remote_source_dir) / 'unexpected.py',
                 Path(first.remote_runtime_dir) / 'weights/protenix/unexpected.ckpt',
                 Path(first.remote_attempt_dir) / 'results/scientific.cif',
                 Path(target.remote_root) / 'lineages/job/metadata.json',
                 Path(target.remote_root) / 'revisions' / job.execution_source_tree / 'legacy.py',
                 Path(target.remote_root) / 'lineages/job/runtime/weights/legacy.ckpt']
    for path in preserved:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'preserve-old-generation')
    calls, uploads = local_transport
    upload_count = len(uploads)
    second = bundle_module.prepare_remote_bundle(job=job, target=target, command=command,
                                                  native_invocation=job.native_invocation)
    assert first.envelope.root_job_id == second.envelope.root_job_id
    assert first.envelope.source_tree == second.envelope.source_tree
    assert first.attempt_id != second.attempt_id
    await cache.stage_cached_bundle(connection=target, bundle=second)
    # Each attempt transports its small authenticated, destination-specific manifest.
    assert len(uploads) == upload_count + 1
    assert Path(uploads[-1]).name == '.bms-runtime-images.json'
    upload_count = len(uploads)
    assert second.remote_source_dir == second.remote_attempt_dir + '/materialized/source'
    assert second.remote_runtime_dir == second.remote_attempt_dir + '/materialized/runtime'
    assert second.envelope.working_directory == second.remote_source_dir
    assert second.envelope.environment['BMS_WEIGHTS'] == second.remote_runtime_dir + '/weights'
    assert (Path(second.remote_source_dir) / 'main.nf').is_file()
    assert (Path(second.remote_runtime_dir) / 'weights/protenix/model.pt').is_file()
    assert not (Path(second.remote_source_dir) / 'unexpected.py').exists()
    assert not (Path(second.remote_runtime_dir) / 'weights/protenix/unexpected.ckpt').exists()
    assert all(path.read_bytes() == b'preserve-old-generation' for path in preserved)
    before = len(calls)
    with pytest.raises(subprocess.CalledProcessError) as error:
        await cache.stage_cached_bundle(connection=target, bundle=first)
    assert b'FileExistsError' in error.value.stderr
    assert len(calls) == before and len(uploads) == upload_count
    assert all(path.read_bytes() == b'preserve-old-generation' for path in preserved)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['remote_source_dir', 'remote_runtime_dir'])
async def test_generation_paths_cannot_target_other_trees(tmp_path, local_transport, field):
    connection, bundle = make_bundle(tmp_path)
    setattr(bundle, field, str(tmp_path / 'outside'))
    with pytest.raises(ValueError, match='belong to this attempt'):
        await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert local_transport == ([], [])
    assert not Path(connection.remote_root).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('component', ['attempts', 'attempt', 'materialized'])
async def test_generation_rejects_symlink_redirection(tmp_path, local_transport, component):
    connection, bundle = make_bundle(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    attempt = Path(bundle.remote_source_dir).parent.parent
    path = {'attempts': attempt.parent, 'attempt': attempt,
            'materialized': attempt / 'materialized'}[component]
    path.parent.mkdir(parents=True)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(subprocess.CalledProcessError):
        await cache.stage_cached_bundle(connection=connection, bundle=bundle)
    assert list(outside.iterdir()) == []
    assert path.is_symlink()
    assert local_transport == ([], [])


@pytest.mark.asyncio
async def test_fence_prevents_any_transport(monkeypatch):
    async def fenced():
        raise RuntimeError('cancelled')
    async def forbidden(*args, **kwargs):
        pytest.fail('transport after cancellation')
    monkeypatch.setattr(cache, 'run_remote', forbidden)
    with pytest.raises(RuntimeError, match='cancelled'):
        await cache._cache_artifacts(connection=SimpleNamespace(remote_root='/worker'), artifacts=[],
                                     operation_id=str(uuid.uuid4()), progress=cache._noop, check_fence=fenced)
