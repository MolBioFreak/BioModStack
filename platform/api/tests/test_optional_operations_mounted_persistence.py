"""Replay actual mounted invocation envelopes through private template HTTP storage."""
import json
import os
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database import UserTemplate
from test_denovo_project_http import app_for
from schemas import SelectedRoundRequest
from services.binder_selected_round import normalize

@pytest.mark.asyncio
async def test_mounted_operation_envelopes_cold_template_roundtrip(tmp_path):
    capture = os.environ.get('BMS_OPERATIONS_EVIDENCE')
    if not capture:
        pytest.skip('Requires actual mounted operation capture; no substitute')
    rows = json.loads(Path(capture).read_text())
    assert len(rows) == int(os.environ.get('BMS_OPERATIONS_EXPECTED_ROWS', '17'))
    url = f'sqlite+aiosqlite:///{tmp_path / "templates.db"}'
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(UserTemplate.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    saved = []
    async with AsyncClient(transport=ASGITransport(app_for(sessions, sessions)), base_url='http://private') as client:
        for row in rows:
            from services.sequence_round_inputs import normalize_request
            initial = normalize_request(row['envelope']).model_dump(mode='json', exclude_unset=True)
            assert initial == row['envelope']
            params = {'invocation_draft': {'enabled': True, 'stages': row['draft']}, 'sequence_design': row['envelope']}
            response = await client.post('/api/user-templates', json={'name': row['model'] + '/' + row['mode'], 'model_id': row['model'], 'mode': row['mode'], 'params': params})
            assert response.status_code in (200, 201), response.text
            assert response.json()['params'] == params
            saved.append((response.json()['id'], params, row))
    await engine.dispose()
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncClient(transport=ASGITransport(app_for(sessions, sessions)), base_url='http://cold') as client:
        for identity, params, row in saved:
            response = await client.get('/api/user-templates/' + identity)
            assert response.status_code == 200, response.text
            assert response.json()['params'] == params
            request = SelectedRoundRequest.model_validate({**row['normalized'], 'round_plan': response.json()['params']['sequence_design']['round_plan']})
            assert normalize(request).model_dump(mode='json') == row['normalized']
    await engine.dispose()
