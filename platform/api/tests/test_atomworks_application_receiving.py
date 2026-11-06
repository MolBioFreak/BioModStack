"""Actual application registration, without lifespan, stores, workers or sockets."""
import pytest
from httpx import ASGITransport, AsyncClient


def test_real_application_registers_closed_graph_contract():
    from main import app
    schema = app.openapi()
    paths = schema['paths']
    graph = paths['/api/molecular-construction/graph']['post']
    reference = graph['requestBody']['content']['application/json']['schema']['$ref']
    # Pydantic may publish distinct -Input/-Output documents. Resolve the actual
    # request reference rather than assuming its component spelling.
    contract = schema['components']['schemas'][reference.rsplit('/', 1)[-1]]
    assert contract['additionalProperties'] is False
    assert contract['properties']['geometry']['const'] == 'unpositioned'
    assert contract['properties']['graph_provider']['const'] == 'rdkit'
    assert {'strands', 'geometry', 'phosphate_state', 'graph_provider'} <= set(contract['required'])
    assert '/api/atomworks-studio/{path}' in paths
    assert 'post' in paths['/api/atomworks-studio/{path}']


@pytest.mark.asyncio
async def test_real_application_explicit_complement_and_private_studio_authority():
    from main import app
    source = {'id': 'top', 'polymer': 'DNA', 'copy_ids': ['one'],
              'residues': [{'id': 'a', 'base': 'A'}, {'id': 'c', 'base': 'C'}],
              'five_prime': 'hydroxyl', 'three_prime': 'phosphate_neutral', 'nicks': []}
    body = {'source': source, 'new_strand_id': 'bottom', 'new_copy_ids': ['independent'],
            'new_residue_ids': ['g', 't'], 'five_prime': 'phosphate_neutral', 'three_prime': 'hydroxyl'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
        response = await client.post('/api/molecular-construction/complement', json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {'id': 'bottom', 'polymer': 'DNA', 'copy_ids': ['independent'],
                                   'residues': [{'id': 'g', 'base': 'G'}, {'id': 't', 'base': 'T'}],
                                   'five_prime': 'phosphate_neutral', 'three_prime': 'hydroxyl', 'nicks': []}
        assert body['source'] == source
        private = await client.get('/api/atomworks-studio/')
        assert private.status_code in {401, 403}, private.text
        assert private.headers['cache-control'] == 'private, no-store'
