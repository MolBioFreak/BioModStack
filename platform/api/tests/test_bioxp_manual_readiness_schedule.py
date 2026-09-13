"""Existing single refresh loop consumes source budgets; no motion submission."""
import asyncio
import copy
import pytest
from tests.test_bioxp_connection import _load, _service


def payload(age=6, **refresh):
    return {'runtime_ready':True, 'available':True, 'cache_state':'fresh',
            'freshness':{'state':'fresh','age_s':age,'fresh_for_s':30},
            'capabilities':['collect_hardware_snapshot'],
            'automatic_snapshot_refresh':refresh}


@pytest.mark.parametrize('observation,expected', [
    (payload(),9.0),
    (payload(14),1.0),
    (payload(0),15.0),
    (payload(40, attempted=True,published=False,retry_deferred=True,retry_after_s=0.5,reason='operator_action_pending'),0.5),
    (payload(40, attempted=False,published=False,retry_deferred=True,retry_after_s=4),4.0),
    (payload(40, attempted=True,published=False,error='transport timeout'),20.0),
])
def test_existing_refresh_once_returns_remaining_budget(tmp_path, observation, expected):
    _,Profile,_,_ = _load()
    clients=[]
    async def scenario():
        service=_service(tmp_path,clients,probe_result=observation)
        await service.save_profile(Profile(api_url='http://robot:8123'))
        await service.connect()
        service.snapshot_refresh_interval_seconds=20
        try:
            delay=await service._snapshot_refresh_once()
            assert delay == expected
            assert clients[0].probes == 1
            assert clients[0].request_calls == []
        finally:
            await service.disconnect()
    asyncio.run(scenario())


def test_loop_honors_deferral_then_populated_collection_duration(tmp_path, monkeypatch):
    _,Profile,_,_ = _load()
    clients=[]
    async def scenario():
        service=_service(tmp_path,clients,probe_result=payload())
        await service.save_profile(Profile(api_url='http://robot:8123'))
        await service.connect()
        service.snapshot_refresh_interval_seconds=20
        sleeps=[]
        rows=[payload(40,attempted=True,published=False,retry_deferred=True,retry_after_s=0.5,
                      reason='operator_action_pending'),payload(6,attempted=True,published=True,snapshot_id='after-door')]
        async def probe():
            clients[0].probes+=1
            return copy.deepcopy(rows[clients[0].probes-1])
        clients[0].probe=probe
        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps)==3: raise asyncio.CancelledError
        try:
            with monkeypatch.context() as m:
                m.setattr(asyncio,'sleep',sleep)
                with pytest.raises(asyncio.CancelledError):
                    await service._snapshot_refresh_loop()
            assert sleeps == [20,0.5,9]
            assert clients[0].probes == 2
            assert clients[0].request_calls == []
        finally:
            await service.disconnect()
    asyncio.run(scenario())
