"""Boltz-community 2.9.5 molecular transport, without sequence substitution.

Native authority: 7ebf1be087d4d61a02234c878402838bf3712d8b,
boltz/data/parse/schema.py (SHA256
c021b4b6b2f6d2fade7c2f949dfb773b5a75af11f1fbea0deb5aa128acd96201).
BMS component types remain BMS types until emission.
"""
from copy import deepcopy
import inspect
import warnings


def component_copies(components):
    """Retain explicit ordered IDs; extend count with unused alphabetic IDs.

    count denotes total copies, not repetitions of the ID list. Explicit IDs
    are authoritative if count is smaller; no authored endpoint is discarded.
    """
    result = deepcopy(components)
    for comp in result:
        comp.setdefault('type', 'protein')
        comp.setdefault('id', 'A')
        if comp.get('copy_ids'):
            comp['id'] = list(comp['copy_ids'])
        if comp['type'] == 'ion' and 'smiles' not in comp:
            comp.setdefault('ccd', 'MG')
    used = {i for c in result for i in (c.get('id', []) if isinstance(c.get('id'), list) else [c.get('id')])}
    def name(n):
        value = ''
        while n:
            n, r = divmod(n - 1, 26)
            value = chr(65 + r) + value
        return value
    cursor = 1
    for comp in result:
        ids = comp.get('id', [])
        ids = list(ids) if isinstance(ids, list) else [ids]
        for _ in range(max(int(comp.get('count') or 1), 1) - len(ids)):
            while name(cursor) in used:
                cursor += 1
            ids.append(name(cursor))
            used.add(ids[-1])
        comp['id'] = ids
    return result


def molecular_entry(component):
    """Only model-native molecular fields, preserving authored CCD and chemistry."""
    kind = {'peptide': 'protein', 'ion': 'ligand'}.get(component['type'], component['type'])
    keys = ('id', 'sequence', 'modifications', 'cyclic') if kind != 'ligand' else ('id', 'ccd', 'smiles')
    return {kind: {key: deepcopy(component[key]) for key in keys if key in component}}


def task_fields(payload):
    return {key: deepcopy(payload[key]) for key in ('constraints', 'templates', 'properties') if key in payload}


def chemistry_group_key(entity_type, sequence, item):
    """Native last-write-wins modifications define chemistry, not parent letters.

    Include cyclic topology and ligand encoding. Do not split by chain ID or MSA:
    identical chemistry still shares native entities and native MSA checks.
    """
    if entity_type in {'protein', 'dna', 'rna'}:
        modifications = {}
        for mod in item.get('modifications', []):
            modifications[mod['position']] = mod['ccd']
        return entity_type, sequence, tuple(sorted(modifications.items())), bool(item.get('cyclic', False))
    return entity_type, sequence, 'ccd' if 'ccd' in item else 'smiles'


def install_native_chemistry_fix():
    """Patch the in-memory native parser, never the installed package/image.

    Compile the native function with its original globals; every operation other
    than the defective entity grouping key stays native. Import before main so
    parse.yaml's function alias and spawned CLI workers see the corrected owner.
    """
    from boltz.data.parse import schema
    if getattr(schema.parse_boltz_schema, '_bms_chemistry', False):
        return
    source = inspect.getsource(schema.parse_boltz_schema)
    old = 'items_to_group.setdefault((entity_type, seq), []).append(item)'
    new = 'items_to_group.setdefault(_bms_chemistry_group_key(entity_type, seq, item[entity_type]), []).append(item)'
    if old not in source:
        warnings.warn('Boltz native grouping source differs from pinned 7ebf1be; retaining installed parser', RuntimeWarning)
        return
    from rdkit.Chem import Descriptors
    schema.__dict__['_bms_descriptors'] = Descriptors
    schema.__dict__['_bms_chemistry_group_key'] = chemistry_group_key
    source = source.replace(old, new).replace('AllChem.Descriptors.MolWt', '_bms_descriptors.MolWt')
    exec(compile(source, schema.__file__, 'exec'), schema.__dict__)
    schema.parse_boltz_schema._bms_chemistry = True
    # Also cover already-imported parser aliases (CPU qualification/embedded use).
    import sys
    for module_name in ('boltz.data.parse.yaml', 'boltz.main'):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, 'parse_boltz_schema'):
            module.parse_boltz_schema = schema.parse_boltz_schema
