"""One consumer response, two real robot producers; no robot lifespan/hardware."""
import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from routers.bioxp import operator_controls as routes
from services.bioxp.errors import RobotResponseError, RobotTimeoutError
from services.bioxp.models import BioXpProfile
from test_bioxp_connection import _service
from test_bioxp_operator_controls import FakeConnection, make_client
from test_debloat_bms_robot_concurrency import robot


@pytest.fixture
def producer(robot, monkeypatch):
    from bioxp import api, operator_controls
    app = FastAPI()
    # Actual route schema builds typed legacy inputs/path metadata. Nothing
    # invokes this mutation in the test; only passive catalog GETs are allowed.
    app.add_api_route('/led/rgb', api.led_rgb, methods=['POST'])
    operator_controls.install_operator_control_plane(app)
    yield app
    app.state.operator_command_plane.stop()
    app.state.operator_poll_cache.close()


@pytest.mark.parametrize('target', [None, 0, 65000, -2147483648, 2147483647])
def test_real_producer_catalog_preserves_both_contracts(producer, tmp_path, target):
    async def scenario():
        clients = []
        service = _service(tmp_path, clients)
        await service.save_profile(BioXpProfile(api_url='http://robot:8123'))
        generation = (await service.connect()).generation
        calls, raw = [], {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(producer), base_url='http://robot') as remote:
            async def request(name, **kw):
                calls.append((name, copy.deepcopy(kw)))
                assert name in {'operator_control_catalog', 'operator_control_catalog_v2'}
                path = '/operator/' + ('v2/' if name.endswith('_v2') else '') + 'control-catalog'
                async def read_producer():
                    while True:
                        result = await remote.get(path, params=kw.get('params'))
                        if result.status_code != 503 or result.json().get('detail') != 'operator_poll_warming':
                            return result
                        await asyncio.sleep(.01)
                result = await asyncio.wait_for(read_producer(), 5)
                assert result.status_code == 200, result.text
                raw[name] = result.json()
                return result.json()
            clients[0].request = request
            app = FastAPI()
            app.state.bioxp_runtime = SimpleNamespace(connection=service)
            app.include_router(routes.router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://bms') as http:
                response = await http.get('/operator-controls/catalog', params={} if target is None else {'z_target_steps': target})
                assert response.status_code == 200, response.text
                body = response.json()
                legacy = raw['operator_control_catalog']
                expected_legacy = routes.OperatorControlCatalog.model_validate(legacy).model_dump(mode='json')
                assert {key: value for key, value in body.items() if key != 'canonical'} == {
                    key: value for key, value in expected_legacy.items() if key != 'canonical'
                }
                expected_canonical = routes.OperatorControlCatalogV2.model_validate(raw['operator_control_catalog_v2']).model_dump(mode='json')
                assert body['canonical'] == expected_canonical
                assert any(a['informational_path'] == '/led/rgb' and a['inputs'] for a in body['actions'])
                assert body['canonical']['actions']
                assert calls == [
                    ('operator_control_catalog', {} if target is None else {'params': {'z_target_steps': target}}),
                    ('operator_control_catalog_v2', {'params': {'schema_version': 'bioxp.operator_control_catalog.v2'}, 'timeout_override': 12.0}),
                ]
                if target is not None:
                    assert body['dashboard']['z_axis']['provider']['target_preview']['requested_position_steps'] == target
                assert service.generation == generation
                assert all(lease.lease_count == 0 for lease in service._generation_leases.values())
                output = os.environ.get('BMS_CATALOG_EXPORT')
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    (Path(output) / f'catalog-{target}.json').write_text(json.dumps({'robot': raw, 'bms': body, 'calls': calls, 'physical_acceptance': False}, indent=2))
        await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('ending', ['replacement', 'legacy_error', 'canonical_error', 'timeout', 'cancel'])
def test_composed_queries_concurrency_generation_and_cleanup(tmp_path, ending):
    async def scenario():
        clients = []
        service = _service(tmp_path, clients)
        await service.save_profile(BioXpProfile(api_url='http://robot:8123'))
        generation = (await service.connect()).generation
        original = clients[0]
        entered = {name: asyncio.Event() for name in ['operator_control_catalog', 'operator_control_catalog_v2']}
        release = asyncio.Event()
        drained = set()
        # Use known schema doubles only for lifecycle faults, not producer proof.
        payloads = FakeConnection().client.responses
        async def request(name, **kw):
            entered[name].set()
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 2)
                if (ending == 'legacy_error' and not name.endswith('_v2')) or (ending == 'canonical_error' and name.endswith('_v2')):
                    raise RobotResponseError(409, {'detail': 'catalog conflict'})
                if ending == 'timeout' and name.endswith('_v2'):
                    raise RobotTimeoutError('offline query timeout', dispatched=False)
                await release.wait()
                return copy.deepcopy(payloads[name])
            finally:
                drained.add(name)
        original.request = request
        task = asyncio.create_task(routes.operator_control_catalog(z_target_steps=0, runtime=SimpleNamespace(connection=service)))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 2)
        disconnect = None
        if ending == 'replacement':
            disconnect = asyncio.create_task(service.disconnect())
            await asyncio.sleep(0)
            replacement = await service.connect()
            assert replacement.generation != generation
            assert not original.closed and not disconnect.done()
            release.set()
            result = await task
            assert result["canonical"] is not None
            await disconnect
            assert not clients[1].request_calls
        elif ending == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(task, 2)
            assert error.value.status_code == (504 if ending == 'timeout' else 409)
        assert drained == set(entered)
        assert all(lease.lease_count == 0 for lease in service._generation_leases.values())
        await service.close()
        assert original.closed
    asyncio.run(scenario())


@pytest.mark.parametrize('target', [-2147483649, 2147483648, 'not-an-integer'])
def test_invalid_target_never_reaches_transport(monkeypatch, target):
    client, runtime = make_client(monkeypatch)
    response = client.get('/api/bioxp/operator-controls/catalog', params={'z_target_steps': target})
    assert response.status_code == 422
    assert runtime.connection.client.calls == []
