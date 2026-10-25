"""Descriptions from rfproteina 8f7f702e metrics/amino_acid_composition.py.

Only this measured definition is claimed; undeclared metrics keep unknown units.
No admission rules or scientific values are introduced here.
"""
SOURCE = 'rfproteina.metrics.amino_acid_composition.AminoAcidCompositionMetric'
REVISION = '8f7f702e46f944713fbe8ff5c566221aaa54eb0b'
DEFINITIONS = {
    'n_generated_unknown_residues': ('residues', 'Generated UNK residues, excluding residues sequence-conditioned to UNK in the input.'),
    'n_standard_residues': ('residues', 'Standard amino-acid residues across all chains, including sequence-conditioned residues; nonstandard residues are excluded.'),
    **{f'{code.lower()}_fraction': ('fraction', f'{code} fraction among the 20 standard amino-acid residue types across all chains, including sequence-conditioned residues; undefined if there are no standard residues.') for code in ('ALA', 'VAL', 'GLY')},
}


def descriptors(metrics):
    return {f'amino_acid_composition.{name}': {'unit': unit, 'description': description,
            'source': SOURCE, 'source_revision': REVISION, 'value_type': 'number'}
            for name, (unit, description) in DEFINITIONS.items()
            if f'amino_acid_composition.{name}' in metrics}
