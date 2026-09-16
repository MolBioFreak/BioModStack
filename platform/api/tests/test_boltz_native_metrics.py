"""Synthetic pinned writer fixtures; real verifier, publication, SQLite and API."""
import json
from pathlib import Path
import pytest
from sqlalchemy import select
from database import Design
from services.result_ingester import ingest_job_results
from test_boltz_scientific_persistence import publication, job, setup

@pytest.mark.asyncio
@pytest.mark.parametrize('metric,endpoint', [('residue_plddt','get_residue_metrics'),('chain_metrics','get_chain_metrics')])
async def test_native_metrics_reload(tmp_path, metric, endpoint):
    publication(tmp_path)
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            session.add(job(tmp_path)); await session.commit()
            await ingest_job_results('job', str(tmp_path), session)
        async with factory() as session:
            from routers import designs
            row = (await session.execute(select(Design))).scalar_one()
            result = await getattr(designs, endpoint)(row.id, session)
            wire = json.loads(result.model_dump_json())
            assert wire['status'] == 'ok'
            assert wire['document']['candidateId'] == row.id
            assert wire['producer_binding']['candidate_id'] != row.id
            assert [r['chain_id'] for r in wire['axis']['residues']] == ['T','T','T','H','H','L']
            if metric == 'residue_plddt':
                assert wire['units'] == 'fraction'
                assert len(wire['values']) == 6
                assert all(type(v) is float and 0 <= v <= 1 for v in wire['values'])
            else:
                assert [c['chain_id'] for c in wire['chain_index_map']] == ['T','H','L']
                assert set(wire['chains_ptm']) == {str(c['native_asym_id']) for c in wire['chain_index_map']}
                assert wire['role_assignment'] is None
                assert wire['role_reason'] == 'missing_role_assignment'
            # Compact persisted rows are not authority and cannot override bytes.
            Path(row.confidence_metrics['core_protein_scientific']['artifacts']['plddt']['path']).write_bytes(b'changed')
            rejected = await getattr(designs, endpoint)(row.id, session)
            assert rejected.status == 'unavailable'
    finally:
        await engine.dispose()
