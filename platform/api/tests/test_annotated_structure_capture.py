"""Real immutable upload/readback for editor exports; no editor or model doubles."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from Bio.PDB import MMCIFIO, PDBParser
from Bio.PDB.MMCIF2Dict import MMCIF2Dict

API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))
sys.path.insert(0, str(API.parent.parent))
from database import get_session
from routers import files
import paths


def annotated_specimen():
    # Deliberately different saved-condition coordinates and atom coordinates.
    pdb = 'ATOM      1  CA  ALA A  42B      1.000   2.000   3.000  1.00 20.00           C  \nEND\n'
    writer = MMCIFIO()
    writer.set_structure(PDBParser(QUIET=True).get_structure('specimen', io.StringIO(pdb)))
    out = io.StringIO()
    writer.save(out)
    columns = MMCIF2Dict(io.StringIO(out.getvalue()))
    for key, values in list(columns.items()):
        if key.startswith('_atom_site.'):
            columns[key] = values * 2
    columns['_atom_site.id'] = ['1', '2']
    columns['_atom_site.pdbx_PDB_model_num'] = ['3', '7']
    columns['_atom_site.auth_asym_id'] = ['long_author', 'long_author']
    columns['_atom_site.Cartn_x'] = ['3.0', '7.0']
    # Unknown supplier categories must survive custody too; not a native model fixture.
    columns['_supplier_condition.idx0'] = ['0', '0']
    columns['_supplier_condition.model_num'] = ['3', '7']
    columns['_supplier_condition.val0'] = ['30.0', '70.0']
    writer.set_dict(columns)
    out = io.StringIO()
    writer.save(out)
    return out.getvalue().encode()


@pytest.fixture
def capture_api(tmp_path, monkeypatch):
    roots = {'inputs': tmp_path / 'inputs', 'bms_results': tmp_path / 'results'}
    for root in roots.values():
        root.mkdir()
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: roots)
    app = FastAPI()
    app.include_router(files.router, prefix='/api/files')
    app.dependency_overrides[files.get_governed_ngs_result_roots] = lambda: ()
    async def no_database():
        yield None
    app.dependency_overrides[get_session] = no_database
    return app, roots['inputs']


def upload(client, raw, *, suffix='cif', digest=None, directory='inputs/annotated_structures'):
    digest = digest or hashlib.sha256(raw).hexdigest()
    return client.post('/api/files/upload-immutable-structure',
        data={'path': directory, 'sha256': digest},
        files={'file': (f'edited-{digest}.{suffix}', raw, 'chemical/x-mmcif')})


@pytest.mark.parametrize('suffix', ['cif', 'mmcif'])
def test_exact_annotated_export_concurrent_publish_and_cold_reopen(capture_api, suffix):
    app, root = capture_api
    raw = annotated_specimen()
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: upload(client, raw, suffix=suffix), range(8)))
        assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
        records = [response.json() for response in responses]
        assert len({record['path'] for record in records}) == 1
        assert sum(not record['existing'] for record in records) == 1
        record = records[0]
        assert record['sha256'] == hashlib.sha256(raw).hexdigest()
        assert record['size'] == len(raw)
        assert (root / 'annotated_structures' / record['filename']).read_bytes() == raw
        assert list((root / 'annotated_structures').iterdir()) == [root / 'annotated_structures' / record['filename']]
    # Fresh client has no editor session and reads only managed bytes.
    with TestClient(app) as cold:
        assert cold.get('/api/files/download/' + record['path']).content == raw
        materialized = cold.post('/api/files/materialize-structure', json={
            'path': record['path'], 'expected_sha256': record['sha256'],
            'model_number': 7, 'output_format': 'native'})
        assert materialized.status_code == 200, materialized.text
        receipt = materialized.json()
        assert receipt['original_sha256'] == record['sha256']
        assert receipt['model_numbers'] == [3, 7]
        assert receipt['model_number'] == 7
        assert {row['auth_asym_id'] for row in receipt['source_positions']} == {'long_author'}
        assert {row['model_number'] for row in receipt['source_positions']} == {7}
        selected = cold.get('/api/files/download/' + receipt['path'])
        assert selected.status_code == 200
        assert b'_supplier_condition' in selected.content and b'70.0' in selected.content
        assert cold.get('/api/files/download/' + receipt['original_path']).content == raw


def test_capture_digest_corruption_and_generic_overwrite_do_not_change_original(capture_api):
    app, root = capture_api
    raw = annotated_specimen()
    with TestClient(app) as client:
        record = upload(client, raw).json()
        wrong = upload(client, raw, digest='0' * 64)
        assert wrong.status_code == 400 and 'mismatch' in wrong.text
        generic = client.post('/api/files/upload', data={'path': 'inputs/annotated_structures'},
            files={'file': (record['filename'], b'changed', 'chemical/x-mmcif')})
        assert generic.status_code == 403
        assert client.get('/api/files/download/' + record['path']).content == raw
        destination = root / 'annotated_structures' / record['filename']
        destination.chmod(0o644)
        destination.write_bytes(b'corrupt')
        conflict = upload(client, raw)
        assert conflict.status_code == 409
        assert destination.read_bytes() == b'corrupt'


def test_cif_capture_does_not_rename_to_pdb_or_require_inspection(capture_api):
    app, _ = capture_api
    # Unrecognized annotations may lack an atom ledger; custody remains usable.
    raw = b'data_unknown\n_supplier.annotation "preserve exactly"\n'
    with TestClient(app) as client:
        response = upload(client, raw)
        assert response.status_code == 200, response.text
        record = response.json()
        materialized = client.post('/api/files/materialize-structure', json={
            'path': record['path'], 'expected_sha256': record['sha256']})
        assert materialized.status_code == 200, materialized.text
        assert materialized.json()['inspection_error']
        assert client.get('/api/files/download/' + materialized.json()['path']).content == raw
        wrong_directory = upload(client, raw, directory='inputs/protein_local_redesign')
        assert wrong_directory.status_code == 400
        pdb_in_cif_directory = upload(client, raw, suffix='pdb')
        assert pdb_in_cif_directory.status_code == 400
