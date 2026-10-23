"""Offline tiny-file owner acceptance; no native inference or image execution."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from services.remote_execution import bundle, result_generation as generation
from scripts.lib import portable_inputs as portable
from tools import bms_artifact_cache as worker
from test_one_copy_remote_runtime_core import pinned
from test_denovo_remote_retained import _input_inventory, _place_and_hide, protenix_package
from test_remote_lifecycle_gaps import store
from test_remote_bundle_runtime_gaps import package


def test_real_bundle_binds_source_membership_version(protenix_package):
    _, _, job, target, command = protenix_package
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command,
                                           native_invocation=job.native_invocation)
    assert prepared.envelope.environment['BMS_SOURCE_MEMBERSHIP_POLICY'] == bundle.SOURCE_MEMBERSHIP_POLICY


def test_real_rfd4_preparation_retains_order_state_and_zero_resume_seed(tmp_path, monkeypatch):
    from services.rfd4_proteina import prepare_for_job
    root = tmp_path / 'controller'
    root.mkdir()
    a, b = root / 'a/target.cif', root / 'b/target.cif'
    state = root / 'state'
    state.mkdir()
    (state / 'state.pt').write_bytes(b'opaque native state')
    (state / 'config.json').write_bytes(b'{"native":"state companion"}')
    for path, body in ((a, b'data_A\n'), (b, b'data_B\n')):
        path.parent.mkdir()
        path.write_bytes(body)
    request = dict(mode='generate', input=dict(kind='annotated', slots=['B', 'A']),
                   resume_slot='state', settings=dict(metrics_tags=[], search=dict(resume_seed=0)))
    prepared = prepare_for_job('generate', {'rfd4_request': request}, root / 'job',
        resolved_sources={'A': a, 'B': b, 'state': state}, allowed_roots=[root])
    refs, assets = _input_inventory(tmp_path, monkeypatch, root, prepared, 'rfd4_proteina', 'generate')
    _place_and_hide(tmp_path, monkeypatch, root, refs, assets)
    closure = portable.resolve_input_path(prepared['rfd4_input_closure'])
    retained = json.loads((closure / 'request.json').read_text())
    bindings = json.loads((closure / 'bindings.json').read_text())
    assert retained['request']['input']['slots'] == ['B', 'A']
    assert retained['request']['settings']['search']['resume_seed'] == 0
    assert (closure / bindings['B']).read_bytes() == b'data_B\n'
    assert (closure / bindings['A']).read_bytes() == b'data_A\n'
    assert (closure / bindings['state'] / 'state.pt').read_bytes() == b'opaque native state'
    assert (closure / bindings['state'] / 'config.json').read_bytes() == b'{"native":"state companion"}'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['prepared', 'prior_moved', 'new_moved', 'flushed', 'commit'])
async def test_rfd4_native_return_prepostcommit_recovery(store, tmp_path, monkeypatch, fault):
    from sqlalchemy import select
    from database import Job, JobArtifact
    from services.remote_execution import executor
    from services.result_ingester import ingest_job_results
    from services.rfd4_proteina_results import read_published_generation_page
    from test_denovo_canonical_return import native_tree, stage_worker_return
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id, job.mode, job.params = 'rfd4_proteina', 'generate', {}
        job.output_dir = str(tmp_path / 'returned')
        output = Path(job.output_dir)
        output.mkdir()
        (output / 'historical.txt').write_bytes(b'historical science')
        await session.commit()
        producer = tmp_path / 'producer'
        native_tree(producer, 'rfd4_proteina', 'partial')
        _, incoming = stage_worker_return(job, producer, tmp_path / 'attempt')
        await session.commit()
        def crash(name):
            if name == fault:
                raise RuntimeError('injected ' + name)
        monkeypatch.setattr(generation, '_checkpoint', crash)
        try:
            executor._publish_result_generation(job, incoming)
            await ingest_job_results(job.id, job.output_dir, session, commit=False)
            if fault == 'flushed':
                await session.flush()
                raise RuntimeError('injected flushed')
            await session.commit()
            if fault == 'commit':
                raise RuntimeError('injected commit')
        except RuntimeError as exc:
            assert str(exc) == 'injected ' + fault
        monkeypatch.setattr(generation, '_checkpoint', lambda name: None)
        await session.rollback()
        job = await session.get(Job, 'job', populate_existing=True)
        assert await executor._recover_result_generation(session, job)
        assert not await executor._recover_result_generation(session, job)
    async with store() as fresh:
        job = await fresh.get(Job, 'job')
        if fault != 'commit':
            assert not list(await fresh.scalars(select(JobArtifact)))
            assert (Path(job.output_dir) / 'historical.txt').read_bytes() == b'historical science'
            # Retry only local retained import; never invoke generation/inference.
            executor._publish_result_generation(job, incoming)
            await ingest_job_results(job.id, job.output_dir, fresh, commit=False)
            await fresh.commit()
            await executor._recover_result_generation(fresh, job)
        before = {a.id for a in await fresh.scalars(select(JobArtifact))}
        page = await read_published_generation_page(job, fresh)
        # Native publication retains both completed-rollout and ranking-event
        # records; a scoring intermediate is not silently discarded as no Design.
        assert page['model_id'] == 'rfd4_proteina' and page['total'] == 2
        assert page['population_completeness'] == 'partial'
        await ingest_job_results(job.id, job.output_dir, fresh)
        assert {a.id for a in await fresh.scalars(select(JobArtifact))} == before


def digest(body):
    return hashlib.sha256(body).hexdigest()


def test_weight_aliases_hash_once_and_revalidate_stamp(tmp_path, monkeypatch, record_property):
    root = tmp_path / 'disco'
    (root / 'blobs').mkdir(parents=True)
    body = root / 'blobs/model'
    body.write_bytes(b'tiny selected weight')
    body.chmod(0o444)
    (root / 'snapshot').symlink_to('blobs', target_is_directory=True)
    (root / 'alias').symlink_to('blobs/model')
    os.link(body, root / 'hardlink')
    calls = []
    original = bundle._sha256_file
    def hashed(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(bundle, '_sha256_file', hashed)
    inventory = {}
    rows = bundle._records_for_source(root, 'runtime/weights/disco', 'runtime', runtime_sources=inventory)
    assert {r.relative_path for r in rows} == {'runtime/weights/disco/' + n for n in
        ('blobs/model', 'snapshot/model', 'alias', 'hardlink')}
    assert len(calls) == 1
    assert all(r.sha256 == digest(b'tiny selected weight') and r.link_target is None for r in rows)
    again = bundle._records_for_source(root, 'runtime/weights/disco', 'runtime', runtime_sources=inventory)
    assert again == rows and len(calls) == 1
    body.chmod(0o644)
    body.write_bytes(b'changed selected weight')
    body.chmod(0o444)
    changed = bundle._records_for_source(root, 'runtime/weights/disco', 'runtime', runtime_sources=inventory)
    assert len(calls) == 2 and all(r.sha256 == digest(body.read_bytes()) for r in changed)
    # Independent operations never inherit an earlier operation's proof.
    bundle._records_for_source(root, 'runtime/weights/disco', 'runtime')
    assert len(calls) == 3
    record_property('logical_bindings', len(rows))
    record_property('first_inventory_body_hashes', 1)
    (root / 'escape').symlink_to(tmp_path)
    with pytest.raises(bundle.RemoteBundleError, match='escapes'):
        bundle._records_for_source(root, 'runtime/weights/disco', 'runtime', runtime_sources=inventory)


def test_weight_alias_cycle_still_rejected(tmp_path):
    (tmp_path / 'cycle').symlink_to('.')
    with pytest.raises(bundle.RemoteBundleError, match='cycles'):
        bundle._records_for_source(tmp_path, 'runtime/weights/cycle', 'runtime')


def ingest(storage, body):
    item = dict(sha256=digest(body), size_bytes=len(body))
    path = Path(storage.root) / 'incoming' / item['sha256']
    path.write_bytes(body)
    storage.ingest(item, path)
    return item


def test_selected_weight_0444_concurrent_warm_and_versioned_views(tmp_path, monkeypatch, record_property):
    layout, native, donor = pinned(tmp_path, monkeypatch)
    before = donor.stat()
    row = bundle._runtime_records(layout, 'runtime/weights/tiny', model_id='tiny')[0]
    assert row.mode == 0o444
    storage = worker.Cache(tmp_path / 'worker/cache')
    item = ingest(storage, donor.read_bytes())
    entries = [dict(item, name='tiny/native/checkpoint.bin', mode=row.mode)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        installed = list(pool.map(lambda _: storage.weights(entries, install=True), range(2)))
    assert installed[0] == installed[1]
    old = Path(installed[0]['root']) / entries[0]['name']
    cas = Path(storage.root) / 'objects/sha256' / item['sha256'][:2] / item['sha256']
    assert (old.stat().st_dev, old.stat().st_ino) == (cas.stat().st_dev, cas.stat().st_ino)
    assert old.stat().st_mode & 0o777 == cas.stat().st_mode & 0o777 == 0o444
    def no_projection(*args, **kwargs):
        pytest.fail('warm installed layout must not republish unchanged members')
    publish_copy = storage._publish_copy
    monkeypatch.setattr(storage, '_publish_copy', no_projection)
    assert storage.weights(entries, install=True) == installed[0]
    assert storage.weights(entries, full=True) == installed[0]
    monkeypatch.setattr(storage, '_publish_copy', publish_copy)
    newer = ingest(storage, b'new selected version')
    new_entries = [dict(newer, name=entries[0]['name'], mode=0o444)]
    newer_view = storage.weights(new_entries, install=True)
    assert newer_view['root'] != installed[0]['root']
    assert old.read_bytes() == b'pinned tiny checkpoint'
    assert (Path(newer_view['root']) / entries[0]['name']).read_bytes() == b'new selected version'
    after = donor.stat()
    assert (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
            before.st_mtime_ns, before.st_ctime_ns) == (after.st_dev, after.st_ino,
            after.st_mode, after.st_nlink, after.st_mtime_ns, after.st_ctime_ns)
    # Historical explicit 0400 projections remain supported and never chmod CAS.
    private = storage.weights([dict(item, name='tiny/checkpoint.bin', mode=0o400)], install=True)
    leaf = Path(private['root']) / 'tiny/checkpoint.bin'
    assert leaf.stat().st_mode & 0o777 == 0o400
    assert leaf.stat().st_ino != cas.stat().st_ino
    assert cas.stat().st_mode & 0o777 == 0o444
    record_property('ordinary_view_same_dev_inode', True)


@pytest.mark.parametrize('model,mode', [('rfd4_proteina', 'evaluate'),
    ('protein_modification_experimental', 'de_novo_design')])
def test_rfd4_retained_tree_opaque_for_explicit_and_parent(tmp_path, monkeypatch, model, mode):
    root = tmp_path / 'controller'
    closure = root / 'retained'
    payloads = {'request.json': b'{"raw":"/absent/provenance"}',
        'sources/a/target.cif': b'data_A\n_sa.SA_SRC /absent/original\nloop_\n_sa_cwi.id\n1\n',
        'sources/b/target.cif': b'data_B\n', 'sources/target.cif.zst': b'opaque compressed cif',
        'sources/target.bcif': b'opaque bcif', 'state/state.pt': b'opaque state\x00',
        # A state companion is opaque even when it looks like another model's JSON.
        'state/companion.json': b'{"components":[{"ligand_file":"/absent/not-an-input"}]}',
        'state/native.yaml': b'!!native opaque state'}
    for name, body in payloads.items():
        path = closure / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    params = dict(generator='rfd4_proteina', rfd4_input_closure=str(closure),
        rfd4_request='/absent/raw-request', native_sources='/absent/originals')
    assert portable.prepared_generation_source_fields(model, mode, params) == {'rfd4_request', 'native_sources'}
    refs, assets = _input_inventory(tmp_path, monkeypatch, root, params, model, mode)
    assert {str(Path(r['source_path']).relative_to(closure)) for r in refs} == set(payloads)
    assert all(r['role'] == 'native-snapshot' for r in refs)
    _place_and_hide(tmp_path, monkeypatch, root, refs, assets)
    for name, body in payloads.items():
        assert portable.resolve_input_path(closure / name).read_bytes() == body
    binding = portable._bindings()
    changed = Path(binding['bindings'][0]['path'])
    changed.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='digest/size mismatch'):
        portable.resolve_input_path(binding['bindings'][0]['reference']['source_path'])


def test_source_projection_exact_members_and_hidden_replay(tmp_path, record_property):
    repo = tmp_path / 'repo'
    repo.mkdir()
    payloads = {'main.nf': b'// inert selected graph\n', 'scripts/helper.py': b'VALUE = 17\n',
                'platform/api/config/models/tiny.yaml': b'id: tiny\n',
                'platform/api/tests/fixtures/bioxp_methods/keep.json': b'{}',
                'artifacts/android/distribution.apk': b'not runtime source'}
    for name, body in payloads.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c',
        'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    sha = bundle._staged_source_archive(repo, tmp_path / 'data', revision, tmp_path / 'stage', extract=False)
    archive = tmp_path / 'stage/.bms-source.tar.gz'
    rows = bundle._source_archive_records(archive, sha)
    names = {r.relative_path.removeprefix('source/') for r in rows if r.relative_path != 'source/.bms-source.tar.gz'}
    expected = set(payloads) - {'artifacts/android/distribution.apk'}
    assert names == expected
    assert bundle.SOURCE_MEMBERSHIP_POLICY == 'bms.execution-source.v1'
    repo.rename(tmp_path / 'controller-hidden')
    storage = worker.Cache(tmp_path / 'worker/cache')
    item = ingest(storage, archive.read_bytes())
    output = tmp_path / 'worker/attempt/source'
    storage.extract_source(item, str(output), immutable=True)
    for name in expected:
        assert (output / name).read_bytes() == payloads[name]
    # Actual source execution from the extracted private tree, not the checkout.
    result = subprocess.check_output(['python3', '-c', 'import runpy,sys; print(runpy.run_path(sys.argv[1])["VALUE"])',
                                      str(output / 'scripts/helper.py')], text=True)
    assert result.strip() == '17'
    record_property('archive_compressed_bytes', archive.stat().st_size)
    record_property('member_logical_bytes', sum(r.size_bytes for r in rows if r.relative_path != 'source/.bms-source.tar.gz'))
    record_property('member_count', len(expected))
    record_property('private_allocated_bytes', sum(p.stat().st_blocks * 512 for p in output.rglob('*') if p.is_file()))


@pytest.mark.parametrize('name,linked', [('collected/rfd4_proteina/candidate.cif', True),
    ('collected/rfd4_proteina/state.pt', False), ('collected/rfd4_proteina/manifest.json', False)])
def test_return_reuse_keeps_mutable_metadata_and_state_private(tmp_path, name, linked):
    output = tmp_path / 'output'
    source = output / name
    source.parent.mkdir(parents=True)
    source.write_bytes(b'returned native bytes')
    job = SimpleNamespace(provenance={}, execution_target_id='target', output_dir=str(output), child_output_dir=None)
    artifact = SimpleNamespace(relative_path=name, sha256=digest(source.read_bytes()), size_bytes=source.stat().st_size)
    target = tmp_path / 'incoming' / name
    assert generation.reuse_payload(job, artifact, {name: artifact}, target)
    assert (source.stat().st_ino == target.stat().st_ino) is linked
    if not linked:
        target.write_bytes(b'importer update')
        assert source.read_bytes() == b'returned native bytes'
    source.write_bytes(b'changed')
    assert not generation.reuse_payload(job, artifact, {name: artifact}, tmp_path / 'delta' / name)
