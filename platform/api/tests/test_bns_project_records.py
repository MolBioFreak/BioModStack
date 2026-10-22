from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from experiment_database import get_experiment_session
from routers.projects import router
from routers.project_manager import list_domain_adapters, _edition_adapter
from services.global_experiments.adapters import AdapterError
from tests.test_project_manager_hierarchy import project_store, _app, _project_payload


def payload(**changes):
    return {'schema': 'bns.ngs-data-record.v1', 'record_key': str(uuid4()), 'title': 'QA plasmid reads',
            'sample_name': 'QA plasmid', 'data_format': 'fastq', 'use_case': 'sequence_identity',
            'workflow_stage': 'basecalled', 'data_location': '', 'notes': 'Operator intake only', 'files': [], **changes}


@pytest.mark.asyncio
async def test_empty_project_records_persist_and_retry_without_duplicate(project_store):
    _, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        project = (await client.post('/api/projects', json=_project_payload())).json()
        path = f"/api/projects/{project['id']}/ngs-data-records"
        draft = payload()
        saved = await client.post(path, json=draft)
        assert saved.status_code == 201, saved.text
        assert saved.json()['author'] == 'operator'
        assert (await client.post(path, json=draft)).json()['id'] == saved.json()['id']
        assert (await client.post(path, json={**draft, 'notes': 'different'})).status_code == 409
        assert (await client.post(path, json=payload(hypothesis='unexpected'))).status_code == 422
        assert (await client.post(path, json=payload(data_format='protein'))).status_code == 422
        await client.post(f"/api/projects/{project['id']}/records", json={'record_kind': 'observation', 'body': 'Unrelated ordinary record'})
        await client.post(path, json=payload(title='RNA run', use_case='transcriptomics'))
        first = (await client.get(path, params={'limit': 1})).json()
        second = (await client.get(path, params={'limit': 1, 'cursor': first['next_cursor']})).json()
        assert first['items'][0]['payload']['title'] == 'RNA run'
        assert second['items'][0]['payload'] == draft
        assert second['next_cursor'] is None
        assert (await client.get(f"/api/projects/{project['id']}/experiments")).json()['items'] == []
    # Reopen with a new application/session against the same migrated DB.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)), base_url='http://test') as client:
        assert len((await client.get(path)).json()['items']) == 2


@pytest.mark.asyncio
async def test_uploaded_inputs_must_belong_to_project_and_match_size(project_store, tmp_path, monkeypatch):
    _, factory = project_store
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path / 'inputs'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)), base_url='http://test') as client:
        project = (await client.post('/api/projects', json=_project_payload())).json()
        root = tmp_path / 'inputs' / 'bns-projects' / project['id']
        root.mkdir(parents=True)
        (root / 'reads.fastq').write_text('@read\nACGT\n+\nIIII\n')
        file = {'name': 'reads.fastq', 'path': f"inputs/bns-projects/{project['id']}/reads.fastq", 'size': 18}
        path = f"/api/projects/{project['id']}/ngs-data-records"
        assert (await client.post(path, json=payload(files=[{**file, 'size': 999}]))).status_code == 422
        assert (await client.post(path, json=payload(files=[{**file, 'path': 'inputs/bns-projects/other/reads.fastq'}]))).status_code == 422
        file['size'] = (root / 'reads.fastq').stat().st_size
        saved = await client.post(path, json=payload(files=[file]))
        assert saved.status_code == 201, saved.text
        assert saved.json()['payload']['files'] == [file]
        unauthenticated = FastAPI()
        unauthenticated.include_router(router)
        unauthenticated.dependency_overrides[get_experiment_session] = _app(factory).dependency_overrides[get_experiment_session]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=unauthenticated), base_url='http://test') as outsider:
            assert (await outsider.post(path, json=payload())).status_code in {401, 403}


@pytest.mark.asyncio
async def test_public_edition_catalogue_excludes_protein_even_without_workspace():
    catalogue = await list_domain_adapters()
    assert catalogue['adapters']
    assert all(item['domain_kind'] == 'ngs_molbio' for item in catalogue['adapters'])
    assert any(item['adapter_id'] == 'bms.molbio.member-molecular-revision.adapter.v1' for item in catalogue['adapters'])
    with pytest.raises(AdapterError):
        _edition_adapter('bms.cm.protenix_v2.adapter.v1')
