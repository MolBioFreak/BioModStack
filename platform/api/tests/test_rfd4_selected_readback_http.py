"""Real discovery HTTP -> selected image CPU Hydra -> readback."""
import os
from pathlib import Path
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from routers import models


@pytest.mark.asyncio
async def test_native_readback_endpoint_preserves_sparse_request(monkeypatch):
    image = os.environ.get('BMS_TEST_RFD4_IMAGE')
    if not image:
        pytest.skip('Selected image required')
    from services.remote_execution import images
    monkeypatch.setattr(images, 'resolve_image', lambda *args: Path(image))
    app = FastAPI()
    app.include_router(models.router, prefix='/models')
    request = {'input': {'kind':'unconditional','name':'http','chains':[{'chain_id':'A','min_length':40,'max_length':40}]},
               'groups': {'generation':'inverse_folding','recycle_strategy':'thinking_low'},
               'settings': {'seed':0, 'generation': {'args': {'nsteps':31}}}}
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        response = await client.post('/models/rfd4_proteina/generation-settings/preview?mode=generate', json=request)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['request']['settings'] == request['settings']
        assert result['effective_config']['generation']['args']['nsteps'] == 31
        assert result['effective_config']['generation']['args']['inverse_folding'] is True
        assert result['effective_config']['generation']['n_recycle_schedule']
        assert result['effective_config']['seed'] == 0
        assert result['runtime_image'] == image
        response = await client.post('/models/rfd4_proteina/generation-settings/preview?mode=evaluate',
                                    json={'input': {'kind':'annotated','slots':['exact']}})
        assert response.status_code == 200, response.text
        assert 'settings' not in response.json()['request']
        assert response.json()['request']['input']['slots'] == ['exact']
