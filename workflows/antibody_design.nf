#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Antibody Design Toolkit - Supports 4 modes
// Consolidates all antibody pipeline logic into a single reusable workflow

include { ANARCI } from '../modules/utils/anarci'
include { ANTIFOLD } from '../modules/antifold'
include { IMMUNEBUILDER } from '../modules/immunebuilder'
include { THERMOMPNN } from '../modules/thermompnn'
include { RFANTIBODY } from '../modules/rfantibody'

workflow ANTIBODY_DESIGN {
    take:
    input_ch // [meta, payload] - varies by mode
    mode // val: 'structure_prediction', 'inverse_folding', 'stability_prediction', 'de_novo'

    main:
    // Initialize output channels as empty
    designs_ch = channel.empty()
    stability_ch = channel.empty()
    probs_ch = channel.empty()
    cdrs_ch = channel.empty()

    // Mode: structure_prediction
    // Input: [meta, fasta] -> IMMUNEBUILDER
    if (mode == 'structure_prediction') {
        IMMUNEBUILDER(input_ch)
        designs_ch = IMMUNEBUILDER.out.structure
    }
    else if (mode == 'inverse_folding') {
        ANARCI(input_ch)
        ANTIFOLD(ANARCI.out.pdb_imgt)
        IMMUNEBUILDER(ANTIFOLD.out.sequences)
        THERMOMPNN(IMMUNEBUILDER.out.structure)

        designs_ch = IMMUNEBUILDER.out.structure
        stability_ch = THERMOMPNN.out.stability
        probs_ch = ANTIFOLD.out.probabilities
        cdrs_ch = ANARCI.out.cdrs
    }
    else if (mode == 'stability_prediction') {
        ANARCI(input_ch)
        THERMOMPNN(ANARCI.out.pdb_imgt)

        stability_ch = THERMOMPNN.out.stability
        cdrs_ch = ANARCI.out.cdrs
    }
    else if (mode == 'de_novo') {
        RFANTIBODY(input_ch)
        THERMOMPNN(RFANTIBODY.out.designs)

        designs_ch = RFANTIBODY.out.designs
        stability_ch = THERMOMPNN.out.stability
    }

    emit:
    designs = designs_ch
    stability = stability_ch
    probs = probs_ch
    cdrs = cdrs_ch
}
