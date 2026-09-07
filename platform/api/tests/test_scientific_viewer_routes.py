from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from services.scientific_viewer_contract import ScientificViewerMetric
from routers import designs


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['get_residue_metrics', 'get_chain_metrics', 'get_pae_data'])
async def test_marked_endpoints_do_not_serve_positional_history(monkeypatch, endpoint):
    design = SimpleNamespace(id='candidate', name='Candidate', job_id='job', residue_plddt=[99], pdb_path='pdb')
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: design)),
                              scalar=AsyncMock(return_value=SimpleNamespace(provenance={'core_protein_scientific_contract': 1})))
    monkeypatch.setattr(designs, '_get_cached_design_analysis_payload', AsyncMock(side_effect=AssertionError('legacy cache read')))
    response = await getattr(designs, endpoint)(design_id='candidate', session=session)
    assert isinstance(response, ScientificViewerMetric)
    import json
    payload = json.loads(response.model_dump_json())
    assert payload['status'] == 'unavailable'
    assert payload['reason'].startswith('missing_producer_')
    assert payload['pae_matrix'] is None


@pytest.mark.asyncio
async def test_unmarked_reads_keep_legacy_shapes_without_rewriting_history(monkeypatch):
    design = SimpleNamespace(id='candidate', name='Candidate', job_id='job', residue_plddt=[0,99], pdb_path='pdb')
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: design)),
                              scalar=AsyncMock(return_value=SimpleNamespace(provenance={})))
    residue = await designs.get_residue_metrics('candidate', session)
    assert residue.model_dump() == {'design_id':'candidate','design_name':'Candidate','plddt':[0,99],'residue_numbers':[1,2],'length':2}
    before = dict(vars(design))
    payload = {'design_id':'candidate','design_name':'Candidate','pae_matrix':[[0]],'size':1}
    monkeypatch.setattr(designs, '_get_cached_design_analysis_payload', AsyncMock(return_value=payload))
    pae = await designs.get_pae_data('candidate', 200, session)
    assert pae.model_dump() == payload
    assert vars(design) == before


@pytest.mark.asyncio
async def test_design_marker_projection_uses_job_not_design_claim(monkeypatch):
    design = SimpleNamespace(id='candidate', job_id='job', core_protein_scientific_contract=1)
    job = SimpleNamespace(id='job', provenance={})

    def execute(statement):
        entity = statement.column_descriptions[0]['entity']
        if entity is designs.Design:
            return SimpleNamespace(scalar_one_or_none=lambda: design)
        assert entity is designs.Job
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [job]))

    session = SimpleNamespace(execute=AsyncMock(side_effect=execute),
                              scalar=AsyncMock(return_value=job))

    def project(candidate, **kwargs):
        assert candidate is design
        assert kwargs['job'] is job
        assert candidate.job_id == job.id
        return SimpleNamespace(core_protein_scientific_contract=1)

    monkeypatch.setattr(designs, '_design_to_response', project)
    response = await designs.get_design('candidate', session)
    assert response.core_protein_scientific_contract is None
    job.provenance = {'core_protein_scientific_contract': 1}
    response = await designs.get_design('candidate', session)
    assert response.core_protein_scientific_contract == 1
