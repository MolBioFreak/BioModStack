"""Final model documents through the actual immutable run-snapshot producer."""
import json
import os
from pathlib import Path
import pytest
from test_bioxp_methods_api import store, BASE
from test_bioxp_methods_integrated import submit_body
from bioxp_method_native import EXPORT, CONTRACTS
from bioxp_method_model import method_catalog


def test_committed_native_pin_and_registration():
    assert EXPORT['source_commit'] == '759ee635e851fc447269c59ac69b3e705ab79b34'
    for name, schema in EXPORT['method_contract']['action_params'].items():
        assert all(CONTRACTS[name][key] == value for key, value in schema.items())
    from bioxp_method_native import native_action
    with pytest.raises(ValueError):
        native_action('pipette_pierce', {'pattern': 'h', 'plate': 99, 'well': 'A1'})
    entries = {a['action']: a for a in method_catalog()['actions']}
    for name in ('park', 'led', 'status_light', 'seal_separate', 'cavro_application', 'liquid_recipe', 'fluid_search', 'pipette_settings', 'pressure_stream'):
        assert entries[name]['status']['registered'] is True
        assert entries[name]['source_revision'] == EXPORT['source_commit']
    assert entries['classifier']['status']['emitted'] is False


@pytest.mark.asyncio
async def test_export_final_immutable_snapshots(store):
    if not os.environ.get('BIOXP_FINAL_DOCUMENTS'):
        return
    client, transport, _, _ = store
    source = Path(os.environ['BIOXP_FINAL_DOCUMENTS'])
    documents = json.loads(source.read_text())
    captures = []
    for doc in documents:
        metadata = doc['metadata']['bms_method']
        body = submit_body(**{k: metadata[k] for k in ('method', 'bindings', 'dependencies', 'initial_state')})
        response = await client.post(BASE + '/quick-runs', json=body)
        assert response.status_code == 202, response.text
        sent = transport.calls[-1][1]['json_data']['document']
        assert sent['metadata']['bms_method_run'] == response.json()['method_snapshot']
        assert sent['metadata']['bms_method_run']['compilation']['document'] == doc
        captures.append(sent)
    assert len(captures) == 87
    source.with_name('run-documents.json').write_text(json.dumps(captures, indent=2) + '\n')
    # Explicit offline recovery scenarios, compiled by the same real producer.
    from copy import deepcopy
    pickup = next(d['metadata']['bms_method'] for d in documents
                  if len([a for s in d['stages'] for a in s['actions']]) >= 2
                  and d['stages'][0]['actions'][0]['params'].get('operation') == 'load_tip')
    recovery = []
    for policy in ('stop', 'pause_for_operator'):
        request = deepcopy({k: pickup[k] for k in ('method', 'bindings', 'dependencies', 'initial_state')})
        for step in request['method']['steps']:
            step['on_error'] = policy
        reply = await client.post(BASE + '/quick-runs', json=submit_body(**request))
        assert reply.status_code == 202, reply.text
        recovery.append(transport.calls[-1][1]['json_data']['document'])
    request = {'method': {'schema': 'bms.bioxp-method.v1', 'name': 'Offline control clock', 'steps': [
        {'step_id': 'clock', 'type': 'action', 'action': 'wait', 'inputs': {'seconds': 60}},
        {'step_id': 'after', 'type': 'action', 'action': 'note', 'inputs': {'message': 'after clock'}}]}}
    reply = await client.post(BASE + '/quick-runs', json=submit_body(**request))
    assert reply.status_code == 202, reply.text
    recovery.append(transport.calls[-1][1]['json_data']['document'])
    request['method']['steps'][0]['inputs']['seconds'] = 0.3
    reply = await client.post(BASE + '/quick-runs', json=submit_body(**request))
    assert reply.status_code == 202, reply.text
    recovery.append(transport.calls[-1][1]['json_data']['document'])
    source.with_name('recovery-documents.json').write_text(json.dumps(recovery, indent=2) + '\n')
