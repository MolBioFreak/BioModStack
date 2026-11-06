"""Real installed DISCO CPU chemistry; offline CCD cache required, no inference.

Run in the selected DISCO image with its existing HF cache bound read-only and
HF_HUB_OFFLINE=1. No substitute atoms or stub chemistry are supplied here.
"""
from copy import deepcopy
import importlib.util
import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec('disco') is None,
                                reason='selected installed DISCO parser required')


@pytest.mark.parametrize('kind,sequence,record,names', [
    ('proteinChain', 'ASG', {'ptmPosition': 2, 'ptmType': 'CCD_SEP'}, ['ALA', 'SEP', 'GLY']),
    ('dnaSequence', 'ACGT', {'basePosition': 2, 'modificationType': 'CCD_5CM'}, ['DA', '5CM', 'DG', 'DT']),
    ('rnaSequence', 'ACGU', {'basePosition': 4, 'modificationType': 'CCD_PSU'}, ['A', 'C', 'G', 'PSU']),
])
def test_real_modified_polymer_native_one_based_identity_and_connectivity(kind, sequence, record, names):
    import biotite.structure as struc
    from disco.data.json_parser import build_polymer
    entity = {kind: {'sequence': sequence, 'count': 2, 'modifications': [record]}}
    before = deepcopy(entity)
    atoms = build_polymer(entity)['atom_array']
    starts = struc.get_residue_starts(atoms)
    assert list(atoms.res_name[starts]) == names
    assert list(atoms.res_id[starts]) == list(range(1, len(names) + 1))
    # build_polymer is one strand; copying is the enclosing native job's owner.
    assert len(starts) == len(sequence)
    edges = [(int(atoms.res_id[i]), int(atoms.res_id[j]), str(atoms.atom_name[i]), str(atoms.atom_name[j]))
             for i, j, _ in atoms.bonds.as_array() if atoms.res_id[i] != atoms.res_id[j]]
    endpoints = ('C', 'N') if kind == 'proteinChain' else ("O3'", 'P')
    assert edges == [(i, i + 1, *endpoints) for i in range(1, len(sequence))]
    assert entity == before


def test_explicit_two_strands_keep_independent_modifications_and_order():
    import biotite.structure as struc
    from disco.data.json_parser import build_polymer
    strands = [
        {'dnaSequence': {'sequence': 'ACGT', 'count': 1, 'modifications': [{'basePosition': 2, 'modificationType': 'CCD_5CM'}]}},
        {'dnaSequence': {'sequence': 'AGGT', 'count': 1, 'modifications': []}},
    ]
    parsed = [build_polymer(row)['atom_array'] for row in strands]
    assert [list(a.res_name[struc.get_residue_starts(a)]) for a in parsed] == [
        ['DA', '5CM', 'DG', 'DT'], ['DA', 'DG', 'DG', 'DT']]
    # Deliberately authored mismatch, not a generated reverse complement.
    assert strands[1]['dnaSequence']['sequence'] == 'AGGT'
