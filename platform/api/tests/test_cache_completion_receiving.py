"""Receiving adaptations of three parent-owned tests; original cases remain untouched.

The only changes are the D01 source producer and D06 request-local arguments;
assertions/fixture custody are retained. Parent should apply the same adapters.
"""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace
import pytest
from component_runtime import SourceIdentity
from services.remote_execution import bundle, cache
from test_remote_bundle_runtime_gaps import package
from test_remote_cache_integration import local_transport
from test_remote_preloading import saved_invocation
REAL_RUN = subprocess.run
REPO = Path(__file__).resolve().parents[3]


def test_cache_completion_receiving_prewarm_plan_forwards_actual_invocation_before_assets(tmp_path, monkeypatch):
    from services.remote_execution import cache
    invocation = saved_invocation()
    monkeypatch.setattr(cache, 'get_code_root', lambda: tmp_path)
    monkeypatch.setattr(cache, 'current_source_identity', lambda repo: ('a' * 40, 'b' * 40))
    class DependencyBoundaryReached(Exception):
        pass
    def dependencies(model_id, mode, params, *, native_invocation, publication, resolved):
        assert (model_id, mode) == ('boltz2', 'predict')
        assert native_invocation is invocation
        assert params == invocation.native_parameters
        raise DependencyBoundaryReached
    monkeypatch.setattr(cache, '_runtime_assets', dependencies)
    with pytest.raises(DependencyBoundaryReached):
        cache._prewarm_plan(SimpleNamespace(model_id='boltz2', mode='predict'),
            list(invocation.command), 'a' * 40, 'b' * 40, tmp_path,
            native_invocation=invocation)
    assert not list(tmp_path.iterdir())


def test_cache_completion_receiving_workflow_preview_forwards_shared_plan_and_scrubs_worker_manifest(tmp_path, monkeypatch):
    import json
    from services import nextflow
    from services.remote_execution import cache, managed_inventory as mi
    from services.remote_execution.contracts import WorkflowProvisionSelection
    selection = WorkflowProvisionSelection(kind='workflow', workflow_request=dict(
        name='Unsaved', model_id='protenix', mode='predict', params={'sequence': 'ACDE'}))
    plan = nextflow.build_selected_execution_plan(model_id='protenix', mode='predict',
        entrypoint='workflows/structure_prediction.nf', requested={'science': 17},
        effective={'science': 17}, native_parameters={'science': 17},
        source_identity=SourceIdentity('a'*40, 'b'*40))
    invocation = SimpleNamespace(model_id='protenix', mode='predict',
        source_identity=plan.source_identity, native_parameters={'science': 17},
        effective_json=plan.effective_json, execution_plan=plan)
    compiled = []
    def compile_request(request):
        compiled.append(request)
        return invocation
    asset = tmp_path / 'model.pt'
    asset.write_bytes(b'controlled dependency fixture')
    def runtime_assets(model, mode, params, *, include_support, selected_plan, publication, resolved):
        assert selected_plan is plan
        assert (model, mode, params, include_support) == ('protenix', 'predict', {'science': 17}, False)
        return [(asset, 'weights/model.pt'), (asset, 'weights/model.pt')]
    monkeypatch.setattr(nextflow, 'compile_workflow_provision_request', compile_request)
    monkeypatch.setattr(cache, '_runtime_assets', runtime_assets)
    monkeypatch.setattr(cache, 'current_source_identity', lambda: ('a'*40, 'b'*40))
    target = SimpleNamespace(id='target', host='worker', port=22, username='root',
        remote_root='/worker', host_key_sha256='e'*64)
    preview, entries = cache.independent_preview(selection, target)
    assert compiled == [selection.workflow_request]
    assert len(entries) == 1
    assert preview.effective_params == {'science': 17}
    assert preview.plan_sha256 == plan.plan_sha256
    assert preview.asset_states[0].state == 'unknown'
    assert preview.transfer_bytes == asset.stat().st_size
    assert preview.storage_bytes == 2 * asset.stat().st_size
    manifest = mi.manifest_for(selection, entries, ('a'*40, 'b'*40))
    assert manifest['selection']['kind'] == 'workflow'
    assert len(manifest['selection']['model_id']) == 64
    wire = json.dumps(manifest)
    assert 'ACDE' not in wire and 'workflow_request' not in wire and 'sequence' not in wire
    assert [path.name for path in tmp_path.iterdir()] == ['model.pt']


@pytest.mark.asyncio
async def test_cache_completion_receiving_real_source_launch_prewarm_lossless_and_cache_reuse(package, local_transport, monkeypatch, tmp_path):
    roots, release, job, target, command = package
    # Launch and prewarm must share one isolated controller archive cache.
    monkeypatch.setattr(cache, 'get_data_root', lambda: roots['data'])
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
    monkeypatch.setattr(cache, 'get_code_root', lambda: roots['repo'])
    prewarmed = [cache._workflow_source_archive(prewarm_dir,
        (job.execution_source_revision, job.execution_source_tree))]
    launch_source, = [a for a in bundle.cache_transfer_artifacts(prepared) if a.role == 'source']
    warm_source, = [a for a in prewarmed if a.role == 'source']
    # One revision-keyed shared archive, privately staged for both consumers.
    assert archive_commands == [['git', 'archive', '--format=tar.gz', '-6', job.execution_source_revision]]
    assert launch_source.source != warm_source.source
    assert (roots['data'] / 'remote-execution/source-archives' /
            (job.execution_source_revision + '.tar.gz')).read_bytes() == launch_source.source.read_bytes()
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

