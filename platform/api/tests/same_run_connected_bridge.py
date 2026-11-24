"""Stdio bridge for the mounted recovery test; no server, sockets or device bindings."""
import json
import sys
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pytest import MonkeyPatch
from test_bioxp_operator_controls import make_client
from test_bioxp_same_run_recovery import held_bundle
from bioxp.oem_runtime_store import OEMRuntimeStore
from bioxp.operator_command_plane import OperatorCommandStore
from bioxp.protocols.models import ProtocolDocument, ProtocolStage, ProtocolAction, ProtocolActionKind as K
from bioxp.protocols.runtime_state import ProtocolRuntimeState
from bioxp.services.protocol_service import bind_protocol_dispatcher, control_protocol_job

JOB = 'protocol-live-' + 'a' * 64


def serve(root, patch):
    client, runtime = make_client(patch)
    OEMRuntimeStore(root).close()
    store = OperatorCommandStore(root)
    bundle = held_bundle()
    orig = bundle['protocol']['document']
    action = ProtocolAction('recipe-1', 's', K.PIPETTE_MANUAL_PHYSICAL, orig['stages'][0]['actions'][0]['params'],
                            source_occurrence_id='occ', on_error='pause_for_operator')
    earlier = ProtocolAction('earlier', 's', K.LED, {'red': 0, 'green': 0, 'blue': 0}, source_occurrence_id='occ')
    following = ProtocolAction('next', 's', K.LED, earlier.params)
    doc = ProtocolDocument('offline', stages=(ProtocolStage('s', actions=(earlier, action, following)),), metadata=orig['metadata'])
    bundle['protocol']['document'] = doc.to_payload()
    bundle['execution']['runtime_state'] = ProtocolRuntimeState.from_document(doc, dry_run=False, job_id=JOB).to_payload()
    bundle.pop('command'); bundle['job_id'] = JOB
    calls, delays, controls = [], [], []

    def leaf(action, state):
        calls.append(action.action_id)
        if action.kind == K.PIPETTE_MANUAL_PHYSICAL:
            delays.append(action.params['recipe']['aspiration_delay_ms'])
            return {'ok': len(delays) > 1, **({'error': 'Simulated aspirate did not complete'} if len(delays) == 1 else {}),
                    'partial': {'amount': None}, 'physical_effect_verified': False}
        return {'ok': True}

    def wait(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = store.get_workflow(JOB)
            if predicate(row):
                return row
            time.sleep(.01)
        raise AssertionError(row)

    readback = []

    async def native(route, **kw):
        if route == 'protocol_jobs':
            return {'rows': store.list_workflow_summaries(limit=kw['params']['limit'])}
        assert kw['path_params']['job_id'] == JOB
        if route == 'protocol_job':
            payload = (store.get_workflow_observation if kw.get('params', {}).get('observation') else store.get_workflow)(JOB)
            readback.append(payload)
            return payload
        assert route == 'protocol_control'
        controls.append(kw['json_data'])
        return control_protocol_job(JOB, controls[-1], command_store=store)

    runtime.connection.client.request = native
    bind_protocol_dispatcher(store, binding_factory=lambda *a, **kw: ({K.PIPETTE_MANUAL_PHYSICAL: leaf, K.LED: leaf}, {}, {}),
                             artifact_store=SimpleNamespace(save=lambda _: None))
    store.admit_workflow(command_id=JOB, idempotency_key='original', plan_fingerprint='fixture',
                         requested_inputs={'bundle': bundle}, ownership_generation=7, resources=(), board_epochs={})
    store.start(lambda _: (_ for _ in ()).throw(AssertionError('unexpected dispatch')))
    try:
        wait(lambda row: (row['execution']['runtime_state']['workflow'] or {}).get('gate') == 'error_hold')
        for line in sys.stdin:
            req = json.loads(line)
            if req.get('witness'):
                if controls:
                    wait(lambda row: row['command']['terminal'])
                out = dict(calls=calls, delays=delays, controls=controls)
            else:
                readback.clear()
                response = client.request(req['method'], req['url'], params=req.get('params'), json=req.get('body'))
                out = dict(status=response.status_code, data=response.json())
                if req['method'].lower() == 'get' and req['url'].endswith('/' + JOB) and response.is_success:
                    assert [response.json()] == readback, 'Strict receiving route changed native fields'
                if response.status_code >= 400:
                    print(req, out, file=sys.stderr)
            print(json.dumps(out), flush=True)
    finally:
        if not store.get_workflow(JOB)['command']['terminal'] and (control := store._workflow_controls.get(JOB)):
            control('offline-teardown', {'action': '_addressed_stop'})
        store.stop()


if __name__ == '__main__':
    with TemporaryDirectory() as root, MonkeyPatch.context() as patch:
        serve(root, patch)
