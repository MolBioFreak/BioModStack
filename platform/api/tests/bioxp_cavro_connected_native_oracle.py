"""Run explicitly by pytest in the native image, not the API test process.

The documents are actual BMS compiler outputs. Only host-only prompt/analysis
jobs are submitted; there are no hardware actions in these documents.
"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_deck_scoped_authority import retained_rig
from tests.test_deck_scoped_integration import installed_retained
from tests.test_protocol_workflow_connected import mount_protocol_routes, request_payload, await_job


@pytest.mark.parametrize('name', ['loop', 'procedure', 'groups'])
def test_bms_repeated_measurement_same_job_native_store(installed_retained, monkeypatch, tmp_path, name):
    from bioxp.operator_command_plane import OperatorCommandStore
    app, provider, primitives, references, root = installed_retained
    monkeypatch.setenv('BIOXP_PROTOCOL_JOBS_ROOT', str(tmp_path / 'artifacts'))
    mount_protocol_routes(app)
    client = TestClient(app)
    document = json.loads(Path('/inputs/references-' + name + '.json').read_text())['result']['document']
    actions = [a for s in document['stages'] for a in s['actions']]
    assert {a['kind'] for a in actions} == {'pause_review', 'calibration'}
    request = request_payload('bms-references-' + name, [])
    request['document'] = document
    response = client.post('/protocol/execute', json=request)
    assert response.status_code == 202, response.text
    job = response.json()['job_id']
    app.state.operator_command_plane.start()
    held, reviews = [], []
    for index, action in enumerate(a for a in actions if a['kind'] == 'pause_review'):
        paused = await_job(client, job, lambda r: (r['operator'].get('pending_review') or {}).get('action_id') == action['action_id'])
        pending = paused['operator']['pending_review']
        assert all(pending[k] == action[k] for k in ('stage_id', 'action_id', 'source_occurrence_id'))
        reopened = OperatorCommandStore(root)
        try:
            assert reopened.get_workflow(job)['operator']['pending_review'] == pending
        finally:
            reopened.stop()
        held.append(paused)
        body = {'command_id': job, 'idempotency_key': 'bms-review-' + name + '-' + str(index),
            'expected_ownership_generation': paused['command']['ownership_generation'],
            **{k: pending[k] for k in ('stage_id', 'action_id', 'source_occurrence_id')},
            'reviewer': 'offline synthetic measurement agent', 'measurements': {'volume': ['19.2500' if index == 0 else '20.1250']}}
        response = client.post('/protocol/jobs/' + job + '/review', json=body)
        assert response.status_code == 200, response.text
        reviews.append(body)
    done = await_job(client, job, lambda r: r['command']['terminal'])
    assert done['command']['status'] == 'completed', done
    rows = [r for r in done['execution']['runtime_state']['action_results'] if 'analysis' in r]
    assert [r['analysis']['resolved_samples'][0]['observation']['measured_ul'] for r in rows] == ['19.2500', '20.1250']
    assert len(done['execution']['runtime_state']['measurement_reviews']) == 2
    app.state.operator_command_plane.stop()
    reopened = OperatorCommandStore(root)
    try:
        loaded = reopened.get_workflow(job)
        assert loaded['execution']['runtime_state'] == done['execution']['runtime_state']
    finally:
        reopened.stop()
    out = Path(os.environ['NON_OEM_NATIVE_EXPORT_DIR'])
    out.mkdir(parents=True, exist_ok=True)
    (out / ('bms-connected-' + name + '.json')).write_text(json.dumps({'request': request, 'held': held,
        'review_requests': reviews, 'completed': done, 'reopened': loaded}, indent=2) + '\n')
