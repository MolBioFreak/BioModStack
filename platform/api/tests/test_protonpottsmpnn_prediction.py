"""Sequence handoff tests, not scientific inference or affinity validation."""
from copy import deepcopy
import sys
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from experiment_database import get_experiment_session
from routers import jobs, protonpottsmpnn_prediction as route
from schemas import JobCreate
from services.protonpottsmpnn_prediction import PredictionSelection, prediction_requests, source_components

COMPONENTS = [{'id': 'A', 'type': 'protein', 'sequence': 'AA'},
              {'id': 'B', 'type': 'protein', 'sequence': 'GG'}]
RESULT = {'contract': 'protonpottsmpnn_design.v1', 'designs': [
    {'design_id': 'criteria0-s0', 'criteria_index': 0, 'native_design_id': 'native-s0',
     'native': {'binder_chain': 'A', 'canonical_sequence': 'HD',
                'extended_tokens': ['HIS-P', 'ASP-D'], 'selective_energy': None}},
    {'design_id': 'criteria1-s0', 'criteria_index': 1, 'native_design_id': 'native-s0',
     'native': {'binder_chain': 'A', 'canonical_sequence': 'DE',
                'extended_tokens': ['ASP-D', 'GLU-P'], 'selective_energy': 1234.0}},
]}


def source_job():
    return Job(id='ph', name='pH redesign', model_id='protonpottsmpnn', mode='redesign',
               status='completed', execution_target_id='old-worker',
               lineage_root_job_id='root', params={'iteration_source_design_ids': ['original-design']})


def request(*, model_id='boltz2', **kwargs):
    params = ({'boltz_use_msa': False} if model_id == 'boltz2' else {'protenix_use_msa': False})
    return PredictionSelection(design_ids=['criteria1-s0', 'criteria0-s0'], model_id=model_id,
                               params={**params, 'msa_cache_only': True}, **kwargs)


@pytest.mark.parametrize('model_id', ['boltz2', 'protenix'])
def test_prediction_uses_exact_redesigned_sequences_fixed_target_and_requested_placement(model_id):
    source = source_job()
    root = Job(id='root', name='generator', model_id='bindcraft2', mode='design', params={})
    original = deepcopy(RESULT)
    children = prediction_requests(source, root, RESULT, COMPONENTS, request(model_id=model_id, execution_target_id=None))
    assert [child.params['sequence_name'] for child in children] == ['criteria1-s0', 'criteria0-s0']
    assert [child.params['complex_components'][0]['sequence'] for child in children] == ['DE', 'HD']
    for child in children:
        assert child.params['complex_components'][1] == COMPONENTS[1]
        assert child.execution_target_id is None
        assert child.parent_job_id is None
        assert child.params['source_stage_job_id'] == 'ph'
        assert child.params['lineage_root_job_id'] == 'root'
        assert child.params['iteration_source_design_ids'] == ['original-design']
        assert 'target_pdb' not in child.params
        assert 'input_pdb' not in child.params
        replay = jobs.normalize_job_request(JobCreate.model_validate(child.model_dump(mode='json')))
        assert replay.model_dump(mode='json') == child.model_dump(mode='json')
    assert RESULT == original
    assert COMPONENTS[0]['sequence'] == 'AA'


@pytest.mark.parametrize('ids', [['missing'], ['criteria0-s0', 'criteria0-s0']])
def test_selection_is_native_id_not_rank_sequence_or_filename(ids):
    with pytest.raises(ValueError):
        prediction_requests(source_job(), Job(id='root', params={}), RESULT, COMPONENTS,
                            PredictionSelection(design_ids=ids, model_id='boltz2'))


def test_operator_settings_cannot_replace_selected_sequence_source():
    with pytest.raises(ValueError, match='bound from the selected redesign'):
        prediction_requests(source_job(), Job(id='root', params={}), RESULT, COMPONENTS,
                            PredictionSelection(design_ids=['criteria0-s0'], model_id='boltz2',
                                                params={'sequence': 'REPLACED'}))


def pdb():
    lines = []
    serial = 0
    for chain, name in [('A', 'ALA'), ('B', 'GLY')]:
        for residue in (1, 2):
            for atom, element in [('N', 'N'), ('CA', 'C'), ('C', 'C'), ('O', 'O')]:
                serial += 1
                lines.append(f'ATOM  {serial:5d} {atom:^4s} {name:3s} {chain}{residue:4d}    '
                             f'{float(serial):8.3f}{2.0:8.3f}{3.0:8.3f}  1.00 80.00          {element:>2s}  ')
        lines.append('TER')
    return '\n'.join(lines) + '\nEND\n'


def test_retained_structure_uses_existing_sequence_source_parser(tmp_path):
    path = tmp_path / 'retained.pdb'
    path.write_text(pdb())
    assert source_components(path) == COMPONENTS


@pytest_asyncio.fixture
async def selected(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'get_inputs_dir', lambda: tmp_path / 'inputs')
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path / 'results')
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "prediction.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = tmp_path / 'retained.pdb'
    path.write_text(pdb())
    async def read_result(session, job_id):
        assert job_id == 'ph'
        return deepcopy(RESULT)
    # Publication-reader seam is explicitly inert; create/normalize/persist stay real.
    monkeypatch.setitem(sys.modules, 'services.protonpottsmpnn_design',
                        SimpleNamespace(read_result=read_result, prepared_source_path=lambda source: path))
    async with sessions() as session:
        source = source_job()
        root = Job(id='root', name='generic generator', model_id='rfdiffusion3', mode='design',
                   status='completed', params={})
        session.add_all([source, root])
        await session.commit()
        app = FastAPI()
        app.include_router(route.router, prefix='/api/jobs')
        async def dependency():
            yield session
        async def experiments():
            yield None  # No Project destination; existing standalone submission owner.
        app.dependency_overrides[get_session] = dependency
        app.dependency_overrides[get_experiment_session] = experiments
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
            yield client, session, source, root
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id', ['boltz2', 'protenix'])
async def test_http_persists_predictions_without_fabricated_designs_or_metric_cutoffs(selected, model_id):
    client, session, _, _ = selected
    response = await client.post('/api/jobs/ph/protonpottsmpnn/predict', json=request(model_id=model_id).model_dump(mode='json'))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['design_ids'] == ['criteria1-s0', 'criteria0-s0']
    assert len(body['launched_jobs']) == 2
    session.expire_all()
    for child_response, identity in zip(body['launched_jobs'], body['design_ids']):
        child = await session.get(Job, child_response['id'])
        assert child.model_id == model_id
        assert child.execution_target_id is None
        assert child.lineage_root_job_id == 'root'
        assert child.params['sequence_name'] == identity
        assert child.params['complex_components'][1] == COMPONENTS[1]
    assert list(await session.scalars(select(Design))) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id', ['boltz2', 'protenix'])
async def test_http_remote_review_retains_native_sequence_identity_and_new_worker(selected, model_id):
    client, session, _, _ = selected
    response = await client.post('/api/jobs/ph/protonpottsmpnn/predict',
                                 json=request(model_id=model_id, execution_target_id='new-worker').model_dump(mode='json'))
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'remote_prepared_job_review_required'
    assert len(detail['job_requests']) == 2
    for retained, identity in zip(detail['job_requests'], ['criteria1-s0', 'criteria0-s0']):
        assert retained['execution_target_id'] == 'new-worker'
        assert retained['params']['sequence_name'] == identity
        assert retained['params']['complex_components'][1] == COMPONENTS[1]
    assert len(list(await session.scalars(select(Job)))) == 2
