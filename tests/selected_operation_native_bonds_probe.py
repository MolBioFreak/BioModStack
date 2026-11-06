"""Run inside pinned ESMFold2 image, with API-emitted evidence and CCD read-only."""
import json
from pathlib import Path
import sys
import torch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_esmfold2_inference as runner
from esm.utils.structure.input_builder import (ProteinInput, DNAInput, RNAInput, LigandInput,
    StructurePredictionInput, serialize_structure_prediction_input, deserialize_structure_prediction_input)
from esm.utils.msa import MSA
from esm.models.esmfold2.processor import ESMFold2InputBuilder
from esm.models.esmfold2.prepare_input import build_chains_from_input


def main(root):
    torch.set_num_threads(1)
    classes = dict(ProteinInput=ProteinInput, DNAInput=DNAInput, RNAInput=RNAInput,
                   LigandInput=LigandInput, StructurePredictionInput=StructurePredictionInput, MSA=MSA)
    records = []
    for path in sorted(root.glob('esmfold2-*.json')):
        payload = json.loads(path.read_text())
        args = runner.build_parser().parse_args(['--output-dir', str(root), '--seed', '0',
            '--complex-components-json', json.dumps(payload['document'])])
        actual, _ = runner.build_structure_prediction_input(args, **classes)
        wire = serialize_structure_prediction_input(actual)
        ESMFold2InputBuilder().prepare_input(deserialize_structure_prediction_input(wire), seed=0, device='cpu')
        source = payload['document'].get('covalent_bonds', [])
        expected = source
        if source and 'left_entity' in source[0]:
            expected = [dict(chain_id1='Y', chain_id2='Z', res_idx1=1, res_idx2=1, atom_idx1=5, atom_idx2=5)]
        assert wire.get('covalent_bonds', []) == expected, path
        records.append({'file': path.name, 'native': wire})
    assert len(records) == 18
    # Mixed copied polymers, CCD ligand, mapped SMILES: use actual native inventory,
    # including covalent-ligand leaving-atom behavior, not guessed atom positions.
    document = {'components': [
        {'type': 'protein', 'id': 'P', 'copy_ids': ['B', 'A'], 'count': 2, 'sequence': 'AC'},
        {'type': 'dna', 'id': 'D', 'sequence': 'AC'},
        {'type': 'ligand', 'id': 'L', 'ccd': 'ATP'},
        {'type': 'ligand', 'id': 'M', 'smiles': '[CH3:7][OH:42]'}],
        'covalent_bonds': [dict(left_entity=1, left_copy=2, left_position=2, left_atom='SG',
             right_entity=3, right_copy=1, right_position=1, right_atom='PA'),
            dict(left_entity=2, left_copy=1, left_position=1, left_atom='P',
             right_entity=4, right_copy=1, right_position=1, right_atom=42)]}
    args = runner.build_parser().parse_args(['--output-dir', str(root), '--seed', '0',
        '--complex-components-json', json.dumps(document)])
    actual, _ = runner.build_structure_prediction_input(args, **classes)
    chains, tokens, atoms = build_chains_from_input(actual, seed=0)
    def atom_name(chain_id, residue, index):
        chain = next(c for c in chains if c.chain_id == chain_id)
        return [a.name for a in atoms if a.is_valid and tokens[a.token_index].asym_id == chain.asym_id
                and tokens[a.token_index].residue_index == residue][index]
    first, second = actual.covalent_bonds
    assert first.chain_id1 == 'A' and first.chain_id2 == 'L'
    assert atom_name(first.chain_id1, first.res_idx1, first.atom_idx1) == 'SG'
    assert atom_name(first.chain_id2, first.res_idx2, first.atom_idx2) == 'PA'
    assert second.chain_id1 == 'D' and second.chain_id2 == 'M' and second.atom_idx2 == 1
    assert atom_name(second.chain_id1, second.res_idx1, second.atom_idx1) == 'P'
    features, _ = ESMFold2InputBuilder().prepare_input(actual, seed=0, device='cpu')
    assert features['token_bonds'].sum().item() > 0
    records.append({'file': 'mixed', 'native': serialize_structure_prediction_input(actual)})
    (root / 'native-esm-results.json').write_text(json.dumps(records, indent=2))
    print(f'PASS {len(records)} real native deserialize/CPU feature cases; no model construction or inference')

if __name__ == '__main__':
    main(Path(sys.argv[1]))
