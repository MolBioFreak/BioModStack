"""Inert ordinary LigandMPNN contract tests; no native sampling."""
import json
from pathlib import Path

import pytest
import yaml

from services.ligandmpnn_design import (
    MODES, NativeOptions, normalize_design_params, prepare_design_request,
    read_design_result, selected_assets,
)


@pytest.mark.parametrize('mode', sorted(MODES))
def test_modes_keep_native_context_and_settings(mode):
    params = {'target_pdb': '/owned/complex.cif', 'design_seed': 0,
              'temperature': 0.27, 'atomize_side_chains': False,
              'designed_chains': ['b'], 'remove_ccds': [],
              'bias_per_residue': {'b17': {'ALA': 0.0}},
              'dna_sequence': 'AC', 'metal_type': 'Mg2+', 'write_structures': False}
    normalized = normalize_design_params(mode, params)
    assert normalize_design_params(mode, normalized) == normalized
    request = prepare_design_request(mode, normalized)
    assert request['model_type'] == 'ligand_mpnn'
    assert request['options']['seed'] == 0
    assert request['options']['designed_chains'] == ['b']
    assert request['options']['bias_per_residue'] == {'b17': {'ALA': 0.0}}
    assert request['options']['remove_ccds'] == []
    assert request['options']['atomize_side_chains'] is False
    assert 'structure_path' not in request['options']
    assert 'dna_sequence' not in request['options']
    assert request['annotations']['dna_sequence'] == 'AC'
    assert not request['write_structures']


@pytest.mark.parametrize('params', [
    {'ligand_smiles': 'O'},
    {'target_pdb': '/p.pdb', 'ligand_pdb': '/l.pdb'},
    {'target_pdb': '/p.pdb', 'model_type': 'protein_mpnn'},
    {'target_pdb': '/p.pdb', 'batch_size': 0},
    {'target_pdb': '/p.pdb', 'designed_chains': ['A'], 'fixed_chains': []},
    {'target_pdb': '/p.pdb', 'designed_residues': ['A1'], 'fixed_chains': ['B']},
    {'target_pdb': '/p.pdb', 'pair_bias': {'ALA': 'bad'}},
])
def test_no_fake_geometry_fallback_or_lost_settings(params):
    with pytest.raises(ValueError):
        normalize_design_params('ligand_aware', params)


def test_legacy_complete_source_alias_and_diagnostic_separation():
    values = normalize_design_params('ligand_aware', {'ligand_pdb': '/complex.pdb'})
    assert values['ligand_pdb'] == '/complex.pdb'
    with pytest.raises(ValueError):
        normalize_design_params('interface_context', values)
    assert selected_assets() == ({'kind': 'image', 'relative_path': 'foundry.sif'},)


def test_schema_covers_ordinary_typed_options_without_diagnostic_mutation():
    config = yaml.safe_load((Path(__file__).parents[1] / 'config/models/ligandmpnn.yaml').read_text())
    fields = {p['name']: p for p in config['params']}
    controls = (set(NativeOptions.model_fields) - {'seed'}) | {'design_seed', 'write_fasta', 'write_structures'}
    for mode in config['modes']:
        if mode['id'] in MODES:
            assert controls <= set(mode['params'])
        else:
            assert mode['params'] == ['ligandmpnn_interface_selection', 'interface_context_manifest',
                                      'binder_chain', 'target_chain', 'target_patch', 'seed', 'samples', 'temperature']
    assert fields['seed']['required'] is True
    assert fields['samples']['maximum'] == 16


def test_reader_accepts_zero_yield_and_rejects_escape(tmp_path):
    doc = {'contract': 'ligandmpnn_design.v1', 'mode': 'ligand_aware',
           'model_type': 'ligand_mpnn', 'records': []}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(doc))
    assert read_design_result(tmp_path)['records'] == []
    doc['records'] = [{'producer': {'name': 'design', 'batch_idx': 4, 'design_idx': 8},
                       'native_input': {'name': 'design'},
                       'native_output': {'model_type': 'ligand_mpnn', 'batch_idx': 4, 'design_idx': 8},
                       'artifacts': [{'path': '../outside', 'sha256': 'x'}]}]
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='escapes'):
        read_design_result(tmp_path)
