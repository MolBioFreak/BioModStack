"""Reversible source-to-native identities without changing original source bytes.

CIF categories (including explicit bonds) are retained. The first source model
is the historical default; an explicit model serial chooses another model.
Native parsing/chemistry completion remains Foundry's responsibility.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import re
import string
from pathlib import Path
from typing import Any

from Bio.PDB import MMCIFIO, PDBParser
from Bio.PDB.MMCIF2Dict import MMCIF2Dict


def _column(data, key, count, default):
    value = data.get(key)
    if value is None:
        return [default] * count
    return value if isinstance(value, list) else [value]


def materialize_source(source: Path, destination: Path, *, model_serial=None) -> dict[str, Any]:
    if source.resolve() == destination.resolve():
        raise ValueError("derived source destination must not overwrite original bytes")
    opener = gzip.open if source.name.endswith('.gz') else open
    with opener(source, 'rt', encoding='utf-8') as handle:
        if source.name.lower().endswith(('.cif', '.mmcif', '.cif.gz', '.mmcif.gz')):
            data = dict(MMCIF2Dict(handle))
        else:
            structure = PDBParser(QUIET=True).get_structure('source', handle)
            writer = MMCIFIO()
            writer.set_structure(structure)
            text = io.StringIO()
            writer.save(text)
            text.seek(0)
            data = dict(MMCIF2Dict(text))
    count = len(data['_atom_site.id'])
    models = _column(data, '_atom_site.pdbx_PDB_model_num', count, '1')
    selected = str(model_serial) if model_serial is not None else models[0]
    chains = _column(data, '_atom_site.auth_asym_id', count, '')
    residues = _column(data, '_atom_site.auth_seq_id', count, '')
    insertions = _column(data, '_atom_site.pdbx_PDB_ins_code', count, '?')
    atoms = _column(data, '_atom_site.auth_atom_id', count, '')
    labels = _column(data, '_atom_site.label_atom_id', count, '')
    altloc = _column(data, '_atom_site.label_alt_id', count, '.')
    components = _column(data, '_atom_site.label_comp_id', count, '')
    # Select a single model without stripping unrelated chemical categories.
    keep = [i for i in range(count) if models[i] == selected]
    chain_map = {}
    residue_map = {}
    per_chain = {}
    mapping = []
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits
    for i in keep:
        chain = chains[i]
        if chain not in chain_map:
            # Do not invent an admission cap; native parser remains authoritative
            # for inputs beyond its single-character namespace.
            candidate = alphabet[len(chain_map)] if len(chain_map) < len(alphabet) else chain
            if candidate in chain_map.values():
                candidate = f"bms_chain_{len(chain_map)}"
                while candidate in chain_map.values():
                    candidate = "_" + candidate
            chain_map[chain] = candidate
        insertion = '' if insertions[i] in {'.', '?'} else insertions[i]
        identity = (chain, residues[i], insertion)
        if identity not in residue_map:
            per_chain[chain] = per_chain.get(chain, 0) + 1
            residue_map[identity] = (chain_map[chain], str(per_chain[chain]))
        native_chain, native_residue = residue_map[identity]
        mapping.append({
            'source': {'model': models[i], 'chain': chain, 'residue': residues[i],
                       'insertion_code': insertion, 'atom': atoms[i] or labels[i],
                       'label_atom': labels[i], 'altloc': altloc[i], 'atom_id': data['_atom_site.id'][i], 'component': components[i]},
            'native': {'model': '1', 'chain': native_chain, 'residue': native_residue,
                       'insertion_code': '', 'atom': labels[i] or atoms[i], 'altloc': altloc[i]},
        })
    for key in list(data):
        if key.startswith('_atom_site.'):
            data[key] = [data[key][i] for i in keep]
    for j, row in enumerate(mapping):
        for suffix in ('auth_asym_id', 'label_asym_id'):
            data['_atom_site.' + suffix][j] = row['native']['chain']
        for suffix in ('auth_seq_id', 'label_seq_id'):
            data['_atom_site.' + suffix][j] = row['native']['residue']
        data.setdefault('_atom_site.pdbx_PDB_ins_code', ['?'] * len(keep))[j] = '?'
        data.setdefault('_atom_site.pdbx_PDB_model_num', ['1'] * len(keep))[j] = '1'
    # Remap declared covalent/coordination bond endpoints; other categories remain
    # original annotations, not claims of equivalent renumbered identifiers.
    for partner in ('ptnr1', 'ptnr2'):
        prefix = '_struct_conn.' + partner
        if prefix + '_auth_asym_id' not in data:
            continue
        for j, chain in enumerate(data[prefix + '_auth_asym_id']):
            residue = data[prefix + '_auth_seq_id'][j]
            ic_key = '_struct_conn.pdbx_' + partner + '_PDB_ins_code'
            ic = data.get(ic_key, ['?'] * len(data[prefix + '_auth_asym_id']))[j]
            mapped = residue_map.get((chain, residue, '' if ic in {'.', '?'} else ic))
            if mapped:
                data[prefix + '_auth_asym_id'][j], data[prefix + '_auth_seq_id'][j] = mapped
                if prefix + '_label_asym_id' in data:
                    data[prefix + '_label_asym_id'][j] = mapped[0]
                if prefix + '_label_seq_id' in data:
                    data[prefix + '_label_seq_id'][j] = mapped[1]
                if ic_key in data:
                    data[ic_key][j] = '?'
    writer = MMCIFIO()
    writer.set_dict(data)
    writer.save(str(destination))
    return {'schema': 'bms.rfd3.source-map.v1',
            'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'native_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'model': selected, 'atoms': mapping,
            'single_character_native_chains': all(len(c) == 1 for c in chain_map.values()),
            'annotation_scope': 'atom_site and struct_conn identities remapped; other source categories retained verbatim'}


def lower_selection(value, mapping):
    """Translate explicit author residues/ranges using actual source membership."""
    residues = {}
    for atom in mapping['atoms']:
        source, native = atom['source'], atom['native']
        residues[(source['chain'], source['residue'], source['insertion_code'])] = native['chain'] + native['residue']
    if isinstance(value, dict):
        lowered = {}
        for key, atoms in value.items():
            native_key = lower_selection(key, mapping)
            for native_residue in native_key.split(','):
                names = {a['source']['atom']: a['native']['atom'] for a in mapping['atoms']
                         if a['native']['chain'] + a['native']['residue'] == native_residue}
                if isinstance(atoms, list):
                    lowered[native_residue] = [names.get(a, a) for a in atoms]
                elif isinstance(atoms, str):
                    lowered[native_residue] = ','.join(names.get(a.strip(), a.strip()) for a in atoms.split(','))
                else:
                    lowered[native_residue] = atoms
        return lowered
    if isinstance(value, list):
        return ','.join(lower_selection(v, mapping) for v in value)
    if not isinstance(value, str):
        return value
    tokens = []
    for token in value.split(','):
        token = token.strip()
        exact = next((v for (c, r, ic), v in residues.items() if c + r + ic == token), None)
        if exact:
            tokens.append(exact)
            continue
        match = re.fullmatch(r'([^0-9]+)(-?\d+)-(-?\d+)', token)
        if match:
            chain, start, end = match.groups()
            matches = [v for (c, r, ic), v in residues.items() if c == chain and int(start) <= int(r) <= int(end)]
            if matches:
                tokens.extend(matches)
                continue
        tokens.append(token)  # length, /0, CCD, or native selector remains native
    return ','.join(tokens)


def lower_native(native, mapping, path):
    result = dict(native)
    result['input'] = str(path)
    for key in ('contig', 'unindex', 'select_fixed_atoms', 'select_unfixed_sequence',
                'select_hotspots', 'select_hbond_donor', 'select_hbond_acceptor',
                'select_buried', 'select_exposed', 'select_partially_buried', 'ligand'):
        if key in result:
            result[key] = lower_selection(result[key], mapping)
    return result
