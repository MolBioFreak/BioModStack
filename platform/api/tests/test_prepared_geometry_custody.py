"""Actual native positioned PDB through immutable HTTP custody; no live stores."""
import hashlib
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
from test_annotated_structure_capture import capture_api  # noqa: F401
from test_molecular_geometry import payload
from services.molecular_geometry import construct_geometry
from services.molecular_geometry_recipe import MolecularGeometryRequest


def test_native_prepared_geometry_exact_upload_readback_materialize(capture_api):
    assert os.environ.get('BMS_MOLECULAR_GEOMETRY_BUNDLE'), 'Native bundle required, not a skip'
    request = payload()
    result = construct_geometry(MolecularGeometryRequest.model_validate(request)).model_dump(mode='json')
    raw = result['coordinate_text'].encode()
    assert hashlib.sha256(raw).hexdigest() == result['coordinate_sha256']
    app, _ = capture_api
    with TestClient(app) as client:
        records = []
        for _ in range(2):
            response = client.post('/api/files/upload-immutable-structure', data={
                'path': 'inputs/protein_local_redesign', 'sha256': result['coordinate_sha256']},
                files={'file': (f"constructed-{result['coordinate_sha256']}.pdb", raw, 'chemical/x-pdb')})
            assert response.status_code == 200, response.text
            record = response.json()
            assert record['sha256'] == result['coordinate_sha256']
            assert record['size'] == len(raw)
            assert record['path'].endswith('.pdb')
            assert client.get('/api/files/download/' + record['path']).content == raw
            records.append(record)
        assert records[0]['path'] == records[1]['path']
        record = records[0]
        source = {'path': record['path'], 'expected_sha256': record['sha256'], 'output_format': 'native'}
        response = client.post('/api/files/materialize-structure', json=source)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt['native_format'] == receipt['format'] == 'pdb'
        assert receipt['sha256'] == result['coordinate_sha256']
        assert client.get('/api/files/download/' + receipt['path']).content == raw
    if os.environ.get('BMS_PREPARED_NATIVE_WIRE'):
        Path(os.environ['BMS_PREPARED_NATIVE_WIRE']).write_text(json.dumps({
            'schema_version': 'bms.molecular-construction-editor.v1', 'editor': request['construction'],
            'graph': result['graph'], 'geometry': {'choices': {k:v for k,v in request.items() if k != 'construction'}, 'request': request, 'result': result},
            'undo': [], 'complement': {}, 'imported_note': {'zero': 0, 'flag': False, 'empty': [], 'nil': None}}))
