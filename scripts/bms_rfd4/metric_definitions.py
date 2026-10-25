"""Measured definitions from rfproteina 8f7f702e metric implementations.

Undeclared metrics keep unknown units; native names and values are unchanged.
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
    definitions = {f'amino_acid_composition.{name}': (unit, description, SOURCE)
                   for name, (unit, description) in DEFINITIONS.items()}
    sse = 'rfproteina.metrics.sse_metrics.SecondaryStructureDistribution.compute'
    for suffix, scope in (('', 'all valid tokens'), ('_coi', 'valid tokens in the native chain of interest')):
        for kind in ('helix', 'sheet', 'loop'):
            definitions[f'sse_distribution.{kind}_fraction{suffix}'] = ('fraction',
                f'P-SEA {kind} fraction over {scope}; nanmean across input structures; undefined when no valid annotations exist.', sse)
        definitions[f'sse_distribution.num_ss_elements{suffix}'] = ('regions',
            f'Contiguous SSE-code regions over {scope}, including loop regions; nanmean across structures, not a helix/sheet-only count.', sse)
    seq = 'rfproteina.metrics.sequence_adherence_metric.SequenceAdherenceMetric.compute'
    definitions['sequence_adherence.sequence_adherence'] = ('fraction',
        'Fraction of C_SEQ-conditioned residues whose generated res_name equals the conditioned identity; undefined without conditioned residues.', seq)
    definitions['sequence_adherence.n_violations'] = ('residues',
        'C_SEQ-conditioned residues whose generated res_name differs from the conditioned identity; zero when none are conditioned.', seq)
    probability = 'rfproteina.metrics.sequence_adherence_metric.SequenceProbabilityAdherenceMetric.compute'
    for kind in ('zero', 'one'):
        definitions[f'sequence_probability_adherence.{kind}_adherence'] = ('fraction',
            f'One minus violated exact-{kind} sequence-probability conditions divided by the number of those conditions; undefined when no such conditions exist.', probability)
        for field, meaning in (('conditions', 'conditioned residue/token-identity pairs'), ('violations', 'violated conditioned residue/token-identity pairs')):
            definitions[f'sequence_probability_adherence.n_{kind}_{field}'] = ('conditions',
                f'Number of {meaning} with probability exactly {kind}; this is not a residue count or a soft-probability error.', probability)
    return {name: {'unit': unit, 'description': description, 'source': source,
                   'source_revision': REVISION, 'value_type': 'number'}
            for name, (unit, description, source) in definitions.items() if name in metrics}
