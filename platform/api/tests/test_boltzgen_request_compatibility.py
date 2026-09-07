import pytest
from services import core_protein_scientific_contract as contract
from test_core_protein_scientific_admission import admission


@pytest.mark.asyncio
async def test_admission_rejects_native_plddt_before_queue(admission, monkeypatch):
    from fastapi import BackgroundTasks, HTTPException
    from routers import jobs
    from schemas import JobCreate
    from sqlalchemy import select
    from database import Job
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('boltzgen', 'ntp_binder')}))
    with pytest.raises(HTTPException, match='native pLDDT'):
        await jobs._create_job(JobCreate(name='synthetic-admission', model_id='boltzgen', mode='ntp_binder',
                              params={'ntp_type': 'ATP', 'min_plddt': 0}), BackgroundTasks(), admission)
    assert list((await admission.execute(select(Job))).scalars()) == []


@pytest.mark.asyncio
async def test_admission_persists_effective_native_rank(admission, monkeypatch):
    from fastapi import BackgroundTasks
    from routers import jobs
    from schemas import JobCreate
    from database import Job
    monkeypatch.setattr(contract, 'ACTIVATED_CALLERS', frozenset({('boltzgen', 'ntp_binder')}))
    response = await jobs._create_job(JobCreate(name='synthetic-admission', model_id='boltzgen', mode='ntp_binder',
                              params={'ntp_type': 'ATP', 'boltzgen_rank_design_ptm_weight': 2}), BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    assert job.params['boltzgen_effective_rank'][0] == {'name': 'design_ptm', 'weight': 2., 'higher_is_better': True, 'unit': 'fraction'}
    assert contract.workflow_params(job, job.params)['boltzgen_effective_rank'] == job.params['boltzgen_effective_rank']


def test_known_native_plddt_incompatibility_is_rejected_before_transport():
    from types import SimpleNamespace
    job = SimpleNamespace(model_id='boltzgen', mode='peptide_binder', provenance={contract.REVISION_KEY: 1})
    for key in ['min_plddt', 'boltzgen_min_plddt']:
        with pytest.raises(ValueError, match='native pLDDT'):
            contract.workflow_params(job, {key: 0})


def test_effective_rank_is_native_declared_and_typed():
    from types import SimpleNamespace
    job = SimpleNamespace(model_id='boltzgen', mode='peptide_binder', provenance={contract.REVISION_KEY: 1})
    params = contract.workflow_params(job, {'boltzgen_rank_design_ptm_weight': 2, 'boltzgen_rank_filter_rmsd_weight': None})
    assert params['boltzgen_metrics_override'] == 'design_ptm=2.0 affinity_probability=1.0 filter_rmsd=none'
    assert params['boltzgen_effective_rank'] == [
        {'name': 'design_ptm', 'higher_is_better': True, 'weight': 2., 'unit': 'fraction'},
        {'name': 'affinity_probability', 'higher_is_better': True, 'weight': 1., 'unit': 'fraction'}]
    for value in [True, '2', 0, float('inf')]:
        with pytest.raises(ValueError):
            contract.workflow_params(job, {'boltzgen_rank_design_ptm_weight': value})
    with pytest.raises(ValueError):
        contract.workflow_params(job, {'boltzgen_metrics_override': 'unknown=2'})
