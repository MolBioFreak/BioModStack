#!/usr/bin/env nextflow
/**
 * RFantibody Backbone Workflow
 * 
 * Standalone entry point for RFantibody backbone generation.
 * GPU assignment is handled by orchestrator/Nextflow scheduler, not hardcoded.
 * 
 * Usage:
 *   nextflow run workflows/rfantibody_backbone.nf -c nextflow.config \
 *     --target_pdb /path/to/antigen.pdb \
 *     --epitope_residues "A:45,A:46,A:47" \
 *     --rfantibody_num_designs 10
 */

nextflow.enable.dsl = 2

include { RFANTIBODY } from '../modules/rfantibody'

// Workflow-specific param defaults
params.target_pdb = null
params.epitope_residues = ''
params.rfantibody_num_designs = 10
params.gpu_id = null  // Optional: orchestrator may pass, otherwise uses Nextflow GPU assignment
params.framework_pdb = null
params.sequence_name = 'rfantibody_child'

workflow RFANTIBODY_BACKBONE {
    take:
        target_pdb          // Path to antigen PDB
        epitope_residues    // Hotspot residues (e.g., "A:45,A:46")
        num_designs         // Number of backbone designs
        framework_pdb       // Optional framework PDB (or dummy file)
    
    main:
        def meta = [id: params.sequence_name ?: 'rfantibody_child']
        
        // GPU assigned by orchestrator via params, or default to 0 for local runs
        def gpu_id_val = params.gpu_id ?: 0
        
        // Prepare input tuple: [meta, target_pdb, hotspots, gpu_id, num_designs]
        def rfantibody_input = channel.of(
            tuple(meta, target_pdb, epitope_residues, gpu_id_val, num_designs)
        )
        
        RFANTIBODY(rfantibody_input, framework_pdb)
    
    emit:
        backbones = RFANTIBODY.out.backbones
        metrics = RFANTIBODY.out.metrics
}

// Entry point for direct invocation
workflow {
    // Validate inputs
    if (!params.target_pdb) {
        error("--target_pdb is required. Provide path to antigen PDB.")
    }
    
    def target = file(params.target_pdb)
    def epitope = params.epitope_residues ?: ''
    def num_designs = params.rfantibody_num_designs ?: 10
    
    // Use framework from params or dummy file for default
    def framework = params.framework_pdb
        ? file(params.framework_pdb)
        : file("${projectDir}/lib/NO_FRAMEWORK")
    
    println("=" * 60)
    println("RFantibody Backbone Workflow")
    println("=" * 60)
    println("* Target PDB: ${params.target_pdb}")
    println("* Epitope: ${epitope ?: 'auto-detect'}")
    println("* Num designs: ${num_designs}")
    println("* Framework: ${params.framework_pdb ?: 'default'}")
    if (params.gpu_id != null) println("* GPU override: ${params.gpu_id}")
    println("=" * 60)
    
    RFANTIBODY_BACKBONE(target, epitope, num_designs, framework)
    
    println("RFantibody backbone generation complete")
}
