"""Type-sensitive native wire closeout; no replacement science or live stores.

Python equality equates False with 0 and True with 1. Compare canonical JSON
so a response serializer cannot silently change opaque JSON/QC scalar types.
"""
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from services.assembly.golden_gate_workflow import run_workflow
from services.assembly.golden_gate_workflow_types import REQUEST_ADAPTER
from services.assembly.golden_gate_workflow_wire import expand_workflow, project_workflow
from test_golden_gate_append_only_save import native_store
from test_golden_gate_wire_receiving import BASE, fixture


def exact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def browser_capture():
    root = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/golden-gate'
    return json.loads((root / 'wire-browser-requests.json').read_text())


def test_alias_projection_preserves_json_types_and_identity():
    body = fixture('aliases')
    assert exact(expand_workflow(project_workflow(body, 'request'), 'request')) == exact(body)
    result = run_workflow(REQUEST_ADAPTER.validate_python(body)).model_dump(mode='json')
    wire = project_workflow(result, 'result').model_dump(mode='json')
    assert exact(expand_workflow(wire, 'result')) == exact(result)
    sources = result['requested']['sources']
    assert [s['id'] for s in sources] == ['donor', 'insert', 'alias']
    assert exact(sources[1]['source']) == exact(sources[2]['source'])
    assert [p['qc']['binding_positions'][0]['reverse_primer_binding']
            for p in result['solutions'][0]['design']['primers']] == [False, True]
    assert all(type(p['qc']['binding_positions'][0]['reverse_primer_binding']) is bool
               for p in result['solutions'][0]['design']['primers'])


@pytest.mark.asyncio
async def test_native_alias_full_and_normalized_keep_qc_json_types(tmp_path):
    body = fixture('aliases')
    native = run_workflow(REQUEST_ADAPTER.validate_python(body)).model_dump(mode='json')
    async with native_store(tmp_path / 'native.db', initialize=True) as (client, _):
        full = await client.post(BASE, json=body)
        normalized = await client.post(BASE, params={'view': 'normalized'},
            json=project_workflow(body, 'request').model_dump(mode='json'))
        assert full.status_code == normalized.status_code == 200
        # First establish losslessness at the normalized owner. The subsequent
        # assertion exposes the native full-response serializer, not the wire.
        assert exact(expand_workflow(normalized.json(), 'result')) == exact(native)
        assert exact(full.json()) == exact(native)
        archives = []
        for value in (full.json(), normalized.json()):
            exported = await client.post(BASE + '/export', json=value)
            assert exported.status_code == 200, exported.text
            with ZipFile(io.BytesIO(exported.content)) as archive:
                archives.append({name: archive.read(name) for name in archive.namelist()})
        assert archives[0] == archives[1]


@pytest.mark.asyncio
async def test_current_browser_native_preview_save_reopen_export_import(tmp_path, monkeypatch):
    calls = browser_capture()
    native = run_workflow(REQUEST_ADAPTER.validate_python(
        expand_workflow(calls[0]['body'], 'request'))).model_dump(mode='json')
    path = tmp_path / 'native.db'
    async with native_store(path, initialize=True) as (client, _):
        preview = await client.post(calls[0]['url'], params=calls[0]['params'], json=calls[0]['body'])
        assert preview.status_code == 200, preview.text
        assert exact(expand_workflow(preview.json(), 'result')) == exact(native)
        saved = await client.post(calls[1]['url'], params=calls[1]['params'], json=calls[1]['body'])
        assert saved.status_code == 200, saved.text
        expanded = expand_workflow(saved.json(), 'saved')
        assert exact(expanded['result']) == exact(native)
        operation = expanded['operation_id']

    def forbidden(*args, **kwargs):
        raise AssertionError('Read/export/import must not recompute science')

    monkeypatch.setattr('services.assembly.golden_gate_workflow_persistence.run_workflow', forbidden)
    monkeypatch.setattr('routers.molbio_golden_gate_design.run_workflow', forbidden)
    async with native_store(path) as (client, _):
        got = await client.get(BASE + '/' + operation, params=calls[2]['params'])
        assert got.status_code == 200, got.text
        assert exact(got.json()) == exact(saved.json())
        # Current wrappers forward the actual returned preview, not an old
        # recorded result lacking now-required native thermodynamic provenance.
        exported = await client.post(calls[3]['url'], json=preview.json())
        assert exported.status_code == 200, exported.text
        with ZipFile(io.BytesIO(exported.content)) as archive:
            document = json.loads(archive.read('workflow.json'))
        assert exact(document['result']) == exact(native)
        imported = await client.post(calls[5]['url'], params=calls[5]['params'],
            json=project_workflow(document, 'portable').model_dump(mode='json'))
        assert imported.status_code == 200, imported.text
        assert exact(expand_workflow(imported.json(), 'result')) == exact(native)


def test_captured_historical_export_lacks_required_tm_provenance():
    """Pin why historical replay cannot simply be asserted to equal a new run."""
    from services.assembly.golden_gate_workflow_types import WorkflowResult
    calls = browser_capture()
    historical = expand_workflow(calls[3]['body'], 'result')
    with pytest.raises(ValidationError) as error:
        WorkflowResult.model_validate(historical)
    missing = {e['loc'] for e in error.value.errors()}
    assert missing == {
        ('solutions', 0, 'design', 'primers', i, 'tm', field)
        for i in (0, 1) for field in ('effective_settings', 'effective_input')}
    native = run_workflow(REQUEST_ADAPTER.validate_python(
        expand_workflow(calls[0]['body'], 'request'))).model_dump(mode='json')
    old_design = historical['solutions'][0]['design']
    new_design = native['solutions'][0]['design']
    assert old_design['digests'][0]['fragments'][0]['wraps_origin'] is True
    assert new_design['digests'][0]['fragments'][0]['wraps_origin'] is False
    assert [p['qc']['off_target_site_count'] for p in old_design['primers']] == [0, 0]
    assert [p['qc']['off_target_site_count'] for p in new_design['primers']] == [None, None]
