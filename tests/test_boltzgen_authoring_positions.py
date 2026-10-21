"""Authoring-only residue maps, qualified against installed native parsers."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import gemmi
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('authoring_positions', ROOT / 'platform/api/services/boltzgen_source_positions.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sources(directory):
    lines = []
    for index, (number, insertion) in enumerate([(5, ''), (19, ''), (19, 'B'), (42, '')]):
        for j, name in enumerate(('N', 'CA', 'C', 'O')):
            lines.append(f'ATOM  {len(lines)+1:5d} {name:^4} GLY A{number:4d}{insertion:1}   '
                         f'{float(index*4+j):8.3f}{float(j%2):8.3f}{0.:8.3f}{1.:6.2f}{20.:6.2f}          {name[0]:>2}  ')
    pdb = directory / 'observed.pdb'
    pdb.write_text('\n'.join(lines) + '\nTER\nEND\n')
    structure = gemmi.read_structure(str(pdb))
    structure.setup_entities()
    for entity in structure.entities:
        entity.full_sequence = ['GLY'] * 5
    for residue, label in zip(structure[0][0], [1, 2, 4, 5]):
        residue.label_seq = label
    cif = directory / 'missing.cif'
    structure.make_mmcif_document().write_file(str(cif))
    return pdb, cif


@pytest.mark.parametrize('kind,positions', [(0, [1, 2, 3, 4]), (1, [1, 2, 4, 5])])
def test_author_insertions_and_gaps_keep_native_positions(tmp_path, kind, positions):
    path = sources(tmp_path)[kind]
    result = MODULE.boltzgen_source_positions(path)
    assert result['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert [r['native_position'] for r in result['positions']] == positions
    assert [(r['auth_seq_id'], r['insertion_code']) for r in result['positions']] == [(5, ''), (19, ''), (19, 'B'), (42, '')]


def test_multimodel_is_not_silently_first_model(tmp_path):
    path = sources(tmp_path)[0]
    body = path.read_text().replace('END\n', '')
    path.write_text('MODEL        1\n' + body + 'ENDMDL\nMODEL        2\n' + body + 'ENDMDL\nEND\n')
    with pytest.raises(ValueError, match='single-model'):
        MODULE.boltzgen_source_positions(path)


NATIVE = r'''
import json, sys
from pathlib import Path
from rdkit import Chem
from boltzgen.data.parse.pdb_parser import parse_pdb
from boltzgen.data.parse.mmcif import parse_mmcif
mol = Chem.MolFromSmiles('NCC(=O)O')
for atom, name in zip(mol.GetAtoms(), ['N','CA','C','O','OXT']): atom.SetProp('name', name)
root = Path(sys.argv[1])
for filename in ['observed.pdb', 'missing.cif']:
    parser = parse_pdb if filename.endswith('.pdb') else parse_mmcif
    native = parser(root / filename, mols={'GLY': mol}, moldir=root, use_assembly=False).data
    mapped = json.loads((root / (filename+'.json')).read_text())['positions']
    actual = []
    for chain in native.chains:
        for residue in native.residues[chain['res_idx']:chain['res_idx']+chain['res_num']]:
            if residue['is_present']:
                actual.append((str(chain['name']), int(residue['res_idx'])+1))
    assert actual == [(r['native_chain'], r['native_position']) for r in mapped], (actual,mapped)
print('Pinned native PDB insertion and mmCIF gap maps agree; no model inference.')
'''


def test_source_positions_http_uses_governed_exact_path(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / 'platform/api'))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.boltzgen import router
    import paths
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': tmp_path})
    path = sources(tmp_path)[0]
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post('/api/boltzgen/source-positions', json={'path': f'inputs/{path.name}'})
        assert response.status_code == 200
        assert response.json()['positions'] == MODULE.boltzgen_source_positions(path)['positions']
        assert client.post('/api/boltzgen/source-positions', json={'path': 'inputs/missing.pdb'}).status_code == 404
        assert client.post('/api/boltzgen/source-positions', json={'path': 'inputs/../../private.pdb'}).status_code == 403
        assert client.post('/api/boltzgen/source-positions', json={'path': 'inputs/missing.pdb', 'fallback': 'primary'}).status_code == 422


def test_installed_native_parser_agreement(tmp_path):
    image = os.environ.get('BMS_TEST_BOLTZGEN_IMAGE')
    if not image:
        pytest.skip('Set BMS_TEST_BOLTZGEN_IMAGE to qualify the installed native parser')
    for path in sources(tmp_path):
        path.with_name(path.name + '.json').write_text(json.dumps(MODULE.boltzgen_source_positions(path)))
    completed = subprocess.run(['apptainer', 'exec', '--containall', '--bind', f'{tmp_path}:/probe', image, 'python', '-c', NATIVE, '/probe'], text=True, capture_output=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'maps agree' in completed.stdout
