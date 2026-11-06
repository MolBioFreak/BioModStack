"""Type-sensitive native wire closeout; no replacement science or live stores.

Python equality equates False with 0 and True with 1. Compare canonical JSON
so a response serializer cannot silently change opaque JSON/QC scalar types.
"""
import io
import json
import os
from copy import deepcopy
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
        # Forward the actual returned preview, including its native receipt.
        exported = await client.post(calls[3]['url'], json=preview.json())
        assert exported.status_code == 200, exported.text
        with ZipFile(io.BytesIO(exported.content)) as archive:
            document = json.loads(archive.read('workflow.json'))
        assert exact(document['result']) == exact(native)
        imported = await client.post(calls[5]['url'], params=calls[5]['params'],
            json=project_workflow(document, 'portable').model_dump(mode='json'))
        assert imported.status_code == 200, imported.text
        assert exact(expand_workflow(imported.json(), 'result')) == exact(native)
        evidence = os.environ.get('BMS_GG_CURRENT_CAPTURE')
        if evidence:
            Path(evidence).write_text(json.dumps(dict(
                request=expand_workflow(calls[0]['body'], 'request'),
                preview=expand_workflow(preview.json(), 'result'), normalized=preview.json(),
                save_request=expand_workflow(calls[1]['body'], 'save'),
                saved=expanded, saved_normalized=saved.json(),
                document=document, imported=imported.json()), ensure_ascii=False, indent=2) + '\n')


def historical_result():
    root = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/golden-gate'
    return expand_workflow(json.loads((root / 'wire-historical-result.json').read_text()), 'result')


@pytest.mark.asyncio
@pytest.mark.parametrize('provenance', ['absent', 'null', 'partial'])
async def test_historical_export_import_preserves_unknown_tm_provenance(tmp_path, monkeypatch, provenance):
    from services.assembly.golden_gate_workflow_types import WorkflowResult
    historical = historical_result()
    for primer in historical['solutions'][0]['design']['primers']:
        assert 'effective_settings' not in primer['tm']
        assert 'effective_input' not in primer['tm']
        if provenance == 'null':
            primer['tm'].update(effective_settings=None, effective_input=None)
        elif provenance == 'partial':
            primer['tm']['effective_input'] = None
    # Defaults add only unknown/null evidence, never inferred calculation inputs.
    expected = WorkflowResult.model_validate(historical).model_dump(mode='json')
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical receiving must not recompute science')
    monkeypatch.setattr('routers.molbio_ops.calculate_primer_tm_result', forbidden)
    monkeypatch.setattr('routers.molbio_golden_gate_design.run_workflow', forbidden)
    monkeypatch.setattr('services.assembly.golden_gate_workflow_persistence.run_workflow', forbidden)
    async with native_store(tmp_path / 'legacy.db', initialize=True) as (client, _):
        historical_document = dict(schema_version='bms.golden-gate-workflow-portable.v1', result=historical)
        for value, params in ((historical_document, {}),
                              (project_workflow(historical_document, 'portable').model_dump(mode='json'), {'view': 'normalized'})):
            imported = await client.post(BASE + '/import', params=params, json=value)
            assert imported.status_code == 200, imported.text
            received = expand_workflow(imported.json(), 'result') if params else imported.json()
            assert exact(received) == exact(expected)
        archives = []
        for value in (historical, project_workflow(historical, 'result').model_dump(mode='json')):
            exported = await client.post(BASE + '/export', json=value)
            assert exported.status_code == 200, exported.text
            with ZipFile(io.BytesIO(exported.content)) as archive:
                archives.append({n: archive.read(n) for n in archive.namelist()})
                document = json.loads(archive.read('workflow.json'))
            assert exact(document['result']) == exact(expected)
            for value, params in ((document, {}), (project_workflow(document, 'portable').model_dump(mode='json'), {'view': 'normalized'})):
                imported = await client.post(BASE + '/import', params=params, json=value)
                assert imported.status_code == 200, imported.text
                received = expand_workflow(imported.json(), 'result') if params else imported.json()
                assert exact(received) == exact(expected)
        assert archives[0] == archives[1]
    # Preserve old observations as historical evidence, not current science.
    design = expected['solutions'][0]['design']
    assert design['digests'][0]['fragments'][0]['wraps_origin'] is True
    assert [p['qc']['off_target_site_count'] for p in design['primers']] == [0, 0]
    for primer in design['primers']:
        assert primer['tm']['effective_settings'] is None
        assert primer['tm']['effective_input'] is None


def test_current_science_and_present_provenance_remain_validated():
    from services.assembly.golden_gate_workflow_types import WorkflowResult
    native = run_workflow(REQUEST_ADAPTER.validate_python(
        expand_workflow(browser_capture()[0]['body'], 'request'))).model_dump(mode='json')
    design = native['solutions'][0]['design']
    assert design['digests'][0]['fragments'][0]['wraps_origin'] is False
    assert [p['qc']['off_target_site_count'] for p in design['primers']] == [None, None]
    for primer in design['primers']:
        assert primer['tm']['effective_settings'] is not None
        assert primer['tm']['effective_input']['sequence'] == primer['annealing_sequence']
    for field, value in [('effective_settings', {'na_mM': -1}),
                         ('effective_input', {'sequence': 'ACGT', 'undocumented': True}),
                         ('effective_settings', {'undocumented': True})]:
        invalid = deepcopy(native)
        invalid['solutions'][0]['design']['primers'][0]['tm'][field] = value
        with pytest.raises(ValidationError):
            WorkflowResult.model_validate(invalid)
