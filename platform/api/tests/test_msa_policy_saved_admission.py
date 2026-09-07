"""Real persisted replay and admission, using the canonical disposable ORM fixture."""
from copy import deepcopy
import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response
from sqlalchemy import select
from database import Job
from routers import jobs
from schemas import JobCreate
from test_core_protein_scientific_admission import admission  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resubmit', 'resume'])
async def test_saved_local_replay_rejected_without_row_mutation(admission, tmp_path, operation):
    params = {'sequence': 'ACDEFGHIK', 'msa_provider': 'local', 'boltz_use_msa': True}
    original = Job(id='saved-local', name='saved', model_id='boltz2', mode='predict',
                   status='failed', params=params, provenance={}, output_dir=str(tmp_path / 'old'))
    admission.add(original)
    await admission.commit()
    before = deepcopy(original.params)
    req = Request({'type': 'http', 'method': 'POST', 'scheme': 'http', 'path': '/', 'headers': []})
    with pytest.raises(HTTPException) as exc:
        if operation == 'resubmit':
            await jobs.resubmit_job(original.id, req, Response(), admission)
        else:
            await jobs.resume_job(original.id, req, Response(), request=None, session=admission)
    assert exc.value.status_code == 422
    assert 'Local MSA search is disabled' in str(exc.value.detail)
    assert len(list((await admission.execute(select(Job))).scalars())) == 1
    await admission.refresh(original)
    assert original.params == before


@pytest.mark.asyncio
async def test_api_mutagenesis_is_actually_blocked_at_admission(admission):
    request = JobCreate(name='api-batch', model_id='boltz2', mode='predict', params={
        'sequence': 'ACDEFGHIK', 'msa_provider': 'colabfold_api', 'boltz_use_msa': True,
        'mutagenesis_variants': [{'name': 'one', 'sequence': 'ACDEFGHIK'}],
    })
    with pytest.raises(HTTPException) as exc:
        await jobs._create_job(request, BackgroundTasks(), admission)
    assert exc.value.status_code == 422
    assert 'mutagenesis batch' in str(exc.value.detail)
    assert not list((await admission.execute(select(Job))).scalars())


@pytest.mark.asyncio
async def test_auto_admission_persists_effective_policy_without_losing_intent(admission):
    request = JobCreate(name='explicit-no-msa', model_id='boltz2', mode='predict', params={
        'sequence': 'ACDEFGHIK', 'msa_provider': 'auto', 'boltz_use_msa': False, 'use_msa': False,
    })
    response = await jobs._create_job(request, BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    assert job.params['msa_provider'] == 'colabfold_api'
    assert job.params['boltz_use_msa'] is False
    policy = job.provenance['msa_search_policy']
    assert policy['requested']['msa_provider'] == 'auto'
    assert policy['effective']['msa_provider'] == 'colabfold_api'
    assert policy['provider_database_version'] is None
