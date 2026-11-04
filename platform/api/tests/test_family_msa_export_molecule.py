"""The retained source, not an operator default, defines export classification."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import family_msa_results as route
from services.family_msa.operations import ManagedOperation, OperationRequest


@pytest.fixture
def export_admission(monkeypatch):
    state = {'molecule': 'dna', 'created': [], 'bound': []}

    async def authorize(*args, **kwargs):
        return None

    async def source(*args):
        async def read(ident):
            assert ident == 'source-receipt'
            return json.dumps({'requested': {'roster': {'molecule': state['molecule']}}}).encode()
        return [{'id': 'source-receipt', 'role': 'receipt'}], read

    async def bind(session, *, request, project_id, shared_states, job_id):
        state['bound'].append(request)
        return ManagedOperation(request=request, project_id=project_id, request_dataset_id='dataset')

    async def create(payload, background, core, *, _preallocated_job_id):
        state['created'].append(payload)
        state['job'] = SimpleNamespace(id=_preallocated_job_id, params=payload.params, status='queued',
                                       queue_status='pending', error_message=None)

    async def get_receipt(*args):
        return SimpleNamespace(workspace_id='p', entity_kind='family_msa_import')

    async def get_job(*args):
        return state['job']

    monkeypatch.setattr(route, '_require_mutation_owner', authorize)
    monkeypatch.setattr(route, 'project_result_data', source)
    monkeypatch.setattr('services.family_msa.operations.bind_request', bind)
    monkeypatch.setattr('routers.jobs._create_job', create)
    state['session'] = SimpleNamespace(get=get_receipt)
    state['core'] = SimpleNamespace(get=get_job)
    return state


def request(molecule):
    return OperationRequest(operation='export', molecule=molecule, domain_experiment_id='d',
                            inputs={'receipt_id': 'receipt', 'shared_state_ids': []})


@pytest.mark.asyncio
@pytest.mark.parametrize(('molecule', 'model'), [('dna', 'family_msa_nucleic'), ('rna', 'family_msa_nucleic'), ('protein', 'family_msa_protein')])
async def test_export_queues_under_verified_source_molecule(export_admission, molecule, model):
    state = export_admission
    state['molecule'] = molecule
    result = await route.submit_operation(None, 'p', request(molecule), state['session'], state['core'])
    assert result.status == 'queued'
    assert state['created'][0].model_id == model
    assert state['bound'][0].molecule == molecule


@pytest.mark.asyncio
@pytest.mark.parametrize(('actual', 'claimed'), [('dna', 'protein'), ('rna', 'dna'), ('protein', 'rna')])
async def test_export_uses_saved_molecule_over_browser_value(export_admission, actual, claimed):
    state = export_admission
    state['molecule'] = actual
    result = await route.submit_operation(None, 'p', request(claimed), state['session'], state['core'])
    assert result.status == 'queued'
    assert state['bound'][0].molecule == actual


@pytest.mark.asyncio
@pytest.mark.parametrize('actual', [None, '', 'unknown'])
async def test_unknown_source_molecule_keeps_requested_value(export_admission, actual):
    state = export_admission
    state['molecule'] = actual
    result = await route.submit_operation(None, 'p', request('dna'), state['session'], state['core'])
    assert result.status == 'queued'
    assert state['bound'][0].molecule == 'dna'
