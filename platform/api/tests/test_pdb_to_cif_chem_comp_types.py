"""PDB -> CIF derivatives must say which components are polymer.

Gemmi leaves ``_chem_comp.type`` as ``.`` unless a monomer library is loaded.
Mol* then counts zero polymer residues and draws atoms and bonds, not a ribbon.
"""
import pathlib

import gemmi
import pytest
from Bio.PDB.MMCIF2Dict import MMCIF2Dict

from services import binder_source_materialization as sources

PDB = """\
ATOM      1  N   ALA A   1      11.104  13.207   2.100  1.00 10.00           N
ATOM      2  CA  ALA A   1      12.560  13.207   2.100  1.00 10.00           C
ATOM      3  C   ALA A   1      13.100  14.600   2.100  1.00 10.00           C
ATOM      4  O   ALA A   1      12.400  15.600   2.100  1.00 10.00           O
ATOM      5  N   MSE A   2      14.400  14.700   2.100  1.00 10.00           N
ATOM      6  CA  MSE A   2      15.100  15.980   2.100  1.00 10.00           C
ATOM      7  C   MSE A   2      16.600  15.800   2.100  1.00 10.00           C
ATOM      8  O   MSE A   2      17.200  14.700   2.100  1.00 10.00           O
ATOM      9  P    DA B   1      30.000  30.000  30.000  1.00 10.00           P
ATOM     10  O5'  DA B   1      30.500  31.000  30.000  1.00 10.00           O
ATOM     11  P    DT B   2      32.000  33.000  30.000  1.00 10.00           P
ATOM     12  O5'  DT B   2      32.500  34.000  30.000  1.00 10.00           O
HETATM   13 MN    MN A 101      20.000  20.000  20.000  1.00 10.00          MN
HETATM   14  O   HOH A 201      25.000  25.000  25.000  1.00 10.00           O
HETATM   15  C1  GTP A 301      35.000  35.000  35.000  1.00 10.00           C
END
"""


def derived_types(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'to_allowed_relative', str)
    sources.materialize_source_bytes(PDB.encode(), '.pdb', tmp_path, output_format='cif')
    columns = MMCIF2Dict(str(tmp_path / 'derived.cif'))
    return dict(zip(columns['_chem_comp.id'], columns['_chem_comp.type']))


def test_pdb_to_cif_types_every_component(tmp_path, monkeypatch):
    types = derived_types(tmp_path, monkeypatch)
    assert types == {
        'ALA': 'L-peptide linking', 'MSE': 'L-peptide linking',
        'DA': 'DNA linking', 'DT': 'DNA linking',
        'MN': 'non-polymer', 'HOH': 'non-polymer', 'GTP': 'non-polymer',
    }
    assert '.' not in types.values() and '?' not in types.values()


def test_derived_cif_has_polymer_residues_for_a_reader_that_trusts_chem_comp(tmp_path, monkeypatch):
    """Same decision Mol* makes: polymer residues are those whose component type is a linking type."""
    types = derived_types(tmp_path, monkeypatch)
    atoms = MMCIF2Dict(str(tmp_path / 'derived.cif'))['_atom_site.label_comp_id']
    polymer_atoms = [name for name in atoms if 'linking' in types[name]]
    assert polymer_atoms and set(polymer_atoms) == {'ALA', 'MSE', 'DA', 'DT'}


def test_existing_component_types_are_never_overwritten():
    columns = {'_chem_comp.id': ['ALA', 'DCP', 'XYZ'],
               '_chem_comp.type': ['L-peptide linking', 'non-polymer', '.']}
    filled = sources._fill_blank_chem_comp_types(columns, gemmi)
    assert filled['_chem_comp.type'] == ['L-peptide linking', 'non-polymer', 'non-polymer']
    custom = {'_chem_comp.id': ['ALA'], '_chem_comp.type': ['peptide linking']}
    assert sources._fill_blank_chem_comp_types(custom, gemmi)['_chem_comp.type'] == ['peptide linking']


def test_missing_chem_comp_category_is_left_alone():
    assert sources._fill_blank_chem_comp_types({'_atom_site.id': ['1']}, gemmi) == {'_atom_site.id': ['1']}


def test_glycine_uses_the_ccd_spelling_and_other_residues_keep_chirality():
    assert sources._chem_comp_type('GLY', gemmi) == 'peptide linking'
    assert sources._chem_comp_type('ALA', gemmi) == 'L-peptide linking'
    assert sources._chem_comp_type('DAL', gemmi) == 'D-peptide linking'
    assert sources._chem_comp_type('NOT_A_COMPONENT', gemmi) == 'non-polymer'
