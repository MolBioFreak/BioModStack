"""Real HTTP/connection lease contract with inert transport, not native motion proof."""
import asyncio
import copy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from routers.bioxp import operator_controls as routes
from routers.bioxp.dependencies import get_bioxp_runtime, require_bioxp_mutation_access
from test_bioxp_connection import _load, _service

WELL = 'oem.deck.move_to_well'
NAMED = 'oem.deck.move_to_location'


def body(generation, inputs):
    return dict(schema_version='bioxp.operator_action_request.v2', idempotency_key='well-contract-1',
                expected_connection_generation=generation, expected_ownership_generation=4,
                expected_board_epoch_by_board={'4': 2}, inputs=inputs)


def run(tmp_path, scenario):
    async def main():
        clients = []
        service = _service(tmp_path, clients)
        _, Profile, _, _ = _load()
        await service.save_profile(Profile(api_url='http://robot:8123'))
        generation = (await service.connect()).generation
        app = FastAPI()
        app.include_router(routes.router, prefix='/api/bioxp')
        app.dependency_overrides[get_bioxp_runtime] = lambda: SimpleNamespace(connection=service)
        app.dependency_overrides[require_bioxp_mutation_access] = lambda: None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://bms') as http:
                await scenario(http, service, clients, generation)
        finally:
            await service.close()
    asyncio.run(main())


@pytest.mark.parametrize('inputs', [
    {}, {'location_id': 1, 'well': 'A1'},
    {'location_id': True, 'well': 'A1', 'position_flag': 1},
    {'location_id': '1', 'well': 'A1', 'position_flag': 1},
    {'location_id': 1, 'well': False, 'position_flag': 1},
    {'location_id': 1, 'well': 1.5, 'position_flag': 1},
    {'location_id': 1, 'well': 'A1', 'position_flag': True},
    {'location_id': 1, 'well': 'A1', 'position_flag': 1.0},
    {'location_id': 1, 'well': 'A1', 'position_flag': 3},
    {'location_id': 1, 'well': 'A1', 'position_flag': 1, 'x': 0},
])
def test_closed_explicit_well_inputs_never_reach_transport(tmp_path, inputs):
    async def scenario(http, service, clients, generation):
        response = await http.post(f'/api/bioxp/operator-controls/v2/actions/{WELL}', json=body(generation, inputs))
        assert response.status_code == 422, response.text
        assert clients[0].request_calls == []
    run(tmp_path, scenario)


@pytest.mark.parametrize('well', ['A1', 'H1', 0, 84])
@pytest.mark.parametrize('flag', [0, 1, 2])
def test_mixed_intents_forward_only_local_fence_removed_and_reply_untouched(tmp_path, well, flag):
    async def scenario(http, service, clients, generation):
        calls = []
        reply = {'action_id': WELL, 'command_id': 'native-1', 'physical_effect_verified': False,
                 'native_extension': {'unknown': None, 'source_return': False}, 'child_receipts': []}
        async def request(route, **kwargs):
            calls.append((route, copy.deepcopy(kwargs)))
            return reply
        clients[0].request = request
        service._last_reachable = False
        service._observed_at = None  # observation absence is not admission
        for action, inputs in [(NAMED, {'target': 'LOC_PARK', 'camera_offset': False}),
                               (WELL, {'location_id': 1, 'well': well, 'position_flag': flag})]:
            sent = body(generation, inputs)
            response = await http.post(f'/api/bioxp/operator-controls/v2/actions/{action}', json=sent)
            assert response.status_code == 202, response.text
            assert response.json() == reply
            assert calls[-1][1]['json_data'] == {k: v for k, v in sent.items() if k != 'expected_connection_generation'}
            assert calls[-1][1]['path_params'] == {'action_id': action}
        stale = await http.post(f'/api/bioxp/operator-controls/v2/actions/{WELL}', json=body(generation + 1, inputs))
        assert stale.status_code == 409
        assert len(calls) == 2
    run(tmp_path, scenario)


@pytest.mark.parametrize('failure', ['timeout', 'refusal'])
def test_failed_admission_releases_lease_without_replay(tmp_path, failure):
    from services.bioxp.errors import RobotResponseError, RobotTimeoutError
    async def scenario(http, service, clients, generation):
        calls = []
        async def request(route, **kwargs):
            calls.append(route)
            if failure == 'timeout':
                raise RobotTimeoutError('inert timeout', dispatched=True)
            raise RobotResponseError(409, {'detail': {'error': 'native_interlock', 'unknown': None}})
        clients[0].request = request
        response = await http.post(f'/api/bioxp/operator-controls/v2/actions/{WELL}', json=body(generation, {'location_id': 1, 'well': 'A1', 'position_flag': 1}))
        assert response.status_code == (504 if failure == 'timeout' else 409)
        if failure == 'timeout':
            assert response.json()['detail']['dispatch_state'] == 'outcome_ambiguous'
        else:
            assert response.json()['detail'] == {'error': 'native_interlock', 'unknown': None}
        await asyncio.wait_for(service.disconnect(), 2)
        assert clients[0].closed and calls == ['invoke_operator_action_v2']
    run(tmp_path, scenario)


@pytest.mark.parametrize('alignment', ['absent', None, -1, 0, 3, 999])
def test_additive_alignment_and_native_sample_times_pass_through(tmp_path, alignment):
    async def scenario(http, service, clients, generation):
        payload = {'deck': {}, 'telemetry': {'snapshot': {'observed_at': 12.5,
            'domain_observed_at': {'axes': 12.5, 'pipettes': None}, 'clock_skew_detected': True}}}
        if alignment != 'absent':
            payload['deck']['head_alignment'] = None if alignment is None else {
                'tip_location': alignment, 'semantic_state_revision': 4, 'producer_operation': None,
                'producer_command_id': 'native-command', 'ownership_generation': None}
        async def request(route, **kwargs):
            assert route == 'operator_dashboard_v2'
            return payload
        clients[0].request = request
        response = await http.get('/api/bioxp/operator-controls/v2/dashboard')
        assert response.status_code == 200 and response.json() == payload
    run(tmp_path, scenario)


def test_held_well_lease_allows_stop_and_drains_original_client_on_disconnect(tmp_path):
    async def scenario(http, service, clients, generation):
        old = clients[0]
        old.request_started = asyncio.Event()
        old.request_release = asyncio.Event()
        old.blocking_route_name = 'invoke_operator_action_v2'
        old.blocking_action_id = WELL
        post = asyncio.create_task(http.post(f'/api/bioxp/operator-controls/v2/actions/{WELL}',
            json=body(generation, {'location_id': 1, 'well': 'A1', 'position_flag': 1})))
        await asyncio.wait_for(old.request_started.wait(), 2)
        stop = await http.post('/api/bioxp/operator-controls/v2/interrupts/oem.x.stop', json={
            'schema_version': 'bioxp.operator_interrupt_request.v1', 'expected_connection_generation': generation,
            'idempotency_key': 'stop-contract-1', 'reason': 'test', 'observed_ownership_generation': 4,
            'observed_board_epoch_by_board': {'4': 2}})
        assert stop.status_code == 200, stop.text
        disconnect = asyncio.create_task(service.disconnect())
        await asyncio.sleep(0)
        assert not old.closed
        old.request_release.set()
        response = await asyncio.wait_for(post, 2)
        await asyncio.wait_for(disconnect, 2)
        assert response.status_code == 202  # original admitted lease returns its own response
        assert old.closed
        assert [r for r, _ in old.request_calls] == ['invoke_operator_action_v2', 'interrupt_operator_action_v1']
        new_generation = (await service.connect()).generation
        stale = await http.post(f'/api/bioxp/operator-controls/v2/actions/{WELL}', json=body(generation, {'location_id': 1, 'well': 'A1', 'position_flag': 1}))
        assert stale.status_code == 409
        assert new_generation != generation and clients[1].request_calls == []
    run(tmp_path, scenario)
