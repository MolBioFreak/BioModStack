"""Discovery for distinct native structure dialects; not a submission gate.

The native preparation owners retain numerical and validation authority.
"""


def _object(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def _array(items):
    return {'type': 'array', 'items': items}


def parameter_schema(model_id):
    """Expose model-native chemistry alongside the model's scalar catalog."""
    string = {'type': 'string'}
    number = {'type': 'number'}
    boolean = {'type': 'boolean'}
    zero = {'type': 'integer', 'minimum': 0}
    one = {'type': 'integer', 'minimum': 1}
    ids = {'oneOf': [string, _array(string)]}
    boltz = model_id == 'boltz2'
    modification = _object({'position': one if boltz else zero, 'ccd': string}, ('position', 'ccd'))
    component = {
        'type': {'enum': ['protein', 'peptide', 'dna', 'rna', 'ligand', 'ion']},
        'id': ids, 'copy_ids': _array(string), 'count': one, 'name': string,
        'sequence': string, 'smiles': string, 'ccd': ids,
        'modifications': _array(modification), 'msa_path': string,
    }
    if boltz:
        component.update(cyclic=boolean, msa=string)
        atom = {'type': 'array', 'prefixItems': [string, one, string], 'minItems': 3, 'maxItems': 3}
        token = {'type': 'array', 'prefixItems': [string, {'oneOf': [one, string]}], 'minItems': 2, 'maxItems': 2}
        constraints = _array({'oneOf': [
            _object({'bond': _object({'atom1': atom, 'atom2': atom}, ('atom1', 'atom2'))}, ('bond',)),
            _object({'contact': _object({'token1': token, 'token2': token, 'max_distance': number, 'force': boolean}, ('token1', 'token2', 'max_distance'))}, ('contact',)),
            _object({'pocket': _object({'binder': string, 'contacts': _array(token), 'max_distance': number, 'force': boolean}, ('binder', 'contacts'))}, ('pocket',)),
        ]})
        fields = {
            'constraints': constraints,
            'properties': _array(_object({'affinity': _object({'binder': string}, ('binder',))}, ('affinity',))),
            'templates': _array(_object({'cif': string, 'pdb': string, 'chain_id': ids, 'template_id': ids, 'force': boolean, 'threshold': number})),
            'boltz_seed': {'type': ['integer', 'null'], 'minimum': 0},
        }
    else:
        component.update(msa={'oneOf': [{'type': 'null'}, _object({'sequences': _array(string)}, ('sequences',))]},
                         msa_format={'enum': ['auto', 'a3m', 'stockholm']},
                         msa_max_sequences={'type': ['integer', 'null'], 'minimum': 1},
                         msa_remove_insertions=boolean)
        bond = {key: string if key.startswith('chain') else zero for key in
                ('chain_id1', 'res_idx1', 'atom_idx1', 'chain_id2', 'res_idx2', 'atom_idx2')}
        fields = {
            'covalent_bonds': _array(_object(bond, tuple(bond))),
            'distogram_conditioning': _array(_object({'chain_id': string, 'distogram': _array(_array(number))}, ('chain_id', 'distogram'))),
        }
    fields['complex_components'] = _array(_object(component))
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': model_id + ' native molecular settings', 'schema_version': 1,
            'type': 'object', 'properties': fields,
            'description': 'Chemistry subset; scalar settings remain in the model parameter catalog. Native validation remains with this model.'}
