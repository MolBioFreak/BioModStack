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
[
    boltzgen_target_pdb_path: null,
    boltzgen_num_designs: 8,
    boltzgen_scaffold_length: 80,
    boltzgen_mode: 'nanobody_binder',
    boltzgen_parallel_mode: false,
    boltzgen_designs_per_job: 100,
    run_boltzgen_only: true,
].each { key, value ->
    params.put(key, value)
}

def boltzgenDefaults = [
    boltzgen_ligand_smiles: null,
    boltzgen_ntp_type: null,
    boltzgen_binding_site_residues: null,
    boltzgen_catalytic_site: false,
    boltzgen_protein_sequence: null,
    boltzgen_dna_template_seq: null,
    boltzgen_dna_primer_seq: null,
    boltzgen_dna_structure: null,
    boltzgen_secondary_structure: null,
    boltzgen_protocol: 'protein-anything',
    boltzgen_covalent_bonds: null,
    boltzgen_nanobody_framework: null,
    boltzgen_nanobody_scaffold_specs: null,
    boltzgen_cdr_h1_length: '5-8',
    boltzgen_cdr_h2_length: '6-10',
    boltzgen_cdr_h3_length: '12-18',
    boltzgen_input_pdb: null,
    boltzgen_ligand_pdb: null,
    boltzgen_step_scale: null,
    boltzgen_noise_scale: null,
    boltzgen_inverse_fold_avoid: null,
    boltzgen_inverse_fold_num_sequences: null,
    boltzgen_checkpoint_mode: null,
    boltzgen_skip_inverse_folding: false,
    boltzgen_reuse: false,
    boltzgen_min_plddt: null,
    boltzgen_min_conf_score: null,
    boltzgen_refolding_rmsd_threshold: null,
    boltzgen_max_rmsd: null,
    boltzgen_budget: null,
    boltzgen_alpha: '0.01',
    boltzgen_filter_biased: true,
    boltzgen_metrics_override: null,
    boltzgen_additional_filters: null,
    boltzgen_size_buckets: null,
    boltzgen_extra_config: null,
    boltzgen_extra_params: null,
    boltzgen_batch_size: null,
    boltzgen_diffusion_batch_size: null,
    boltzgen_parallel_mode: false,
    interactive_swa: false,
    interactive_gating: false,
    interactive_gate_stage: 'post_boltzgen',
    interactive_gate_continue: false,
    framework_type: 'nanobody',
    antibody_chains: 'H',
    structure_validator: 'boltz2',
]

boltzgenDefaults.each { key, value ->
    if (!params.containsKey(key)) {
        params[key] = value
    }
}

process OpenInteractiveGate {
    label 'process_low'

    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: "*.json"

    input:
    val job_id
    val stage_name
    val gate_trigger
    val candidate_dir
    val raw_dir
    val filtered_dir
    val framework_type
    val antibody_chains
    val structure_validator

    output:
    path "gate_${stage_name}.json", emit: report

    script:
    def filteredArg = filtered_dir ? "--filtered_dir \"${filtered_dir}\"" : ""
    def rawArg = raw_dir ? "--raw_dir \"${raw_dir}\"" : ""
    """
    echo "Gate trigger ready: ${gate_trigger}" >&2
    python3 ${params.code_root}/scripts/open_stage_gate.py \\
        --job_id "${job_id}" \\
        --stage "${stage_name}" \\
        --candidate_dir "${candidate_dir}" \\
        ${rawArg} \\
        ${filteredArg} \\
        --framework_type "${framework_type ?: ''}" \\
        --antibody_chains "${antibody_chains ?: ''}" \\
        --structure_validator "${structure_validator ?: ''}" \\
        --api_url "${params.api_url}" \\
        --output "gate_${stage_name}.json"
    """
}

workflow BOLTZGEN_DESIGN {
    main:
        // Prepare input config from params
        PrepBoltzGenInput(
            params.get('boltzgen_ligand_smiles') ?: '',
            params.get('boltzgen_ntp_type') ?: '',
            params.get('boltzgen_scaffold_length'),
            params.get('boltzgen_num_designs'),
            params.get('boltzgen_binding_site_residues') ?: '',
            params.get('boltzgen_catalytic_site') ?: false,
            params.get('boltzgen_protein_sequence') ?: '',
            params.get('boltzgen_dna_template_seq') ?: '',
            params.get('boltzgen_dna_primer_seq') ?: '',
            params.get('boltzgen_secondary_structure') ?: '',
            params.get('boltzgen_protocol') ?: 'protein-anything',
            params.get('boltzgen_covalent_bonds') ?: '',
            params.get('boltzgen_nanobody_framework') ?: '',
            params.get('boltzgen_cdr_h1_length') ?: '5-8',
            params.get('boltzgen_cdr_h2_length') ?: '6-10',
            params.get('boltzgen_cdr_h3_length') ?: '12-18',
            params.get('boltzgen_input_pdb') ? file(params.get('boltzgen_input_pdb')) : file("${params.code_root}/lib/NO_INPUT_PDB"),
            params.get('boltzgen_ligand_pdb') ? file(params.get('boltzgen_ligand_pdb')) : file("${params.code_root}/lib/NO_LIGAND_PDB"),
            params.get('boltzgen_dna_structure') ? file(params.get('boltzgen_dna_structure')) : file("${params.code_root}/lib/NO_DNA_STRUCT"),
            params.get('boltzgen_target_pdb_path') ? file(params.get('boltzgen_target_pdb_path')) : file("${params.code_root}/lib/NO_TARGET_PDB"),
        )
        
        // Determine execution mode
        def parallel_mode_set = params.containsKey('parallel_mode')
        def use_orchestrator = parallel_mode_set
            ? (params.parallel_mode == 'full_orchestrator')
            : (params.get('boltzgen_parallel_mode') == true)
        def interactiveGateEnabled = params.get('interactive_gating') == true || params.get('interactive_swa') == true
        def shouldPauseAfterBoltzGen = interactiveGateEnabled &&
            (params.get('interactive_gate_stage') ?: 'post_boltzgen') == 'post_boltzgen' &&
            params.get('interactive_gate_continue') != true
        def boltzgenRawDir = params.out_dir ? "${params.out_dir}/collected/boltzgen_raw" : ""
        def boltzgenFilteredDir = params.out_dir ? "${params.out_dir}/collected/boltzgen_filtered" : ""
        
        if (use_orchestrator) {
            // SWA Pattern: Spawn child jobs for large campaigns
            println("BoltzGen PARALLEL MODE: Spawning ${Math.ceil(params.get('boltzgen_num_designs') / params.get('boltzgen_designs_per_job'))} child jobs")
            def batch_name = params.batch_name ?: params.name ?: 'boltzgen_campaign'
            
            def target_pdb = params.get('boltzgen_target_pdb_path') 
                ? file(params.get('boltzgen_target_pdb_path')) 
                : file("${params.code_root}/lib/NO_TARGET_PDB")
            
            SpawnBoltzGenJobs(
                params.job_id ?: 'unknown',
                params.get('boltzgen_num_designs'),
                params.get('boltzgen_designs_per_job') ?: 100,
                PrepBoltzGenInput.out.yaml,
                target_pdb,
                params.get('boltzgen_mode') ?: 'nanobody_binder',
                batch_name,
            )
            
            WaitForBoltzGenChildren(
                params.job_id ?: 'unknown',
                SpawnBoltzGenJobs.out.result,
                batch_name,
            )
            
            CollectBoltzGenOutputs(WaitForBoltzGenChildren.out.result)
            
            AggregateBoltzGenResults(
                params.job_id ?: 'unknown',
                CollectBoltzGenOutputs.out.pdbs.collect(),
                CollectBoltzGenOutputs.out.jsons.collect(),
                CollectBoltzGenOutputs.out.manifest,
            )

            FilterBoltzGen(CollectBoltzGenOutputs.out.pdbs, CollectBoltzGenOutputs.out.jsons)

            output_pdbs = FilterBoltzGen.out.pdbs.flatten().collect()
            output_jsons = FilterBoltzGen.out.jsons.flatten().collect()
        }
        else {
            // Single-process execution
            RunBoltzGen(PrepBoltzGenInput.out.yaml)
            FilterBoltzGen(RunBoltzGen.out.pdbs, RunBoltzGen.out.jsons)
            
            output_pdbs = FilterBoltzGen.out.pdbs.flatten().collect()
            output_jsons = FilterBoltzGen.out.jsons.flatten().collect()
        }

        output_pdbs.subscribe { pdbs ->
            try {
                def file_list = pdbs instanceof List ? pdbs : [pdbs]
                def count = file_list.size()
                println("BoltzGen candidate collection complete: ${count} structures")
                def report_files = count > 50 ? file_list[0..49] : file_list
                def args = [params.job_id, "boltzgen", "complete"] + report_files.collect { it.toString() }
                def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage boltzgen: ${e.message}"
            }
        }

        if (shouldPauseAfterBoltzGen) {
            boltzgen_gate_trigger = output_pdbs.map { pdbs ->
                (pdbs instanceof Collection ? pdbs.size() : 1) as Integer
            }
            println("Interactive SWA gate: pausing after BoltzGen candidate generation")
            OpenInteractiveGate(
                params.job_id ?: "unknown",
                "post_boltzgen",
                boltzgen_gate_trigger,
                boltzgenFilteredDir ?: "",
                boltzgenRawDir ?: "",
                boltzgenFilteredDir ?: "",
                params.get('framework_type') ?: "nanobody",
                params.get('antibody_chains') ?: "H",
                params.get('structure_validator') ?: "boltz2"
            )
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
    println("* Target PDB: ${params.get('boltzgen_target_pdb_path') ?: 'not specified'}")
    println("* Num designs: ${params.get('boltzgen_num_designs')}")
    println("* Scaffold length: ${params.get('boltzgen_scaffold_length')}")
    println("* Mode: ${params.get('boltzgen_mode')}")
    println("* Protocol: ${params.get('boltzgen_protocol') ?: 'protein-anything'}")
    println("* Parallel mode: ${params.get('boltzgen_parallel_mode')}")
    if (params.get('boltzgen_ligand_smiles')) println("* Ligand SMILES: ${params.get('boltzgen_ligand_smiles')}")
    if (params.get('boltzgen_nanobody_framework')) println("* Nanobody framework: ${params.get('boltzgen_nanobody_framework')}")
    println("=" * 60)
    
    BOLTZGEN_DESIGN()
    
    println("BoltzGen design complete")
}
