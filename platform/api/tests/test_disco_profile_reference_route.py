"""DISCO discovery remains available through the existing HTTP route."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from routers import models
from scripts.lib.disco_contract import native_profile_reference


@pytest.mark.asyncio
async def test_disco_profile_reference_route():
    app = FastAPI()
    app.include_router(models.router, prefix='/api/models')
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        response = await client.get('/api/models/protein_modification_experimental/generation-settings', params={'mode': 'de_novo_design', 'generator': 'disco'})
    assert response.status_code == 200, response.text
    assert response.json()['native_profile_reference'] == native_profile_reference()
