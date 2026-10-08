#!/usr/bin/env nextflow
/**
 * BoltzGen Design Workflow
 * 
 * Standalone entry point for BoltzGen all-atom binder generation.
 * Supports both standalone execution and SWA (Spawn-Wait-Aggregate) parallel mode.
 * GPU assignment is handled by orchestrator/Nextflow scheduler - NOT hardcoded here.
 * 
 * Usage:
 *   nextflow run workflows/boltzgen_design.nf -c nextflow.config \
 *     --boltzgen_target_pdb_path /path/to/target.pdb \
 *     --boltzgen_num_designs 100 \
 *     --boltzgen_mode nanobody_binder
 */

nextflow.enable.dsl = 2

include { PrepBoltzGenInput ; RunBoltzGen ; FilterBoltzGen ; SpawnBoltzGenJobs ; WaitForBoltzGenChildren ; CollectBoltzGenOutputs ; AggregateBoltzGenResults } from '../modules/boltzgen.nf'

// Workflow-specific param defaults (inherit from nextflow.config when present)
params.boltzgen_target_pdb_path = null
params.boltzgen_num_designs = 8
params.boltzgen_scaffold_length = 80
params.boltzgen_mode = 'nanobody_binder'
params.boltzgen_parallel_mode = false
params.boltzgen_designs_per_job = 100
params.run_boltzgen_only = true

workflow BOLTZGEN_DESIGN {
    main:
        // Prepare input config from params
        PrepBoltzGenInput(
            params.boltzgen_ligand_smiles ?: '',
            params.boltzgen_ntp_type ?: '',
            params.boltzgen_scaffold_length,
            params.boltzgen_num_designs,
            params.boltzgen_binding_site_residues ?: '',
            params.boltzgen_catalytic_site ?: false,
            params.boltzgen_protein_sequence ?: '',
            params.boltzgen_dna_template_seq ?: '',
            params.boltzgen_dna_primer_seq ?: '',
            params.boltzgen_secondary_structure ?: '',
            params.boltzgen_protocol ?: 'protein-anything',
            params.boltzgen_covalent_bonds ?: '',
            params.boltzgen_nanobody_framework ?: '',
            params.boltzgen_cdr_h1_length ?: '5-8',
            params.boltzgen_cdr_h2_length ?: '6-10',
            params.boltzgen_cdr_h3_length ?: '12-18',
            params.boltzgen_input_pdb ? file(params.boltzgen_input_pdb) : file("${params.code_root}/lib/NO_INPUT_PDB"),
            params.boltzgen_ligand_pdb ? file(params.boltzgen_ligand_pdb) : file("${params.code_root}/lib/NO_LIGAND_PDB"),
            params.boltzgen_dna_structure ? file(params.boltzgen_dna_structure) : file("${params.code_root}/lib/NO_DNA_STRUCT"),
            params.boltzgen_target_pdb_path ? file(params.boltzgen_target_pdb_path) : file("${params.code_root}/lib/NO_TARGET_PDB"),
        )
        
        // Determine execution mode
        def parallel_mode_set = params.containsKey('parallel_mode')
        def use_orchestrator = parallel_mode_set
            ? (params.parallel_mode == 'full_orchestrator')
            : (params.boltzgen_parallel_mode == true)
        
        if (use_orchestrator) {
            // SWA Pattern: Spawn child jobs for large campaigns
            println("BoltzGen PARALLEL MODE: Spawning ${Math.ceil(params.boltzgen_num_designs / params.boltzgen_designs_per_job)} child jobs")
            
            def target_pdb = params.boltzgen_target_pdb_path 
                ? file(params.boltzgen_target_pdb_path) 
                : file("${params.code_root}/lib/NO_TARGET_PDB")
            
            SpawnBoltzGenJobs(
                params.job_id ?: 'unknown',
                params.boltzgen_num_designs,
                params.boltzgen_designs_per_job ?: 100,
                PrepBoltzGenInput.out.yaml,
                target_pdb,
                params.boltzgen_mode ?: 'nanobody_binder',
                params.name ?: 'boltzgen_campaign',
            )
            
            WaitForBoltzGenChildren(
                params.job_id ?: 'unknown',
                SpawnBoltzGenJobs.out.result,
            )
            
            CollectBoltzGenOutputs(WaitForBoltzGenChildren.out.result)
            
            AggregateBoltzGenResults(
                params.job_id ?: 'unknown',
                CollectBoltzGenOutputs.out.pdbs.collect(),
                CollectBoltzGenOutputs.out.jsons.collect(),
                CollectBoltzGenOutputs.out.manifest,
            )
            
            output_pdbs = CollectBoltzGenOutputs.out.pdbs.flatten().collect()
            output_jsons = CollectBoltzGenOutputs.out.jsons.flatten().collect()
        }
        else {
            // Single-process execution
            RunBoltzGen(PrepBoltzGenInput.out.yaml)
            FilterBoltzGen(RunBoltzGen.out.pdbs, RunBoltzGen.out.jsons)
            
            output_pdbs = FilterBoltzGen.out.pdbs.flatten().collect()
            output_jsons = FilterBoltzGen.out.jsons.flatten().collect()
        }
    
    emit:
        pdbs = output_pdbs
        jsons = output_jsons
}

// Entry point for direct invocation
workflow {
    println("=" * 60)
    println("BoltzGen Design Workflow")
    println("=" * 60)
    println("* Target PDB: ${params.boltzgen_target_pdb_path ?: 'not specified'}")
    println("* Num designs: ${params.boltzgen_num_designs}")
    println("* Scaffold length: ${params.boltzgen_scaffold_length}")
    println("* Mode: ${params.boltzgen_mode}")
    println("* Protocol: ${params.boltzgen_protocol ?: 'protein-anything'}")
    println("* Parallel mode: ${params.boltzgen_parallel_mode}")
    if (params.boltzgen_ligand_smiles) println("* Ligand SMILES: ${params.boltzgen_ligand_smiles}")
    if (params.boltzgen_nanobody_framework) println("* Nanobody framework: ${params.boltzgen_nanobody_framework}")
    println("=" * 60)
    
    BOLTZGEN_DESIGN()
    
    println("BoltzGen design complete")
}
