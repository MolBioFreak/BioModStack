"""Actual source export receiving, with inert query transport only."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.bioxp.operator_controls import router
from routers.bioxp.dependencies import get_bioxp_runtime


def test_update_relay_preserves_all_source_bytes_and_selectors():
    path = os.environ.get('BIOXP_CATALOG_UPDATE_EXPORT')
    if not path:
        pytest.skip('actual source update export required')
    export = json.loads(Path(path).read_text())
    calls = []
    body = export['cold']
    async def query(route, **kwargs):
        calls.append((route, kwargs))
        return copy.deepcopy(body['canonical'] if route.endswith('_v2') else {k:v for k,v in body.items() if k != 'canonical'})
    runtime = SimpleNamespace(connection=SimpleNamespace(generation=7, request_active_query=query, request_active_v2_query=query))
    app = FastAPI()
    app.include_router(router, prefix='/api/bioxp')
    app.dependency_overrides[get_bioxp_runtime] = lambda: runtime
    with TestClient(app) as client:
        for body in [export['cold'], *export['windows']['idle']['responses'], *export['windows']['changing']['responses']]:
            response = client.get('/api/bioxp/operator-controls/catalog', params={
                'view': 'assessment', 'assessment_base': export['cold']['assessment_revision'],
                'canonical_assessment_base': export['cold']['canonical']['assessment_revision'], 'z_target_steps': -2147483648})
            assert response.status_code == 200, response.text
            assert response.json() == body
            assert calls[-2][1]['params']['assessment_base'] == export['cold']['assessment_revision']
            assert calls[-2][1]['params']['z_target_steps'] == -2147483648
            assert calls[-1][1]['params'] == {'view':'assessment', 'schema_version':'bioxp.operator_control_catalog.v2', 'assessment_base':export['cold']['canonical']['assessment_revision']}
            assert all(kwargs['expected_generation'] == 7 for _, kwargs in calls[-2:])
        body = export['windows']['idle']['expected'][0]
        response = client.get('/api/bioxp/operator-controls/catalog', params={'view':'assessment'})
        assert response.json() == body  # Existing callers keep the prior contract.
        response = client.get('/api/bioxp/operator-controls/catalog', params={'view':'assessment', 'assessment_base':'', 'canonical_assessment_base':''})
        assert response.status_code == 426  # Never periodic old-peer bulk fallback.
