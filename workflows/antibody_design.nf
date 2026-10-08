include { ANARCI } from '../modules/utils/anarci'
include { ANTIFOLD } from '../modules/antifold'
include { IMMUNEBUILDER } from '../modules/immunebuilder'
include { THERMOMPNN } from '../modules/thermompnn'

workflow ANTIBODY_DESIGN {
    take:
    input_channel // tuple(meta, pdb)

    main:
    // 1. Numbering and CDR extraction
    ANARCI(input_channel)

    // 2. Inverse Folding (Sequence Design)
    ANTIFOLD(ANARCI.out.pdb_imgt)

    // 3. Structure Prediction of designed sequences
    IMMUNEBUILDER(ANTIFOLD.out.sequences)

    // 4. Stability Analysis
    THERMOMPNN(IMMUNEBUILDER.out.structure)

    emit:
    designs = IMMUNEBUILDER.out.structure
    stability = THERMOMPNN.out.stability
    probs = ANTIFOLD.out.probabilities
}
