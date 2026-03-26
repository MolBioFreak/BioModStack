#!/usr/bin/env nextflow
/**
 * PPIFlow Maturation Child Workflow
 * 
 * Standalone entry point for PPIFlow partial flow maturation on antibody designs.
 * Spawned by parent orchestrator for multi-GPU parallelization.
 * GPU assignment is handled by orchestrator/Nextflow scheduler - NOT hardcoded here.
 * 
 * Usage:
 *   nextflow run workflows/maturation_child.nf -c nextflow.config \
 *     --pdb_paths "/path/to/batch/*.pdb"
 */

nextflow.enable.dsl = 2

// Workflow-specific param defaults
params.pdb_paths = null
params.framework_type = 'standard-fv'
params.maturation_redesign_enabled = true
params.maturation_redesign_top_n = 0
params.ppiflow_require_anchors = true
params.ppiflow_selected_loops = null
params.maturation_selected_loops = null
params.selected_cdr_loops = null
params.ppiflow_region_mode = 'selected_cdrs'
params.ppiflow_maturation_region_mode = 'selected_cdrs'
params.ppiflow_backbone_region_mode = 'selected_cdrs'
params.ppiflow_mode = 'maturation'
params.maturation_stage_name = null
// 0 = use all

include { MATURATION_CHILD_CORE as MATURATION_CHILD_IMPL } from './maturation_child_core.nf'

workflow MATURATION_CHILD {
    take:
    pdb_list // List of PDB file paths

    main:
    MATURATION_CHILD_IMPL(pdb_list)

    emit:
    matured_pdbs = MATURATION_CHILD_IMPL.out.matured_pdbs
    scores = MATURATION_CHILD_IMPL.out.scores
}

// Entry point for direct invocation
workflow {
    // Validate inputs
    if (!params.pdb_paths) {
        error("--pdb_paths is required. Provide comma-separated PDB paths.")
    }

    // Parse PDB paths
    def pdb_paths_raw = params.pdb_paths.toString()
    def pdb_list = pdb_paths_raw
        .split(',')
        .collect { it.strip().replaceAll(/[\[\]'"]/, '') }
        .findAll { it }
        .collect { file(it) }

    if (pdb_list.isEmpty()) {
        error("No valid PDB files found in pdb_paths: ${params.pdb_paths}")
    }

    println("=" * 60)
    println("PPIFlow Maturation Child Workflow")
    println("=" * 60)
    println("* PDB paths: ${params.pdb_paths}")
    println("* Processing: ${pdb_list.size()} PDBs")
    println("* Redesign enabled: ${params.maturation_redesign_enabled}")
    println("* Top N selection: ${params.maturation_redesign_top_n ?: 'all'}")
    println("=" * 60)

    MATURATION_CHILD(pdb_list)

    println("PPIFlow maturation child job complete")
}
