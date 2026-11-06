"""Real BMS ASGI routers and scratch SQLite, with in-process HTTP transport."""
import asyncio
import io
from pathlib import Path
from urllib.error import HTTPError

import pytest
pytest.importorskip('fastapi')
pytest.importorskip('sqlalchemy')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bms_alignment.http import AlignmentClient, BmsHttpError, JobSubmission
from bms_alignment.models import AlignmentRequest

# This optional integration test runs only with the BMS API environment available.
pytest.importorskip('database')
pytest.importorskip('duckdb')
pytest.importorskip('pysam')
pytest.importorskip('pydna')
pytest.importorskip('trimesh')
from database import Base
from model_registry import ModelRegistry
from routers import jobs, models


def test_sdk_real_asgi_scratch_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_CORE_RUNTIME_MODE', '0')
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'artifacts'))
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    api = Path(__file__).resolve().parents[3] / 'platform' / 'api'
    registry = ModelRegistry(api / 'config' / 'models')
    monkeypatch.setattr(jobs, 'get_registry', lambda: registry)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "jobs.sqlite"}')
    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def session():
        async with factory() as db:
            yield db
    app = FastAPI()
    app.dependency_overrides[jobs.get_session] = session
    app.dependency_overrides[jobs.get_experiment_session] = session
    app.include_router(models.router, prefix='/api/models')
    app.include_router(jobs.router, prefix='/api/jobs')
    try:
        with TestClient(app) as transport:
            class InProcessOpener:
                def open(self, request, timeout):
                    response = transport.request(request.method, request.full_url,
                        headers=dict(request.header_items()), content=request.data)
                    if response.is_error:
                        raise HTTPError(request.full_url, response.status_code, '', response.headers, io.BytesIO(response.content))
                    return io.BytesIO(response.content)
            client = AlignmentClient('http://testserver')
            client._opener = InProcessOpener()
            from bms_alignment import cli, http
            monkeypatch.setattr(http, 'build_opener', lambda *_: InProcessOpener())
            monkeypatch.setattr('sys.argv', ['bms-alignment', 'api', '--url', 'http://testserver', 'model', 'family_msa_protein'])
            assert cli.main() == 0
            info = client.model('family_msa_protein')
            assert info.modes[0].parameter_schema == AlignmentRequest.model_json_schema()
            request = AlignmentRequest.model_validate({'roster': {'molecule': 'protein', 'rows': [
                {'row_id': 'a', 'header': 'a', 'sequence': 'ACDE'},
                {'row_id': 'b', 'header': 'b', 'sequence': 'ACDF'}]},
                'settings': {'engine': 'famsa', 'keep_duplicates': False, 'medoid_seeds': None}})
            submission = JobSubmission(name='scratch sdk', model_id='family_msa_protein', params=request)
            preview = client.preview(submission)
            assert preview.request['params']['settings']['keep_duplicates'] is False
            assert preview.request['params']['settings']['medoid_seeds'] is None
            created = client.submit(submission)
            reopened = client.job(created.id)
            assert reopened.id == created.id and reopened.model_id == 'family_msa_protein'
            assert reopened.status == 'queued'
            with pytest.raises(BmsHttpError) as error:
                client.job('missing-sdk-job')
            assert error.value.status == 404
    finally:
        asyncio.run(engine.dispose())
