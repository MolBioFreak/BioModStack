"""Closeout counterexamples exercise real ASGI downloads and analytics."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from database import Design, get_session
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication, job, setup


@pytest.mark.asyncio
async def test_cached_metric_binds_download_snapshot(tmp_path, monkeypatch):
    from routers.designs import router
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(job(tmp_path))
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
            row = (await session.execute(select(Design))).scalar_one()
            app = FastAPI()
            app.include_router(router, prefix='/designs')
            async def dependency():
                yield session
            app.dependency_overrides[get_session] = dependency
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
                metric = (await client.get(f'/designs/{row.id}/residue-metrics')).json()
                assert metric['status'] == 'ok'
                expected = metric['document']['contentSha256']
                response = await client.get(f'/designs/{row.id}/pdb')
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == expected
                if os.environ.get('BMS_STRUCTURE_BYTES_WIRE'):
                    Path(os.environ['BMS_STRUCTURE_BYTES_WIRE']).write_text(json.dumps({'metric': metric, 'structure': response.text}))
                path = Path(row.pdb_path)
                original = path.read_bytes()
                path.write_bytes(original.replace(b'ATOM', b'HETATM', 1))
                rejected = await client.get(f'/designs/{row.id}/pdb')
                assert rejected.status_code == 409
                path.write_bytes(original)
                # Change disk *after* verification: response must use retained bytes,
                # never a FileResponse which reopens the now-different pathname.
                import services.boltz_scientific_consumer as consumer
                verify = consumer.verified_boltz_design
                async def raced(*args):
                    selected = await verify(*args)
                    path.write_bytes(b'changed after verification')
                    return selected
                monkeypatch.setattr(consumer, 'verified_boltz_design', raced)
                response = await client.get(f'/designs/{row.id}/pdb')
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == expected
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unsupported_marked_owner_reason_survives_asgi(tmp_path):
    from routers.designs import router
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            owner = job(tmp_path)
            session.add(owner)
            await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
            owner.model_id = 'protenix'
            await session.commit()
            app = FastAPI()
            app.include_router(router, prefix='/designs')
            async def dependency():
                yield session
            app.dependency_overrides[get_session] = dependency
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
                response = await client.get('/designs/by-job/job/plotly-metrics')
                assert response.status_code == 200, response.text
                wire = response.json()
                point = wire['points'][0]
                assert point['metrics'] == {}
                # Unsupported scalar/axis owner retains the role-independent structure.
                structure = await client.get(f"/designs/{point['id']}/pdb")
                assert structure.status_code == 200
                metric = (await client.get(f"/designs/{point['id']}/residue-metrics")).json()
                assert metric['status'] == 'unavailable'
                assert point['publication_state'] == {'state': 'unavailable', 'value': None, 'reason_code': 'missing_canonical_publication'}
                if os.environ.get('BMS_UNSUPPORTED_ANALYTICS_WIRE'):
                    Path(os.environ['BMS_UNSUPPORTED_ANALYTICS_WIRE']).write_text(json.dumps(wire))
    finally:
        await engine.dispose()
