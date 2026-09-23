"""BC2 saved-shape preview, pinned compilation, handoff and receipt readback."""
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.models import router
from database import get_session
from types import SimpleNamespace
from services import bindcraft2_launch as launch
from services.bindcraft2_native import compile_for_native, _canonical
import hashlib


def setup_source(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, 'get_results_dir', lambda: tmp_path)
    monkeypatch.setattr(launch, 'get_allowed_roots', lambda: {'bms_results': tmp_path})
    monkeypatch.setattr(launch, 'resolve_allowed_path', lambda name: tmp_path / name.removeprefix('bms_results/'))
    source = tmp_path / 'target.fasta'
    source.write_text('>target\n' + 'A' * 60 + '\n')
    settings = {'max_trajectories': 1, 'modality': ['binder'],
                'targets': [{'name': 'target', 'target_path': 'bms_results/target.fasta'}]}
    return source, settings


def stub_compiler(request, campaign):
    compiled = compile_for_native(request, campaign, lambda native: {**native, 'native_default': 7})
    compiled['requested_settings'] = request
    compiled['request_sha256'] = hashlib.sha256(_canonical(request)).hexdigest()
    return compiled


def test_preview_to_job_owned_native_handoff_and_readback(tmp_path, monkeypatch):
    source, settings = setup_source(tmp_path, monkeypatch)
    preview = launch.preview_campaign(settings, compiler=stub_compiler)
    assert not (tmp_path / '.bc2-preview').exists()
    assert preview['effective_settings']['native_default'] == 7
    materialized = launch.materialize_campaign(settings, tmp_path / 'job',
                                               preview_digest=preview['preview_digest'], compiler=stub_compiler)
    receipt = launch.read_campaign_receipt(tmp_path / 'job')
    assert receipt['effective_sha256'] == materialized['bc2_effective_sha256']
    assert receipt['requested_settings']['targets'][0]['target_path'] == 'bms_results/target.fasta'
    assert json.loads(Path(materialized['bc2_compilation']).read_text())['native_request']['targets'][0]['target_path'] == str(tmp_path / 'job/bindcraft2/sources/target_0.fasta')
    assert (tmp_path / 'job/bindcraft2/sources/target_0.fasta').read_bytes() == source.read_bytes()
    assert materialized['bc2_campaign_dir'] == str(tmp_path / 'job/bindcraft2')
    source.write_text('>changed\nAAAAAAAA\n')
    with pytest.raises(ValueError, match='stale'):
        launch.materialize_campaign(settings, tmp_path / 'other', preview_digest=preview['preview_digest'], compiler=stub_compiler)
    with pytest.raises(ValueError, match='digest'):
        receipt_path = Path(materialized['bc2_compilation'])
        payload = json.loads(receipt_path.read_text())
        payload['effective_sha256'] = '0' * 64
        receipt_path.write_text(json.dumps(payload))
        launch.read_campaign_receipt(tmp_path / 'job')


def test_preview_route_rejects_wrong_saved_shape(tmp_path, monkeypatch):
    setup_source(tmp_path, monkeypatch)
    monkeypatch.setattr(launch, 'preview_campaign', lambda settings: {'settings': settings})
    app = FastAPI()
    app.include_router(router, prefix='/models')
    client = TestClient(app)
    request = {'model_id': 'bindcraft2', 'mode': 'campaign', 'params': {'bindcraft2_settings': {'max_trajectories': 1}}}
    assert client.post('/models/bindcraft2/campaign/preview', json=request).json() == {'settings': {'max_trajectories': 1}}
    for altered in ({**request, 'mode': 'design'}, {**request, 'params': {'settings': {}}}):
        assert client.post('/models/bindcraft2/campaign/preview', json=altered).status_code == 422


def test_settings_readback_route_uses_exact_job_receipt(tmp_path, monkeypatch):
    _, settings = setup_source(tmp_path, monkeypatch)
    preview = launch.preview_campaign(settings, compiler=stub_compiler)
    launch.materialize_campaign(settings, tmp_path / 'job', preview_digest=preview['preview_digest'], compiler=stub_compiler)
    job = SimpleNamespace(model_id='bindcraft2', mode='campaign', output_dir=str(tmp_path / 'job'), params={'bindcraft2_settings': settings})
    class Session:
        async def get(self, model, ident):
            return job if ident == 'test-job' else None
    app = FastAPI()
    app.include_router(router, prefix='/models')
    app.dependency_overrides[get_session] = lambda: Session()
    client = TestClient(app)
    url = '/models/bindcraft2/campaign/jobs/test-job/settings'
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()['requested_settings'] == settings
    assert response.json()['effective_sha256'] == launch.read_campaign_receipt(tmp_path / 'job')['effective_sha256']
    job.params = {'bindcraft2_settings': {'max_trajectories': 99}}
    assert client.get(url).status_code == 409


def test_actual_image_cpu_compilation_and_native_handoff(tmp_path, monkeypatch):
    if not launch.IMAGE.is_file():
        pytest.skip('pinned BC2 image is unavailable')
    source, settings = setup_source(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(router, prefix='/models')
    route_preview = TestClient(app).post('/models/bindcraft2/campaign/preview', json={
        'model_id': 'bindcraft2', 'mode': 'campaign', 'params': {'bindcraft2_settings': settings},
    })
    assert route_preview.status_code == 200, route_preview.text
    preview = launch.preview_campaign(settings)
    assert route_preview.json()['preview_digest'] == preview['preview_digest']
    handoff = launch.materialize_campaign(settings, tmp_path / 'job', preview_digest=preview['preview_digest'])
    assert launch.read_campaign_receipt(tmp_path / 'job')['effective_sha256'] == handoff['bc2_effective_sha256']
    assert (tmp_path / 'job/bindcraft2/sources/target_0.fasta').read_bytes() == source.read_bytes()
    root = Path(__file__).resolve().parents[3]
    subprocess.run(['apptainer', 'exec', '--bind', f'{root}:{root}', '--bind', f'{tmp_path}:{tmp_path}',
                    str(launch.IMAGE), 'python3', str(root / 'scripts/run_bindcraft2_campaign.py'),
                    handoff['bc2_compilation'], handoff['bc2_campaign_dir'], '--native-source', '/opt/bindcraft'],
                   check=True, capture_output=True, text=True, timeout=120)
    assert (tmp_path / 'job/bindcraft2/native_settings.json').is_file()
    assert launch.read_campaign_receipt(tmp_path / 'job')['effective_sha256'] == handoff['bc2_effective_sha256']
