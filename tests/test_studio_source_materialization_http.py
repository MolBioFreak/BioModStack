"""Exact source owner -> real private Studio -> immutable frozen receiving.

Synthetic single-residue coordinates only; no inference or managed services.
Run with STUDIO_TEST_PYTHON pointing at the qualified isolated Studio venv.
"""
import hashlib
import io
import json
from pathlib import Path


import pytest
from Bio.PDB.MMCIF2Dict import MMCIF2Dict
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_atomworks_studio_http import BASE, client, sidecar
from database import get_session
from routers import files



def pdb_fixture():
    header = ('HEADER    SYNTHETIC SOURCE CUSTODY TEST\n'
              'SEQRES   1 A    1  ALA\n'
              'REMARK 350 BIOMOLECULE: 1\n'
              'REMARK 350 APPLY THE FOLLOWING TO CHAINS: A\n'
              'REMARK 350   BIOMT1   1  1.000000  0.000000  0.000000       50.00000\n'
              'REMARK 350   BIOMT2   1  0.000000  1.000000  0.000000        0.00000\n'
              'REMARK 350   BIOMT3   1  0.000000  0.000000  1.000000        0.00000\n')
    frames = []
    for model, shift in [(7, 0), (42, 20)]:
        atoms = [('N', 'N', 0, 0, 0, ' '), ('CA', 'C', 1.45, 0, 0, ' '),
                 ('C', 'C', 2, 1.4, 0, ' '), ('O', 'O', 1.4, 2.4, 0, ' '),
                 ('CB', 'C', 1.8, -.7, 1.2, 'A'), ('CB', 'C', 8.8, -.7, 1.2, 'B')]
        frame = f'MODEL     {model:4d}\n'
        for serial, (name, element, x, y, z, alt) in enumerate(atoms, 1):
            frame += (f'ATOM  {serial:5d} {name:^4s}{alt}ALA A  11B   '
                      f'{x + shift:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {element:>2s}  \n')
        frames.append(frame + 'TER\nENDMDL\n')
    return (header + ''.join(frames) + 'END\n').encode()


@pytest.fixture
def custody(tmp_path, monkeypatch):
    import paths
    roots = {'inputs': tmp_path / 'inputs'}
    roots['inputs'].mkdir()
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: roots)
    app = FastAPI()
    app.include_router(files.router, prefix='/api/files')
    async def session():
        yield None  # governed path source, never a database or live producer
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[files.get_governed_ngs_result_roots] = lambda: ()
    with TestClient(app) as api:
        yield api, roots['inputs']


def download(api, path):
    response = api.get('/api/files/download/' + path)
    assert response.status_code == 200, response.text
    return response.content


def materialize(api, source):
    response = api.post('/api/files/materialize-structure', json=source)
    assert response.status_code == 200, response.text
    result = response.json()
    raw = download(api, result['path'])
    assert hashlib.sha256(raw).hexdigest() == result['sha256']
    return result, raw


@pytest.mark.parametrize('model', [7, 42])
def test_selected_pdb_cif_private_studio_exact_frozen_custody(client, custody, model):
    api, inputs = custody
    raw = pdb_fixture()
    (inputs / 'source.pdb').write_bytes(raw)
    source = {'path': 'inputs/source.pdb', 'model_number': model,
              'expected_sha256': hashlib.sha256(raw).hexdigest(), 'output_format': 'cif'}
    receipt, derived = materialize(api, source)
    assert receipt['model_numbers'] == [7, 42] and receipt['model_number'] == model
    assert receipt['format'] == 'cif' and receipt['native_format'] == 'pdb'
    assert receipt['original_sha256'] == source['expected_sha256'] != receipt['sha256']
    assert download(api, receipt['original_path']) == raw
    assert download(api, receipt['native_path']) == raw
    assert receipt['selected_path'].endswith(f'model-{model}.pdb')
    assert receipt['path'].endswith('derived.cif')
    columns = MMCIF2Dict(io.StringIO(derived.decode()))
    assert set(columns['_atom_site.pdbx_PDB_model_num']) == {str(model)}
    assert set(columns['_atom_site.auth_seq_id']) == {'11'}
    assert set(columns['_atom_site.pdbx_PDB_ins_code']) == {'B'}
    assert columns['_atom_site.label_alt_id'][-2:] == ['A', 'B']
    assert columns['_entity_poly_seq.mon_id'] == ['ALA']
    assert columns['_pdbx_struct_oper_list.vector[1]'] == ['50']
    assert len(columns['_atom_site.id']) == 6  # only the selected frame, both altlocs
    replay, replay_bytes = materialize(api, receipt['source_structure'])
    assert replay_bytes == derived and replay['sha256'] == receipt['sha256']
    # Exactly the mounted import options. Assembly is the materialized source,
    # not an implicit assembly-1 expansion; altloc is Studio's existing first.
    options = {'parse_preset': 'minimal', 'assembly': 'asym', 'model': '1',
               'advanced_options': json.dumps({'load_standard_annotations': True, 'altloc': 'first'}, separators=(',', ':'))}
    client.headers['x-test-principal'] = 'source-custody'
    imported = client.post(BASE + '/api/v1/sessions', files={'file': ('source.cif', derived)}, data=options)
    assert imported.status_code == 200, imported.text
    session = BASE + '/api/v1/sessions/' + imported.json()['session_id']
    changed = client.post(session + '/conditions', json={'condition': 'hotspot', 'atom_indices': [0], 'scope': 'atom', 'enabled': True})
    assert changed.status_code == 200, changed.text
    annotation = client.get(session + '/annotations/hotspot')
    assert annotation.status_code == 200, annotation.text
    response = client.post(session + '/export')
    assert response.status_code == 200, response.text
    frozen = response.json()
    frozen_bytes = frozen['cif'].encode()
    exported = MMCIF2Dict(io.StringIO(frozen['cif']))
    assert set(exported['_atom_site.pdbx_PDB_model_num']) == {'1'}  # distinct derivative identity
    xs = [float(x) for x in exported['_atom_site.Cartn_x']]
    assert xs == pytest.approx([0, 1.45, 2, 1.4, 1.8] if model == 7 else [20, 21.45, 22, 21.4, 21.8])
    assert len(xs) == 5  # selected A, not B; no assembly translation or other MODEL
    digest = hashlib.sha256(frozen_bytes).hexdigest()
    uploaded_paths = []
    for existing in (False, True):
        upload = api.post('/api/files/upload-immutable-structure',
            data={'path': 'inputs/annotated_structures', 'sha256': digest},
            files={'file': (f'annotated-{digest}.cif', frozen_bytes)})
        assert upload.status_code == 200, upload.text
        published = upload.json()
        assert published['sha256'] == digest and published['existing'] is existing
        assert download(api, published['path']) == frozen_bytes
        uploaded_paths.append(published['path'])
    assert uploaded_paths[0] == uploaded_paths[1]
    assert client.delete(session).status_code == 204
    # Cold immutable readback doesn't ask an expired session for another export.
    cold, cold_bytes = materialize(api, {'path': uploaded_paths[0], 'expected_sha256': digest})
    assert cold_bytes == frozen_bytes and cold['model_numbers'] == [1]
    assert b'hotspot' in cold_bytes
    reopened = client.post(BASE + '/api/v1/sessions', files={'file': ('saved.cif', cold_bytes)}, data=options)
    assert reopened.status_code == 200, reopened.text
    reopened_path = BASE + '/api/v1/sessions/' + reopened.json()['session_id']
    saved_annotation = client.get(reopened_path + '/annotations/hotspot')
    assert saved_annotation.status_code == 200, saved_annotation.text
    assert saved_annotation.json() == annotation.json()
    assert client.delete(reopened_path).status_code == 204
    assert (inputs / 'source.pdb').read_bytes() == raw
    (inputs.parent / 'source-custody-evidence.json').write_text(json.dumps({
        'request': source, 'materialization': receipt, 'studio_import_options': options,
        'frozen_sha256': digest, 'frozen_size': len(frozen_bytes),
        'frozen_source': {'path': uploaded_paths[0], 'expected_sha256': digest, 'model_number': 1},
        'exported_x': xs, 'saved_annotation': saved_annotation.json(),
        'replayed_materialization_sha256': replay['sha256'],
        'cold_sha256': cold['sha256'], 'cold_models': cold['model_numbers'],
    }, indent=2))


def test_pdb_implicit_model_zero_and_unselected_ensemble(custody):
    api, inputs = custody
    raw = pdb_fixture()
    (inputs / 'models.pdb').write_bytes(raw)
    all_models, data = materialize(api, {'path': 'inputs/models.pdb', 'output_format': 'cif'})
    assert all_models['model_number'] is None
    assert set(MMCIF2Dict(io.StringIO(data.decode()))['_atom_site.pdbx_PDB_model_num']) == {'7', '42'}
    implicit = raw.split(b'MODEL        7\n')[1].split(b'ENDMDL')[0] + b'END\n'
    (inputs / 'implicit.pdb').write_bytes(implicit)
    result, data = materialize(api, {'path': 'inputs/implicit.pdb', 'model_number': 0, 'output_format': 'cif'})
    assert result['model_numbers'] == [0]
    assert set(MMCIF2Dict(io.StringIO(data.decode()))['_atom_site.pdbx_PDB_model_num']) == {'0'}


def test_selected_saved_native_conditions_survive_real_private_import(client, custody):
    from Bio.PDB import MMCIFIO
    api, inputs = custody
    # Source-free upstream segment specimen: no molecular sequence or target.
    fixture = Path(__file__).resolve().parents[1] / 'platform/frontend/tests/fixtures/rfd4-saved-conditions.cif'
    columns = MMCIF2Dict(str(fixture))
    for key, values in list(columns.items()):
        if key.startswith('_atom_site.'):
            columns[key] = values * 2
    columns['_atom_site.id'] = ['101', '201']
    columns['_atom_site.pdbx_PDB_model_num'] = ['7', '42']
    for category in ('_condition_coordinate_1_atom', '_condition_InternalCoordinate_1_atom'):
        for key, values in list(columns.items()):
            if key.startswith(category + '.'):
                columns[key] = values * 2
        columns[category + '.model_num'] = ['7', '42']
    columns['_condition_InternalCoordinate_1_atom.val0'] = ['7000', '42000']
    writer = MMCIFIO(); writer.set_dict(columns)
    text = io.StringIO(); writer.save(text)
    raw = text.getvalue().encode(); (inputs / 'saved.cif').write_bytes(raw)
    receipt, selected = materialize(api, {'path': 'inputs/saved.cif', 'model_number': 42, 'output_format': 'cif'})
    observed = MMCIF2Dict(io.StringIO(selected.decode()))
    assert observed['_atom_site.id'] == ['201']
    assert observed['_condition_InternalCoordinate_1_atom.model_num'] == ['42']
    assert observed['_condition_InternalCoordinate_1_atom.idx0'] == ['0']
    assert observed['_condition_InternalCoordinate_1_atom.val0'] == ['42000']
    assert download(api, receipt['original_path']) == raw
    client.headers['x-test-principal'] = 'saved-native-conditions'
    imported = client.post(BASE + '/api/v1/sessions', files={'file': ('selected.cif', selected)},
        data={'parse_preset': 'minimal', 'assembly': 'asym', 'model': '1',
              'advanced_options': json.dumps({'load_standard_annotations': True, 'altloc': 'first'})})
    assert imported.status_code == 200, imported.text
    session = BASE + '/api/v1/sessions/' + imported.json()['session_id']
    exported = client.post(session + '/export')
    assert exported.status_code == 200, exported.text
    native = MMCIF2Dict(io.StringIO(exported.json()['cif']))
    assert set(native['_atom_site.pdbx_PDB_model_num']) == {'1'}
    assert [float(v) for v in native['_condition_InternalCoordinate_1_atom.val0']] == [42000]
    assert native['_atom_site.condition_CoordinateGroup_1_atom'] == ['7']
    assert native['_atom_site.condition_sequence_1_residue'] == ['<M>']
    assert client.delete(session).status_code == 204


def test_selected_annotated_cif_and_bcif_do_not_decode_full_source(custody):
    from biotite.structure.io.pdbx import BinaryCIFFile, BinaryCIFBlock, BinaryCIFCategory
    api, inputs = custody
    (inputs / 'base.pdb').write_bytes(pdb_fixture())
    _, converted = materialize(api, {'path': 'inputs/base.pdb', 'output_format': 'cif'})
    columns = MMCIF2Dict(io.StringIO(converted.decode()))
    columns['_annotation_coordinate_1_atom.model_num'] = ['7', '42']
    columns['_annotation_coordinate_1_atom.idx0'] = ['0', '0']
    for axis in range(3):
        columns[f'_annotation_coordinate_1_atom.val{axis}'] = [str(700 + axis), str(4200 + axis)]
    columns['_custom_source.note'] = ['retained opaque annotation']
    import gemmi
    text_document = gemmi.cif.Document()
    block = text_document.add_new_block('annotated')
    for key, values in columns.items():
        if key != 'data_':
            loop = block.init_loop('', [key])
            for value in values:
                loop.add_row([gemmi.cif.quote(value)])
    text = text_document.as_string()
    categories = {}
    for key, values in columns.items():
        if key == 'data_': continue
        category, field = key[1:].split('.', 1)
        categories.setdefault(category, {})[field] = values
    binary = BinaryCIFFile({'source': BinaryCIFBlock({k: BinaryCIFCategory(v) for k, v in categories.items()})})
    buf = io.BytesIO(); binary.write(buf)
    for suffix, raw in [('cif', text.encode()), ('bcif', buf.getvalue())]:
        (inputs / f'annotated.{suffix}').write_bytes(raw)
        result, selected = materialize(api, {'path': f'inputs/annotated.{suffix}', 'output_format': 'cif', 'model_number': 42})
        assert download(api, result['original_path']) == raw
        observed = MMCIF2Dict(io.StringIO(selected.decode()))
        assert set(observed['_atom_site.pdbx_PDB_model_num']) == {'42'}
        assert len(observed['_atom_site.id']) == 6
        assert observed['_annotation_coordinate_1_atom.val0'] == ['4200']
        assert observed['_annotation_coordinate_1_atom.idx0'] == ['0']
        assert observed['_custom_source.note'] == ['retained opaque annotation']
        assert observed['_pdbx_struct_oper_list.vector[1]'] == ['50']
