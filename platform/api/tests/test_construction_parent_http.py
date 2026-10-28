"""Real persisted owners receive the exact mounted construction companion.

Graph/recipe remain editor state, outside the unchanged scientific Job request.
No live stores, inference, substitute envelopes or new admission rules.
"""
import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from test_core_protein_scientific_admission import admission  # noqa: F401
from test_project_workflow_setups import setup_store  # noqa: F401
from test_denovo_complete_project_http import test_mounted_native_project_http_lifecycle as project_lifecycle


def mounted_envelope():
    source = os.environ.get('BMS_CONSTRUCTION_PARENT_WIRE')
    if not source:
        pytest.skip('Requires exact mounted construction parent envelope')
    envelope = json.loads(Path(source).read_text())
    companion = envelope['editor_state']['molecular_construction']
    assert companion['graph']['geometry'] == 'unpositioned'
    assert companion['graph']['coordinates'] is None
    assert companion['graph']['atoms'] and companion['graph']['bonds']
    assert companion['imported_note'] == {'zero': 0, 'flag': False, 'empty': [], 'nil': None}
    assert 'molecular_construction' not in envelope['native_job_request']['params']
    return envelope


@pytest.mark.asyncio
async def test_mounted_construction_template_twice_and_cold_read(admission):
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from database import get_session
    from routers import user_templates

    envelope = mounted_envelope()
    engine = admission.bind
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def application():
        app = FastAPI()
        app.include_router(user_templates.router, prefix='/api/user-templates')

        async def sessions():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        return app

    async with AsyncClient(transport=ASGITransport(application()), base_url='http://private') as client:
        response = await client.post('/api/user-templates', json={
            'name': 'Shared construction companion', 'model_id': 'rfd4_proteina',
            'mode': 'generate', 'base_template_id': 'protein_modification_experimental', 'params': envelope,
        })
        assert response.status_code == 201, response.text
        url = '/api/user-templates/' + response.json()['id']
        for _ in range(2):
            response = await client.put(url, json={'params': envelope})
            assert response.status_code == 200, response.text
            assert response.json()['params'] == envelope
    await engine.dispose()
    async with AsyncClient(transport=ASGITransport(application()), base_url='http://cold') as client:
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.json()['params'] == envelope


@pytest.mark.asyncio
async def test_mounted_construction_project_twice_cold_prepare(admission, setup_store, tmp_path, monkeypatch, record_property):
    envelope = mounted_envelope()
    captures = tmp_path / 'construction-envelopes'
    captures.mkdir()
    (captures / 'native-envelope-rfd4_proteina-generate.json').write_text(json.dumps(envelope))
    monkeypatch.setenv('BMS_DENOVO_AUTHORING_ENVELOPES', str(captures))
    # Existing real migrated Project POST/PUT/twice/cold GET/Prepare, exact native
    # Job insertion and launch binding; only scientific execution stays inert.
    await project_lifecycle(admission, setup_store, tmp_path, monkeypatch, record_property, 'rfd4_proteina', 'generate')
