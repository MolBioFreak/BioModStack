from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import analysis_registry as registry
from services import analysis_subprocess as worker


@pytest.mark.asyncio
async def test_job_authority_is_exact_integer_not_design_claim():
    design = SimpleNamespace(job_id='job', provenance={'core_protein_scientific_contract': 1})
    for marker, expected in [(None, None), (True, 'invalid'), ('1', 'invalid'), (1, 1), (2, 'invalid')]:
        session = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(provenance={} if marker is None else {'core_protein_scientific_contract': marker})))
        resolver = getattr(registry, 'scientific_contract_revision', None)
        assert callable(resolver), 'Job-owned revision resolver is missing'
        if expected == 'invalid':
            with pytest.raises(ValueError):
                await resolver(design, session)
        else:
            assert await resolver(design, session) == expected


@pytest.mark.asyncio
async def test_revision_cache_separates_legacy_without_changing_legacy_signature(monkeypatch):
    design = SimpleNamespace(job_id='job', id='candidate')
    definition = registry.AnalysisDefinition('pae_matrix', 'design', '1', 'cpu', lambda p: p, lambda *a: 'legacy')
    resolver = AsyncMock(return_value=None)
    monkeypatch.setattr(registry, 'scientific_contract_revision', resolver, raising=False)
    assert await registry.build_analysis_input_signature(definition, design, {}, None) == 'legacy'
    resolver.return_value = 1
    assert await registry.build_analysis_input_signature(definition, design, {}, None) != 'legacy'


def test_marked_pae_without_producer_map_never_calls_legacy_loader(monkeypatch):
    design = SimpleNamespace(id='candidate', name='Candidate')
    monkeypatch.setattr(worker, 'load_aligned_error_artifact', lambda **k: pytest.fail('legacy loader called'))
    import inspect
    assert 'contract_revision' in inspect.signature(worker._compute_pae_matrix).parameters
    result, summary, inline = worker._compute_pae_matrix(design, {}, contract_revision=1)
    assert result['status'] == 'unavailable'
    assert result['reason'] == 'missing_producer_native_axis_ledger'
    assert result['pae_matrix'] is None
    assert result['row_axis'] is None
    assert result['column_axis'] is None
