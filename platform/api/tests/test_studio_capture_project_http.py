"""Replay the exact mounted Studio-capture envelope into real private owners.

Transport-frozen CIF is not a claim of actual Studio editing or model inference.
No synthesized envelope is used when the mounted capture has not been supplied.
"""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from test_annotated_structure_capture import capture_api  # noqa: F401
from test_core_protein_scientific_admission import admission  # noqa: F401
from test_project_workflow_setups import setup_store  # noqa: F401
from test_denovo_complete_project_http import test_mounted_native_project_http_lifecycle as run_project_lifecycle


@pytest.mark.asyncio
async def test_exact_mounted_studio_capture_template_save_twice_cold_read(admission, monkeypatch, record_property):
    source = os.environ.get('BMS_STUDIO_CAPTURE_WIRE')
    if not source:
        pytest.skip('Requires the actual mounted Studio receiving capture; no replacement fixture')
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from database import get_session
    from routers import user_templates
    envelope = json.loads(Path(source).read_text())['envelope']
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
            'name': 'Exact edited document', 'model_id': 'rfd4_proteina', 'mode': 'generate',
            'base_template_id': 'protein_modification_experimental', 'params': envelope})
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
    record_property('template_cold_read', 'exact mounted envelope after two real PUTs and engine disposal')


@pytest.mark.asyncio
async def test_exact_mounted_studio_capture_save_twice_cold_prepare_job(admission, setup_store, capture_api, tmp_path, monkeypatch, record_property):
    source = os.environ.get('BMS_STUDIO_CAPTURE_WIRE')
    if not source:
        pytest.skip('Requires the actual mounted Studio receiving capture; no replacement fixture')
    wire = json.loads(Path(source).read_text())
    envelope = wire['envelope']
    selected = envelope['native_job_request']['params']['native_sources']['source']
    document = envelope['editor_state']['molecular_documents']['source']
    assert selected == document['source']
    assert document['original_source']['path'] == 'inputs/original.cif'
    assert document['provenance']['revision'] == 4
    assert 'session_id' not in document['provenance']
    app, inputs = capture_api
    async with AsyncClient(transport=ASGITransport(app), base_url='http://private') as client:
        for item in wire['uploads']:
            raw = item['text'].encode('utf8')
            assert hashlib.sha256(raw).hexdigest() == item['sha256']
            response = await client.post('/api/files/upload-immutable-structure',
                data={'path': 'inputs/annotated_structures', 'sha256': item['sha256']},
                files={'file': (Path(item['path']).name, raw, 'chemical/x-mmcif')})
            assert response.status_code == 200, response.text
            assert response.json()['path'] == item['path'] == selected['path']
            response = await client.get('/api/files/download/' + quote(item['path'], safe=''))
            assert response.status_code == 200 and response.content == raw
    captures = tmp_path / 'mounted-envelopes'
    captures.mkdir()
    (captures / 'native-envelope-rfd4_proteina-generate.json').write_text(json.dumps(envelope))
    monkeypatch.setenv('BMS_DENOVO_AUTHORING_ENVELOPES', str(captures))
    # Exercise existing real POST/PUT/second-PUT/connection-disposed-GET/Prepare,
    # canonical Job insertion and launch-context binding, not copied persistence.
    await run_project_lifecycle(admission, setup_store, tmp_path, monkeypatch, record_property, 'rfd4_proteina', 'generate')
    retained = inputs / 'annotated_structures' / Path(selected['path']).name
    assert retained.read_bytes() == wire['uploads'][0]['text'].encode('utf8')
    assert hashlib.sha256(retained.read_bytes()).hexdigest() == selected['expected_sha256']
