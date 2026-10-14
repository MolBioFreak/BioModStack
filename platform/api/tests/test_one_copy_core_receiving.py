"""Integrated preview/storage receiving; no providers or native inference."""
from pathlib import Path
import json
import shutil

import pytest
from test_remote_cache_integration import local_transport, make_bundle, next_attempt
from test_remote_bundle_path_gaps import roots


def test_weight_object_classification_resolves_only_selected_store_alias(tmp_path, monkeypatch):
    from tools import bms_container
    weights = tmp_path / 'retained-weights'
    weights.mkdir()
    alias = tmp_path / 'weights'
    alias.symlink_to(weights, target_is_directory=True)
    monkeypatch.setenv('BMS_HOST_WEIGHTS', str(alias))
    monkeypatch.delenv('BMS_WEIGHTS', raising=False)
    relative = 'objects/sha256/' + 'a' * 64 + '/runtime.sif'
    assert bms_container._weight_acquisition_object(weights / relative)
    assert not bms_container._weight_acquisition_object(tmp_path / 'image-store' / relative)


def test_selected_frustra_metadata_keeps_remote_packing_source_owner():
    from model_registry import selected_execution_metadata
    from native_components import PROCESS_CONTRACTS
    metadata = selected_execution_metadata('frustrampnn', 'analyze', {}, 'workflows/frustrampnn_analysis.nf')
    assert any(d.relative_path == 'scripts/stage_frustrampnn_parent_candidate.py'
               for d in metadata.dependencies if d.kind == 'support_tool')
    contract = PROCESS_CONTRACTS['modules/frustrampnn_parent_fanout.nf:SpawnWaitFrustraMPNNParentChildren']
    assert 'scripts/stage_frustrampnn_parent_candidate.py' in contract[3]


@pytest.mark.asyncio
async def test_executor_and_real_cache_have_one_support_placement_owner(tmp_path, monkeypatch, local_transport):
    from services.remote_execution import cache, executor
    from services.remote_execution.bundle import TransferPlan

    connection, first = make_bundle(tmp_path)
    executor_uploads = []
    cache_upload = cache.rsync_to_remote

    async def upload(connection, source, destination, **kwargs):
        executor_uploads.append(str(source))
        assert Path(source) != tmp_path / 'support', 'duplicate whole support upload outside cache'
        return await cache_upload(connection, source, destination, **kwargs)

    monkeypatch.setattr(executor, 'run_remote', cache.run_remote)
    monkeypatch.setattr(executor, 'rsync_to_remote', upload)
    input_file = tmp_path / 'private-input'
    input_file.write_bytes(b'inert scientific input')
    cases = [first, next_attempt(connection, first)]
    retained = []
    cache_counts = []
    for bundle in cases:
        bundle.remote_attempt_dir = str(Path(bundle.remote_runtime_dir).parents[1])
        bundle.local_attempt_dir = tmp_path / 'controller-attempts' / bundle.attempt_id
        bundle.local_attempt_dir.mkdir(parents=True)
        (bundle.local_attempt_dir / 'fixture-control.json').write_text(json.dumps({'attempt': bundle.attempt_id}))
        bundle.input_transfers = (TransferPlan(input_file, bundle.remote_attempt_dir + '/inputs/selected'),)
        prior = len(executor_uploads)
        await executor._stage_bundle(connection, bundle)
        assert executor_uploads[prior:] == [str(bundle.local_attempt_dir), str(input_file)]
        assert (Path(bundle.remote_attempt_dir) / 'inputs/selected').read_bytes() == input_file.read_bytes()
        assert (Path(bundle.remote_attempt_dir) / 'bundle/source/workflow.nf').read_bytes() == b'workflow'
        retained.append(Path(bundle.remote_runtime_dir) / 'support-python/library')
        assert retained[-1].read_bytes() == b'support payload'
        cache_counts.append(len(local_transport[1]) - len(executor_uploads))
    assert cache_counts == [1, 1], 'warm launch must not transfer unchanged support bodies'
    assert retained[0].samefile(retained[1])
    assert not retained[0].stat().st_mode & 0o222
    shutil.rmtree(cases[0].remote_attempt_dir)
    assert retained[1].read_bytes() == b'support payload'


@pytest.mark.asyncio
@pytest.mark.parametrize('retained_request', [False, True])
async def test_cm_jobs_http_preview_binds_exact_installed_checkpoint(roots, tmp_path, monkeypatch, retained_request):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Base, get_session
    from routers import jobs
    from test_one_copy_cm_core import request_fixture

    request, checkpoint = request_fixture(roots, tmp_path, monkeypatch, 'pinned', retained_request)
    original = request.request_path.read_bytes()
    before = (checkpoint.stat().st_ino, checkpoint.stat().st_size, checkpoint.stat().st_mode)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "preview.sqlite"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[jobs.get_experiment_session] = session_dependency
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
            response = await client.post('/api/jobs/execution-plan/preview', json={
                'name': 'tiny CM preview', 'model_id': 'conformational_mapping', 'mode': 'map',
                'params': {**request.launch_params, 'run_frustrampnn': True},
            })
        assert response.status_code == 200, response.text
        result = response.json()
        runtime_refs = [row for row in result['input_identities'] if row['role'] == 'runtime']
        assert len(runtime_refs) == 1
        assert runtime_refs[0]['sha256'] == json.loads(original)['confornets']['checkpoint']['sha256']
        assert not any(row['role'] == 'runtime-snapshot' for row in result['input_identities'])
        assert request.request_path.read_bytes() == original
        assert before == (checkpoint.stat().st_ino, checkpoint.stat().st_size, checkpoint.stat().st_mode)
    finally:
        await engine.dispose()
