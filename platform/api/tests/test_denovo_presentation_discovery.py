"""Actual generation-settings serializer retains native presentation metadata."""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from routers import models
from scripts.lib.disco_contract import native_profile_reference
from scripts.lib.laproteina_contract import native_profile_defaults
from scripts.lib.rfd3_scientific_settings import SAMPLER_FIELDS


@pytest.mark.asyncio
async def test_generation_discovery_preserves_native_help_and_qualified_references():
    app = FastAPI()
    app.include_router(models.router, prefix='/models')
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        documents = {}
        for generator in ('rfd3', 'laproteina', 'disco'):
            response = await client.get('/models/protein_modification_experimental/generation-settings', params={'mode': 'de_novo_design', 'generator': generator})
            assert response.status_code == 200, response.text
            documents[generator] = response.json()
    sampler = documents['rfd3']['execution']['inference_sampler']['properties']
    assert sampler == SAMPLER_FIELDS
    for key in ('gamma_0', 's_trans', 'cfg_t_max'):
        assert sampler[key]['title'] and sampler[key]['description'] and sampler[key]['x-help-source']
    disco = documents['disco']
    assert disco['native_profile_reference'] == native_profile_reference()
    assert disco['parameter_schema']['$defs']['SequenceSampling']['properties']['planner_name']['x-null-label'] == 'No sequence planner (null)'
    la = documents['laproteina']
    assert la['native_profile_defaults'] == native_profile_defaults()
    assert la['parameter_schema']['properties']['bb_ca_gt_clamp_val']['x-null-label'] == 'No upper clamp (null)'
    # Display-only source references must not silently become request defaults.
    assert all('default' not in leaf for leaf in la['parameter_schema']['properties'].values())
    assert all('default' not in leaf for leaf in disco['parameter_schema']['properties'].values())
