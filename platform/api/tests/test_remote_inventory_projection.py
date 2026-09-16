"""Optional inventory cannot poison listings or promote stale evidence."""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from database import ExecutionTarget, get_session
from routers.execution_targets import router
from test_remote_preloading import store


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['nonobject', 'missing', 'bad_artifact', 'bad_time',
    'utc_z', 'offset', 'expired', 'future', 'endpoint', 'operation', 'phase', 'inactive'])
async def test_inventory_get_and_list_are_defensive_read_only(store, case):
    now = datetime.now(timezone.utc)
    async with store() as session:
        target = await session.get(ExecutionTarget, 'vast:1')
        metadata = copy.deepcopy(target.provider_metadata)
        binding = (target.host, target.port, target.username, target.remote_root, target.host_key_sha256)
        metadata['preload'] = dict(operation_id='op', job_id=None,
            source_revision='a'*40, source_tree='b'*40, request_sha256='c'*64,
            phase='source_download_ready', message='verified download',
            started_at=now.isoformat(), updated_at=now.isoformat())
        raw = dict(operation_id='op', selection={'kind':'image','model_id':'protenix'},
            observed_at=now.isoformat(), artifacts=[dict(name='containers/protenix.sif',
            sha256='d'*64, size_bytes=1)], state='download_verified',
            endpoint_sha256=hashlib.sha256(json.dumps(binding).encode()).hexdigest())
        if case == 'nonobject': raw = ['bad']
        elif case == 'missing': raw = {'state': 'download_verified'}
        elif case == 'bad_artifact': raw['artifacts'][0]['name'] = '../escape'
        elif case == 'bad_time': raw['observed_at'] = 'not-a-date'
        elif case == 'utc_z': raw['observed_at'] = now.isoformat().replace('+00:00','Z')
        elif case == 'offset': raw['observed_at'] = now.astimezone(timezone(timedelta(hours=5))).isoformat()
        elif case == 'expired': raw['observed_at'] = (now-timedelta(days=1)).isoformat()
        elif case == 'future': raw['observed_at'] = (now+timedelta(days=1)).isoformat()
        elif case == 'endpoint': target.host = 'changed.invalid'
        elif case == 'operation': metadata['preload']['operation_id'] = 'successor'
        elif case == 'phase': metadata['preload']['phase'] = 'failed'
        elif case == 'inactive': target.active = False
        metadata['artifact_inventory'] = raw
        target.provider_metadata = metadata
        await session.commit()
        before = copy.deepcopy(target.provider_metadata)

    app = FastAPI()
    app.include_router(router, prefix='/execution-targets')
    async def sessions():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/execution-targets/vast:1/artifact-inventory')
        assert response.status_code == 200, response.text
        listing = await client.get('/execution-targets')
        assert listing.status_code == 200, listing.text
        observed = response.json()
        assert listing.json()[0]['artifact_inventory'] == observed
        if case in {'nonobject','missing','bad_artifact','bad_time'}:
            assert observed is None
        else:
            assert observed['state'] == ('download_verified' if case in {'utc_z','offset'} else 'stale')
    async with store() as session:
        assert (await session.get(ExecutionTarget,'vast:1')).provider_metadata == before
