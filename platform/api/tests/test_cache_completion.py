"""Actual controller/cache receiving proofs using inert scratch assets only."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import uuid

import pytest
from component_runtime import NativeInvocation, SelectedExecutionPlan, SourceIdentity, UnresolvedField
from model_registry import selected_execution_metadata
from database import ExecutionTarget
from services.remote_execution import bundle, cache, preloading as p, hf_assets
from services.remote_execution.contracts import PreloadRequest, ProvisionSelection
from test_remote_preloading import store, settle
from test_remote_cache_integration import local_transport

MODELS = [
    ('bindcraft2', 'campaign', 'workflows/bindcraft2.nf'),
    ('boltzgen', 'protein_binder', 'workflows/boltzgen_generation.nf'),
    ('antibody_denovo', 'antibody_denovo_pipeline', 'workflows/antibody_denovo.nf'),
    ('ppiflow', 'protein_binder', 'workflows/ppiflow_generation.nf'),
]


def setup_recipe(tmp_path, monkeypatch, model, mode, entrypoint):
    params = {'protenix_use_msa': False}
    if model == 'boltzgen':
        params['boltzgen_protocol'] = 'protein-anything'
    metadata = selected_execution_metadata(model, mode, params, entrypoint)
    assert metadata.dependency_closure_complete
    metadata = replace(metadata, blockers=metadata.blockers + (
        UnresolvedField(model, 'result_retrieval', 'fixture retained-result owner',
                        'Inert launch-only blocker', ('launch',)),))
    invocation = NativeInvocation.capture(model_id=model, mode=mode, command=['nextflow', entrypoint],
        requested=params, effective=params, native_parameters=params, entrypoint=entrypoint)
    identity = SourceIdentity('a' * 40, 'b' * 40)
    plan = SelectedExecutionPlan(identity, model, model, mode, entrypoint,
        invocation.requested_json, invocation.effective_json, invocation.native_parameters_json, metadata)
    invocation = replace(invocation, source_identity=identity, execution_plan=plan)
    roots = {kind: tmp_path / kind for kind in ('image', 'weights', 'data', 'code')}
    for root in roots.values():
        root.mkdir()
    for dep in plan.dependencies:
        if dep.kind in {'support_tool', 'image', 'weights', 'database', 'reference_database', 'runtime_data'}:
            root = roots['code' if dep.kind == 'support_tool' else dep.kind if dep.kind in roots else 'data']
            path = root / dep.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(('inert asset ' + dep.logical_id).encode())
    for module in (bundle, cache):
        monkeypatch.setattr(module, 'get_code_root', lambda: roots['code'])
        monkeypatch.setattr(module, 'get_data_root', lambda: roots['data'])
        monkeypatch.setattr(module, 'current_source_identity', lambda *args: ('a' * 40, 'b' * 40))
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: roots['weights'])
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: roots['image'])
    import paths
    monkeypatch.setattr(paths, 'get_weights_root', lambda: roots['weights'])
    monkeypatch.setattr(paths, 'get_container_dir', lambda: roots['image'])
    monkeypatch.setattr(hf_assets, 'publication_index', lambda: None)
    return SimpleNamespace(model_id=model, mode=mode), invocation, roots


@pytest.mark.parametrize('model,mode,entrypoint', MODELS)
def test_c01_real_closures_download_equal_configured_launch_refuses(tmp_path, monkeypatch, model, mode, entrypoint, record_property):
    job, invocation, roots = setup_recipe(tmp_path, monkeypatch, model, mode, entrypoint)
    def forbidden(*args, **kwargs):
        pytest.fail('Weight selection must precede source staging; no inputs/MSA/GPU')
    monkeypatch.setattr(cache, '_staged_source_archive', forbidden)
    saved = cache._prewarm_plan(job, list(invocation.command), 'a'*40, 'b'*40, tmp_path,
                               native_invocation=invocation)
    configured, plan = cache.workflow_plan(SimpleNamespace(), compiled_plan=invocation.execution_plan)
    def roster(entries, saved=False):
        return sorted((e.remote_destination.removeprefix('runtime/') if saved else e.remote_destination,
                       e.sha256, e.size_bytes, e.mode, e.role, e.link_target) for e in entries)
    assert roster(saved, True) == roster(configured)
    assert any(e.remote_destination.startswith('runtime/weights/') for e in saved)
    assert not any(e.role == 'source' for e in saved)
    with pytest.raises(bundle.RemoteBundleError, match='complete selected native execution plan'):
        bundle.compile_remote_dependencies(model, mode, list(invocation.command), native_invocation=invocation)
    record_property('configured_saved_equal_artifacts', len(saved))
    metadata = replace(plan.metadata, closure_reviewed=False)
    unresolved = replace(invocation, execution_plan=replace(plan, metadata=metadata))
    with pytest.raises(bundle.RemoteBundleError, match='dependency closure'):
        cache._prewarm_plan(job, list(invocation.command), 'a'*40, 'b'*40, tmp_path, native_invocation=unresolved)
    with pytest.raises(ValueError, match='dependency closure'):
        cache.workflow_plan(SimpleNamespace(), compiled_plan=unresolved.execution_plan)


def git_source(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'inert.txt').write_text('inert source')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.invalid',
                    'commit', '-qm', 'fixture'], check=True)
    return repo, bundle.current_source_identity(repo)


@pytest.mark.asyncio
@pytest.mark.parametrize('weights', [True, False])
async def test_d01_real_saved_source_one_warm_copy_receipt(tmp_path, monkeypatch, local_transport, weights, record_property):
    repo, (revision, tree) = git_source(tmp_path)
    data = tmp_path / 'data'
    for module in (cache, bundle):
        monkeypatch.setattr(module, 'get_code_root', lambda: repo)
        monkeypatch.setattr(module, 'get_data_root', lambda: data)
    invocation = replace(NativeInvocation.capture(model_id='fixture', mode='fixture', command=['nextflow', 'fixture.nf'],
        requested={}, effective={}, native_parameters={}, entrypoint='fixture.nf'), source_identity=SourceIdentity(revision, tree),
        execution_plan=None)
    asset = tmp_path / 'asset'
    asset.write_bytes(b'inert transport bytes')
    monkeypatch.setattr(cache, '_runtime_assets', lambda *a, **kw: [(asset, 'weights/checkpoint' if weights else 'data/reference')])
    monkeypatch.setattr(cache, 'verify_selected_preparation_inputs', lambda *a: None)
    monkeypatch.setattr(cache, 'verify_selected_runtime_hashes', lambda *a: None)
    monkeypatch.setattr(hf_assets, 'publication_index', lambda: None)
    stages = []
    original = cache._staged_source_archive
    def staged(*args, **kwargs):
        stages.append(args[3])
        return original(*args, **kwargs)
    monkeypatch.setattr(cache, '_staged_source_archive', staged)
    archive_digest = bundle._staged_source_archive(repo, data, revision, tmp_path / 'prime', extract=False)
    archive = data / 'remote-execution/source-archives' / (revision + '.tar.gz')
    reads = []
    original_open = Path.open
    class Reader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def read(self, size=-1):
            value = self.stream.read(size)
            reads.append(len(value))
            return value
    def opened(path, mode='r', *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return Reader(stream) if path == archive and mode == 'rb' else stream
    monkeypatch.setattr(Path, 'open', opened)
    receipt = await cache.prewarm_cache(connection=SimpleNamespace(remote_root=str(tmp_path / 'worker')),
        job=SimpleNamespace(model_id='fixture', mode='fixture'), command=list(invocation.command),
        source_revision=revision, source_tree=tree, operation_id=str(uuid.uuid4()), progress=cache._noop,
        check_fence=cache._noop, native_invocation=invocation)
    assert len(stages) == 1
    assert sum(reads) == archive.stat().st_size
    source = [r for r in receipt['artifacts'] if r['name'] == 'source/.bms-source.tar.gz']
    assert source == [dict(name='source/.bms-source.tar.gz', sha256=archive_digest, size_bytes=archive.stat().st_size)]
    assert not any(c['action'] == 'extract_source' for c in local_transport[0])
    record_property('source_stages', len(stages))
    record_property('archive_size', archive.stat().st_size)
    record_property('warm_archive_bytes_read', sum(reads))


@pytest.mark.asyncio
@pytest.mark.parametrize('writer_error', [False, True])
async def test_d12_actual_plan_twice_cancelled_writer_lifetime(tmp_path, monkeypatch, writer_error, record_property):
    repo, (revision, tree) = git_source(tmp_path)
    data = tmp_path / 'data'
    for module in (cache, bundle):
        monkeypatch.setattr(module, 'get_code_root', lambda: repo)
        monkeypatch.setattr(module, 'get_data_root', lambda: data)
    invocation = replace(NativeInvocation.capture(model_id='fixture', mode='fixture', command=['nextflow'],
        requested={}, effective={}, native_parameters={}, entrypoint='fixture.nf'),
        source_identity=SourceIdentity(revision, tree), execution_plan=None)
    monkeypatch.setattr(cache, '_runtime_assets', lambda *a, **kw: [])
    monkeypatch.setattr(cache, 'verify_selected_preparation_inputs', lambda *a: None)
    monkeypatch.setattr(cache, 'verify_selected_runtime_hashes', lambda *a: None)
    monkeypatch.setattr(hf_assets, 'publication_index', lambda: None)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    owned = []
    original = cache._staged_source_archive
    def writer(*args, **kwargs):
        path = args[3]
        path.mkdir()
        owned.append(path)
        entered.set()
        try:
            assert release.wait(5)
            assert path.exists()
            if writer_error:
                raise ValueError('inert writer failure')
            return original(*args, **kwargs)
        finally:
            exited.set()
    monkeypatch.setattr(cache, '_staged_source_archive', writer)
    events = []
    async def progress(event): events.append(event)
    async def forbidden(**kwargs): pytest.fail('Cancelled writer must never publish')
    monkeypatch.setattr(cache, '_cache_artifacts', forbidden)
    task = asyncio.create_task(cache.prewarm_cache(connection=SimpleNamespace(),
        job=SimpleNamespace(model_id='fixture', mode='fixture'), command=list(invocation.command),
        source_revision=revision, source_tree=tree, operation_id=str(uuid.uuid4()), progress=progress,
        check_fence=cache._noop, native_invocation=invocation))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done() and owned[0].exists() and not exited.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    assert exited.is_set() and not owned[0].exists() and events == []
    record_property('cancellations', 2)
    record_property('active_writers_after_join', 0)


@pytest.mark.asyncio
async def test_d04_real_cache_batches_sql_deltas_concurrent_and_warm(store, tmp_path, monkeypatch, local_transport, record_property):
    monkeypatch.setattr(cache, 'BATCH_COUNT', 2)
    artifacts = []
    for index in range(9):
        source = tmp_path / str(index)
        data = ('inert ' + str(index)).encode()
        source.write_bytes(data)
        artifacts.append(bundle.CacheTransferArtifact(source, 'runtime/' + str(index), hashlib.sha256(data).hexdigest(), len(data), 0o644, 'runtime'))
    events, sql, statuses = [], [], []
    from services.remote_execution.contracts import ProvisionArtifactProgress, PreloadProgress
    validations, full_dumps = [], []
    validate = ProvisionArtifactProgress.model_validate
    dump = PreloadProgress.model_dump_json
    def validated(cls, row, *args, **kwargs):
        validations.append(row)
        return validate(row, *args, **kwargs)
    def dumped(self, *args, **kwargs):
        full_dumps.append(True)
        return dump(self, *args, **kwargs)
    monkeypatch.setattr(ProvisionArtifactProgress, 'model_validate', classmethod(validated))
    monkeypatch.setattr(PreloadProgress, 'model_dump_json', dumped)
    from sqlalchemy import event
    engine = store.kw['bind'].sync_engine
    def statement(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE'):
            sql.append((statement, parameters))
    event.listen(engine, 'before_cursor_execute', statement)
    upload = cache.rsync_to_remote
    arrived, release = [], asyncio.Event()
    async def simultaneous(connection, source, destination, **kwargs):
        arrived.append(asyncio.current_task())
        if len(arrived) == 5:
            release.set()
        await asyncio.wait_for(release.wait(), 5)
        return await upload(connection, source, destination, **kwargs)
    monkeypatch.setattr(cache, 'rsync_to_remote', simultaneous)
    class Controller(p.PreloadController):
        active_db = 0
        max_active_db = 0
        async def _publish(self, *args, **kwargs):
            self.active_db += 1
            self.max_active_db = max(self.max_active_db, self.active_db)
            try:
                return await super()._publish(*args, **kwargs)
            finally:
                self.active_db -= 1
    async def prewarm(**kwargs):
        async def progress(event):
            events.append(event)
            status = await kwargs['progress'](event)
            statuses.append(status)
            async with store() as session:
                row = await session.get(ExecutionTarget, 'vast:1')
                retained = row.provider_metadata['preload']['artifact_progress']
                if row.provider_metadata['preload']['sequence'] == status.sequence:
                    assert p.artifact_summary({'artifact_progress':retained}) == status.artifact_summary.model_dump()
        receipts = await cache._cache_artifacts(connection=kwargs['connection'], artifacts=artifacts,
            operation_id=kwargs['operation_id'], progress=progress, check_fence=kwargs['check_fence'], track_artifacts=True)
        return dict(source_revision='a'*40, source_tree='b'*40, artifacts=receipts)
    controller = Controller(store, prewarm=prewarm)
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        target.remote_root = str(tmp_path / 'worker')
        await session.commit()
        await controller.start(session, 'vast:1', PreloadRequest(job_id='recipe'))
    await settle(controller)
    async with store() as session:
        raw = (await session.get(ExecutionTarget, 'vast:1')).provider_metadata['preload']
        assert raw['phase'] == 'source_download_ready'
        assert len(raw['artifacts']) == 9
        assert all(row['state'] == 'verified' for row in raw['artifact_progress'])
    assert [s.sequence for s in statuses] == list(range(1, len(statuses)+1))
    resets = [e for e in events if 'artifact_progress' in e]
    deltas = [e for e in events if 'artifact_delta' in e]
    assert len(resets) == 2 and all(len(e['artifact_progress']) == 9 for e in resets)
    assert sum(len(e['artifact_delta']) for e in deltas) == 27
    assert max(len(e['artifact_delta']) for e in deltas) == 2
    assert len(validations) == 9 + 27 + 9  # admission, changed deltas, terminal
    assert len(full_dumps) == 2  # controller admission and terminal only

    # Parameter paths contain row indices, never a JSON whole-preload replacement.
    row_writes = [params for stmt, params in sql if any(isinstance(x,str) and x.startswith('$.preload.artifact_progress[') for x in params)]
    assert row_writes
    assert all('$.preload' not in params for params in row_writes)
    assert len(set(arrived)) == 5 and controller.max_active_db == 1
    record_property('admission_roster_rows', 9)
    record_property('routine_delta_rows', 27)
    record_property('max_changed_batch_rows', 2)
    record_property('routine_whole_roster_writes', 0)
    record_property('concurrent_transfer_owners', len(set(arrived)))
    record_property('max_concurrent_progress_db_owners', controller.max_active_db)
    uploads = len(local_transport[1])
    events.clear()
    async with store() as session:
        await controller.start(session, 'vast:1', PreloadRequest(job_id='recipe'))
    await settle(controller)
    assert len(local_transport[1]) == uploads
    assert sum(len(e.get('artifact_delta', [])) for e in events) == 9
    event.remove(engine, 'before_cursor_execute', statement)


@pytest.mark.asyncio
@pytest.mark.parametrize('negative', ['identity', 'index', 'aggregate', 'cancel'])
async def test_d04_delta_negatives_reset_and_cancel_exact(store, negative):
    first = dict(name='runtime/packed', sha256='a'*64, size_bytes=8, state='verified')
    rows = [dict(name='runtime/remainder', sha256='b'*64, size_bytes=3, state='pending'),
            dict(name='source/archive', sha256='c'*64, size_bytes=4, state='pending')]
    async def prewarm(**kwargs):
        await kwargs['progress'](dict(phase='checking', message='Packed roster', artifact_progress=[first]))
        await kwargs['progress'](dict(phase='checking', message='Remainder reset', artifact_progress=rows))
        await kwargs['progress'](dict(phase='verifying', message='Verified first', artifact_delta=[(0, dict(rows[0],state='verified'))]))
        if negative == 'cancel':
            raise asyncio.CancelledError()
        event = dict(phase='transferring', message='Invalid must not publish', artifact_delta=[(1, dict(rows[1],state='transferring'))])
        if negative == 'identity': event['artifact_delta'][0][1]['sha256'] = 'd'*64
        if negative == 'index': event['artifact_delta'][0] = (9, event['artifact_delta'][0][1])
        if negative == 'aggregate': event['artifact_summary'] = dict(total_count=2,total_bytes=7,verified_count=2,verified_bytes=7)
        await kwargs['progress'](event)
        pytest.fail('Malformed delta published')
    async def quiet(*args): return True
    controller = p.PreloadController(store, prewarm=prewarm, quiesce=quiet)
    async with store() as session: await controller.start(session, 'vast:1', PreloadRequest(job_id='recipe'))
    await asyncio.gather(*list(controller.tasks.values()), return_exceptions=True)
    async with store() as session:
        raw = (await session.get(ExecutionTarget, 'vast:1')).provider_metadata['preload']
        assert raw['phase'] == ('cancelled' if negative == 'cancel' else 'failed')
        assert [(r['name'],r['state']) for r in raw['artifact_progress']] == [('runtime/remainder','verified'),('source/archive','interrupted')]
        assert raw['message'] != 'Invalid must not publish'


def test_d06_independent_resolver_and_publication_once_fresh_change(tmp_path, monkeypatch, record_property):
    import paths
    root = tmp_path / 'weights'
    root.mkdir()
    source = root / 'fixture'
    source.write_bytes(b'inert asset')
    monkeypatch.setattr(paths, 'get_weights_root', lambda: root)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: root)
    calls = []
    monkeypatch.setattr(cache, '_independent_dependencies', lambda s: (calls.append('resolve') or (SimpleNamespace(kind='weights',relative_path='fixture'),)))
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a'*40,'b'*40))
    publication = dict(schema=hf_assets.NAMED_INDEX_SCHEMA, dependencies=['weights/fixture'], artifacts=[dict(name='weights/fixture',sha256='a'*64,size_bytes=11,mode=0o644)])
    def index():
        calls.append('index')
        return publication
    monkeypatch.setattr(hf_assets, 'publication_index', index)
    target = SimpleNamespace(id='vast:1',host='fixture',port=22,username='fixture',remote_root='/worker',host_key_sha256='a'*64,managed_inventory=None)
    selection = ProvisionSelection(kind='model',model_id='boltz2')
    before, entries = cache.independent_preview(selection,target)
    assert calls == ['index','resolve']
    calls.clear()
    fresh, again = cache.independent_preview(selection,target)
    assert before == fresh and entries == again and calls == ['index','resolve']
    publication['artifacts'][0]['sha256'] = 'b'*64
    changed, _ = cache.independent_preview(selection,target)
    assert changed.preview_sha256 != before.preview_sha256
    record_property('publication_reads_per_request',1)
    record_property('dependency_resolutions_per_request',1)


@pytest.mark.parametrize('state', ['absent','malformed','valid'])
def test_d06_advisory_pack_fallback(tmp_path, monkeypatch, state):
    monkeypatch.setattr(cache,'get_data_root',lambda:tmp_path)
    monkeypatch.setattr(hf_assets,'weights_archive',lambda:('d'*64,100))
    path = tmp_path / 'remote-execution/hf-archives' / ('d'*64) / 'index.json'
    if state != 'absent':
        path.parent.mkdir(parents=True)
        path.write_text('{broken' if state == 'malformed' else json.dumps(dict(archive=dict(sha256='d'*64,size_bytes=100),digest_sizes={'a'*64:3})))
    entry = SimpleNamespace(sha256='a'*64,size_bytes=3)
    result = cache._weights_archive_artifact([entry])
    assert (result is None) == (state == 'valid')


@pytest.mark.parametrize('catalog', ['valid', 'absent', 'malformed'])
def test_d06_real_legacy_catalog_read_parse_and_row_reuse(tmp_path, monkeypatch, catalog, record_property):
    job, invocation, roots = setup_recipe(tmp_path, monkeypatch, *MODELS[0])
    index = tmp_path / 'publication.json'
    rows, dependencies = [], []
    for dep in invocation.execution_plan.dependencies:
        if dep.kind in {'image', 'weights'}:
            prefix = ('containers/' if dep.kind == 'image' else 'weights/') + dep.relative_path
            path = roots[dep.kind] / dep.relative_path
            dependencies.append(prefix)
            rows.append(dict(name=prefix, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                             size_bytes=path.stat().st_size, mode=0o644, source=str(path)))
    payload = dict(schema=hf_assets.NAMED_INDEX_SCHEMA, archive=dict(sha256='d'*64,size_bytes=100),
                   artifacts=rows, dependencies=dependencies)
    if catalog != 'absent':
        index.write_text(json.dumps(payload) if catalog == 'valid' else '{broken')
        index.chmod(0o600)
    # setup_recipe deliberately disables discovery; restore the actual owner.
    monkeypatch.setattr(hf_assets, 'publication_index', _real_publication_index)
    monkeypatch.setattr(hf_assets, 'publication_index_path', lambda:index)
    monkeypatch.setattr(hf_assets, 'weights_archive', lambda:('d'*64,100))
    opens, parses, projections = [], [], []
    open_regular, load, project = hf_assets._open_regular, hf_assets.json.load, hf_assets._published_asset_rows
    def opened(path):
        opens.append(path)
        return open_regular(path)
    def loaded(stream):
        parses.append(True)
        return load(stream)
    def projected(prefix, **kwargs):
        projections.append(prefix)
        return project(prefix, **kwargs)
    monkeypatch.setattr(hf_assets, '_open_regular', opened)
    monkeypatch.setattr(hf_assets.json, 'load', loaded)
    monkeypatch.setattr(hf_assets, '_published_asset_rows', projected)
    entries, _ = cache.workflow_plan(SimpleNamespace(), compiled_plan=invocation.execution_plan)
    assert len(opens) == 1 and len(parses) == (0 if catalog == 'absent' else 1)
    assert len(projections) == len(set(projections))
    assert {e.remote_destination for e in entries} == set(dependencies)
    record_property('index_opens_per_planning_request',len(opens))
    record_property('index_parses_per_planning_request',len(parses))
    record_property('asset_row_projections',len(projections))
    if catalog == 'valid':
        previous = [(e.sha256,e.size_bytes) for e in entries]
        payload['artifacts'][0]['sha256'] = 'e'*64
        index.write_text(json.dumps(payload))
        fresh, _ = cache.workflow_plan(SimpleNamespace(), compiled_plan=invocation.execution_plan)
        assert [(e.sha256,e.size_bytes) for e in fresh] != previous


_real_publication_index = hf_assets.publication_index


@pytest.mark.asyncio
async def test_d04_actual_baseline_candidate_origin_payload_counters(tmp_path, monkeypatch, local_transport, record_property):
    import types
    baseline = types.ModuleType('services.remote_execution.cache_completion_baseline')
    baseline.__file__ = cache.__file__
    source = subprocess.check_output(['git', 'show',
        'd6b67fd3271e0f9b2a05d4d8ba7697ceb127325f:platform/api/services/remote_execution/cache.py'])
    exec(compile(source, baseline.__file__, 'exec'), baseline.__dict__)
    baseline.run_remote, baseline.rsync_to_remote = cache.run_remote, cache.rsync_to_remote
    baseline.BATCH_COUNT = 2
    monkeypatch.setattr(cache, 'BATCH_COUNT', 2)
    monkeypatch.setattr(hf_assets, 'configuration', lambda:None)
    artifacts = []
    for index in range(9):
        path = tmp_path / str(index)
        path.write_bytes(('inert ' + str(index)).encode())
        artifacts.append(bundle.CacheTransferArtifact(path, 'runtime/' + str(index),
            hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, 0o644, 'runtime'))
    measurements, receipts = [], []
    for name, owner in [('baseline',baseline),('candidate',cache)]:
        events = []
        async def progress(event): events.append(event)
        receipts.append(await owner._cache_artifacts(connection=SimpleNamespace(remote_root=str(tmp_path/name)),
            artifacts=artifacts,operation_id=str(uuid.uuid4()),progress=progress,check_fence=cache._noop,track_artifacts=True))
        full_rows = sum(len(e.get('artifact_progress',[])) for e in events)
        delta_rows = sum(len(e.get('artifact_delta',[])) for e in events)
        wire_bytes = sum(len(json.dumps(e).encode()) for e in events)
        measurements.append((full_rows,delta_rows,wire_bytes))
        record_property(name+'_full_roster_serialized_rows',full_rows)
        record_property(name+'_changed_serialized_rows',delta_rows)
        record_property(name+'_progress_serialized_bytes',wire_bytes)
    assert receipts[0] == receipts[1]
    assert measurements[1][0] == 18 and measurements[1][1] == 27
    assert measurements[1][0]+measurements[1][1] < measurements[0][0]
    assert measurements[1][2] < measurements[0][2]


@pytest.mark.asyncio
async def test_d04_large_delta_sql_chunks_retained_exact(store, record_property):
    rows = [dict(name='runtime/' + str(i),sha256=hashlib.sha256(str(i).encode()).hexdigest(),
                 size_bytes=i,state='pending') for i in range(85)]
    updates = []
    from sqlalchemy import event
    engine = store.kw['bind'].sync_engine
    def statement(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE'): updates.append(parameters)
    event.listen(engine, 'before_cursor_execute', statement)
    async def prewarm(**kwargs):
        await kwargs['progress'](dict(phase='checking',message='Admission',artifact_progress=rows))
        before = len(updates)
        status = await kwargs['progress'](dict(phase='verifying',message='Changed selected batch',
            artifact_delta=[(i,dict(row,state='verified')) for i,row in enumerate(rows)]))
        assert len(updates)-before == 4  # three bounded row updates plus one scalar publication
        assert status.sequence == 2 and status.artifact_summary.verified_count == 85
        async with store() as session:
            raw = (await session.get(ExecutionTarget,'vast:1')).provider_metadata['preload']
            assert raw['artifact_progress'] == [dict(row,state='verified') for row in rows]
        return dict(source_revision='a'*40,source_tree='b'*40,
                    artifacts=[{k:v for k,v in row.items() if k != 'state'} for row in rows])
    controller = p.PreloadController(store,prewarm=prewarm)
    async with store() as session: await controller.start(session,'vast:1',PreloadRequest(job_id='recipe'))
    await settle(controller)
    async with store() as session:
        raw = (await session.get(ExecutionTarget,'vast:1')).provider_metadata['preload']
        assert raw['phase'] == 'source_download_ready' and raw['sequence'] == 3
        assert len(raw['artifacts']) == 85
    event.remove(engine, 'before_cursor_execute', statement)
    record_property('changed_batch_rows',85)
    record_property('bounded_row_sql_updates',3)
    record_property('scalar_sql_updates',1)
    record_property('sequence_increments_per_callback',1)
