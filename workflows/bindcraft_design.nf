#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

/*
 * BindCraft Design Workflow
 * 
 * Orchestrates de novo minibinder design using BindCraft with
 * GPU orchestration support via the Spawn-Wait-Aggregate (SWA) pattern.
 * 
 * Each trajectory runs on a single GPU; parallelism is achieved by
 * spawning multiple independent child jobs with different random seeds.
 */

// Import modules
include { PrepBindCraftInput ; RunBindCraft ; FilterBindCraft } from '../modules/bindcraft'
include { PrepBoltz ; RunBoltz } from '../modules/boltz'
include { OpenMMRelaxation ; OpenMMScore } from '../modules/openmm'

// Helper: preserve explicit false values (avoid ?: swallowing false)
def paramOrDefault(val, deflt) {
    val != null ? val : deflt
}

// =============================================================================
// SWA ORCHESTRATOR PROCESSES
// =============================================================================

process SpawnBindCraftJobs {
    label 'process_low'

    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"

    input:
    path target_pdb
    val total_trajectories
    val trajectories_per_job
    val parent_job_id
    val batch_name

    output:
    path "spawn_bindcraft_result.json", emit: spawn_result
    path "*.log"

    script:
    def params_json = groovy.json.JsonOutput.toJson(
        [
            target_pdb: target_pdb.name,
            hotspot_residues: params.bindcraft_hotspot_residues ?: '',
            binder_lengths: params.bindcraft_binder_lengths ?: '80-120',
            num_final_designs: params.bindcraft_num_final_designs ?: 100,
            design_algorithm: params.bindcraft_design_algorithm ?: '4stage',
            chains: params.bindcraft_chains ?: 'A',
            use_multimer_design: paramOrDefault(params.bindcraft_use_multimer_design, true),
            num_recycles_design: params.bindcraft_num_recycles_design ?: 3,
            num_recycles_validation: params.bindcraft_num_recycles_validation ?: 3,
            mpnn_weights: params.bindcraft_mpnn_weights ?: 'soluble',
            num_mpnn_sequences: params.bindcraft_num_mpnn_sequences ?: 8,
            min_iptm: params.bindcraft_min_iptm ?: 0.6,
            max_hotspot_rmsd: params.bindcraft_max_hotspot_rmsd ?: 3.0,
            zip_animations: paramOrDefault(params.bindcraft_zip_animations, true),
            zip_plots: paramOrDefault(params.bindcraft_zip_plots, true),
            remove_unrelaxed_trajectory: paramOrDefault(params.bindcraft_remove_unrelaxed_trajectory, true),
            remove_unrelaxed_complex: paramOrDefault(params.bindcraft_remove_unrelaxed_complex, true),
            remove_binder_monomer: paramOrDefault(params.bindcraft_remove_binder_monomer, true),
            save_trajectory_pickle: paramOrDefault(params.bindcraft_save_trajectory_pickle, false),
            mask_mode: params.bindcraft_mask_mode ?: 'none',
            redesign_ranges: params.bindcraft_redesign_ranges ?: '',
            rm_template_seq_design: paramOrDefault(params.bindcraft_rm_template_seq_design, false),
            rm_template_sc_design: paramOrDefault(params.bindcraft_rm_template_sc_design, false),
            predict_initial_guess: paramOrDefault(params.bindcraft_predict_initial_guess, false),
            use_termini_distance_loss: paramOrDefault(params.bindcraft_use_termini_distance_loss, false),
            cdr_sampling_enabled: paramOrDefault(params.bindcraft_cdr_sampling_enabled, false),
            cdr_sampling_count: params.bindcraft_cdr_sampling_count ?: 5,
        ]
    )
    """
    python3 ${params.code_root}/scripts/spawn_bindcraft_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --total_trajectories ${total_trajectories} \\
        --trajectories_per_job ${trajectories_per_job} \\
        --target_pdb "${target_pdb}" \\
        --batch_name "${batch_name}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_bindcraft_result.json \\
        2>&1 | tee spawn_bindcraft.log
    """
}

process WaitForBindCraftChildren {
    label 'process_low'

    input:
    val parent_job_id
    val stage_name
    val poll_interval_seconds
    val batch_name

    output:
    path "child_outputs.json", emit: child_outputs

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output child_outputs.json
    """
}

process CollectBindCraftOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/collected/bindcraft", mode: 'copy', pattern: "*.pdb"
    publishDir "${params.out_dir}/collected/bindcraft", mode: 'copy', pattern: "*.csv"

    input:
    path child_outputs_json

    output:
    path "*.pdb", emit: pdbs, optional: true
    path "merged_stats.csv", emit: stats, optional: true
    path "collection_manifest.json", emit: manifest

    script:
    """
    #!/usr/bin/env python3
    import json
    import shutil
    import pandas as pd
    from pathlib import Path
    
    with open("${child_outputs_json}") as f:
        data = json.load(f)
    
    output_dirs = data.get("child_output_dirs", [])
    collected_pdbs = []
    stats_dfs = []
    
    for job_idx, output_dir in enumerate(output_dirs):
        dir_path = Path(output_dir)
        if not dir_path.exists():
            print(f"Warning: Output dir not found: {output_dir}")
            continue
        
        # Collect accepted PDBs
        for pdb_file in dir_path.glob("**/Accepted/*.pdb"):
            new_name = f"child{job_idx}_{pdb_file.name}"
            shutil.copy(pdb_file, new_name)
            collected_pdbs.append(new_name)
        
        # Collect stats CSVs
        for stats_file in dir_path.glob("**/final_design_stats.csv"):
            try:
                df = pd.read_csv(stats_file)
                df['source_job'] = job_idx
                stats_dfs.append(df)
            except Exception as e:
                print(f"Warning: Could not read {stats_file}: {e}")
    
    # Merge all stats
    if stats_dfs:
        merged_df = pd.concat(stats_dfs, ignore_index=True)
        merged_df.to_csv("merged_stats.csv", index=False)
        print(f"Merged {len(stats_dfs)} stats files with {len(merged_df)} total designs")
    
    # Write manifest
    manifest = {
        "child_jobs": len(output_dirs),
        "pdbs_collected": len(collected_pdbs),
        "designs_in_stats": len(merged_df) if stats_dfs else 0
    }
    
    with open("collection_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    print(f"Collected {len(collected_pdbs)} PDBs from {len(output_dirs)} child jobs")
    """
}

// =============================================================================
// MAIN WORKFLOW
// =============================================================================
workflow BINDCRAFT_DESIGN {

    main:
    // Input validation
    if (!params.bindcraft_target_pdb) {
        error("Missing required parameter: bindcraft_target_pdb")
    }

    target_pdb = file(params.bindcraft_target_pdb)
    if (!target_pdb.exists()) {
        error("Target PDB not found: ${params.bindcraft_target_pdb}")
    }
    job_id = params.job_id ?: UUID.randomUUID().toString().take(8)
    batch_name = params.batch_name ?: "bindcraft_${job_id}"

    // Determine orchestration mode (prefer explicit parallel_mode, fallback to legacy SWA)
    total_trajectories = params.bindcraft_total_trajectories ?: 100
    trajectories_per_job = params.bindcraft_trajectories_per_job ?: 25
    def parallel_mode_set = params.containsKey('parallel_mode')
    def use_orchestrator = parallel_mode_set
        ? (params.parallel_mode == 'full_orchestrator')
        : (params.bindcraft_use_swa != null
            ? params.bindcraft_use_swa
            : (total_trajectories > trajectories_per_job))

    if (use_orchestrator) {
        // =====================================================================
        // SWA Mode: Spawn multiple child jobs for parallel trajectory generation
        // =====================================================================

        // 1. Spawn child jobs
        SpawnBindCraftJobs(
            target_pdb,
            total_trajectories,
            trajectories_per_job,
            job_id,
            batch_name,
        )

        // 2. Wait for all children to complete
        WaitForBindCraftChildren(
            job_id,
            "bindcraft",
            params.bindcraft_poll_interval ?: 60,
            batch_name,
        )

        // 3. Collect outputs from all children
        CollectBindCraftOutputs(
            WaitForBindCraftChildren.out.child_outputs
        )

        collected_pdbs = CollectBindCraftOutputs.out.pdbs
        collected_stats = CollectBindCraftOutputs.out.stats
    }
    else {
        // =====================================================================
        // Single Job Mode: Run BindCraft directly
        // =====================================================================

        // 1. Prepare configuration files
        def scaffold_pdb = params.bindcraft_scaffold_pdb
            ? file(params.bindcraft_scaffold_pdb)
            : file("${params.code_root}/lib/NO_TARGET_PDB")

        PrepBindCraftInput(
            target_pdb,
            params.bindcraft_hotspot_residues ?: '',
            params.bindcraft_binder_lengths ?: '80-120',
            params.bindcraft_num_final_designs ?: 100,
            params.bindcraft_design_algorithm ?: '4stage',
            params.bindcraft_chains ?: 'A',
            params.binder_name ?: 'binder',
            params.bindcraft_design_mode ?: 'denovo',
            scaffold_pdb,
            params.bindcraft_binder_chain ?: 'B',
            paramOrDefault(params.bindcraft_use_multimer_design, true),
            params.bindcraft_num_recycles_design ?: 3,
            params.bindcraft_num_recycles_validation ?: 3,
            params.bindcraft_mpnn_weights ?: 'soluble',
            params.bindcraft_num_mpnn_sequences ?: 8,
            params.bindcraft_min_iptm ?: 0.6,
            params.bindcraft_max_hotspot_rmsd ?: 3.0,
            paramOrDefault(params.bindcraft_zip_animations, true),
            paramOrDefault(params.bindcraft_zip_plots, true),
            paramOrDefault(params.bindcraft_remove_unrelaxed_trajectory, true),
            paramOrDefault(params.bindcraft_remove_unrelaxed_complex, true),
            paramOrDefault(params.bindcraft_remove_binder_monomer, true),
            paramOrDefault(params.bindcraft_save_trajectory_pickle, false),
            params.bindcraft_mask_mode ?: 'none',
            params.bindcraft_redesign_ranges ?: '',
            paramOrDefault(params.bindcraft_rm_template_seq_design, false),
            paramOrDefault(params.bindcraft_rm_template_sc_design, false),
            paramOrDefault(params.bindcraft_predict_initial_guess, false),
            paramOrDefault(params.bindcraft_use_termini_distance_loss, false),
            paramOrDefault(params.bindcraft_cdr_sampling_enabled, false),
            params.bindcraft_cdr_sampling_count ?: 5,
        )

        // 2. Run BindCraft
        RunBindCraft(
            PrepBindCraftInput.out.target_settings,
            PrepBindCraftInput.out.advanced_settings,
            PrepBindCraftInput.out.filter_settings,
            PrepBindCraftInput.out.target_pdb_out,
            job_id,
        )

        collected_pdbs = RunBindCraft.out.accepted_pdbs
        collected_stats = RunBindCraft.out.stats
    }

    // 4. Filter and rank results
    FilterBindCraft(
        collected_pdbs,
        collected_stats,
        params.bindcraft_budget ?: '',
        params.bindcraft_alpha ?: 0.01,
    )

    // 5. Optional: Boltz-2 validation (if enabled)
    def boltz_enabled = params.bindcraft_boltz_validation == true
    if (boltz_enabled) {
        boltz_input = FilterBindCraft.out.pdbs.collect()
        PrepBoltz(boltz_input)
        boltz_batches = PrepBoltz.out.yamls.map { yamls -> tuple(0, yamls) }
        RunBoltz(boltz_batches)
        validated_pdbs = RunBoltz.out.pdbs_jsons.map { pdbs, _jsons -> pdbs }
    }
    else {
        validated_pdbs = FilterBindCraft.out.pdbs
    }

    // 6. Optional: OpenMM Physics Refinement (if enabled)
    // Provides energy minimization and MM-GBSA scoring for binder designs
    if (params.openmm_enabled == true) {
        log.info("Running OpenMM physics refinement on BindCraft designs...")
        log.info("  Compute tier: ${params.openmm_compute_tier ?: 'fast'}")

        // Batch validated PDBs for GPU processing
        openmm_batched = validated_pdbs
            .flatten()
            .buffer(size: 10, remainder: true)
            .map { batch -> tuple("bindcraft_${batch.hashCode()}", batch) }

        // Run energy minimization (no CDR-only for general binders)
        OpenMMRelaxation(
            openmm_batched,
            params.openmm_compute_tier ?: 'fast',
            false,
            params.openmm_restraint_mode ?: 'none',
            'B',
            params.openmm_force_field ?: 'amber14sb',
        )

        // Run MM-GBSA if full tier or explicitly requested
        if (params.openmm_compute_tier == 'full' || params.openmm_mmgbsa_mode != 'off') {
            mmgbsa_batched = OpenMMRelaxation.out.relaxed_pdbs
                .collect()
                .flatten()
                .buffer(size: 5, remainder: true)
                .map { batch -> tuple("mmgbsa_${batch.hashCode()}", batch) }

            OpenMMScore(
                mmgbsa_batched,
                params.openmm_mmgbsa_mode ?: 'interface',
                'B',
                'A',
                params.openmm_force_field ?: 'amber14sb',
            )
        }

        final_pdbs = OpenMMRelaxation.out.relaxed_pdbs
    }
    else {
        final_pdbs = validated_pdbs
    }

    emit:
    final_pdbs = final_pdbs
    summary = FilterBindCraft.out.summary
}

// =============================================================================
// ENTRY POINT
// =============================================================================
workflow {
    BINDCRAFT_DESIGN()
}
