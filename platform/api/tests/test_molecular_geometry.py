"""Real native construction, not mocked or synthesized coordinate responses."""
import copy
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import subprocess
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from services.molecular_geometry import construct_geometry, GeometryUnavailable, _supported
from services.molecular_geometry_recipe import MolecularGeometryRequest, MolecularGeometryResult
from services.molecular_construction import construct_graph


def payload(form='abdna', sequence=None) -> dict[str, Any]:
    polymer = 'RNA' if form == 'arna' else 'DNA'
    sequence = sequence or ('ACGU' if polymer == 'RNA' else 'ACGT')
    complement = dict(zip('ACGU' if polymer == 'RNA' else 'ACGT', 'UGCA' if polymer == 'RNA' else 'TGCA'))
    second = ''.join(complement[x] for x in reversed(sequence))
    def strand(s,seq):
        return dict(id=s,polymer=polymer,copy_ids=['copy'],five_prime='hydroxyl',three_prime='hydroxyl',
                    residues=[dict(id=f'r{i+1}',base=b) for i,b in enumerate(seq)])
    def endpoint(s,i):
        return dict(strand_id=s,copy_id='copy',residue_id=f'r{i}')
    return dict(operation='create_new_idealized_geometry',provider='ambertools24_fd_helix',form=form,
                topology='canonical_antiparallel_duplex',hydrogen_policy='heavy_atom_coordinates_graph_implicit_hydrogens',
                construction=dict(graph_provider='rdkit',phosphate_state='neutral_acid',geometry='unpositioned',
                  strands=[strand('first',sequence),strand('second',second)],
                  pairing=[dict(id='duplex',orientation='antiparallel',pairs=[
                      dict(first=endpoint('first',i+1),second=endpoint('second',len(sequence)-i),relation='canonical')
                      for i in range(len(sequence))])]))


@pytest.fixture
def native():
    if not os.environ.get('BMS_MOLECULAR_GEOMETRY_BUNDLE'):
        pytest.skip('native bundle not provisioned: native qualification unperformed')
    pytest.importorskip('rdkit.Chem')


@pytest.mark.parametrize('form,sequence,rise,twist', [
    ('abdna','ACGT',3.38,36.0),('adna','ACGT',2.56,32.7),('arna','ACGU',2.81,32.7),
    ('abdna','A',3.38,36.0),('adna','C',2.56,32.7),('arna','U',2.81,32.7)])
def test_actual_native_coordinates_graph_form_and_exact_readback(native,form,sequence,rise,twist,tmp_path):
    request = MolecularGeometryRequest.model_validate(payload(form,sequence))
    before = request.model_dump_json()
    result = construct_geometry(request)
    assert request.model_dump_json() == before
    data = result.coordinate_text.encode('ascii')
    assert result.coordinate_sha256 == hashlib.sha256(data).hexdigest()
    assert result.coordinate_size_bytes == len(data)
    # Exact bytes directly from independently invoked actual native executable.
    direct = subprocess.check_output([str(Path(os.environ['BMS_MOLECULAR_GEOMETRY_BUNDLE'])/'amber-fd-helix'),form,sequence])
    assert data == direct
    file = tmp_path/'native.pdb'
    file.write_bytes(data)
    # Independent structural parser (no wrapper serializer).
    from biotite.structure.io.pdb import PDBFile
    structure = PDBFile.read(file).get_structure(model=1)
    assert len(structure) == len(result.graph.atoms) == len(result.atom_map)
    assert set(structure.chain_id) == {'A','B'}
    assert len(result.graph.connected_components) == 2
    assert all(a.formal_charge == 0 for a in result.graph.atoms)
    lookup = {a.graph_atom_id:a for a in result.atom_map}
    assert set(lookup) == {a.id for a in result.graph.atoms}
    assert len({(a.x,a.y,a.z) for a in result.atom_map}) > 1
    assert result.graph.coordinates is None
    # Covalent connectivity remains exact, pairing creates no interstrand bond.
    for bond in result.graph.bonds:
        a,b = lookup[bond.first],lookup[bond.second]
        assert a.native_chain == b.native_chain
        distance = math.dist((a.x,a.y,a.z),(b.x,b.y,b.z))
        assert 1.0 < distance < 2.1, (bond,distance)
    # Native form readback from sugar positions measures actual rise and twist.
    sugar = [a for a in result.atom_map if a.native_chain == 'A' and a.native_atom_name == "C1'"]
    for a,b in zip(sugar,sugar[1:]):
        assert b.z-a.z == pytest.approx(rise,abs=.002)
        angle = math.degrees(math.atan2(b.y,b.x)-math.atan2(a.y,a.x)) % 360
        assert angle == pytest.approx(twist,abs=.03)
    # Independently infer sugar absolute stereochemistry from real coordinates.
    from test_molecular_construction import rebuild
    from rdkit import Chem
    molecule = rebuild(result.graph)
    conformer = Chem.Conformer(molecule.GetNumAtoms())
    for i,atom in enumerate(result.graph.atoms):
        p = lookup[atom.id]
        conformer.SetAtomPosition(i,(p.x,p.y,p.z))
    molecule.AddConformer(conformer)
    Chem.AssignAtomChiralTagsFromStructure(molecule,replaceExistingTags=True)
    Chem.AssignStereochemistry(molecule,cleanIt=True,force=True)
    for i,atom in enumerate(result.graph.atoms):
        if atom.cip:
            assert molecule.GetAtomWithIdx(i).GetProp('_CIPCode') == atom.cip
    # Check actual complementary contact geometry in the explicit register.
    for i,residue in enumerate(request.construction.strands[0].residues):
        a = lookup[f"first:copy:r{i+1}:"+('N1' if residue.base in 'AG' else 'N3')]
        b = lookup[f"second:copy:r{len(sequence)-i}:"+('N3' if residue.base in 'AG' else 'N1')]
        assert 2.3 < math.dist((a.x,a.y,a.z),(b.x,b.y,b.z)) < 3.5
    reopened = MolecularGeometryResult.model_validate_json(result.model_dump_json())
    assert reopened.coordinate_text.encode('ascii') == direct
    assert reopened.request == request
    assert reopened.graph.model_dump() == construct_graph(request.construction).model_dump()


@pytest.mark.parametrize('form,sequence',[('abdna','AAGCT'),('adna','TGCAAC'),('arna','AACGU')])
def test_independently_authored_nonpalindromic_duplex(native,form,sequence):
    request = MolecularGeometryRequest.model_validate(payload(form,sequence))
    result = construct_geometry(request)
    for index,strand in enumerate(request.construction.strands):
        chain = 'AB'[index]
        native_components = []
        for atom in result.atom_map:
            if atom.native_chain == chain and atom.native_atom_name == "C1'":
                native_components.append(atom.native_component)
        prefix = 'D' if strand.polymer == 'DNA' else ''
        assert native_components == [prefix+r.base for r in strand.residues]


@pytest.mark.parametrize('strand',['first','second'])
def test_modified_either_strand_is_not_silently_canonicalized(strand):
    d = payload()
    d['construction']['replacements'] = [dict(id='m',kind='base',strand_id=strand,
        copy_id='copy',residue_ids=['r2'],ccd_id='5CM',omit_atoms=[],atom_map={})]
    request = MolecularGeometryRequest.model_validate(d)
    with pytest.raises(ValueError,match='modifications'):
        construct_geometry(request)


def test_optional_adapter_exact_byte_identity(native,monkeypatch,tmp_path):
    import shutil
    bundle = Path(os.environ['BMS_MOLECULAR_GEOMETRY_BUNDLE'])
    shutil.copy(bundle/'receipt.json',tmp_path/'receipt.json')
    shutil.copy(bundle/'amber-fd-helix',tmp_path/'amber-fd-helix')
    with (tmp_path/'amber-fd-helix').open('ab') as f:
        f.write(b'changed')
    monkeypatch.setenv('BMS_MOLECULAR_GEOMETRY_BUNDLE',str(tmp_path))
    with pytest.raises(GeometryUnavailable,match='bytes differ'):
        construct_geometry(MolecularGeometryRequest.model_validate(payload()))


@pytest.mark.parametrize('arguments',[[],['bdna','ACGT'],['abdna','ACGU'],['arna','ACGT'],['abdna',''],['abdna','A'*1000]])
def test_actual_wrapper_rejects_unsupported_inputs_without_coordinates(native,arguments):
    binary = Path(os.environ['BMS_MOLECULAR_GEOMETRY_BUNDLE'])/'amber-fd-helix'
    run = subprocess.run([str(binary),*arguments],capture_output=True)
    assert run.returncode == 2
    assert run.stdout == b''


@pytest.mark.parametrize('field',['operation','provider','form','topology','hydrogen_policy','construction'])
def test_sparse_request_never_chooses_geometry(field):
    value = payload()
    del value[field]
    with pytest.raises(ValidationError):
        MolecularGeometryRequest.model_validate(value)


@pytest.mark.parametrize('change,match',[
    ('single','two explicit'),('mismatch','mismatches'),('overhang','overhangs'),
    ('register','reverse-register'),('unspecified','canonical base pairs'),
    ('orientation','antiparallel'),('terminus','hydroxyl termini'),
    ('nick','nicks'),('copies','multiple copies'),('links','custom covalent'),
    ('no_pairing','complete'),('wrong_form','polymer differ'),
])
def test_unsupported_is_scoped_and_does_not_repair_intent(change,match):
    d = payload()
    c = d['construction']
    if change == 'single': c['strands'] = c['strands'][:1]; c['pairing'] = []
    if change == 'mismatch': c['strands'][1]['residues'][0]['base'] = 'G'
    if change == 'overhang': c['strands'][1]['residues'].append(dict(id='extra',base='A'))
    if change == 'register': c['pairing'][0]['pairs'] = c['pairing'][0]['pairs'][:-1]
    if change == 'unspecified': c['pairing'][0]['pairs'][0]['relation'] = 'unspecified'
    if change == 'orientation': c['pairing'][0]['orientation'] = 'parallel'
    if change == 'terminus': c['strands'][1]['three_prime'] = 'phosphate_neutral'
    if change == 'nick': c['strands'][0]['nicks'] = [dict(after='r2',left_three_prime='hydroxyl',right_five_prime='hydroxyl')]
    if change == 'copies': c['strands'][0]['copy_ids'].append('other')
    if change == 'links':
        p = c['pairing'][0]['pairs'][0]
        c['covalent_links'] = [dict(first=dict(**p['first'],atom_name="O3'"),second=dict(**p['second'],atom_name='P'),order='single')]
    if change == 'no_pairing': c['pairing'] = []
    if change == 'wrong_form': d['form'] = 'arna'
    request = MolecularGeometryRequest.model_validate(d)
    original = copy.deepcopy(d)
    with pytest.raises(ValueError,match=match), patch('services.molecular_geometry._runtime',side_effect=AssertionError('must not run')):
        construct_geometry(request)
    assert d == original


def test_no_t_u_conversion_or_imported_geometry():
    d = payload()
    d['construction']['strands'][0]['residues'][0]['base'] = 'U'
    with pytest.raises(ValidationError,match='no T/U conversion'):
        MolecularGeometryRequest.model_validate(d)
    d = payload()
    d['imported_coordinate_text'] = 'ATOM original geometry is not a construction input'
    with pytest.raises(ValidationError):
        MolecularGeometryRequest.model_validate(d)


def test_unconfigured_optional_adapter(monkeypatch):
    monkeypatch.delenv('BMS_MOLECULAR_GEOMETRY_BUNDLE',raising=False)
    with pytest.raises(GeometryUnavailable,match='not configured'):
        construct_geometry(MolecularGeometryRequest.model_validate(payload()))


@pytest.mark.asyncio
async def test_private_typed_http_actual_native_and_errors(native,monkeypatch,tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    spec = importlib.util.spec_from_file_location('s05_geometry_router',Path(__file__).parents[1]/'routers/molecular_geometry.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    # Existing imported file is not read, reserialized or replaced by construction.
    original = tmp_path/'imported.pdb'
    original.write_bytes(b'REMARK exact imported coordinates and metadata\r\n'+
                         (Path(os.environ['BMS_MOLECULAR_GEOMETRY_BUNDLE'])/'adna-ACGT.pdb').read_bytes())
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://private') as client:
        response = await client.post('/api/molecular-geometry/construct',json=payload())
        assert response.status_code == 200,response.text
        body = MolecularGeometryResult.model_validate(response.json())
        assert body.coordinate_text.encode('ascii') == subprocess.check_output([
            str(Path(os.environ['BMS_MOLECULAR_GEOMETRY_BUNDLE'])/'amber-fd-helix'),'abdna','ACGT'])
        schema = (await client.get('/openapi.json')).json()['components']['schemas']
        assert schema['MolecularGeometryRequest']['additionalProperties'] is False
        for field in ('operation','provider','form','topology','hydrogen_policy','construction'):
            assert field in schema['MolecularGeometryRequest']['required']
        assert (await client.post('/api/molecular-geometry/construct',json={})).status_code == 422
        d=payload();d['construction']['pairing']=[]
        assert (await client.post('/api/molecular-geometry/construct',json=d)).status_code == 422
        monkeypatch.delenv('BMS_MOLECULAR_GEOMETRY_BUNDLE')
        assert (await client.post('/api/molecular-geometry/construct',json=payload())).status_code == 503
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
