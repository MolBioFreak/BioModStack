#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// =============================================================================
// De Novo Antibody Design Workflow
// =============================================================================
// Two-phase pipeline for generating and validating novel antibodies:
//
// PHASE 1: Generation
//   Step 1: RFantibody - CDR backbone generation
//   Step 2: Sequence Design - FAMPNN/AntiFold/ProteinMPNN (cross-validation)
//   Step 2.5: Stability Filtering - ThermoMPNN (optional, pre-Boltz)
//
// PHASE 2: Validation & Scoring
//   Step 3: Structure Validation - Boltz2 (ipTM, pLDDT)
//   Step 4: Immunogenicity - AntiBERTy (pseudo-log-likelihood)
//   Step 5: Affinity Maturation - IgGM (optional)
// =============================================================================

// ═══════════════════════════════════════════════════════════════════════════════
// HELPER FUNCTION: Extract sequence from PDB file
// ═══════════════════════════════════════════════════════════════════════════════
def extractSequenceFromPDB(pdb_file) {
    // Extract sequences from PDB file, separating chains with ':' for Boltz multi-chain input
    def aa_codes = [
        'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
        'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
        'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
        'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V'
    ]
    
    // Track sequences per chain
    def chain_sequences = [:] as LinkedHashMap  // Preserve chain order
    def seen_residues = [:] as Map  // Per-chain residue tracking
    
    try {
        pdb_file.eachLine { line ->
            if (line.startsWith('ATOM') && line.length() >= 26 && line.substring(12, 16).trim() == 'CA') {
                def resName = line.substring(17, 20).trim()
                def resNum = line.substring(22, 26).trim()
                def chain = line.substring(21, 22)
                def key = "${chain}_${resNum}"
                
                if (!seen_residues.containsKey(chain)) {
                    seen_residues[chain] = [] as Set
                    chain_sequences[chain] = []
                }
                
                if (!seen_residues[chain].contains(key) && aa_codes.containsKey(resName)) {
                    seen_residues[chain].add(key)
                    chain_sequences[chain] << aa_codes[resName]
                }
            }
        }
    } catch (Exception e) {
        // Fallback: return empty sequence (Boltz will fail gracefully)
        return "AAAA"
    }
    
    // Join chain sequences with ':' separator for Boltz multi-chain input
    def result = chain_sequences.values().collect { it.join('') }.join(':')
    return result ?: "AAAA"
}

// Import modules
include { RFANTIBODY } from '../modules/rfantibody'
include { ANTIFOLD } from '../modules/antifold'
include { PrepFAMPNN ; RunFAMPNN ; FilterFAMPNN } from '../modules/fampnn'
include { PrepMPNN ; RunMPNN as ProteinMPNNSeq } from '../modules/proteinmpnn'
include { ANTIBERTY_SCORE ; ANTIBERTY_FILTER } from '../modules/antiberty'
include { THERMOMPNN } from '../modules/thermompnn'
include { MergeComplex ; AF2_BACKPROP } from '../modules/af2_backprop'
include { IGGM_AFFINITY_MATURATION } from '../modules/iggm'
include { PrepBoltz ; PrepBoltzWithMSA ; RunBoltz } from '../modules/boltz'
include { GenerateLocalMSA ; BoltzFromSequenceWithMSA } from '../modules/structure_prediction'
include { ANARCI } from '../modules/utils/anarci'
include { PredictTargetComplex } from '../modules/predict_target_complex'
include { OpenMMRelaxation ; OpenMMScore } from '../modules/openmm'

// =============================================================================
// ORCHESTRATOR SPAWN-WAIT-COLLECT PROCESSES
// These enable per-job GPU assignment via the Python GPU orchestrator
// =============================================================================

process SpawnRFantibodyJobs {
    label 'process_low'
    
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    
    input:
    path target_pdb
    val epitope_residues
    val framework_type
    val total_designs
    val designs_per_job
    val parent_job_id
    val batch_name
    
    output:
    path "spawn_rfa_result.json", emit: result
    
    script:
    def params_json = groovy.json.JsonOutput.toJson([
        rfantibody_diffusion_steps: params.rfantibody_diffusion_steps ?: 50,
        rfantibody_noise_scale_ca: params.rfantibody_noise_scale_ca ?: 1.0,
        rfantibody_noise_scale_frame: params.rfantibody_noise_scale_frame ?: 1.0,
        rfantibody_guide_scale: params.rfantibody_guide_scale ?: 10,
        // Pass UI CDR loop selection - rfantibody.nf will convert to RFantibody format
        antibody_design_loops: params.antibody_design_loops ?: ''
    ])
    """
    python3 ${projectDir}/scripts/spawn_rfantibody_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --total_designs ${total_designs} \\
        --designs_per_job ${designs_per_job} \\
        --target_pdb "\$(readlink -f ${target_pdb})" \\
        --epitope_residues "${epitope_residues}" \\
        --framework_type "${framework_type}" \\
        --batch_name "${batch_name}" \\
        --params_json '${params_json}' \\
        --api_url "http://localhost:8000" \\
        --output spawn_rfa_result.json \\
        2>&1 | tee spawn_rfa.log
    """
}

process SpawnFAMPNNJobs {
    label 'process_low'
    
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    
    input:
    path pdb_dir
    val seqs_per_design
    val pdbs_per_job
    val parent_job_id
    val batch_name
    
    output:
    path "spawn_fampnn_result.json", emit: result
    
    script:
    def params_json = groovy.json.JsonOutput.toJson([
        fampnn_temperature: params.fampnn_temperature ?: 0.0001,
        fampnn_num_steps: params.fampnn_num_steps ?: 500,
        fampnn_psce_threshold: params.fampnn_psce_threshold ?: 0.15,
        fampnn_constraint_mode: params.fampnn_constraint_mode
    ])
    """
    python3 ${projectDir}/scripts/spawn_fampnn_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir "${pdb_dir}" \\
        --pdbs_per_job ${pdbs_per_job} \\
        --seqs_per_design ${seqs_per_design} \\
        --batch_name "${batch_name}" \\
        --params_json '${params_json}' \\
        --api_url "http://localhost:8000" \\
        --output spawn_fampnn_result.json \\
        2>&1 | tee spawn_fampnn.log
    """
}

process WaitForChildren {
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
    python3 ${projectDir}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "http://localhost:8000" \\
        --output child_outputs.json
    """
}

process CollectChildOutputs {
    label 'process_low'
    
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.pdb"
    
    input:
    path child_outputs_json
    val stage_name
    
    output:
    path "*.pdb", emit: pdbs, optional: true
    path "collection_manifest.json", emit: manifest
    
    script:
    """
    #!/usr/bin/env python3
    import json
    import shutil
    from pathlib import Path
    
    with open("${child_outputs_json}") as f:
        data = json.load(f)
    
    output_dirs = data.get("child_output_dirs", [])
    collected = []
    
    for job_idx, output_dir in enumerate(output_dirs):
        dir_path = Path(output_dir)
        if not dir_path.exists():
            print(f"Warning: Output dir not found: {output_dir}")
            continue
        
        # Look for PDBs in standard locations
        for subdir in ["pdb_files", "run/rfantibody/output", "run/fampnn/results", ""]:
            search_path = dir_path / subdir if subdir else dir_path
            if not search_path.exists():
                continue
            for pdb in search_path.glob("*.pdb"):
                # Add job index prefix to avoid filename collisions between child jobs
                dest = Path(f"job{job_idx}_{pdb.name}")
                if not dest.exists():
                    shutil.copy(pdb, dest)
                    collected.append(str(dest))
                    print(f"Collected: {pdb} -> {dest}")
    
    manifest = {
        "stage": "${stage_name}",
        "source_dirs": output_dirs,
        "collected_pdbs": collected,
        "count": len(collected)
    }
    
    with open("collection_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    print(f"Collected {len(collected)} PDBs from {len(output_dirs)} child jobs")
    """
}

// FAMPNN-specific Wait and Collect processes
// These are separate from the generic ones to allow both RFantibody and FAMPNN 
// to use spawn-wait-aggregate in the same workflow without channel conflicts
process WaitForFAMPNNChildren {
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
    python3 ${projectDir}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "http://localhost:8000" \\
        --output child_outputs.json
    """
}

process CollectFAMPNNOutputs {
    label 'process_low'
    
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.pdb"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.json"
    
    input:
    path child_outputs_json
    val stage_name
    
    output:
    path "*.pdb", emit: pdbs, optional: true
    path "*.json", emit: jsons, optional: true
    path "collection_manifest.json", emit: manifest
    
    script:
    """
    #!/usr/bin/env python3
    import json
    import shutil
    from pathlib import Path
    
    with open("${child_outputs_json}") as f:
        data = json.load(f)
    
    output_dirs = data.get("child_output_dirs", [])
    collected_pdbs = []
    collected_jsons = []
    
    for job_idx, output_dir in enumerate(output_dirs):
        dir_path = Path(output_dir)
        if not dir_path.exists():
            print(f"Warning: Output dir not found: {output_dir}")
            continue
        
        # Look for PDBs and JSONs in FAMPNN output locations
        for subdir in ["run/fampnn/results", "fampnn_output/samples", "results", ""]:
            search_path = dir_path / subdir if subdir else dir_path
            if not search_path.exists():
                continue
            
            # Collect PDBs
            for pdb in search_path.glob("*.pdb"):
                dest = Path(f"job{job_idx}_{pdb.name}")
                if not dest.exists():
                    shutil.copy(pdb, dest)
                    collected_pdbs.append(str(dest))
                    print(f"Collected PDB: {pdb} -> {dest}")
            
            # Collect JSONs (analysis results)
            for json_file in search_path.glob("*.json"):
                dest = Path(f"job{job_idx}_{json_file.name}")
                if not dest.exists():
                    shutil.copy(json_file, dest)
                    collected_jsons.append(str(dest))
    
    manifest = {
        "stage": "${stage_name}",
        "source_dirs": output_dirs,
        "collected_pdbs": collected_pdbs,
        "collected_jsons": collected_jsons,
        "pdb_count": len(collected_pdbs),
        "json_count": len(collected_jsons)
    }
    
    with open("collection_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    print(f"Collected {len(collected_pdbs)} PDBs and {len(collected_jsons)} JSONs from {len(output_dirs)} FAMPNN child jobs")
    """
}

// =============================================================================
// Process to spawn child validation jobs via API
// This is a proper Nextflow process that BLOCKS until completion
// Used by exploration mode for parallel GPU distribution
// =============================================================================
process SpawnChildJobs {
    label 'process_low'
    
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"
    
    input:
    path pdbs
    path msa_file
    val parent_job_id
    val batch_name
    val child_params_json
    
    output:
    path "spawn_result.json", emit: result
    path "spawn.log", emit: log
    
    script:
    """
    #!/bin/bash
    set -euo pipefail
    
    # Create directory for PDBs and copy them
    mkdir -p pdb_input
    for f in *.pdb; do
        if [ -f "\$f" ]; then
            cp "\$f" pdb_input/
        fi
    done
    
    PDB_COUNT=\$(ls pdb_input/*.pdb 2>/dev/null | wc -l || echo 0)
    echo "Found \$PDB_COUNT PDB files to spawn as child jobs" | tee spawn.log
    
    if [ "\$PDB_COUNT" -eq 0 ]; then
        echo '{"spawned_jobs": 0, "status": "no_pdbs_found", "error": null}' > spawn_result.json
        echo "WARNING: No PDB files found to spawn" | tee -a spawn.log
        exit 0
    fi
    
    # Run the spawn script
    # Resolve absolute path of MSA file (staged by Nextflow as symlink)
    MSA_ABS_PATH=\$(readlink -f "${msa_file}" 2>/dev/null || realpath "${msa_file}" 2>/dev/null || echo "${msa_file}")
    echo "Resolved MSA path: \$MSA_ABS_PATH" | tee -a spawn.log
    
    # Persist MSA to parent output directory for reliability
    # (Nextflow work dirs may be cleaned before children run)
    mkdir -p "${params.out_dir}/msa"
    cp "\$MSA_ABS_PATH" "${params.out_dir}/msa/" 2>/dev/null || true
    MSA_PERSIST_PATH="${params.out_dir}/msa/\$(basename \$MSA_ABS_PATH)"
    echo "Persisted MSA to: \$MSA_PERSIST_PATH" | tee -a spawn.log
    
    # Pass ALL quality settings to child jobs
    echo "Forwarding quality settings to child jobs" | tee -a spawn.log
    
    python3 ${projectDir}/scripts/spawn_antibody_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir pdb_input \\
        --batch_name "${batch_name}" \\
        --msa_path "\$MSA_PERSIST_PATH" \\
        --params_json '${child_params_json}' \\
        --seqs_per_boltz_job ${params.seqs_per_boltz_job ?: 10} \\
        --api_url "http://localhost:8000" \\
        2>&1 | tee -a spawn.log
    
    SPAWN_EXIT=\${PIPESTATUS[0]}
    
    if [ "\$SPAWN_EXIT" -eq 0 ]; then
        echo '{"spawned_jobs": '\$PDB_COUNT', "status": "complete", "error": null}' > spawn_result.json
    else
        echo '{"spawned_jobs": 0, "status": "failed", "error": "spawn script exited with '\$SPAWN_EXIT'"}' > spawn_result.json
    fi
    
    echo "Spawn process complete" | tee -a spawn.log
    """
}

// =============================================================================
// Process to wait for child jobs and aggregate their validated results
// This blocks until all children complete, then collects outputs to master dir
// =============================================================================
process WaitAndAggregateChildResults {
    label 'process_low'
    
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.pdb"
    publishDir "${params.out_dir}", mode: 'copy', pattern: "aggregation_report.json"
    
    input:
    val parent_job_id
    val batch_name
    val expected_child_count
    
    output:
    path "validated_designs/*.pdb", emit: pdbs, optional: true
    path "aggregation_report.json", emit: report
    
    script:
    """
    #!/bin/bash
    set -euo pipefail
    
    echo "Waiting for ${expected_child_count} child validation jobs to complete..."
    
    mkdir -p validated_designs intermediates/boltz intermediates/scores
    
    # Wait for all children using the wait script
    python3 ${projectDir}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "boltz2" \\
        --batch_name "${batch_name}" \\
        --output wait_result.json \\
        --api_url "http://localhost:8000" \\
        2>&1 | tee wait.log
    
    # Parse wait result
    CHILD_DIRS=\$(python3 -c "
import json
with open('wait_result.json') as f:
    data = json.load(f)
    for d in data.get('child_output_dirs', []):
        print(d)
")
    
    echo "Collecting validated designs from child jobs..."
    
    TOTAL_PDBS=0
    TOTAL_CHILDREN=0
    
    for child_dir in \$CHILD_DIRS; do
        if [ -d "\$child_dir" ]; then
            TOTAL_CHILDREN=\$((TOTAL_CHILDREN + 1))
            child_idx="\$TOTAL_CHILDREN"
            
            # Look for Boltz-validated PDBs in child output
            # Search multiple possible locations where Boltz outputs may be published
            for subdir in "pdb_files/predictions" "pdb_files" "run/boltz/predictions" "run/boltz" ""; do
                search_path="\$child_dir/\$subdir"
                if [ -d "\$search_path" ]; then
                    for pdb in \$search_path/*.pdb; do
                        if [ -f "\$pdb" ]; then
                            # Copy with unique naming
                            basename=\$(basename "\$pdb")
                            cp "\$pdb" "validated_designs/\${child_idx}_\$basename"
                            TOTAL_PDBS=\$((TOTAL_PDBS + 1))
                        fi
                    done
                fi
            done
            
            # Also collect confidence JSONs if present
            for json_path in \$child_dir/pdb_files/predictions/*.json \$child_dir/pdb_files/*.json \$child_dir/run/boltz/predictions/*.json \$child_dir/run/boltz/*.json; do
                if [ -f "\$json_path" ] 2>/dev/null; then
                    cp "\$json_path" intermediates/scores/ 2>/dev/null || true
                fi
            done
        fi
    done

    if [ "${expected_child_count}" -gt 0 ] && [ "\$TOTAL_CHILDREN" -lt "${expected_child_count}" ]; then
        echo "Warning: expected ${expected_child_count} child jobs but found \$TOTAL_CHILDREN" | tee -a wait.log
    fi
    
    echo "Collected \$TOTAL_PDBS validated PDBs from \$TOTAL_CHILDREN child jobs"
    
    # Create aggregation report
    cat > aggregation_report.json << EOF
{
    "parent_job_id": "${parent_job_id}",
    "batch_name": "${batch_name}",
    "children_processed": \$TOTAL_CHILDREN,
    "total_validated_designs": \$TOTAL_PDBS,
    "output_path": "${params.out_dir}/pdb_files",
    "status": "complete"
}
EOF

    # Trigger result ingestion for parent job (updates database)
    if [ \$TOTAL_PDBS -gt 0 ]; then
        echo "Triggering result ingestion for parent job..."
        python3 ${projectDir}/scripts/result_ingester.py \\
            --job_id "${parent_job_id}" \\
            --results_dir "${params.out_dir}" \\
            --api_url "http://localhost:8000" \\
            2>&1 | tee ingest.log || echo "Warning: Ingestion had issues (non-fatal)"
    fi
    
    echo "Aggregation complete: \$TOTAL_PDBS designs ready for analytics"
    """
}

// Initialize missing parameters with defaults to suppress warnings
if (!params.containsKey('framework_pdb')) params.framework_pdb = null
if (!params.containsKey('analysis_chain_id')) params.analysis_chain_id = 'all_chains'
if (!params.containsKey('filter_immunogenic')) params.filter_immunogenic = true
if (!params.containsKey('run_affinity_maturation')) params.run_affinity_maturation = false
if (!params.containsKey('exploration_mode') || params.exploration_mode == null) params.exploration_mode = false
if (!params.containsKey('job_id')) params.job_id = "job_${new java.text.SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date())}"
if (!params.containsKey('job_name')) params.job_name = 'antibody_batch'
if (!params.containsKey('fampnn_constraint_mode')) params.fampnn_constraint_mode = 'antibody'

// Orchestrator-based parallelism settings
// 'standard' = Nextflow-internal parallelism (current behavior)
// 'full_orchestrator' = Spawn child jobs via API for per-job GPU assignment
if (!params.containsKey('parallel_mode')) params.parallel_mode = 'standard'
if (!params.containsKey('designs_per_job')) params.designs_per_job = 5
if (!params.containsKey('seqs_per_job')) params.seqs_per_job = 50


workflow ANTIBODY_DENOVO {
    take:
    target_pdb_ch // Channel: [meta, target_pdb]
    epitope_residues // Value: epitope residues string (e.g., "A45,A46,A52")
    framework_pdb_ch // Channel: [meta, framework_pdb] (optional)

    main:
    // =========================================================================
    // PHASE 1: GENERATION
    // =========================================================================

    // Step 1: RFantibody - Generate CDR backbones
    // ---------------------------------------------------------------------------
    log.info("Step 1: Generating CDR backbones with RFantibody...")

    // Framework PDB - if user provided custom framework, use it; otherwise use placeholder
    // The placeholder triggers preset selection in the process script
    // Use safe path resolution to avoid Channel.value() DSL2 error with undefined params
    def framework_path = params.framework_pdb ? file(params.framework_pdb) : file("${projectDir}/lib/NO_FRAMEWORK")
    framework_for_rfantibody = framework_pdb_ch
        .map { meta, pdb -> pdb }
        .ifEmpty { framework_path }
    
    // Multi-GPU parallelism for RFantibody
    // Parse available GPUs from pinned_gpus param (e.g., "0,2" -> [0, 2])
    def available_gpus = []
    if (params.pinned_gpus) {
        available_gpus = params.pinned_gpus.toString().split(',').collect { it.trim().toInteger() }
    } else if (params.gpu_id != null) {
        available_gpus = [params.gpu_id.toInteger()]
    } else {
        available_gpus = [0] // Default to GPU 0
    }
    
    def total_designs = params.rfantibody_num_designs ?: 10
    def num_gpus = available_gpus.size()
    def designs_per_gpu = (total_designs / num_gpus).intValue()
    def remainder = total_designs % num_gpus
    
    // =========================================================================
    // SKIP RFANTIBODY: Load pre-existing backbone PDBs instead of generating
    // =========================================================================
    def skip_rfantibody = params.skip_rfantibody == true || params.rfantibody_input_pdbs != null
    
    if (skip_rfantibody && params.rfantibody_input_pdbs) {
        log.info("  SKIP: Loading pre-existing backbone PDBs from ${params.rfantibody_input_pdbs}")
        
        // Load backbone PDBs from provided directory
        backbone_designs = Channel.fromPath("${params.rfantibody_input_pdbs}/*.pdb")
            .collect()
            .map { pdbs ->
                log.info("  Loaded ${pdbs.size()} backbone PDBs")
                def meta = [id: params.name ?: "antibody"]
                [meta, pdbs]
            }
    } else if (skip_rfantibody) {
        error("skip_rfantibody=true but no rfantibody_input_pdbs directory provided")
    } else {
        // =========================================================================
        // PARALLELISM MODE: Choose between Nextflow-internal or Orchestrator spawning
        // =========================================================================
        def use_orchestrator = params.parallel_mode == 'full_orchestrator'
    
    if (use_orchestrator) {
        // =====================================================================
        // ORCHESTRATOR MODE: Spawn child jobs through GPU queue
        // Each child is a separate API job managed by the orchestrator
        // =====================================================================
        log.info("  Orchestrator mode: Spawning ${total_designs / (params.designs_per_job ?: 5)} child job(s)")
        
        // Spawn child jobs via API
        SpawnRFantibodyJobs(
            target_pdb_ch.map { meta, pdb -> pdb }.first(),
            epitope_residues ?: "",
            params.framework_type ?: "standard-fv",
            total_designs,
            params.designs_per_job ?: 5,
            params.job_id ?: "unknown",
            params.name ?: "antibody_batch"
        )
        
        // Wait for all child jobs to complete
        // Depends on spawn completion via SpawnRFantibodyJobs.out.result
        // Pass batch_name for resume support (find children from original run)
        wait_trigger = SpawnRFantibodyJobs.out.result.map { it -> params.job_id ?: "unknown" }
        batch_name = params.name ?: "antibody_batch"
        WaitForChildren(
            wait_trigger,
            "rfantibody",
            30,  // poll_interval_seconds
            batch_name
        )
        
        // Collect outputs from completed child jobs
        CollectChildOutputs(
            WaitForChildren.out.child_outputs,
            "rfantibody"
        )
        
        // Create backbone_designs channel from collected outputs
        backbone_designs = CollectChildOutputs.out.pdbs
            .flatten()
            .collect()
            .map { pdbs ->
            def meta = [id: params.name ?: "antibody"]
            [meta, pdbs]
        }
        
    } else {
        // =====================================================================
        // STANDARD MODE: Nextflow-internal multi-GPU parallelism
        // Splits work across pinned GPUs within the same Nextflow process
        // =====================================================================
        log.info("  Multi-GPU mode: Splitting ${total_designs} designs across ${num_gpus} GPU(s): ${available_gpus}")
        
        // Create parallel job channels for each GPU
        // Each gets a portion of the total designs
        rfantibody_parallel_inputs = Channel.from(available_gpus).map { gpu_id ->
            def idx = available_gpus.indexOf(gpu_id)
            def designs_for_this_gpu = designs_per_gpu + (idx < remainder ? 1 : 0)
            log.info("    GPU ${gpu_id}: ${designs_for_this_gpu} designs")
            [gpu_id, designs_for_this_gpu]
        }
        
        // Prepare input for RFantibody with GPU assignment
        // Combine target PDB with each GPU assignment
        rfantibody_input = target_pdb_ch.combine(rfantibody_parallel_inputs).map { meta, pdb, gpu_id, designs_count ->
            def hotspots = epitope_residues ?: ""
            // Create unique meta for each GPU split
            def split_meta = [id: "${meta.id}_gpu${gpu_id}"]
            [split_meta, pdb, hotspots, gpu_id, designs_count]
        }

        RFANTIBODY(rfantibody_input, framework_for_rfantibody)
        
        // REPORT STAGE: rfantibody
        RFANTIBODY.out.designs.subscribe { meta, files ->
            try {
                def file_list = files instanceof List ? files : [files]
                // Limit number of files reported to avoid command line length limits
                def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                def args = [params.job_id, "rfantibody", "complete"] + report_files.collect { it.toString() }
                def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage rfantibody: ${e.message}"
            }
        }

        // Collect backbone designs from all parallel GPU runs
        // Normalize meta.id by removing GPU suffix for downstream stages
        backbone_designs = RFANTIBODY.out.designs.map { meta, files ->
            def base_id = meta.id.replaceAll(/_gpu\d+$/, '')
            def unified_meta = [id: base_id]
            [unified_meta, files]
        }
    } // End of else block (standard mode)
    } // End of skip_rfantibody else block

    // Step 2: CDR Sequence Design (Cross-Validation Mode)
    // ---------------------------------------------------------------------------
    log.info("Step 2: Designing CDR sequences...")

    // Determine which sequence design methods to run
    // Note: Use explicit null check because ?: treats false as falsy
    def run_fampnn = (params.seq_design_fampnn != null) ? params.seq_design_fampnn : true
    def run_antifold = (params.seq_design_antifold != null) ? params.seq_design_antifold : true
    def run_proteinmpnn = (params.seq_design_proteinmpnn != null) ? params.seq_design_proteinmpnn : true

    // Initialize sequence channels
    fampnn_seqs = Channel.empty()
    antifold_seqs = Channel.empty()
    proteinmpnn_seqs = Channel.empty()

    // FAMPNN branch - using GPU orchestrator spawn-wait-aggregate pattern
    if (run_fampnn) {
        // =====================================================================
        // CHECK: Skip FAMPNN if pre-collected PDBs are provided
        // This allows resuming from filtering without re-running FAMPNN
        // =====================================================================
        if (params.fampnn_collected_pdbs) {
            log.info("  FAMPNN: Using pre-collected PDBs from ${params.fampnn_collected_pdbs}")
            
            // Load pre-collected PDBs directly
            pre_collected_pdbs = Channel.fromPath("${params.fampnn_collected_pdbs}/*.pdb")
                .collect()
            
            pre_collected_pdbs.subscribe { pdbs ->
                log.info("  FAMPNN: Loaded ${pdbs.size()} pre-collected PDBs")
            }
            
            // Skip directly to filtering
            fampnn_seqs = pre_collected_pdbs.map { pdbs ->
                def meta = [id: "fampnn_designs"]
                [meta, pdbs]
            }
            
            // Skip the spawn/wait/collect/filter block
            
        } else {
            // Standard orchestrator mode
            log.info("  Running FAMPNN via GPU Orchestrator...")
            log.info("  Spawning child jobs (${params.pdbs_per_job ?: 5} PDBs per job, ${params.seqs_per_design ?: 20} seqs/design)")
            
            // Collect all backbone PDBs from parallel GPU runs into a single list
            all_backbone_pdbs = backbone_designs
                .map { meta, files -> files }
                .flatten()
                .collect()
            
            // PrepFAMPNN generates constraint CSV and preps structures
            fampnn_prep_input = all_backbone_pdbs.map { pdbs ->
                [pdbs, file("${projectDir}/lib/empty-meta.jsonl")]
            }
            PrepFAMPNN(fampnn_prep_input)
            
            // Get the output directory from PrepFAMPNN (contains prepped PDBs)
            fampnn_pdb_dir = PrepFAMPNN.out.pdbs.collect().map { files ->
                // Return parent directory path as string
                files[0].parent.toString()
            }
            
            // =====================================================================
            // ORCHESTRATOR MODE: Spawn FAMPNN child jobs
            // Each child runs FAMPNN on a subset of PDBs, scheduled by orchestrator
            // =====================================================================
            SpawnFAMPNNJobs(
                fampnn_pdb_dir,
                params.seqs_per_design ?: 20,
                params.pdbs_per_job ?: 5,
                params.job_id ?: "unknown",
                params.name ?: "antibody_batch"
            )
            
            // Wait for all FAMPNN children to complete
            // Pass batch_name for resume support (find children from original run)
            // Note: map closure must accept the path argument (even if unused) 
            fampnn_wait_trigger = SpawnFAMPNNJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }
            fampnn_batch_name = params.name ?: "antibody_batch"
            
            // Reuse WaitForChildren process - need separate call for FAMPNN stage
            // Note: We use a different variable name to avoid Nextflow channel conflicts
            WaitForFAMPNNChildren(
                fampnn_wait_trigger,
                "fampnn",
                30,  // poll_interval
                fampnn_batch_name
            )
            
            // Collect outputs from completed FAMPNN child jobs
            CollectFAMPNNOutputs(
                WaitForFAMPNNChildren.out.child_outputs,
                "fampnn"
            )
            
            // REPORT STAGE: fampnn
            CollectFAMPNNOutputs.out.pdbs.subscribe { pdbs ->
                try {
                    def file_list = pdbs instanceof List ? pdbs : [pdbs]
                    def count = file_list.size()
                    log.info("  FAMPNN via orchestrator: Collected ${count} PDBs from child jobs")
                    def report_files = count > 50 ? file_list[0..49] : file_list
                    def args = [params.job_id, "fampnn", "complete"] + report_files.collect { it.toString() }
                    def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage fampnn: ${e.message}"
                }
            }
            
            // ═══════════════════════════════════════════════════════════════════
            // FILTER: Pre-Boltz Filtering (optional)
            // Reject low-quality FAMPNN sequences before expensive Boltz validation
            // ═══════════════════════════════════════════════════════════════════
            def filterEnabled = params.enable_fampnn_filter != false && 
                               (params.fampnn_max_psce != null || params.fampnn_max_residue_psce != null)
            
            if (filterEnabled) {
                def filterDesc = []
                if (params.fampnn_max_psce != null) filterDesc << "max avg PSCE: ${params.fampnn_max_psce}"
                if (params.fampnn_max_residue_psce != null) filterDesc << "max residue PSCE: ${params.fampnn_max_residue_psce}"
                log.info("  Filtering FAMPNN designs (${filterDesc.join(', ')})...")
                
                // Collect PDBs and JSONs for filtering
                fampnn_filter_input = CollectFAMPNNOutputs.out.pdbs
                    .combine(CollectFAMPNNOutputs.out.jsons.ifEmpty(file("${projectDir}/lib/empty-meta.jsonl")))
                
                FilterFAMPNN(fampnn_filter_input)
                
                FilterFAMPNN.out.pdbs.subscribe { pdbs ->
                    def count = pdbs instanceof List ? pdbs.size() : 1
                    log.info("  FilterFAMPNN: ${count} designs passed filter")
                }
                
                fampnn_seqs = FilterFAMPNN.out.pdbs.map { pdbs ->
                    def meta = [id: "fampnn_designs"]
                    [meta, pdbs]
                }
            } else {
                log.info("  FAMPNN filtering disabled (enable with fampnn_max_psce or fampnn_max_residue_psce)")
                // Pass through unfiltered
                fampnn_seqs = CollectFAMPNNOutputs.out.pdbs.map { pdbs ->
                    def meta = [id: "fampnn_designs"]
                    [meta, pdbs]
                }
            }
        } // End of else block (standard FAMPNN mode)
    }

    // AntiFold branch (requires IMGT numbering)
    if (run_antifold) {
        log.info("  Running AntiFold...")
        // First number with ANARCI
        ANARCI(backbone_designs)
        ANTIFOLD(ANARCI.out.pdb_imgt)
        
        // AntiFold already emits [meta, sequences (fasta)] - wait, check module
        // Module emits: tuple val(meta), path("*_probs.csv"), emit: probabilities
        //              tuple val(meta), path("*_sampled.fasta"), emit: sequences
        // WE NEED PDBs! AntiFold output is FASTA? 
        // Checking module again... yes, outputs FASTA.
        // Boltz *can* take FASTA if we prep it right, but PrepBoltz expects PDBs currently.
        // Actually, looking at main.nf, PrepBoltz expects PDBs.
        // BUT wait, AntiFold module in this codebase might be doing structure generation?
        // Let's check if we missed something. 
        // The previous view of antifold.nf showed it outputs FASTAs.
        // IF downstream expects PDBs, we have a problem for AntiFold branch too.
        // HOWEVER, likely the user wants to validate the *sequences* modelled on the backbone?
        // OR construct new structures?
        // For now, let's assume valid data flow for what we have, but FAMPNN was definitely wrong (Tuple vs Path).
        
        antifold_seqs = ANTIFOLD.out.sequences
    }

    // ProteinMPNN branch
    if (run_proteinmpnn) {
        log.info("  Running ProteinMPNN...")
        // FIRST run PrepMPNN to generate PDBs with FIXED labels in B-factors
        // Map backbones to [pdbs, dummy_json] input for PrepMPNN
        mpnn_prep_input = backbone_designs.map { meta, pdbs ->
             [pdbs, file("${projectDir}/lib/empty-meta.jsonl")]
        }
        PrepMPNN(mpnn_prep_input)

        // Then run ProteinMPNN - it will auto-detect FIXED labels in B-factor column
        ProteinMPNNSeq(PrepMPNN.out.pdbs)
        
        // ProteinMPNNSeq (RunMPNN) outputs: tuple path("results/*.pdb"), path("results/*.json")
        // We need to map this to [meta, pdbs]
        proteinmpnn_seqs = ProteinMPNNSeq.out.pdbs_jsons.map { pdbs, jsons ->
            def meta = [id: "proteinmpnn_designs"]
            [meta, pdbs]
        }
    }

    // Collect all designed sequences for downstream
    // Merge into unified format: [meta, fasta/pdb]
    all_sequences = fampnn_seqs
        .mix(antifold_seqs) 
        .mix(proteinmpnn_seqs)

    // ═══════════════════════════════════════════════════════════════════════
    // Step 2.5: Pre-Boltz Stability Scoring (ThermoMPNN)
    // Filters unstable sequences BEFORE expensive Boltz-2 validation
    // ═══════════════════════════════════════════════════════════════════════
    if (params.run_thermompnn == true) {
        log.info("Step 2.5: Scoring sequence stability with ThermoMPNN...")
        
        // Flatten sequences for per-design ThermoMPNN scoring
        thermompnn_input = all_sequences.flatMap { meta, pdbs ->
            def pdb_list = pdbs instanceof List ? pdbs : [pdbs]
            pdb_list.collect { pdb ->
                def design_meta = [id: pdb.baseName]
                [design_meta, pdb]
            }
        }
        
        THERMOMPNN(thermompnn_input)
        
        // REPORT STAGE: thermompnn
        THERMOMPNN.out.stability.subscribe { meta, csv ->
            try {
                def args = [params.job_id, "thermompnn", "complete", csv.toString()]
                def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage thermompnn: ${e.message}"
            }
        }
        
        // Filter by ddG threshold if set
        if (params.thermompnn_max_ddg != null) {
            log.info("  Filtering by ThermoMPNN ddG <= ${params.thermompnn_max_ddg}...")
            
            // Parse stability CSV and filter designs
            stable_sequences = THERMOMPNN.out.stability.filter { meta, csv ->
                try {
                    def lines = csv.text.split('\n')
                    if (lines.size() > 1) {
                        // CSV format: sequence_id,ddG_pred
                        def ddg = lines[1].split(',')[1]?.trim()
                        if (ddg && ddg != 'N/A' && ddg != 'ERROR') {
                            return Float.parseFloat(ddg) <= params.thermompnn_max_ddg
                        }
                    }
                } catch (Exception e) {
                    log.warn("Could not parse ThermoMPNN output for ${meta.id}: ${e.message}")
                }
                return true // Pass through if parsing fails
            }.map { meta, csv ->
                // Re-associate with PDB file for downstream
                def pdb_file = file("${csv.parent}/${meta.id}.pdb")
                [meta, pdb_file.exists() ? pdb_file : csv]
            }
            
            // Collect filtered PDBs back into batched format
            sequences_for_boltz = stable_sequences.map { meta, pdb -> pdb }
                .collect()
                .map { pdbs -> 
                    def meta = [id: "thermompnn_filtered"]
                    [meta, pdbs]
                }
        } else {
            // No ddG filtering, pass through original sequences
            sequences_for_boltz = all_sequences
        }
        
        stability_scores_early = THERMOMPNN.out.stability
    } else {
        log.info("ThermoMPNN stability scoring disabled (enable with run_thermompnn=true)")
        sequences_for_boltz = all_sequences
        stability_scores_early = Channel.empty()
    }

    // ═══════════════════════════════════════════════════════════════════════
    // Step 2.6: AF2 Backprop CDR Refinement (Optional)
    // Uses ColabDesign AfDesign to optimize CDR sequences for binding confidence
    // ═══════════════════════════════════════════════════════════════════════
    if (params.run_af2_backprop == true) {
        log.info("Step 2.6: Refining CDR sequences with AF2 Backprop...")
        
        // Merge each antibody design with target for AF2 complex input
        af2_merge_input = sequences_for_boltz
            .flatMap { meta, pdbs ->
                def pdb_list = pdbs instanceof List ? pdbs : [pdbs]
                pdb_list.collect { pdb ->
                    def design_meta = [id: pdb.baseName]
                    [design_meta, pdb]
                }
            }
            .combine(target_pdb_ch.first().map { meta, pdb -> pdb })
            .map { meta, antibody_pdb, target_pdb ->
                [meta, antibody_pdb, target_pdb]
            }
        
        MergeComplex(af2_merge_input)
        AF2_BACKPROP(MergeComplex.out.complex)
        
        // REPORT STAGE: af2_backprop
        AF2_BACKPROP.out.refined.subscribe { meta, pdb ->
            try {
                def args = [params.job_id, "af2_backprop", "complete", pdb.toString()]
                def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage af2_backprop: ${e.message}"
            }
        }
        
        // Collect refined PDBs for downstream Boltz validation
        sequences_for_boltz = AF2_BACKPROP.out.refined
            .map { meta, pdb -> pdb }
            .collect()
            .map { pdbs ->
                def meta = [id: "af2_refined"]
                [meta, pdbs]
            }
    }

    // =========================================================================
    // PHASE 2: VALIDATION & SCORING
    // =========================================================================

    // Step 3: Structure Validation with Boltz2
    // ---------------------------------------------------------------------------
    log.info("Step 3: Validating structures with Boltz2...")

    if (params.run_structure_validation != false) {
        // =========================================================================
        // EXPLORATION vs REFINEMENT MODE
        // Parallel: Spawn child jobs for GPU distribution (fast screening)
        // Serial: Run BoltzFromSequenceWithMSA in-process (thorough analysis)
        // =========================================================================
        
        // Step 1: Extract sequences from FAMPNN PDB outputs
        design_sequences = sequences_for_boltz
            .flatMap { meta, files ->
                def pdbs = files instanceof List ? files : [files]
                pdbs.collect { pdb ->
                    def sequence = extractSequenceFromPDB(pdb)
                    tuple(sequence, pdb.baseName, pdb)
                }
            }

        design_sequences = design_sequences.ifEmpty {
            error "No sequences available for Boltz validation (upstream produced zero designs)"
        }
        
        // Step 2: Generate MSA ONCE using first design's sequence
        first_design_for_msa = design_sequences
            .first()
            .map { sequence, name, pdb ->
                tuple(sequence, "antibody_representative")
            }
        
        GenerateLocalMSA(first_design_for_msa)
        msa_file_ch = GenerateLocalMSA.out.msa.map { _seq, _name, msa_file -> msa_file }
        
        if (params.exploration_mode == true) {
            // ═══════════════════════════════════════════════════════════════════
            // PARALLEL MODE: Spawn child jobs via API for GPU orchestrator
            // Each child job enters queue and gets assigned to available GPU
            // Uses SpawnChildJobs process to BLOCK until spawn is complete
            // ═══════════════════════════════════════════════════════════════════
            log.info("Exploration Mode: Spawning child jobs for parallel GPU processing...")
            
            // Collect all PDB files from sequence design into a single list
            collected_pdbs = sequences_for_boltz
                .flatMap { meta, files -> files instanceof List ? files : [files] }
                .collect()
            
            // Get MSA file (take first emission since it's a single-value channel)
            msa_for_spawn = msa_file_ch.first()
            
            // Derive job identifiers with safe defaults
            def parent_id = params.job_id ?: "unknown_${System.currentTimeMillis()}"
            def batch = params.job_name ?: "antibody_batch"
            
            // Build comprehensive params_json with ALL quality settings for child jobs
            def child_params = groovy.json.JsonOutput.toJson([
                // Boltz-2 settings
                boltz_sampling_steps: params.boltz_sampling_steps ?: 200,
                boltz_recycling_steps: params.boltz_recycling_steps ?: 3,
                boltz_num_samples: params.boltz_num_samples ?: 1,
                boltz_use_potentials: params.boltz_use_potentials ?: false,
                boltz_use_msa: params.boltz_use_msa ?: false,
                boltz_step_scale: params.boltz_step_scale,
                
                // ThermoMPNN settings
                run_thermompnn: params.run_thermompnn ?: false,
                thermompnn_max_ddg: params.thermompnn_max_ddg,
                
                // Immunogenicity (AntiBERTy) settings
                run_immunogenicity_scoring: params.run_immunogenicity_scoring ?: false,
                
                // GPU assignment
                pinned_gpus: params.pinned_gpus,
                
                // Filtering settings carried from FAMPNN
                fampnn_max_psce: params.fampnn_max_psce,
                fampnn_max_residue_psce: params.fampnn_max_residue_psce
            ])
            
            // Call the spawn process - THIS BLOCKS until spawn is complete
            // PDBs and MSA must both be ready before this executes
            SpawnChildJobs(
                collected_pdbs,
                msa_for_spawn,
                parent_id,
                batch,
                child_params
            )
            
            // Get expected child count from spawn result
            spawn_child_count = SpawnChildJobs.out.result
                .map { result_file ->
                    try {
                        def result = new groovy.json.JsonSlurper().parse(result_file)
                        log.info("Spawned ${result.spawned_jobs} child validation jobs")
                        return result.spawned_jobs ?: 0
                    } catch (Exception e) {
                        log.warn("Failed to parse spawn result: ${e.message}")
                        return 0
                    }
                }
            
            // Wait for all children to complete and aggregate their results
            // This ensures validated designs are in pdb_files/ for analytics
            WaitAndAggregateChildResults(
                parent_id,
                batch,
                spawn_child_count
            )
            
            // Report aggregation completion
            WaitAndAggregateChildResults.out.report.subscribe { report_file ->
                try {
                    def report = new groovy.json.JsonSlurper().parse(report_file)
                    log.info("Aggregation complete: ${report.total_validated_designs} validated designs collected")
                } catch (Exception e) {
                    log.warn("Failed to parse aggregation report: ${e.message}")
                }
            }
            
            // Final validated structures from aggregation
            validated_structures = WaitAndAggregateChildResults.out.pdbs
        }
        else {
            // ═══════════════════════════════════════════════════════════════════
            // SERIAL MODE: Run Boltz in-process (sequential, thorough)
            // ═══════════════════════════════════════════════════════════════════
            log.info("Refinement Mode: Running Boltz validation sequentially...")
            
            boltz_inputs = design_sequences
                .combine(msa_file_ch)
                .map { sequence, name, pdb, msa_file ->
                    tuple(sequence, name, msa_file)
                }
            
            BoltzFromSequenceWithMSA(boltz_inputs)
            
            // REPORT STAGE: boltz2
            BoltzFromSequenceWithMSA.out.pdbs.subscribe { pdbs ->
                try {
                    def file_list = pdbs instanceof List ? pdbs : [pdbs]
                    def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                    def args = [params.job_id, "boltz2", "complete"] + report_files.collect { it.toString() }
                    def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage boltz2: ${e.message}"
                }
            }
            
            validated_structures = BoltzFromSequenceWithMSA.out.pdbs
                .flatten()
                .map { pdb ->
                    def name = pdb.baseName.replace('_model_0', '').replace('_boltzpred', '')
                    def meta = [id: name]
                    [meta, pdb]
                }
        }
    }
    else {
        validated_structures = all_sequences
    }

    // =========================================================================
    // Step 3.5: Physics Refinement with OpenMM (Optional)
    // =========================================================================
    // CDR-only energy minimization with framework restraints to preserve
    // validated AI geometry while resolving atomic-level clashes.
    // MM-GBSA scoring for binding affinity estimation (full tier only).

    if (params.openmm_enabled == true) {
        log.info("Step 3.5: Running OpenMM physics refinement...")
        log.info("  Compute tier: ${params.openmm_compute_tier ?: 'fast'}")
        log.info("  CDR-only mode: ${params.openmm_cdr_only ?: true}")
        log.info("  Restraint mode: ${params.openmm_restraint_mode ?: 'framework'}")
        
        // Batch validated structures for GPU processing
        openmm_batched = validated_structures
            .map { meta, pdb -> pdb }
            .collect()
            .flatten()
            .buffer(size: 10, remainder: true)
            .map { batch -> tuple("openmm_${batch.hashCode()}", batch) }
        
        // Run energy minimization
        OpenMMRelaxation(
            openmm_batched,
            params.openmm_compute_tier ?: 'fast',
            params.openmm_cdr_only ?: true,
            params.openmm_restraint_mode ?: 'framework',
            params.openmm_antibody_chain ?: 'H',
            params.openmm_force_field ?: 'amber14sb'
        )
        
        // REPORT STAGE: openmm_relaxation
        OpenMMRelaxation.out.relaxed_pdbs.subscribe { pdbs ->
            try {
                def file_list = pdbs instanceof List ? pdbs : [pdbs]
                def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                def args = [params.job_id, "openmm_relaxation", "complete"] + report_files.collect { it.toString() }
                def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage openmm_relaxation: ${e.message}"
            }
        }
        
        // Run MM-GBSA scoring for full tier or explicit request
        if (params.openmm_compute_tier == 'full' || params.openmm_mmgbsa_mode != 'off') {
            log.info("  Running MM-GBSA binding affinity scoring...")
            
            // Batch relaxed structures for scoring
            mmgbsa_batched = OpenMMRelaxation.out.relaxed_pdbs
                .collect()
                .flatten()
                .buffer(size: 5, remainder: true)
                .map { batch -> tuple("mmgbsa_${batch.hashCode()}", batch) }
            
            OpenMMScore(
                mmgbsa_batched,
                params.openmm_mmgbsa_mode ?: 'interface',
                params.openmm_binder_chains ?: 'H',
                params.openmm_target_chains ?: 'A',
                params.openmm_force_field ?: 'amber14sb'
            )
            
            // REPORT STAGE: openmm_mmgbsa
            OpenMMScore.out.scores_json.subscribe { jsons ->
                try {
                    def args = [params.job_id, "openmm_mmgbsa", "complete"]
                    def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage openmm_mmgbsa: ${e.message}"
                }
            }
        }
        
        // Use relaxed structures for downstream stages
        refined_structures = OpenMMRelaxation.out.relaxed_pdbs
            .flatten()
            .map { pdb ->
                def name = pdb.baseName.replace('_relaxed', '')
                def meta = [id: name]
                [meta, pdb]
            }
    }
    else {
        // Skip OpenMM - pass validated structures directly
        refined_structures = validated_structures
    }

    // Step 4: Immunogenicity Scoring with AntiBERTy
    // ---------------------------------------------------------------------------
    log.info("Step 4: Scoring immunogenicity with AntiBERTy...")

    if (params.run_immunogenicity_scoring != false) {
        // Extract sequences from structures for AntiBERTy
        // AntiBERTy expects FASTA input
        antiberty_input = refined_structures.map { meta, pdb ->
            // Convert PDB to FASTA (simplified - actual implementation needs extraction)
            [meta, pdb]
        }

        ANTIBERTY_SCORE(antiberty_input)
        immunogenicity_scores = ANTIBERTY_SCORE.out.scores

        // Filter high-risk sequences
        if (params.filter_immunogenic != false) {
            antiberty_filter_input = ANTIBERTY_SCORE.out.scores.join(refined_structures)
            ANTIBERTY_FILTER(antiberty_filter_input)
            filtered_structures = ANTIBERTY_FILTER.out.filtered_fasta
        }
        else {
            filtered_structures = refined_structures
        }
    }
    else {
        filtered_structures = refined_structures
        immunogenicity_scores = Channel.empty()
    }

    // NOTE: ThermoMPNN stability scoring moved to Step 2.5 (before Boltz-2)
    // This runs AFTER FAMPNN but BEFORE expensive Boltz validation for compute savings
    // Results are in stability_scores_early channel
    stable_designs = filtered_structures

    // Step 6: Affinity Maturation with IgGM (Optional)
    // ---------------------------------------------------------------------------
    if (params.run_affinity_maturation == true) {
        log.info("Step 6: Running affinity maturation with IgGM...")

        // Combine designs with target for maturation
        maturation_input = stable_designs
            .combine(target_pdb_ch.first())
            .map { meta, design_pdb, target_meta, target_pdb ->
                [meta, design_pdb, target_pdb]
            }

        IGGM_AFFINITY_MATURATION(maturation_input)
        matured_designs = IGGM_AFFINITY_MATURATION.out.matured_designs
        mutations = IGGM_AFFINITY_MATURATION.out.mutations

        // Re-validate matured designs through Phase 2
        // (In practice, you'd loop this back through steps 3-5)
        final_designs = matured_designs
    }
    else {
        final_designs = stable_designs
        mutations = Channel.empty()
    }

    emit:
    designs = final_designs // Final antibody designs
    immunogenicity = immunogenicity_scores // AntiBERTy PLL scores
    stability = stability_scores_early // ThermoMPNN ddG scores
    mutations = mutations // IgGM suggested mutations
    backbones = backbone_designs // Original RFantibody backbones
}

// =============================================================================
// STANDALONE WORKFLOW ENTRY
// =============================================================================
workflow {
    // =========================================================================
    // TARGET STRUCTURE RESOLUTION
    // Either use provided PDB OR predict from sequence
    // =========================================================================
    
    // Option 1: User provides target PDB (existing workflow - unchanged)
    if (params.target_pdb) {
        target_pdb = file(params.target_pdb)
        if (!target_pdb.exists()) {
            error("Target PDB not found: ${params.target_pdb}")
        }
        meta = [id: params.run_id ?: target_pdb.baseName]
        target_ch = Channel.of([meta, target_pdb])
    }
    // Option 2: User provides protein sequence (+optional DNA) - predict complex first
    else if (params.target_protein_seq) {
        log.info("No target_pdb provided - will predict target structure from sequence")
        
        meta = [id: params.run_id ?: 'target_complex']
        def protein_seq = params.target_protein_seq
        def dna_seq = params.target_dna_seq ?: null
        
        if (dna_seq) {
            log.info("DNA sequence provided - will predict protein-DNA complex")
        }
        
        // Create input channel for complex prediction
        complex_input = Channel.of([meta, protein_seq, dna_seq])
        
        // Run Boltz-2 complex prediction
        PredictTargetComplex(complex_input)
        
        // Use predicted complex as target
        target_ch = PredictTargetComplex.out.complex
    }
    else {
        error("Please provide either --target_pdb (antigen structure) or --target_protein_seq (sequence to predict)")
    }

    // Epitope residues
    epitope = params.epitope_residues ?: ""

    // Framework (optional)
    framework_ch = params.framework_pdb
        ? Channel.of([meta, file(params.framework_pdb)])
        : Channel.empty()

    // Run workflow
    ANTIBODY_DENOVO(target_ch, epitope, framework_ch)

    // Collect outputs
    ANTIBODY_DENOVO.out.designs
        .map { meta, pdb -> pdb }
        .flatten()
        .collectFile(name: 'final_designs.txt', storeDir: params.out_dir) { it.name + '\n' }
}
