"""Replay newly executed offline robot producers, never synthetic receipt fixtures.

Set PRODUCER_FINISH_OUTPUT to bms-producer-finish-robot.py's output directory.
"""
import json
import os
import time
import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse
from routers.bioxp import operator_controls as routes
from routers.bioxp.dependencies import get_bioxp_runtime
from services.bioxp.operator_models import OperatorActionReceiptDetailV2

@pytest.fixture
def exports():
    location = os.environ.get('PRODUCER_FINISH_OUTPUT')
    if not location:
        pytest.skip('requires newly executed isolated robot producer export')
    root = Path(location)
    return root, json.loads((root / 'producer.json').read_text())

class Replay:
    generation = 9
    def __init__(self, data, root):
        self.data, self.root = data, root
        self.calls = []
    async def query(self, name, **kw):
        assert kw['expected_generation'] == 9
        self.calls.append(name)
        if name == 'pipette_readback':
            return json.loads((self.root / 'readback.json').read_text())
        if name in ('operator_action_receipt_v2', 'operator_action_receipt'):
            cid = kw['path_params']['command_id']
            return next(row for row in self.data['receipts'] if row['detail']['command_id'] == cid)['detail' if name.endswith('_v2') else 'legacy']
        return self.data[{'operator_report_export_detail': 'metadata', 'operator_report_export_list': 'list', 'operator_report_export_create': 'create'}[name]]
    request_active_query = query
    request_active_v2_query = query
    request_active = query
    async def request_active_bytes(self, name, **kw):
        assert name == 'operator_report_export_download'
        content = (self.root / 'download.json').read_bytes()
        return SimpleNamespace(content=content, content_type='application/json', sha256=hashlib.sha256(content).hexdigest())

def client_for(data, root):
    replay = Replay(data, root)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_bioxp_runtime] = lambda: SimpleNamespace(connection=replay)
    return TestClient(app), replay

def test_actual_manual_and_pipette_source_receipt_is_lossless(exports):
    root, data = exports
    client, replay = client_for(data, root)
    assert len(data['receipts']) == 4
    for row in data['receipts']:
        assert row['detail']['source_receipt'] == row['legacy']
        cid = row['detail']['command_id']
        response = client.get('/operator-controls/v2/receipts/' + cid, params={'detail': True})
        assert response.status_code == 200, response.text
        # Untyped original source is preserved exactly through BMS/FastAPI.
        assert response.json()['source_receipt'] == row['legacy']
    assert replay.calls == ['operator_action_receipt_v2'] * 4
    assert any(r.path.endswith('/receipts/{command_id}/assessment') and 'POST' in r.methods for r in routes.router.routes)

def test_actual_export_metadata_is_not_redundant(exports):
    root, data = exports
    client, replay = client_for(data, root)
    eid = data['create']['export_id']
    unique = {'receipt', 'snapshot', 'filter', 'filter_sha256', 'completed_at'}
    assert unique <= data['metadata'].keys()
    assert not unique & data['create'].keys()
    assert not unique & data['list']['items'][0].keys()
    response = client.get('/operator-controls/reports/exports/' + eid)
    assert response.status_code == 200, response.text
    assert response.json()['receipt'] == data['metadata']['receipt']
    assert response.json()['filter'] == data['metadata']['filter']
    assert response.json()['receipt']['retention_deadline'] > response.json()['receipt']['created_at']
    for method, path in [('get', '/operator-controls/reports/exports'), ('post', '/operator-controls/reports/exports')]:
        res = getattr(client, method)(path, **({'json': {'format': 'json', 'limit': 100}} if method == 'post' else {}))
        assert res.status_code == 200, res.text
    download = client.get('/operator-controls/reports/exports/' + eid + '/download')
    assert download.status_code == 200
    assert download.content == (root / 'download.json').read_bytes()
    assert hashlib.sha256(download.content).hexdigest() == data['create']['sha256']

def test_actual_readback_route_conversion_measurement(exports, monkeypatch):
    import inspect
    from collections import Counter
    from pydantic import BaseModel
    root, data = exports
    client, replay = client_for(data, root)
    # Exact old post-validation conversion, wrapped around the real current
    # handler. The request/transport/envelope validation are identical.
    async def old_handler(*args, **kwargs):
        envelope = await routes.pipette_readback(*args, **kwargs)
        return routes._validate(routes.PipetteReadbackResponse, envelope.model_dump(include=set(routes.PipetteReadbackResponse.model_fields)))
    old_handler.__signature__ = inspect.signature(routes.pipette_readback, eval_str=True)
    client.app.add_api_route('/old-readback', old_handler, methods=['POST'], response_model=routes.PipetteReadbackResponse)
    counts = Counter()
    original_dump = BaseModel.model_dump
    original_validate = BaseModel.model_validate.__func__
    def dump(self, *a, **kw):
        counts['dump:' + type(self).__name__] += 1
        return original_dump(self, *a, **kw)
    def validate(cls, *a, **kw):
        counts['validate:' + cls.__name__] += 1
        return original_validate(cls, *a, **kw)
    monkeypatch.setattr(BaseModel, 'model_dump', dump)
    monkeypatch.setattr(BaseModel, 'model_validate', classmethod(validate))
    metrics, bodies = {}, {}
    for label, path in [('before', '/old-readback'), ('after', '/operator-controls/pipettes/readback')]:
        counts.clear()
        times = []
        for _ in range(50):
            start = time.perf_counter_ns()
            response = client.post(path, params={'expected_connection_generation': 9}, headers={'Idempotency-Key': 'offline-readback-finish'}, json={'include_data': False})
            times.append(time.perf_counter_ns() - start)
            assert response.status_code == 200, response.text
        bodies[label] = response.content
        metrics[label] = {'counts': dict(counts), 'iterations': 50, 'bytes': len(response.content), 'total_ns': sum(times), 'median_ns': sorted(times)[25]}
    assert bodies['before'] == bodies['after']
    assert metrics['before']['counts']['dump:PipetteReadbackPostEnvelope'] == 50
    assert metrics['before']['counts']['validate:PipetteReadbackResponse'] == 50
    assert metrics['after']['counts'].get('dump:PipetteReadbackPostEnvelope', 0) == 0
    assert metrics['after']['counts'].get('validate:PipetteReadbackResponse', 0) == 0
    metrics['scope'] = 'actual fresh FourPipetteTransport/service/SQLite readback output replayed through real BMS handler+FastAPI; host ASGI CPU microbenchmark, not hardware latency; before is reconstructed removed dump/validate step'
    (root / 'readback-serialization.json').write_text(json.dumps(metrics, indent=2))


def test_populated_real_receipt_encode_microbenchmark(exports, monkeypatch):
    root, data = exports
    raw = data['receipts'][0]['detail']
    model = OperatorActionReceiptDetailV2.model_validate(routes._normalize_interrupt_evidence(raw))
    # Controlled old conversion shape versus typed return. Both pass FastAPI's
    # actual response_model serializer; these are CPU microbenchmarks, not robot latency.
    counts = {'dump': 0, 'validate': 0}
    original_dump = OperatorActionReceiptDetailV2.model_dump
    original_validate = OperatorActionReceiptDetailV2.model_validate.__func__
    def dump(self, *a, **kw):
        counts['dump'] += 1
        return original_dump(self, *a, **kw)
    def validate(cls, *a, **kw):
        counts['validate'] += 1
        return original_validate(cls, *a, **kw)
    monkeypatch.setattr(OperatorActionReceiptDetailV2, 'model_dump', dump)
    monkeypatch.setattr(OperatorActionReceiptDetailV2, 'model_validate', classmethod(validate))
    app = FastAPI()
    @app.get('/typed', response_model=OperatorActionReceiptDetailV2)
    def typed():
        return model
    @app.get('/roundtrip', response_model=OperatorActionReceiptDetailV2)
    def roundtrip():
        return OperatorActionReceiptDetailV2.model_validate(model.model_dump(mode='json'))
    results = {}
    with TestClient(app) as client:
        wire = {}
        for variant in ('roundtrip', 'typed'):
            counts.update(dump=0, validate=0)
            start = time.perf_counter_ns()
            for _ in range(50):
                response = client.get('/' + variant)
                assert response.status_code == 200
            elapsed = time.perf_counter_ns() - start
            wire[variant] = response.content
            results[variant] = {**counts, 'iterations': 50, 'elapsed_ns': elapsed, 'bytes': len(response.content)}
    assert wire['typed'] == wire['roundtrip']
    assert results['roundtrip']['dump'] == results['roundtrip']['validate'] == 50
    assert results['typed']['dump'] == results['typed']['validate'] == 0
    results['scope'] = 'host CPU/ASGI microbenchmark on actual populated robot pipette-init receipt; NOT readback handler or hardware timing'
    (root / 'serialization.json').write_text(json.dumps(results, indent=2))
