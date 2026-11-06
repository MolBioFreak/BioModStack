"""CPU-only source transport; not an inference or activity qualification."""
import sys
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'platform/api')]
from services.boltzgen_request_compatibility import BOLTZGEN_GENERATION_PROTOCOLS, normalize_boltzgen_generation_request
from scripts.lib.boltzgen_inputs import materialize_generation_input
from scripts.prep_boltzgen import NTP_TEMPLATES


@pytest.mark.parametrize('mode', BOLTZGEN_GENERATION_PROTOCOLS)
def test_all_public_modes_materialize_native_entities_and_keep_values(tmp_path, mode):
    # Transport fixture only. Native parser qualification uses RDKit molecular fixtures separately.
    source = tmp_path / 'source.pdb'
    source.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
    request = {'alpha': 0, 'noise_scale': 0, 'filter_biased': False, 'min_plddt': None}
    if mode == 'ligand_binder': request['ligand_smiles'] = 'CCO'
    if mode == 'ntp_binder': request.update(ntp_type='ATP', catalytic_site=True)
    if mode == 'scaffold_around_ligand': request['ligand_pdb'] = str(source)
    if mode == 'backbone_docking': request.update(input_pdb=str(source), ntp_type='ATP')
    if mode == 'antibody_binder': request.update(scaffold_path=str(source), scaffold_chain='A', scaffold_design_ranges='1')
    params = normalize_boltzgen_generation_request(mode, request)
    assert params['boltzgen_alpha'] == 0 and params['boltzgen_noise_scale'] == 0
    assert params['boltzgen_filter_biased'] is False and params['boltzgen_min_plddt'] is None
    assert normalize_boltzgen_generation_request(mode, params) == params
    materialize_generation_input(params, tmp_path / 'prepared', allowed_input_roots=[tmp_path])
    spec = yaml.safe_load((tmp_path / 'prepared/boltzgen_input.yaml').read_text())
    entities = spec['entities']
    assert all('path' not in e.get('ligand', {}) for e in entities)
    if mode == 'ligand_binder': assert entities[1]['ligand']['smiles'] == 'CCO'
    if mode == 'ntp_binder':
        assert entities[1]['ligand']['smiles'] == NTP_TEMPLATES['ATP']
        assert entities[2]['ligand']['smiles'] == '[Mg+2]'
        assert 'constraints' not in spec  # no fabricated catalytic geometry
    if mode == 'scaffold_around_ligand': assert 'file' in entities[1]
    if mode == 'backbone_docking':
        assert 'file' in entities[0]
        assert entities[1]['ligand']['smiles'] == NTP_TEMPLATES['ATP']
        assert not any('design' in e.get('file', {}) for e in entities)
    if mode == 'antibody_binder': assert entities[0]['file']['design'][0]['chain']['res_index'] == '1'


def test_cyclic_peptide_native_entity(tmp_path):
    params = normalize_boltzgen_generation_request('peptide_binder', {'cyclic': True, 'binder_sequence': '10'})
    materialize_generation_input(params, tmp_path / 'prepared', allowed_input_roots=[tmp_path])
    spec = yaml.safe_load((tmp_path / 'prepared/boltzgen_input.yaml').read_text())
    assert spec['entities'] == [{'protein': {'id': 'B', 'sequence': '10', 'cyclic': True}}]
