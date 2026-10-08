#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// =============================================================================
// Antibody Child Workflow - Single Design Validation
// =============================================================================
// Runs structure validation and scoring for a single antibody design.
// Spawned by parent antibody_denovo workflow in exploration mode.
// Each child job is independently scheduled by GPU orchestrator.
// =============================================================================

include { BoltzFromSequenceWithMSA } from '../modules/structure_prediction'
include { ANTIBERTY_SCORE } from '../modules/antiberty'
include { THERMOMPNN } from '../modules/thermompnn'

workflow ANTIBODY_CHILD {
    take:
    pdb_path      // Path to design PDB
    sequence      // Amino acid sequence
    msa_path      // Path to MSA file (or empty string)
    
    main:
    // =========================================================================
    // Step 1: Structure Validation with Boltz2
    // =========================================================================
    
    // Prepare MSA file (use provided or generate if needed)
    def msa_file = msa_path ? file(msa_path) : file("${projectDir}/lib/NO_MSA")
    def design_name = file(pdb_path).baseName
    
    // Create input channel for BoltzFromSequenceWithMSA
    boltz_input = Channel.of(tuple(sequence, design_name, msa_file))
    
    BoltzFromSequenceWithMSA(boltz_input)
    
    // =========================================================================
    // Step 2: Immunogenicity Scoring with AntiBERTy
    // =========================================================================
    
    input_pdb = file(pdb_path)
    antiberty_input = Channel.of(tuple([id: design_name], input_pdb))
    
    ANTIBERTY_SCORE(antiberty_input)
    
    // =========================================================================
    // Step 3: Stability Scoring with ThermoMPNN
    // =========================================================================
    
    THERMOMPNN(input_pdb)
    
    // =========================================================================
    // Aggregate Results
    // =========================================================================
    
    // Emit all results for aggregation
    emit:
    boltz_pdbs = BoltzFromSequenceWithMSA.out.pdbs
    boltz_jsons = BoltzFromSequenceWithMSA.out.jsons
    antiberty_scores = ANTIBERTY_SCORE.out.scores
    thermompnn_scores = THERMOMPNN.out.scores
}

// Entry point when run as standalone workflow
workflow {
    // Read params from job submission
    def pdb_path = params.pdb_path
    def sequence = params.sequence
    def msa_path = params.msa_path ?: ""
    
    ANTIBODY_CHILD(pdb_path, sequence, msa_path)
}
