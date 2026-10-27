"""Scratch Job insertion and replay; no worker, external service or user data."""
import pytest
from fastapi import BackgroundTasks, Request, Response
from database import Job
from schemas import JobCreate
from routers import jobs
from services.gpu_orchestrator import estimate_vram
from tests.test_core_protein_scientific_admission import admission


@pytest.mark.parametrize(('molecule', 'model'), [('protein', 'family_msa_protein'), ('dna', 'family_msa_nucleic'), ('rna', 'family_msa_nucleic')])
@pytest.mark.asyncio
async def test_create_and_resubmit_cpu_job(admission, molecule, model):
    payload = {'roster': {'molecule': molecule, 'rows': [
        {'row_id': 'r1', 'header': 'one', 'sequence': 'ACGT'},
        {'row_id': 'r2', 'header': 'two', 'sequence': 'ACGT'}]}, 'settings': {'threads': 2}}
    created = await jobs._create_job(JobCreate(name='scratch alignment', model_id=model,
        mode='family_msa', params=payload, pinned_gpu=1), BackgroundTasks(), admission)
    row = await admission.get(Job, created.id)
    assert row.status == 'queued' and row.vram_estimate_mb == 0 and row.pinned_gpu is None
    from services.family_msa.requests import prepare_request
    assert row.params['roster'] == prepare_request(payload)[1]['roster']
    await jobs.cancel_job(row.id, admission)
    await admission.refresh(row)
    assert row.status == 'cancelled'
    response = await jobs.resubmit_job(row.id, Request({'type':'http','headers':[]}), Response(), admission)
    second_response = await jobs.resubmit_job(row.id, Request({'type':'http','headers':[]}), Response(), admission)
    again = await admission.get(Job, response['new_job_id'])
    second = await admission.get(Job, second_response['new_job_id'])
    assert len({row.output_dir, again.output_dir, second.output_dir}) == 3
    assert again.id != row.id and again.status == 'queued'
    assert again.vram_estimate_mb == 0 and again.pinned_gpu is None
    assert again.params['roster'] == row.params['roster']
    assert estimate_vram(model, 10000, payload) == 0


@pytest.mark.asyncio
async def test_mixed_case_family_identity_is_canonical(admission):
    payload = {'roster': {'molecule': 'dna', 'rows': [
        {'row_id': 'r1', 'header': 'one', 'sequence': 'ACGT'}]}, 'settings': {}}
    created = await jobs._create_job(JobCreate(name='canonical', model_id=' FAMILY_MSA_NUCLEIC ',
        mode=' FAMILY_MSA ', params=payload), BackgroundTasks(), admission)
    row = await admission.get(Job, created.id)
    assert (row.model_id, row.mode) == ('family_msa_nucleic', 'family_msa')
