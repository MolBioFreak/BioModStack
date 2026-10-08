"""Recovered batch through its production parent and normalized scalar retention."""
import io
import json
from zipfile import ZipFile

import pytest

from services.assembly.golden_gate_workflow import freeze_selection
from services.assembly.golden_gate_workflow_types import WorkflowResult, SaveDesignRequest
from services.assembly.golden_gate_workflow_wire import project_workflow, expand_workflow
from test_golden_gate_batch_receiving import batch_request, URL
from test_golden_gate_workflow_receiving import client_store


def test_actual_app_registers_batch_without_lifespan():
    from main import app
    # FastAPI may retain lazy included routers rather than flatten app.routes.
    schema = app.openapi()
    assert schema['paths'][URL + '/batch']['post']['requestBody']['content']['application/json']['schema']['$ref'].endswith('/BatchRequest')


@pytest.mark.asyncio
async def test_batch_to_normalized_save_retry_reopen_zip_import(tmp_path, monkeypatch):
    async with client_store(tmp_path) as (client, _):
        response = await client.post(URL + '/batch', json=batch_request().model_dump(mode='json'))
        assert response.status_code == 200, response.text
        events = [json.loads(line) for line in response.text.splitlines()]
        result = WorkflowResult.model_validate(events[1]['result'])
        assert result.selected_solution_id is not None
        body = SaveDesignRequest(selection=freeze_selection(result, result.selected_solution_id),
            name='Integrated batch selection', idempotency_key='integrated-batch').model_dump(mode='json')
        wire = project_workflow(body, 'save').model_dump(mode='json')
        first = await client.post(URL + '/save?view=normalized', json=wire)
        retry = await client.post(URL + '/save?view=normalized', json=wire)
        assert first.status_code == retry.status_code == 200
        assert first.json() == retry.json()
        saved = expand_workflow(first.json(), 'saved')
        assert saved['result'] == events[1]['result']

        def forbidden(*args, **kwargs):
            raise AssertionError('Frozen receiving must not recompute science')
        from services.assembly import golden_gate_workflow_persistence as persistence
        from routers import molbio_golden_gate_design as design
        monkeypatch.setattr(persistence, 'run_workflow', forbidden)
        monkeypatch.setattr(design, 'run_workflow', forbidden)
        path = URL + '/' + saved['operation_id']
        reopened = await client.get(path + '?view=normalized')
        assert reopened.status_code == 200
        assert expand_workflow(reopened.json(), 'saved') == saved
        assert (await client.get(path)).json() == saved
        exported = await client.get(path + '/export')
        assert exported.status_code == 200
        with ZipFile(io.BytesIO(exported.content)) as archive:
            portable = json.loads(archive.read('workflow.json'))
        assert portable['result'] == saved['result']
        imported = await client.post(URL + '/import?view=normalized',
            json=project_workflow(portable, 'portable').model_dump(mode='json'))
        assert imported.status_code == 200
        assert expand_workflow(imported.json(), 'result') == saved['result']
