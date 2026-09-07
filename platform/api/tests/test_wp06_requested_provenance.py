import json
from copy import deepcopy
import pytest

@pytest.fixture(autouse=True)
def isolated_scientific_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(tmp_path / 'scientific_artifacts'))

from fastapi import BackgroundTasks
from pydantic import ValidationError
from database import Job
from schemas import JobCreate
from routers import jobs
from services import core_protein_scientific_contract as contract
from services import nextflow
from tests.test_core_protein_scientific_admission import admission

KEY = 'core_protein_requested_params'

@pytest.mark.parametrize('key', [KEY, 'openmm_requested_settings_json', 'esmf_requested_settings_json'])
def test_request_authority_cannot_be_forged(key):
    with pytest.raises(ValidationError, match='server-owned'):
        JobCreate.model_validate(dict(name='x', model_id='esmfold2', mode='predict', params={key: {}}))

@pytest.mark.asyncio
async def test_pre_registry_request_retained_in_sqlite_and_command(admission, monkeypatch):
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('esmfold2','predict')}))
    original = {'sequence':'ACDE', 'esmf_seed':0, 'esmf_msa_remove_insertions':False}
    result = await jobs._create_job(JobCreate(name='receipt', model_id='esmfold2', mode='predict', params=deepcopy(original)), BackgroundTasks(), admission)
    admission.expire_all()
    row = await admission.get(Job, result.id)
    assert row.provenance.get(KEY) == original, 'pre-registry request provenance missing'
    helper = getattr(nextflow, 'build_job_nextflow_command', None)
    assert callable(helper), 'real launches need a Job-owned command wrapper'
    cmd = helper(row, row.params, row.output_dir)
    payload = json.loads(cmd[cmd.index('--esmf_requested_settings_json') + 1])
    assert payload == original
    assert json.loads(cmd[cmd.index('--openmm_requested_settings_json') + 1]) == {}
    from fastapi import Request, Response
    row.status = 'failed'
    await admission.commit()
    result = await jobs.resubmit_job(row.id, Request({'type':'http','headers':[]}), Response(), admission)
    new = await admission.get(Job, result['new_job_id'])
    assert new.provenance.get(KEY) == original, 'resubmission lost original origin'
    resumed = await jobs.resume_job(row.id, Request({'type':'http','headers':[]}), Response(), request=None, session=admission)
    resumed_row = await admission.get(Job, resumed['new_job_id'])
    assert resumed_row.provenance.get(KEY) == original, 'resume lost original origin'
