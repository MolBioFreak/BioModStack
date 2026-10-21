"""Observational author-to-native input positions; never rewrites a structure.

Matches pinned BoltzGen 617e549e pdb_parser.parse_pdb and mmcif.parse_polymer:
PDB SEQRES alignment (or observed sequence), mmCIF label_seq with missing slots,
and native file-entity subchain IDs. No assembly expansion, as in prep_boltzgen.
This is an authoring aid, not an execution/admission predicate.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import gemmi


def boltzgen_source_positions(path: Path) -> dict:
    content = path.read_bytes()
    pdb = path.suffix.lower() == '.pdb'
    text = content.decode('utf-8')
    structure = gemmi.read_pdb_string(text) if pdb else gemmi.make_structure_from_block(gemmi.cif.read_string(text).sole_block())
    if len(structure) != 1:
        raise ValueError('Choose a single-model native document for residue adoption; no conformation was substituted.')
    if pdb:
        structure.setup_entities()
        for entity in structure.entities:
            if entity.entity_type.name != 'Polymer':
                continue
            polymer = structure[0].get_subchain(entity.subchains[0])
            if not entity.full_sequence:
                entity.full_sequence = [res.name for res in polymer]
            alignment = gemmi.align_sequence_to_polymer(entity.full_sequence, polymer, entity.polymer_type, gemmi.AlignmentScoring()).match_string
            index = 0
            for position, symbol in enumerate(alignment, 1):
                if symbol == '|':
                    polymer[index].label_seq = position
                    index += 1
    structure.merge_chain_parts()
    structure.remove_waters()
    structure.remove_hydrogens()
    structure.remove_alternative_conformations()
    structure.remove_empty_chains()
    entities = {subchain: entity for entity in structure.entities for subchain in entity.subchains}
    rows = []
    for chain in structure[0]:
        for residue in chain:
            entity = entities.get(residue.subchain)
            if not entity or entity.entity_type.name != 'Polymer' or entity.polymer_type.name not in {'PeptideL', 'Dna', 'Rna'}:
                continue
            position = residue.label_seq
            if not position or position < 1:
                continue
            rows.append({'model_number': int(getattr(structure[0], 'num', None) or structure[0].name), 'auth_asym_id': chain.name,
                         'auth_seq_id': residue.seqid.num, 'insertion_code': residue.seqid.icode.strip(),
                         'native_chain': chain.name if pdb else residue.subchain,
                         'native_position': position})
    return {'schema_version': 1, 'source_sha256': hashlib.sha256(content).hexdigest(),
            'source_path': str(path), 'positions': rows}
