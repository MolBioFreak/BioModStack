#!/usr/bin/env nextflow
/**
 * FAMPNN Child Workflow
 * 
 * Standalone entry point for FAMPNN sequence design on a batch of backbone PDBs.
 * Spawned by an authorized parent orchestrator for multi-GPU parallelization.
 * GPU assignment is handled by orchestrator/Nextflow scheduler, not hardcoded.
 * 
 * Usage:
 *   nextflow run workflows/fampnn_child.nf -c nextflow.config \
 *     --pdb_paths "/path/to/batch/*.pdb" \
 *     --seqs_per_design 4
 */

nextflow.enable.dsl = 2

include { PrepFAMPNN ; FilterFAMPNN ; RunFAMPNN } from '../modules/fampnn.nf'

// Workflow-specific param defaults
params.pdb_paths = null
params.gpu_id = null
// Optional: orchestrator may pass, otherwise uses Nextflow GPU assignment
params.seqs_per_design = 4
params.analysis_chain_id = 'all_chains'
params.enable_fampnn_filter = true
params.fampnn_max_psce = null
params.fampnn_max_residue_psce = null

workflow FAMPNN_CHILD {
    take:
    pdb_list // List of PDB file paths
    chain_id // Chain to analyze (or 'all_chains')

    main:
    // GPU is assigned by Nextflow executor/label, or passed via params if orchestrator specifies
    def gpu_id_val = params.gpu_id ?: 0

    // Prepare FAMPNN input - PrepFAMPNN expects tuple [pdbs, jsons]
    def fampnn_prep_input = Channel.of(tuple(pdb_list, file("${params.code_root}/lib/NO_JSON")))

    PrepFAMPNN(fampnn_prep_input)

    // RunFAMPNN expects tuple [batch_id, pdbs, csv, gpu_id], analysis_chain_id
    def fampnn_run_input = PrepFAMPNN.out.pdbs
        .collect()
        .combine(PrepFAMPNN.out.csv)
        .map { payload ->
            tuple(0, FampnnAnalysisPolicy.stagePrepared(params, payload[0..-2]), payload[-1], gpu_id_val)
        }

    RunFAMPNN(fampnn_run_input, chain_id, FampnnAnalysisPolicy.forChild(params))

    // Optional filtering based on pSCE thresholds
    def filterEnabled = params.enable_fampnn_filter != false && (params.fampnn_max_psce != null || params.fampnn_max_residue_psce != null)

    if (filterEnabled) {
        FilterFAMPNN(RunFAMPNN.out.pdbs_jsons)
        output_pdbs = FilterFAMPNN.out.pdbs
        output_jsons = FilterFAMPNN.out.jsons
    }
    else {
        output_pdbs = RunFAMPNN.out.pdbs_jsons.map { pair -> pair[0] }
        output_jsons = RunFAMPNN.out.pdbs_jsons.map { pair -> pair[1] }
    }

    emit:
    pdbs = output_pdbs
    jsons = output_jsons
}

// Entry point for direct invocation
workflow {
    // Validate inputs
    if (!params.pdb_paths) {
        error("--pdb_paths is required. Provide comma-separated PDB paths or glob pattern.")
    }

    def chain_id = params.analysis_chain_id ?: 'all_chains'

    // Parse PDB paths (comes as comma-separated string or glob)
    def pdb_paths_raw = params.pdb_paths.toString()
    def pdb_list = pdb_paths_raw
        .split(',')
        .collect { pathStr -> pathStr.strip().replaceAll(/[\[\]'"]/, '') }
        .findAll { str -> str }
        .collect { validStr -> file(validStr) }

    if (pdb_list.isEmpty()) {
        error("No valid PDB files found in pdb_paths: ${params.pdb_paths}")
    }

    println("=" * 60)
    println("FAMPNN Child Workflow")
    println("=" * 60)
    println("* PDB paths: ${params.pdb_paths}")
    println("* Processing: ${pdb_list.size()} PDBs")
    println("* Seqs per design: ${params.seqs_per_design}")
    println("* Analysis chain: ${chain_id}")
    println("* Filtering: ${params.enable_fampnn_filter}")
    if (params.gpu_id != null) {
        println("* GPU override: ${params.gpu_id}")
    }
    if (params.fampnn_max_psce) {
        println("* Max pSCE: ${params.fampnn_max_psce}")
    }
    if (params.fampnn_max_residue_psce) {
        println("* Max residue pSCE: ${params.fampnn_max_residue_psce}")
    }
    println("=" * 60)

    FAMPNN_CHILD(pdb_list, chain_id)

    println("FAMPNN child workflow configured")
}
