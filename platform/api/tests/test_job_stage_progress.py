"""Presentation/HTTP transport fixtures, not native scientific execution."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs
from schemas import JobResponse
from services.job_stage_progress import project_execution_stages


MODELS = [
    ('ppiflow', 'binder_denovo', 'RunPPIFlowGeneration'),
    ('boltzgen', 'binder_denovo', 'RunBoltzGen'),
    ('bindcraft2', 'binder_denovo', 'RunBindCraft2'),
    ('rfd3', 'monomer_denovo', 'RunRFD3'),
    ('rfantibody', 'antibody_denovo', 'RunRFAntibody'),
    ('antibody', 'antibody_pipeline', 'rfantibody'),
    ('proteinmpnn', 'sequence_design', 'proteinmpnn'),
    ('fampnn', 'sequence_design', 'fampnn'),
    ('caliby', 'sequence_design', 'caliby'),
    ('protenix', 'predict', 'protenix'),
    ('boltz2', 'predict', 'boltz2'),
    ('esmfold', 'predict', 'esmfold'),
    ('nanopore', 'construct_screening', 'fastq_align'),
    ('molecular_dynamics', 'simulate', 'production'),
    ('future_model', 'future_mode', 'FutureNativeProcess'),
]


def fixture_job(model_id='future_model', mode='future_mode', **overrides):
    return dict(model_id=model_id, mode=mode, status='completed',
                completed_stages=None, current_stage=None, provenance=None,
                params={}, **overrides)


def plan(*names, dynamic=()):
    return {'execution_plan_approval': {'plan': {'metadata': {
        'static_components': [{'component_key': name} for name in names],
        'dynamic_templates': [{'component_key': name} for name in dynamic],
    }}}}


@pytest.mark.parametrize('model,mode,stage', MODELS)
def test_historical_missing_stage_history_is_neutral(model, mode, stage):
    job = fixture_job(model, mode)
    job['params'] = {'run_structure_validation': True, 'seq_design_fampnn': True}
    assert project_execution_stages(job) == [
        {'id': model, 'label': model, 'state': 'unknown', 'source': 'model'}]
    job['completed_stages'] = [stage, stage]
    assert project_execution_stages(job) == [
        {'id': stage, 'label': stage, 'state': 'completed', 'source': 'recorded'}]


@pytest.mark.parametrize('status', ['pending', 'queued', 'running', 'completed', 'failed', 'cancelled'])
@pytest.mark.parametrize('model,mode,stage', MODELS)
def test_retained_plan_is_not_a_completion_receipt(status, model, mode, stage):
    job = fixture_job(model, mode)
    job.update(status=status, provenance=plan(stage, 'unobserved', dynamic=['optional_template']),
               current_stage=stage)
    before = deepcopy(job)
    actual = project_execution_stages(job)
    assert [row['id'] for row in actual] == [stage, 'unobserved']
    assert actual[0]['state'] == (status if status in {'running', 'failed', 'cancelled'} else 'unknown')
    assert actual[1]['state'] == ('unknown' if status in {'completed', 'failed', 'cancelled'} else 'planned')
    assert [row['source'] for row in actual] == ['recorded', 'plan']
    assert job == before


@pytest.mark.parametrize('message', ['Complete', 'Failed', 'Queued', 'Waiting for GPU',
                                     'Result Ingestion Failed', 'Native validation pending'])
def test_lifecycle_messages_are_not_scientific_stages(message):
    job = fixture_job()
    job['current_stage'] = message
    assert project_execution_stages(job)[0]['source'] == 'model'


def test_review_is_not_failure_and_explicit_terminal_states_are_retained():
    job = fixture_job()
    job.update(status='failed', current_stage='review', awaiting_stage='review', awaiting_input=True,
               completed_stages=['generated'], provenance=plan('generated', 'review', 'skipped'))
    job['provenance']['stage_terminal_states'] = {
        'failed_child_stage': {'status': 'failed'}, 'skipped': {'status': 'skipped'}}
    actual = {row['id']: row['state'] for row in project_execution_stages(job)}
    assert actual == {'generated': 'completed', 'review': 'awaiting_input',
                      'skipped': 'unknown', 'failed_child_stage': 'failed'}
    # Explicit child-job metadata/round state cannot mark the parent complete.
    job['provenance']['binder_round'] = {'children': [{'status': 'completed', 'stage': 'prediction'}]}
    assert 'prediction' not in {row['id'] for row in project_execution_stages(job)}


def test_recorded_completion_supersedes_plan_source_without_completing_other_stages():
    job = fixture_job()
    job.update(provenance=plan('actual', 'unobserved'), completed_stages=['actual'])
    assert project_execution_stages(job) == [
        {'id': 'actual', 'label': 'actual', 'state': 'completed', 'source': 'recorded'},
        {'id': 'unobserved', 'label': 'unobserved', 'state': 'unknown', 'source': 'plan'},
    ]


def test_remote_resource_inventory_without_approval_and_no_io(monkeypatch):
    job = fixture_job('ppiflow', 'binder_denovo')
    job.update(current_stage='Complete', provenance={'remote_execution_assignment': {
        'resources': {'components': [{'component_key': 'RunPPIFlowGeneration'}]}}})
    def forbidden(*args, **kwargs):
        raise AssertionError('projection must not read files or compile plans')
    monkeypatch.setattr('builtins.open', forbidden)
    monkeypatch.setattr('pathlib.Path.open', forbidden)
    monkeypatch.setattr('pathlib.Path.exists', forbidden)
    monkeypatch.setattr('services.nextflow.build_selected_execution_plan', forbidden)
    assert project_execution_stages(SimpleNamespace(**job)) == [{
        'id': 'RunPPIFlowGeneration', 'label': 'RunPPIFlowGeneration',
        'source': 'plan', 'state': 'unknown'}]


def test_disabled_antibody_optional_steps_are_not_recreated():
    job = fixture_job('rfantibody', 'antibody_denovo')
    job.update(status='queued', provenance=plan('RunRFAntibody'),
               params={'seq_design_fampnn': False, 'seq_design_antifold': False,
                       'seq_design_proteinmpnn': False, 'run_structure_validation': False})
    assert [row['id'] for row in project_execution_stages(job)] == ['RunRFAntibody']


def test_job_response_orm_and_mapping_share_projection():
    job = Job(id='schema', name='schema', model_id='future', mode='future', status='completed',
              params={}, completed_stages=['actual'], current_stage='Complete', provenance=plan('planned'))
    expected = project_execution_stages(job)
    assert JobResponse.model_validate(job).model_dump()['execution_stages'] == expected
    assert JobResponse(id=job.id, name=job.name, model_id=job.model_id, mode=job.mode,
                       status=job.status, params=job.params, provenance=job.provenance,
                       completed_stages=job.completed_stages).model_dump()['execution_stages'] == expected


def test_antibody_output_discovery_does_not_mutate_completion(tmp_path, monkeypatch):
    job = Job(model_id='rfantibody', mode='antibody_refinement_pipeline',
              status='completed', completed_stages=['recorded'], stage_outputs={})
    # The existing output owner mutates its completed argument; presentation must
    # pass a copy and retain outputs without turning existence into success.
    def infer(_job, completed, outputs):
        completed.append('file_only')
        outputs['file_only'] = [str(tmp_path / 'candidate.pdb')]
        return completed, outputs
    monkeypatch.setattr(jobs, 'infer_antibody_stage_state', infer)
    completed, outputs = jobs._resolve_stage_state_for_response(job)
    assert completed == job.completed_stages == ['recorded']
    assert 'file_only' in outputs
    assert [row['id'] for row in project_execution_stages(job)] == ['recorded']


@pytest.mark.parametrize('provenance', [None, {}, {'execution_plan_approval': None},
                                       {'execution_plan_approval': {'plan': {'metadata': {'static_components': [None, {}, 4]}}}}])
def test_partial_historical_metadata_is_not_a_response_error(provenance):
    job = fixture_job()
    job['provenance'] = provenance
    assert project_execution_stages(job)[0]['source'] == 'model'


@pytest.mark.asyncio
async def test_http_summary_full_detail_stages_agree_without_n_plus_one(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stages.db'}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    expected = {}
    async with Session() as session:
        for index, (model, mode, stage) in enumerate(MODELS):
            for historical in (False, True):
                job = Job(id=f'{index}-{historical}', name=f'{model}-{historical}',
                          model_id=model, mode=mode, status='completed',
                          params={'large_unused': 'x' * 10000}, current_stage='Complete',
                          completed_stages=None if historical else [stage],
                          provenance=None if historical else plan(stage, 'not_observed'))
                expected[job.id] = project_execution_stages(job)
                session.add(job)
        remote = Job(id='remote', name='remote', model_id='ppiflow', mode='binder_denovo',
                     status='completed', params={}, current_stage='Complete', completed_stages=[],
                     provenance={'remote_execution_assignment': {'resources': {
                         'components': [{'component_key': 'RunPPIFlowGeneration'}]}},
                         'stage_terminal_states': {'separate_failure': {'status': 'failed'}}})
        session.add(remote)
        expected[remote.id] = project_execution_stages(remote)
        for status in ('running', 'awaiting_input', 'failed', 'cancelled'):
            active = Job(id=status, name=status, model_id='future', mode='future',
                         status=status, params={}, current_stage='native_stage',
                         completed_stages=['prior'], provenance=plan('prior', 'native_stage', 'later'),
                         awaiting_input=status == 'awaiting_input',
                         awaiting_stage='native_stage' if status == 'awaiting_input' else None)
            session.add(active)
            expected[active.id] = project_execution_stages(active)
        # A completed child cannot mark its parent's unobserved prediction done.
        session.add(Job(id='separate-child', name='child', model_id='protenix', mode='predict',
                        status='completed', params={}, parent_job_id='remote',
                        completed_stages=['prediction']))
        await session.commit()

    async def get_session():
        async with Session() as session:
            yield session

    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    app.dependency_overrides[jobs.get_session] = get_session
    selects = []

    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('SELECT'):
            selects.append(statement)

    with TestClient(app) as client:
        summary = client.get('/api/jobs?summary=true&limit=100')
        assert summary.status_code == 200, summary.text
        query_count = len(selects)
        assert summary.json()['total'] == len(expected)
        for row in summary.json()['jobs']:
            assert row['execution_stages'] == expected[row['id']]
            assert row['params'] == {} and row['provenance'] is None
        selects.clear()
        assert client.get('/api/jobs?summary=true&limit=1').status_code == 200
        assert len(selects) == query_count  # all auxiliary counts remain batched
        full = client.get('/api/jobs?limit=100')
        assert full.status_code == 200, full.text
        for row in full.json()['jobs']:
            assert row['execution_stages'] == expected[row['id']]
            detail = client.get(f"/api/jobs/{row['id']}")
            stages = client.get(f"/api/jobs/{row['id']}/stages")
            assert detail.status_code == stages.status_code == 200
            assert detail.json()['execution_stages'] == stages.json()['execution_stages'] == expected[row['id']]
            assert stages.json()['all_stages'] == [s['id'] for s in expected[row['id']]]
            assert stages.json()['completed_stages'] == (row['completed_stages'] or [])
            assert stages.json()['can_resume'] is (row['status'] in {'failed', 'cancelled', 'awaiting_input'})
        child = client.get('/api/jobs/separate-child').json()
        assert child['execution_stages'] == [
            {'id': 'prediction', 'label': 'prediction', 'state': 'completed', 'source': 'recorded'}]
    async with Session() as session:
        stored = await session.get(Job, 'remote')
        assert stored.completed_stages == [] and stored.current_stage == 'Complete'
    await engine.dispose()
