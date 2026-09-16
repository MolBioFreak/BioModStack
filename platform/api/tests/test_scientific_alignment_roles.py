import importlib
import sys
from pathlib import Path

import pytest
from Bio.PDB import PDBParser, MMCIFIO

from test_scientific_residue_identity import atom

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'


@pytest.fixture
def scripts(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.chdir(tmp_path)
    return importlib.import_module('align_boltz'), importlib.import_module('align_protenix')


def test_explicit_chain_remap_then_role_validation(scripts, tmp_path):
    boltz, _ = scripts
    p = tmp_path / 'chains.pdb'
    p.write_text(''.join(atom(i+1, 'CA', c, 1) for i, c in enumerate('ABC')))
    mobile = PDBParser(QUIET=True).get_structure('mobile', p)
    boltz.validate_scientific_roles(['H', 'L', 'T'], mobile, ['H', 'L'], ['T'], chain_map={'A': 'T', 'B': 'H', 'C': 'L'})
    assert [c.id for c in mobile.get_chains()] == ['T', 'H', 'L']
    with pytest.raises(ValueError, match='roles'):
        boltz.validate_scientific_roles(['H', 'L', 'T'], mobile, ['H', 'L'], ['X'])


@pytest.mark.parametrize('binder,target,mapping', [([], ['T'], None), (['H'], ['H'], None), (['H','H'], ['T'], None), (['H'], ['T'], {'A':'H', 'B':'H', 'C':'T'}), (['H'], ['T'], None)])
def test_missing_overlapping_ambiguous_roles_fail(scripts, tmp_path, binder, target, mapping):
    boltz, _ = scripts
    p = tmp_path / 'chains.pdb'
    p.write_text(''.join(atom(i+1, 'CA', c, 1) for i, c in enumerate('ABC')))
    mobile = PDBParser(QUIET=True).get_structure('mobile', p)
    with pytest.raises(ValueError, match='roles|map'):
        boltz.validate_scientific_roles(['H','L','T'], mobile, binder, target, chain_map=mapping)
    assert [c.id for c in mobile.get_chains()] == ['A', 'B', 'C']


@pytest.mark.parametrize('provider', ['boltz', 'protenix'])
def test_marked_production_alignment_rejects_shared_chain_fallback(scripts, tmp_path, provider):
    boltz, protenix = scripts
    p = tmp_path / 'design.pdb'
    p.write_text(''.join(atom(i*3+j+1, 'CA', c, j+1, x=j) for i, c in enumerate('ABC') for j in range(3)))
    out = tmp_path / 'out'
    out.mkdir()
    if provider == 'boltz':
        result = boltz.align_structures((p, p, out/'aligned.pdb', tmp_path/'conf.json', out/'conf.json', tmp_path/'pae.npz', out/'pae.npz', 0, 0, 'binder', 'H,L', 'T', 'flexible', None, 1, None))
        error = result[2]
    else:
        cif = tmp_path / 'design_sample_0.cif'
        io = MMCIFIO()
        io.set_structure(PDBParser(QUIET=True).get_structure('m', p))
        io.save(str(cif))
        summary = tmp_path / 'design_summary_confidence_sample_0.json'
        result = protenix.align_structure((p, summary, out, 'binder', 'H,L', 'T', 'flexible', None, 1, None))
        error = result[1]
    assert error and 'roles' in error
    assert not list(out.iterdir())



@pytest.mark.parametrize('binder,target,mapping', [
    ('B,', 'A', None), (',B', 'A', None),
    (' , B , ', ' , A , ', None), (' B ', ' A ', None),
    ('B', 'A', {'X': 'B', 'Y': 'A'}),
])
def test_strict_boltz_preserves_normalized_roles_before_mutation(scripts, tmp_path, monkeypatch, binder, target, mapping):
    boltz, _ = scripts
    ref, mobile = tmp_path/'ref.pdb', tmp_path/'mobile.pdb'
    ref.write_text(''.join(atom(i*3+j+1, 'CA', c, j+1, x=i*10+j) for i,c in enumerate('AB') for j in range(3)))
    mobile.write_text(''.join(atom(i*3+j+1, 'CA', c, j+1, x=i*10+j+2) for i,c in enumerate('YX' if mapping else 'AB') for j in range(3)))
    import numpy as np
    np.savez(tmp_path/'pae.npz', pae=np.zeros((6, 6)))
    (tmp_path/'conf.json').write_text('{}')
    original_match = boltz.get_matched_ca_atoms
    original_replace = boltz.replace_target_chains
    selected = []
    def match(ref_structure, mobile_structure, chains=None):
        if not selected:
            # First selection happens before superposition or frozen replacement.
            assert chains == ['A']
            assert next(mobile_structure[0]['A'].get_atoms()).coord[0] == 2
        selected.append(chains)
        return original_match(ref_structure, mobile_structure, chains)
    replaced = []
    def replace(mobile_structure, ref_structure, chains):
        assert chains == ['A']
        replaced.extend(chains)
        return original_replace(mobile_structure, ref_structure, chains)
    monkeypatch.setattr(boltz, 'get_matched_ca_atoms', match)
    monkeypatch.setattr(boltz, 'replace_target_chains', replace)
    result = boltz.align_structures((ref, mobile, tmp_path/'out.pdb', tmp_path/'conf.json', tmp_path/'out.json', tmp_path/'pae.npz', tmp_path/'out.npz', 0, 0, 'binder', binder, target, 'frozen', None, 1, mapping))
    assert result[2] is None
    assert selected == [['A'], None, ['B']]
    assert replaced == ['A']
    assert (tmp_path/'out.pdb').exists()


def test_strict_protenix_matching_never_uses_sequential_fallback(scripts, tmp_path):
    _, protenix = scripts
    p, q = tmp_path/'p.pdb', tmp_path/'q.pdb'
    p.write_text(''.join(atom(i+1, 'CA', 'H', 100, code) for i, code in enumerate(['', 'A', 'B'])))
    q.write_text(''.join(atom(i+1, 'CA', 'H', i+1) for i in range(3)))
    ref = PDBParser(QUIET=True).get_structure('r', p)
    mobile = PDBParser(QUIET=True).get_structure('m', q)
    with pytest.raises(ValueError, match='Insufficient'):
        protenix.get_matched_ca_atoms(ref, mobile, ['H'], contract_revision=1)
    assert [r.id[1] for r in mobile.get_residues()] == [1,2,3]


@pytest.mark.parametrize('provider', ['boltz', 'protenix'])
def test_cli_explicit_revision_rejects_invalid_marker_before_output(tmp_path, provider):
    import subprocess
    result = subprocess.run([sys.executable, str(SCRIPTS/f'align_{provider}.py'), '--design_dir', str(tmp_path), f'--{provider}_dir', str(tmp_path), '--output_dir', str(tmp_path/'out'), '--design_type', 'binder', '--core_protein_scientific_contract', '2'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'contract revision' in result.stdout + result.stderr
    assert not (tmp_path/'out').exists()



def test_strict_production_protenix_does_not_renumber_mobile(scripts, tmp_path):
    _, protenix = scripts
    p, q = tmp_path/'design.pdb', tmp_path/'mobile.pdb'
    p.write_text(''.join(atom(i*3+j+1, 'CA', c, 100, code) for i,c in enumerate('HT') for j,code in enumerate(['','A','B'])))
    q.write_text(''.join(atom(i*3+j+1, 'CA', c, j+1) for i,c in enumerate('HT') for j in range(3)))
    io = MMCIFIO()
    io.set_structure(PDBParser(QUIET=True).get_structure('m', q))
    io.save(str(tmp_path/'design_sample_0.cif'))
    out = tmp_path/'out'
    out.mkdir()
    _, error = protenix.align_structure((p, tmp_path/'design_summary_confidence_sample_0.json', out, 'binder', 'H', 'T', 'flexible', None, 1, None))
    assert error and 'Insufficient exact residue identities' in error
    assert not list(out.iterdir())
