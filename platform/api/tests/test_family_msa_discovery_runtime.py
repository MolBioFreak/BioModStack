import hashlib
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_model_discovery_and_schema():
    from routers.models import list_models, get_model, get_model_modes
    from model_registry import model_runtime_dependencies
    from services.family_msa.requests import request_schema, prepare_request
    listed = {item['id']: item for item in await list_models()}
    for model, molecule in [('family_msa_protein', 'protein'), ('family_msa_nucleic', 'dna')]:
        detail = await get_model(model)
        modes = await get_model_modes(model)
        assert listed[model]['modes'][0]['parameter_schema'] == request_schema()
        assert detail['modes'][0]['parameter_schema'] == modes['modes'][0]['parameter_schema'] == request_schema()
        assert molecule in detail['capabilities']['molecules']
        assert model_runtime_dependencies(model)[0].relative_path == 'msa_engines.sif'
        payload = {'roster': {'molecule': molecule, 'rows': []}, 'settings': {'threads': 2}}
        _, effective = prepare_request(payload)
        assert effective['_family_msa_requested'] == payload
        assert effective['settings']['threads'] == 2
        assert effective['settings']['strategy'] == 'fft_ns_2'
        assert prepare_request(effective)[1] == effective


def test_release_selection_and_host_fallback(tmp_path, monkeypatch):
    import paths
    from services.family_msa.runtime import select_runtime
    from services.remote_execution.images import image_environment_key
    from publish_runtime_images import publish_references
    root = tmp_path / 'store'
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(root))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_LANE', 'production')
    monkeypatch.delenv('BMS_FAMILY_MSA_SIF', raising=False)
    monkeypatch.setattr(paths, 'get_container_dir', lambda: tmp_path)
    assert select_runtime()[0] == ''
    source = tmp_path / 'fixture'
    source.write_bytes(b'inert image fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    selector = 'BMS_RUNTIME_IMAGE_' + image_environment_key('msa_engines.sif').removeprefix('BMS_SELECTED_IMAGE_')
    publish_references(root, 'production', {selector: {'source': str(source), 'sha256': digest}})
    image = root / 'objects' / 'sha256' / digest / 'runtime.sif'
    assert select_runtime() == (str(image), {'selection': 'managed', 'release_sha256': digest})
    monkeypatch.setenv('BMS_FAMILY_MSA_SIF', '/explicit/test.sif')
    assert select_runtime()[0] == '/explicit/test.sif'
