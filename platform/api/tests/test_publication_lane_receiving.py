"""Publication-lane receiving repairs; inert CPU and scratch custody only."""
from collections import Counter
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, JobArtifact
from routers.models import router
from services import bindcraft2_launch as launch, bindcraft2_publication as publication
from services.result_ingester import ingest_job_results
from test_bindcraft2_launch import setup_source, stub_compiler
from test_bindcraft2_publication import campaign
from test_binder_continuation import selected
from test_binder_round_orchestration import fixture_root
from test_project_workflow_setups import setup_store


@pytest.mark.asyncio
async def test_round_inputs_once_per_missing_candidate_target_pair(selected, monkeypatch):
    from schemas import BinderRoundRequest
    from services import binder_round as rounds, binder_round_inputs as inputs
    session, root, _ = await fixture_root(selected)
    envelope = deepcopy(root.provenance[rounds.REQUEST])
    envelope['prediction']['params']['num_parallel_jobs'] = 3
    request = BinderRoundRequest.model_validate(envelope)
    calls = []
    original = inputs.prediction_request
    def prediction(*args):
        calls.append(args[2].id)
        return original(*args)
    monkeypatch.setattr(inputs, 'prediction_request', prediction)
    progress = rounds.progress_of(root)
    await rounds._plan(session, root, progress, request)
    assert not progress['errors'], progress
    assert calls == ['d0']
    assert len(progress['steps']) == 3
    steps = list(progress['steps'].values())
    assert {step['metadata']['sample_index'] for step in steps} == {0, 1, 2}
    assert len({step['request']['name'] for step in steps}) == 3
    assert all(step['request']['params'] == steps[0]['request']['params'] for step in steps)
    assert all(step['request']['params']['num_parallel_jobs'] == 1 for step in steps)
    # Copy isolation: editing one child cannot edit another retained envelope.
    steps[0]['request']['params']['protenix_n_sample'] = 99
    assert steps[1]['request']['params']['protenix_n_sample'] == 2
    await rounds._plan(session, root, progress, request)
    assert calls == ['d0']  # no missing sample means no extraction
    del progress['steps'][steps[1]['metadata']['step_id']]
    await rounds._plan(session, root, progress, request)
    assert calls == ['d0', 'd0']
    assert len(progress['steps']) == 3


@pytest.mark.parametrize('mode', ['protein_binder', 'antibody_binder', 'nanobody_binder'])
def test_ppiflow_display_requiredness_does_not_change_normalizer(mode, tmp_path):
    from services.ppiflow_generation import ppiflow_generation_inventory, parameter_contract
    from test_ppiflow_generation_launch import settings
    from routers.jobs import normalize_job_request
    from schemas import JobCreate
    before = parameter_contract(mode)
    target, framework = tmp_path / 'target.pdb', tmp_path / 'framework.pdb'
    requested = settings(mode, (target, framework, tmp_path))
    normalized = normalize_job_request(JobCreate(name='requiredness', model_id='ppiflow', mode=mode, params=requested))
    fields = {p['name']: p for p in ppiflow_generation_inventory(mode)['parameters']}
    assert parameter_contract(mode) == before
    assert normalize_job_request(normalized).params == normalized.params
    if mode == 'protein_binder':
        assert fields['binder_chain']['required_when'] == {'field': 'target_pdb', 'operator': 'truthy'}
        assert 'CSV' in fields['binder_chain']['required_help']
        csv_params = {**requested, 'target_pdb': None, 'binder_chain': None, 'input_csv': str(tmp_path / 'source.csv')}
        assert normalize_job_request(JobCreate(name='csv', model_id='ppiflow', mode=mode, params=csv_params)).params['binder_chain'] is None
        for rate in ('sample_hotspot_rate_min', 'sample_hotspot_rate_max'):
            assert any(condition['field'] == rate for condition in fields['specified_hotspots']['required_when']['any'])
        missing = {**requested, 'binder_chain': None}
    else:
        for key in ('target_pdb', 'framework_pdb', 'antigen_chain', 'heavy_chain', 'specified_hotspots'):
            assert fields[key]['required'] and fields[key]['nullable'] is False
        if mode == 'antibody_binder':
            assert fields['light_chain']['required'] and fields['light_chain']['nullable'] is False
        else:
            assert 'light_chain' not in fields  # the mode excludes this input entirely
            assert 'heavy-only' in ppiflow_generation_inventory(mode)['native_behavior']['light_chain']
        missing = {**requested, 'specified_hotspots': None}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as rejected:
        normalize_job_request(JobCreate(name='missing', model_id='ppiflow', mode=mode, params=missing))
    assert rejected.value.status_code == 422


def test_preview_materialization_reuses_only_leading_observations(tmp_path, monkeypatch):
    source, settings = setup_source(tmp_path, monkeypatch)
    counts = Counter()
    for name in ('validate_request', '_sources', '_identity'):
        original = getattr(launch, name)
        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(launch, name, counted)
    def compiler(*args):
        counts['compiler'] += 1
        return stub_compiler(*args)
    preview = launch.preview_campaign(settings, compiler=compiler)
    launch.materialize_campaign(settings, tmp_path / 'job', preview_digest=preview['preview_digest'], compiler=compiler)
    assert counts == {'validate_request': 3, '_sources': 3, '_identity': 6, 'compiler': 3}
    def mutate(request, destination):
        result = stub_compiler(request, destination)
        source.write_text('>changed\nG\n')
        return result
    with pytest.raises(ValueError, match='source changed during compilation'):
        launch.preview_campaign(settings, compiler=mutate)


def test_native_timeout_is_sanitized_at_preview_http_boundary(tmp_path, monkeypatch):
    _, settings = setup_source(tmp_path, monkeypatch)
    image = tmp_path / 'image.sif'
    image.touch()
    def timeout(command, **kwargs):
        assert kwargs['timeout'] == 120
        raise subprocess.TimeoutExpired(command, 120, output='private request', stderr='private stderr')
    monkeypatch.setattr(launch.subprocess, 'run', timeout)
    # Route delegates to the real model owner, with only image lookup overridden.
    original_preview = launch.preview_campaign
    monkeypatch.setattr(launch, 'preview_campaign', lambda request: original_preview(request,
        compiler=lambda native, destination: launch._native_compile(native, destination, image=image)))
    app = FastAPI()
    app.include_router(router, prefix='/models')
    response = TestClient(app).post('/models/bindcraft2/campaign/preview', json={
        'model_id': 'bindcraft2', 'mode': 'campaign', 'params': {'bindcraft2_settings': settings}})
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == 'BC2 native compilation timed out after 120 seconds'
    assert 'private' not in response.text


def test_native_timeout_stops_and_reaps_actual_compiler_process(tmp_path, monkeypatch):
    setup_source(tmp_path, monkeypatch)
    image = tmp_path / 'image.sif'
    image.touch()
    process_ids = []
    real_run, real_popen = subprocess.run, subprocess.Popen
    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        process_ids.append(child.pid)
        return child
    def runner(command, **kwargs):
        assert kwargs['timeout'] == .05
        return real_run([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
    monkeypatch.setattr(launch, 'COMPILE_TIMEOUT_SECONDS', .05)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(subprocess, 'run', runner)
    with pytest.raises(ValueError, match='timed out after 0.05 seconds'):
        launch._native_compile({}, tmp_path / 'campaign', image=image)
    assert len(process_ids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(process_ids[0], 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('zero', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
async def test_bc2_one_inventory_pass_and_launch_offline_reopen(tmp_path, monkeypatch, zero, legacy):
    root = tmp_path / 'output'
    campaign(root, zero=zero)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'publication.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='bc', name='bc', model_id='bindcraft2', mode='campaign',
                      params={'bc2_compilation': str(tmp_path / 'gone/compilation.json')},
                      output_dir=str(root), status='running')
            session.add(job)
            await session.commit()
            assert await ingest_job_results(job.id, str(root), session) == (0 if zero else 1)
            if legacy:
                receipt = deepcopy(job.provenance['bindcraft2_native_publication'])
                receipt.pop('inventory_version')
                receipt.pop('campaign_root')
                receipt['files'] = publication._inventory(root, publication.read_native_publication(root), legacy=True)
                job.provenance = {**job.provenance, 'bindcraft2_native_publication': receipt}
                # Historical inventory's native JSON media types are unchanged for this roster.
            await session.commit()
        async with factory() as session:
            job = await session.get(Job, 'bc')
            reads = Counter()
            original = publication._regular
            def regular(root, relative):
                reads[relative] += 1
                return original(root, relative)
            monkeypatch.setattr(publication, '_regular', regular)
            native, receipt = await publication.read_published_native_results(job, session)
            assert reads == Counter({name: 1 for name in receipt['files']})
            assert len(receipt['candidates']) == (0 if zero else 1)
            artifact = await session.scalar(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))
            artifact.media_type = 'wrong/type'
            with pytest.raises(publication.PublicationError, match='artifact receipt changed'):
                await publication.read_published_native_results(job, session)
    finally:
        await engine.dispose()
