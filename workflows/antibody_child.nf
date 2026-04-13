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

include { BatchBoltzValidation ; BatchProtenixValidation ; BatchImmunogenicity ; BatchStability } from '../modules/antibody_batch'

def resolveBooleanParam(value, defaultValue = false) {
    if (value == null) {
        return defaultValue
    }
    if (value instanceof Boolean) {
        return value
    }
    return value.toString().equalsIgnoreCase('true')
}

workflow ANTIBODY_CHILD {
    take:
    pdb_paths // List of PDB files (stage them all)
    msa_path // Path to shared MSA file (or empty string)

    main:
    // Prepare MSA file (use provided or generate if needed)
    def msa_file = msa_path ? file(msa_path) : file("${params.code_root}/lib/NO_MSA")

    // Convert input paths to file objects if they aren't already
    def pdb_files = pdb_paths.collect { pathStr -> file(pathStr) }

    def structure_validator = (params.structure_validator ?: 'boltz2').toString().toLowerCase()
    if (!(structure_validator in ['boltz2', 'protenix'])) {
        log.warn("Unknown structure_validator '${structure_validator}', defaulting to boltz2")
        structure_validator = 'boltz2'
    }

    // =========================================================================
    // Step 1: Batch Structure Validation
    // =========================================================================
    def validated_pdbs_ch = channel.empty()
    def validation_scores_ch = channel.empty()

    if (structure_validator == 'protenix') {
        BatchProtenixValidation(pdb_files, msa_file)
        validated_pdbs_ch = BatchProtenixValidation.out.pdbs
        validation_scores_ch = BatchProtenixValidation.out.scores
    } else {
        BatchBoltzValidation(pdb_files, msa_file)
        validated_pdbs_ch = BatchBoltzValidation.out.pdbs
        validation_scores_ch = BatchBoltzValidation.out.scores
    }

    // =========================================================================
    // Step 2: Batch Scoring (Conditional)
    // =========================================================================
    // Use the *validated* structures from Boltz (folded) for downstream scoring

    // ThermoMPNN stability scoring - only if enabled
    def run_thermompnn = resolveBooleanParam(params.run_thermompnn, false)
    if (run_thermompnn) {
        BatchStability(validated_pdbs_ch)
        thermompnn_scores = BatchStability.out.scores
    }
    else {
        // Empty channel placeholder
        thermompnn_scores = channel.empty()
    }

    // AntiBERTy immunogenicity scoring - only if enabled  
    def run_immunogenicity = resolveBooleanParam(params.run_immunogenicity_scoring, false)
    if (run_immunogenicity) {
        BatchImmunogenicity(validated_pdbs_ch)
        antiberty_scores = BatchImmunogenicity.out.scores
    }
    else {
        // Empty channel placeholder
        antiberty_scores = channel.empty()
    }

    emit:
    validated_pdbs = validated_pdbs_ch
    validation_scores = validation_scores_ch
    antiberty_scores = antiberty_scores
    thermompnn_scores = thermompnn_scores
}

// Entry point when run as standalone workflow
workflow {
    // Read params from job submission
    // Support both single pdb_path (legacy) and pdb_paths (batch list)

    def msa_path = params.msa_path ?: ""
    def pdbs_to_process = []

    if (params.pdb_paths) {
        // Parse list from string "[path1, path2]" or JSON
        def clean_paths = params.pdb_paths.toString().replace('[', '').replace(']', '').split(',')
        pdbs_to_process = clean_paths.collect { pathStr -> pathStr.trim() }.findAll { pathStr -> pathStr }
    }
    else if (params.pdb_path) {
        // Legacy single mode
        pdbs_to_process = [params.pdb_path]
    }
    else {
        error("No input PDBs provided. Use --pdb_paths or --pdb_path")
    }

    ANTIBODY_CHILD(pdbs_to_process, msa_path)
}
