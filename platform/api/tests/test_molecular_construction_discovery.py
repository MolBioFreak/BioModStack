"""Discovery is a projection of actual closed models, never a chemistry catalog."""
import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from services.molecular_construction_recipe import ComplementRequest, ConstructionResult, MolecularConstructionRecipe


@pytest.mark.asyncio
async def test_schema_discovery_is_exact_native_contract_without_constructing_graph():
    spec = importlib.util.spec_from_file_location("construction_discovery_router", Path(__file__).parents[1] / "routers/molecular_construction.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://inert") as client:
        response = await client.get("/api/molecular-construction/schema")
    assert response.status_code == 200
    assert response.json() == {
        "recipe": MolecularConstructionRecipe.model_json_schema(),
        "complement": ComplementRequest.model_json_schema(),
        "result": ConstructionResult.model_json_schema(),
    }
    schema = response.json()["recipe"]
    assert schema["$defs"]["CCDReplacement"]["properties"]["atom_map"]["additionalProperties"]["$ref"] == "#/$defs/CCDAtomTarget"
    assert schema["additionalProperties"] is False
