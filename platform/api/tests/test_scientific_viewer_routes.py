import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from services.scientific_viewer_contract import ScientificViewerMetric
from routers import designs


@pytest_asyncio.fixture
async def viewer_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'viewer.db'}")

    @event.listens_for(engine.sync_engine, 'connect')
    def enable_foreign_keys(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _candidate(session, *, model_id='boltz2', marked=True):
    job = Job(id='job', name='TEST viewer job', model_id=model_id,
              mode='monomer', params={},
              provenance={'core_protein_scientific_contract': 1} if marked else {})
    session.add(job)
    await session.flush()
    design = Design(id='candidate', name='Candidate', job_id=job.id,
                    residue_plddt=[0, 99], pdb_path='pdb')
    session.add(design)
    await session.commit()
    # Flush publishes governed references; reload through the ORM's resolver,
    # as a separate read request does, rather than inspecting write-time refs.
    await session.refresh(design)
    return job, design


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id', ['boltz2', 'esmfold2'])
@pytest.mark.parametrize('endpoint', ['get_residue_metrics', 'get_chain_metrics', 'get_pae_data'])
async def test_marked_endpoints_do_not_serve_positional_history(monkeypatch, viewer_store, endpoint, model_id):
    await _candidate(viewer_store, model_id=model_id)
    cache = AsyncMock(side_effect=AssertionError('legacy cache read'))
    monkeypatch.setattr(designs, '_get_cached_design_analysis_payload', cache)
    kwargs = {'max_size': 200} if endpoint == 'get_pae_data' else {}
    response = await getattr(designs, endpoint)(design_id='candidate', session=viewer_store, **kwargs)
    assert isinstance(response, ScientificViewerMetric)
    payload = json.loads(response.model_dump_json())
    assert payload['status'] == 'unavailable'
    if model_id == 'boltz2':
        assert payload['reason'].startswith('missing_producer_')
    else:
        assert payload['reason'] == 'unsupported_model_native_spatial_metric'
    assert payload['pae_matrix'] is None
    cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmarked_reads_keep_legacy_shapes_without_rewriting_history(monkeypatch, viewer_store):
    _job, design = await _candidate(viewer_store, marked=False)
    residue = await designs.get_residue_metrics('candidate', viewer_store)
    assert residue.model_dump() == {'design_id':'candidate','design_name':'Candidate','plddt':[0,99],'residue_numbers':[1,2],'length':2}
    before = dict(vars(design))
    payload = {'design_id':'candidate','design_name':'Candidate','pae_matrix':[[0]],'size':1}
    monkeypatch.setattr(designs, '_get_cached_design_analysis_payload', AsyncMock(return_value=payload))
    pae = await designs.get_pae_data('candidate', 200, viewer_store)
    assert pae.model_dump() == payload
    assert vars(design) == before
    assert not viewer_store.dirty


@pytest.mark.asyncio
async def test_design_marker_projection_uses_job_not_design_claim(monkeypatch, viewer_store):
    job, design = await _candidate(viewer_store, marked=False)
    design.core_protein_scientific_contract = 1

    def project(candidate, **kwargs):
        assert candidate is design
        assert kwargs['job'] is job
        assert candidate.job_id == job.id
        return SimpleNamespace(core_protein_scientific_contract=1)

    monkeypatch.setattr(designs, '_design_to_response', project)
    response = await designs.get_design('candidate', viewer_store)
    assert response.core_protein_scientific_contract is None
    job.provenance = {'core_protein_scientific_contract': 1}
    await viewer_store.commit()
    response = await designs.get_design('candidate', viewer_store)
    assert response.core_protein_scientific_contract == 1
    assert response.scientific_structure_document is None
