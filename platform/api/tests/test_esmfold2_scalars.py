import json
import pytest

@pytest.fixture(autouse=True)
def isolated_scientific_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'scientific_artifacts'))

from sqlalchemy import select
from database import Design
from tests.test_core_protein_candidates import artifacts, setup, job
from services.result_ingester import ingest_esmfold2_results

def test_runner_serializes_malformed_and_missing_scalars_without_nan():
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    import numpy as np
    path = Path(__file__).resolve().parents[3] / 'scripts/run_esmfold2_inference.py'
    spec = importlib.util.spec_from_file_location('esm_scalar_runner', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    helper = getattr(runner, 'native_scalar_metrics', None)
    assert callable(helper), 'missing typed native scalar adapter'
    for bad in (True, '0.8', float('nan'), float('inf'), 1.1, np.array([]), np.array([0.5, 2])):
        result = helper(SimpleNamespace(plddt=bad, ptm=bad, iptm=None))
        assert result['scalar_states']['plddt_mean'] == 'invalid'
        assert result['scalar_states']['ptm'] == 'invalid'
        assert result['scalar_states']['iptm'] == 'unavailable'
        json.dumps(result, allow_nan=False)
    result = helper(SimpleNamespace(plddt=np.array([0.2, 0.8]), ptm=np.array(0.7), iptm=0))
    assert result['plddt_mean'] == 0.5 and result['iptm'] == 0
    assert result['scalar_dialect'] == DIALECT


DIALECT = {'name': 'biohub_esmfold2_token_scalar_v1',
    'esm_commit': 'c94ed8d763bbd7088b296949e5b401e8ea12073a',
    'transformers_commit': '3a8956fb4d4ea16b0ec8e71deef2c2909b6a5cbf'}

@pytest.mark.asyncio
@pytest.mark.parametrize('value,state,display', [(0.005,'ok',0.5),(0,'ok',0),(1,'ok',100),
    (True,'invalid',None),('0.8','invalid',None),(1.1,'invalid',None),(None,'unavailable',None)])
async def test_native_scalars_persist_without_magnitude_inference(tmp_path, value, state, display):
    root = artifacts(tmp_path, ('a',))
    path = root / 'a.metrics.json'
    payload = json.loads(path.read_text())
    payload.update(scalar_dialect=DIALECT, plddt_mean=value, ptm=0.7, iptm=0)
    path.write_text(json.dumps(payload))
    factory, engine = await setup(tmp_path)
    try:
        async with factory() as session:
            current = job(tmp_path)
            session.add(current)
            await session.commit()
            await ingest_esmfold2_results('job', tmp_path, session, current)
            row = (await session.execute(select(Design))).scalar_one()
            block = row.confidence_metrics.get('core_protein_scientific')
            assert block is not None, 'missing canonical scalar materialization'
            metrics = {m['metric_key']:m for m in block['metrics']}
            assert metrics['plddt']['state'] == state
            assert metrics['plddt']['unit'] == 'fraction'
            assert metrics['plddt']['scope'] == 'model_token_mean'
            assert row.plddt_overall == display
            assert metrics['iptm']['value'] == 0
            assert metrics['ptm']['unit'] == metrics['iptm']['unit'] == 'dimensionless'
            from services.esmfold2_scientific_consumer import verified_esmfold2_design
            selected = await verified_esmfold2_design(row, session)
            assert selected['block']['metrics'] == block['metrics']
            row.confidence_metrics = {**row.confidence_metrics, 'core_protein_scientific': {**block, 'metrics': []}}
            await session.commit()
            with pytest.raises((RuntimeError, ValueError)):
                await verified_esmfold2_design(row, session)
    finally:
        await engine.dispose()
