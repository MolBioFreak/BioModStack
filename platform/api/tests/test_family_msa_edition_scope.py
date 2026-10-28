"""The alignment port adds only its own identities to the trimmed edition."""
import pytest
from fastapi import HTTPException
from product_scope import NGS_MODEL_IDS
from schemas import JobCreate
from routers.jobs import normalize_job_request
from routers.project_manager import list_domain_adapters, _edition_adapter
from services.global_experiments.adapters import AdapterError


@pytest.mark.asyncio
async def test_alignment_adapters_visible_without_reopening_protein_models():
    inventory = (await list_domain_adapters())['adapters']
    ids = {item['adapter_id'] for item in inventory}
    for model in ('family_msa_nucleic', 'family_msa_protein'):
        adapter_id = f'bms.family-msa.{model}.adapter.v1'
        assert model in NGS_MODEL_IDS
        assert adapter_id in ids
        assert _edition_adapter(adapter_id).model_id == model
    assert not {'esmfold2', 'rfd3', 'bindcraft2', 'protenix', 'laproteina', 'complexa'} & NGS_MODEL_IDS
    with pytest.raises(AdapterError, match='not available'):
        _edition_adapter('bms.core-job.esmfold2.adapter.v1')
    with pytest.raises(HTTPException) as error:
        normalize_job_request(JobCreate(name='excluded', model_id='esmfold2', mode='predict', params={}))
    assert error.value.status_code == 422
