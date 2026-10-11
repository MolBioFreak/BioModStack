"""Retained HTTP descriptor lifetime; byte transport, not BAM science."""
import asyncio
import hashlib
import os

import pytest
from starlette.requests import Request

from routers import ngs_alignment_sessions as routes


@pytest.mark.asyncio
@pytest.mark.parametrize('writable', [False, True])
async def test_authorized_range_uses_retained_inode_or_legacy_snapshot(tmp_path, writable):
    source = tmp_path / 'aligned.bam'
    body = b'0123456789'
    source.write_bytes(body)
    source.chmod(0o644 if writable else 0o444)
    metadata = dict(kind='alignment', size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), mime_type='application/octet-stream')
    request = Request(dict(type='http', method='GET', path='/artifact', headers=[(b'range', b'bytes=2-5')]))
    response = await routes._serve_artifact(source, metadata, request, job_id='job')
    assert response.status_code == 206
    assert isinstance(response, routes._ArtifactStreamingResponse)
    snapshot = response._snapshot
    assert (os.fstat(snapshot.fileno()).st_ino == source.stat().st_ino) is (not writable)
    source.unlink()
    assert b''.join([bytes(chunk) async for chunk in response.body_iterator]) == b'2345'
    assert snapshot.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('after_chunk', [False, True])
async def test_disconnect_closes_borrow_even_before_iterator_start(tmp_path, after_chunk):
    source = tmp_path / 'aligned.bam'
    body = b'A' * (1024 * 1024 + 1)
    source.write_bytes(body)
    source.chmod(0o444)
    metadata = dict(kind='alignment', size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), mime_type='application/octet-stream')
    scope = dict(type='http', method='GET', path='/artifact', headers=[], asgi={'spec_version': '2.4'})
    response = await routes._serve_artifact(source, metadata, Request(scope), job_id='job')
    assert isinstance(response, routes._ArtifactStreamingResponse)
    async def receive():
        return {'type': 'http.disconnect'}
    async def send(message):
        if not after_chunk or message['type'] == 'http.response.body':
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await response(scope, receive, send)
    assert response._snapshot.closed
    assert source.read_bytes() == body


@pytest.mark.asyncio
async def test_real_sql_job_capability_catalog_and_retained_http(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Job
    from services import alignment_access, job_result_roots

    output = tmp_path/'results'/'job'/'assembly'
    output.mkdir(parents=True)
    # Transport fixture, not a claim that clone inference produced this FASTA.
    artifact = output/'fixture.final.fasta'
    body = b'>fixture\nACGTACGT\n'
    artifact.write_bytes(body)
    artifact.chmod(0o444)
    monkeypatch.setattr(job_result_roots, 'get_results_dir', lambda: tmp_path/'results')
    monkeypatch.setattr(routes.service, 'get_results_dir', lambda: tmp_path/'results')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/jobs.sqlite')
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Job.__table__.create)
    token = 'task-owned-capability-token'
    job_id = '00000000-0000-4000-8000-000000000009'
    async with factory() as session:
        session.add(Job(id=job_id, name='retained transport fixture', model_id='nanopore',
                        mode='wf_clone_validation', status='completed',
                        params={'ont_workflow_id':'wf_clone_validation', 'ont_input_mode':'bam'},
                        output_dir=str(output.parent),
                        provenance={alignment_access.PROVENANCE_DIGEST_KEY:alignment_access.token_sha256(token),
                                    alignment_access.PROVENANCE_SCHEME_KEY:alignment_access.SCHEME}))
        await session.commit()
    async def database_session():
        async with factory() as session:
            yield session
    app = FastAPI()
    app.add_exception_handler(routes.OntNgsRouteError, routes.ont_ngs_route_error_handler)
    app.include_router(routes.router, prefix='/api')
    for dependency in (routes.get_session, routes.get_molbio_ngs_session, routes.get_experiment_session):
        app.dependency_overrides[dependency] = database_session
    opened = []
    original = routes.service.open_verified_artifact_snapshot
    def observe(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        opened.append((snapshot, os.fstat(snapshot.fileno()).st_ino, kwargs.get('retained')))
        return snapshot
    monkeypatch.setattr(routes.service, 'open_verified_artifact_snapshot', observe)
    try:
        async with AsyncClient(transport=ASGITransport(app=app, client=('127.0.0.1',40000)), base_url='https://test') as client:
            url = f'/api/jobs/{job_id}/ngs-artifacts'
            assert (await client.get(url)).status_code == 403
            client.cookies.set(alignment_access.cookie_name(job_id, secure=True), token)
            inventory = await client.get(url)
            assert inventory.status_code == 200, inventory.text
            descriptor = inventory.json()['artifacts'][0]
            ranged = await client.get(descriptor['url'], headers={'Range':'bytes=2-5'})
            assert ranged.status_code == 206, ranged.text
            assert ranged.content == body[2:6]
            full = await client.get(descriptor['url'])
            assert full.status_code == 200 and full.content == body
            head = await client.head(descriptor['url'])
            assert head.status_code == 200 and head.content == b''
            assert opened and all(retained and inode == artifact.stat().st_ino and handle.closed for handle,inode,retained in opened)
    finally:
        await engine.dispose()
