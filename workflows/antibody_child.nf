#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// =============================================================================
// Antibody Child Workflow - Single Design Validation
// =============================================================================
// Runs structure validation and scoring for a single antibody design.
// Spawned by parent antibody_denovo workflow in exploration mode.
// Each child job is independently scheduled by GPU orchestrator.
// =============================================================================

// =============================================================================
// Antibody Child Workflow - BATCH Validation
// =============================================================================
// Runs structure validation and scoring for a BATCH of antibody designs
// sharing the same backbone.
// Spawned by parent antibody_denovo workflow in exploration mode.
// =============================================================================

include { BatchBoltzValidation ; BatchImmunogenicity ; BatchStability } from '../modules/antibody_batch'

workflow ANTIBODY_CHILD {
    take:
    pdb_paths   // List of PDB files (stage them all)
    msa_path    // Path to shared MSA file (or empty string)
    
    main:
    // Prepare MSA file (use provided or generate if needed)
    def msa_file = msa_path ? file(msa_path) : file("${projectDir}/lib/NO_MSA")
    
    // Convert input paths to file objects if they aren't already
    def pdb_files = pdb_paths.collect { file(it) }
    
    // =========================================================================
    // Step 1: Batch Structure Validation with Boltz2
    // =========================================================================
    // Processes all sequences in one Boltz execution (highly efficient)
    BatchBoltzValidation(pdb_files, msa_file)
    
    // =========================================================================
    // Step 2: Batch Scoring (Parallel)
    // =========================================================================
    // Use the *validated* structures from Boltz (folded) for downstream scoring
    // Or prefer the designed sequences? Usually we score the predicted structure.
    // BatchBoltzValidation outputs a directory of PDBs.
    
    // Note: BatchBoltzValidation.out.pdbs is a list of files.
    BatchImmunogenicity(BatchBoltzValidation.out.pdbs)
    BatchStability(BatchBoltzValidation.out.pdbs)
    
    // =========================================================================
    // Aggregate Results
    // =========================================================================
    
    emit:
    boltz_pdbs = BatchBoltzValidation.out.pdbs
    boltz_scores = BatchBoltzValidation.out.scores
    antiberty_scores = BatchImmunogenicity.out.scores
    thermompnn_scores = BatchStability.out.scores
}

// Entry point when run as standalone workflow
workflow {
    // Read params from job submission
    // Support both single pdb_path (legacy) and pdb_paths (batch list)
    
    def msa_path = params.msa_path ?: ""
    def pdbs_to_process = []
    
    if (params.pdb_paths) {
        // Parse list from string "[path1, path2]" or JSON
        def clean_paths = params.pdb_paths.toString().replace('[','').replace(']','').split(',')
        pdbs_to_process = clean_paths.collect { it.strip() }.findAll { it }
    } else if (params.pdb_path) {
        // Legacy single mode
        pdbs_to_process = [params.pdb_path]
    } else {
        error "No input PDBs provided. Use --pdb_paths or --pdb_path"
    }
    
    ANTIBODY_CHILD(pdbs_to_process, msa_path)
}
