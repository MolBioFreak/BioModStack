"""Bind ordinary Protenix file selections to compiler-owned input artifacts."""
from copy import deepcopy
from pathlib import Path


def recipe_from_source_positions(positions, declared):
    """Project observed chemistry into the existing ordered component contract.

    No coordinate-derived bonds, assembly expansion or guessed CCD aliases.
    Unresolved observations stay beside the editable recipe, never an admission gate.
    """
    from Bio.Data.PDBData import protein_letters_3to1, nucleic_letters_3to1_extended

    # A canonical sibling is evidence for an otherwise untyped polymer chain;
    # unknown positions in that same chain must not disappear from its sequence.
    protein_chains = {(r['model_number'], r.get('label_asym_id') or r.get('auth_asym_id'))
                      for r in positions if r.get('polymer_membership') is not False and r.get('canonical_amino_acid')}
    nucleic_chains = {}
    for row in positions:
        if row.get('hetero_flag') != ' ':
            continue
        code = row['component_id']
        kind = 'dna' if code in {'DA', 'DC', 'DG', 'DT', 'DI'} else 'rna' if code in {'A', 'C', 'G', 'U', 'I'} else None
        if kind:
            key = (row['model_number'], row.get('auth_asym_id'))
            previous = nucleic_chains.get(key, kind)
            nucleic_chains[key] = kind if previous == kind else 'unresolved'
    groups = {}
    for row in positions:
        polymer = row.get('polymer_type') or ''
        canonical = row.get('canonical_amino_acid')
        # PDB has no entity table; ATOM records and the retained canonical
        # mapping are evidence, not a reason to discard HETATM modifications.
        pdb_nucleic = nucleic_chains.get((row['model_number'], row.get('auth_asym_id'))) if row.get('evidence_origin') == 'PDB ATOM/HETATM' else None
        kind = ('dna' if polymer == 'polydeoxyribonucleotide' else
                'rna' if polymer == 'polyribonucleotide' else
                pdb_nucleic if pdb_nucleic else
                'protein' if polymer.startswith('polypeptide') or
                (row.get('polymer_membership') is not False and
                 (canonical or
                  (row['model_number'], row.get('label_asym_id') or row.get('auth_asym_id')) in protein_chains)) else None)
        if kind is None and (row.get('polymer_membership') is True or row.get('hetero_flag') == ' '):
            kind = 'unresolved'
        chain = row.get('label_asym_id') or row.get('auth_asym_id')
        key = (row['model_number'], chain, kind)
        if kind is None:
            key += (row.get('auth_seq_id'), row.get('pdbx_PDB_ins_code'), row['component_id'])
        groups.setdefault(key, []).append(row)
    components, entries, unresolved, endpoints = [], [], [], []
    for key, rows in groups.items():
        model, chain, kind = key[:3]
        if kind == 'unresolved':
            unresolved.extend(rows)
            continue
        identifier = str(chain or 'entity')
        if any(c['id'] == identifier for c in components):
            identifier = f'{identifier}:{len(components) + 1}'
        component = dict(id=identifier, type=kind or 'ligand', count=1)
        if kind:
            letters, modifications = [], []
            for index, row in enumerate(rows, 1):
                code = row['component_id']
                letter = (row.get('canonical_amino_acid') if kind == 'protein' else
                          nucleic_letters_3to1_extended.get(code.ljust(3)))
                letters.append(letter or ('X' if kind == 'protein' else 'N'))
                if not letter:
                    unresolved.append(row)
                standard = code in protein_letters_3to1 if kind == 'protein' else code in ({'DA', 'DC', 'DG', 'DT'} if kind == 'dna' else {'A', 'C', 'G', 'U'})
                if not standard and letter:
                    modifications.append({'ptmPosition': index, 'ptmType': 'CCD_' + code} if kind == 'protein' else
                                         {'basePosition': index, 'modificationType': 'CCD_' + code})
            component['sequence'] = ''.join(letters)
            if modifications:
                component['modifications'] = modifications
        else:
            component['ccd'] = rows[0]['component_id']
        components.append(component)
        entries.append(dict(id=identifier, model_number=model, auth_asym_id=rows[0].get('auth_asym_id'), positions=rows))
        for index, row in enumerate(rows, 1):
            endpoint = dict(entity=len(components), position=index, copy=1)
            endpoints.append((model, row, endpoint))
    bonds = []
    for bond in declared.get('struct_conn', []):
        if bond.get('conn_type_id') not in {'covale', 'disulf'}:
            continue
        for model in dict.fromkeys(r['model_number'] for r in positions):
            pair = []
            for i in (1, 2):
                candidates = []
                for number, row, endpoint in endpoints:
                    if number != model:
                        continue
                    supplied = {field: bond.get(f'ptnr{i}_{field}') for field in
                                ('label_asym_id', 'label_seq_id', 'auth_asym_id', 'auth_seq_id')}
                    supplied = {k: v for k, v in supplied.items() if v not in {None, '.', '?'}}
                    atom = bond.get(f'ptnr{i}_label_atom_id')
                    if supplied and all(row.get(k) == v for k, v in supplied.items()) and atom not in {None, '.', '?'}:
                        insertion = bond.get(f'pdbx_ptnr{i}_PDB_ins_code')
                        if insertion not in {None, '.', '?'} and row.get('pdbx_PDB_ins_code') != insertion:
                            continue
                        candidates.append(endpoint)
                pair.append(candidates[0] if len(candidates) == 1 else None)
            if not all(pair):
                unresolved.append({'bond': bond, 'model_number': model})
                continue
            native = {}
            for side, i, endpoint in zip(('left', 'right'), (1, 2), pair):
                native.update({f'{side}_{k}': v for k, v in endpoint.items()})
                native[f'{side}_atom'] = bond[f'ptnr{i}_label_atom_id']
            bonds.append(native)
    return dict(complex_components=components, covalent_bonds=bonds, entries=entries, unresolved=unresolved, declared_chemistry=deepcopy(declared))


def resolve_ligand_file(value):
    from paths import resolve_allowed_path
    if Path(value).is_absolute():
        raise ValueError('ligand_file requires a managed file selection, not a runtime path')
    source = resolve_allowed_path(value)
    if not source.is_file() or source.suffix.lower() not in ('.mol', '.mol2', '.sdf', '.pdb'):
        raise ValueError('ligand_file must select an existing managed mol/mol2/sdf/pdb file')
    return source


def validate_component_files(params):
    for component in params.get('complex_components') or []:
        if component.get('ligand_file'):
            resolve_ligand_file(component['ligand_file'])


def stage_component_files(params, *, output_dir, write_input):
    params = deepcopy(params)
    for index, component in enumerate(params.get('complex_components') or []):
        if component.get('ligand_file'):
            source = resolve_ligand_file(component['ligand_file'])
            destination = Path(output_dir) / 'protenix_ligands' / f'{index + 1}{source.suffix.lower()}'
            write_input(destination, source.read_bytes())
            component['ligand_file'] = str(destination)
    return params
