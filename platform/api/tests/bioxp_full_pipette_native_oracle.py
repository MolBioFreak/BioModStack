"""API-owned native-image receiving: real BMS HTTP outputs, inert physical leaves."""
import json
import os
from pathlib import Path
from dataclasses import replace

import pytest
from tests.test_deck_scoped_authority import retained_rig
from tests.test_deck_scoped_integration import installed_retained
from tests.test_cavro_application import rig as wire_rig
from tests.test_method_runtime_connected import mount, thermal_leaf
from tests.test_protocol_workflow_connected import request_payload, await_job


KEYS = ['pipette_volume_calibration', 'pipette_height_calibration', 'rc_calibration', 'oc_calibration', 'tc_calibration', 'custom-rc', 'custom-oc', 'custom-tc']


@pytest.mark.parametrize('key', KEYS)
def test_actual_bms_calibration_document_same_job(wire_rig, installed_retained, thermal_leaf, monkeypatch, tmp_path, key):
    from bioxp import api
    from bioxp.operator_command_plane import OperatorCommandStore
    from bioxp.pipette.receipts import PipetteReceiptStore
    from bioxp.oem_runtime_store import OEMRuntimeStore
    from bioxp.oem_calibration_settings import CalibrationSettingsService, CalibrationSettingsPatch
    from oem_machine_bundle_test_support import bind_serial206_oem_snapshot
    app, client = mount(installed_retained, monkeypatch, tmp_path)
    provider, root = installed_retained[1], installed_retained[4]
    provider.primitives.pipette_transport = wire_rig.group
    receipts = PipetteReceiptStore(root)
    monkeypatch.setattr(api, '_pipette_receipts', receipts)
    monkeypatch.setattr(api, '_require_motion_route_ready', lambda: None)
    snapshot = bind_serial206_oem_snapshot(monkeypatch)
    snapshot = replace(snapshot, fields={**snapshot.fields, 'machine.calibrated': replace(snapshot.fields['machine.calibrated'], value=True)})
    settings_store = OEMRuntimeStore(root)
    settings = CalibrationSettingsService(settings_store, snapshot)
    settings.save(CalibrationSettingsPatch.model_validate({'positions': [{'name': 'LOC_TC', 'zLow': 7777}]}))
    monkeypatch.setattr(api.app.state, 'calibration_settings', settings, raising=False)
    before = settings.read()
    source = json.loads(Path('/inputs/delivery-' + key + '.json').read_text())
    document = source['result']['document']
    from bioxp.protocols.compiler import compile_native_protocol
    assert compile_native_protocol(document).to_payload() == document
    request = request_payload('delivery-' + key, [])
    request['document'] = document
    response = client.post('/protocol/execute', json=request)
    assert response.status_code == 202, response.text
    job = response.json()['job_id']
    app.state.operator_command_plane.start()
    actions = [a for s in document['stages'] for a in s['actions']]
    held, reviews = [], []
    for index, action in enumerate(a for a in actions if a['kind'] == 'pause_review'):
        paused = await_job(client, job, lambda r: (r['operator'].get('pending_review') or {}).get('action_id') == action['action_id'])
        pending = paused['operator']['pending_review']
        identity_keys = ('stage_id', 'action_id', 'source_occurrence_id') if action['params'].get('measurement_form') else ('stage_id', 'action_id')
        assert all(pending[k] == action[k] for k in identity_keys)
        reopened = OperatorCommandStore(root)
        try:
            assert reopened.get_workflow(job)['operator']['pending_review'] == pending
        finally:
            reopened.stop()
        body = {'command_id': job, 'idempotency_key': key + '-review-' + str(index),
            'expected_ownership_generation': paused['command']['ownership_generation'],
            **{k: pending[k] for k in identity_keys}, 'reviewer': 'inert software fixture'}
        form = action['params'].get('measurement_form')
        if form:
            field = form['fields'][0]['field_id']
            body['measurements'] = {field: [{'z': '88000', 'volume': '19.2500', 'temperature': '24.7500'}[field]]}
        wire_before, thermal_before = list(wire_rig.wire), list(thermal_leaf[1])
        wrong = client.post('/protocol/jobs/' + job + '/review', json={**body, 'idempotency_key': 'wrong-' + body['idempotency_key'], 'action_id': 'wrong'})
        assert wrong.status_code == 409, wrong.text
        assert wire_rig.wire == wire_before and thermal_leaf[1] == thermal_before
        accepted = client.post('/protocol/jobs/' + job + '/review', json=body)
        assert accepted.status_code == 200, accepted.text
        held.append(paused); reviews.append(body)
    done = await_job(client, job, lambda r: r['command']['terminal'])
    assert done['command']['status'] == 'completed', done
    rows = done['execution']['runtime_state']['action_results']
    if key == 'pipette_height_calibration':
        assert next(r for r in rows if 'height_adjustment' in r)['height_adjustment']['measured_raw_z'] == 88000
        assert next(r for r in rows if r.get('applied'))['positions']
        decision = next(r for r in rows if 'calibration_run' in r)['calibration_run']
        assert decision['decision'] == 'restore'
        assert settings.read()['active_positions'] == before['active_positions']
    else:
        analysis = next(r for r in rows if 'analysis' in r)['analysis']
        sample = analysis['resolved_samples'][0]
        if key == 'pipette_volume_calibration':
            assert sample['observation']['measured_ul'] == '19.2500'
            assert wire_rig.wire.count((0, 'P22.5,1R')) == 1
            assert wire_rig.wire.count((0, 'D22.5,1R')) == 1
            assert receipts.read(limit=100)
        else:
            assert sample['measured_c'] == '24.7500'
            assert sample['module'] == (key.removeprefix('custom-') if key.startswith('custom-') else key.split('_')[0])
            assert analysis['correction'] is None and analysis['applied'] is False
            assert len([w for w in thermal_leaf[1] if w[1] == 140]) == (2 if key.startswith('custom-') else 1)
            if key.startswith('custom-'):
                analyses = [r['analysis'] for r in rows if 'analysis' in r]
                assert [a['resolved_samples'][0]['target_c'] for a in analyses] == ['31.2500', '37.0000']
                assert len(done['execution']['runtime_state']['measurement_reviews']) == 2
    wire_before, thermal_before = list(wire_rig.wire), list(thermal_leaf[1])
    for body in reviews:
        replay = client.post('/protocol/jobs/' + job + '/review', json=body)
        assert replay.status_code == 200, replay.text
    assert wire_rig.wire == wire_before and thermal_leaf[1] == thermal_before
    app.state.operator_command_plane.stop()
    reopened = OperatorCommandStore(root)
    try:
        loaded = reopened.get_workflow(job)
        assert loaded['execution']['runtime_state'] == done['execution']['runtime_state']
    finally:
        reopened.stop()
    out = Path(os.environ['NON_OEM_NATIVE_EXPORT_DIR']); out.mkdir(parents=True, exist_ok=True)
    (out / ('delivery-' + key + '.json')).write_text(json.dumps({'request': request, 'held': held,
        'review_requests': reviews, 'completed': done, 'reopened': loaded, 'wire': wire_before,
        'thermal_leaves': thermal_before, 'receipts': receipts.read(limit=100)}, indent=2))
    settings_store.close()
