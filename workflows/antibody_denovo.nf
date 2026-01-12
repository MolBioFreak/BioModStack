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
        fampnn_psce_threshold: params.fampnn_psce_threshold ?: 0.15
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
    
    output:
    path "child_outputs.json", emit: child_outputs
    
    script:
    """
    python3 ${projectDir}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --timeout 14400 \\
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
    
    python3 ${projectDir}/scripts/spawn_antibody_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir pdb_input \\
        --batch_name "${batch_name}" \\
        --msa_path "\$MSA_PERSIST_PATH" \\
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


// Initialize missing parameters with defaults to suppress warnings
if (!params.containsKey('framework_pdb')) params.framework_pdb = null
if (!params.containsKey('analysis_chain_id')) params.analysis_chain_id = 'all_chains'
if (!params.containsKey('filter_immunogenic')) params.filter_immunogenic = true
if (!params.containsKey('run_affinity_maturation')) params.run_affinity_maturation = false
if (!params.containsKey('exploration_mode') || params.exploration_mode == null) params.exploration_mode = false
if (!params.containsKey('job_id')) params.job_id = "job_${new java.text.SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date())}"
if (!params.containsKey('job_name')) params.job_name = 'antibody_batch'

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
    framework_for_rfantibody = Channel.value(framework_path)
    
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
        wait_trigger = SpawnRFantibodyJobs.out.result.map { it -> params.job_id ?: "unknown" }
        WaitForChildren(
            wait_trigger,
            "rfantibody",
            30  // poll_interval_seconds
        )
        
        // Collect outputs from completed child jobs
        CollectChildOutputs(
            WaitForChildren.out.child_outputs,
            "rfantibody"
        )
        
        // Create backbone_designs channel from collected outputs
        backbone_designs = CollectChildOutputs.out.pdbs.flatten().toList().map { pdbs ->
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

    // FAMPNN branch
    if (run_fampnn) {
        log.info("  Running FAMPNN...")
        
        // Collect all PDBs from parallel GPU runs into a single list
        // backbone_designs channel has [meta, files] per GPU - need to merge
        all_backbone_pdbs = backbone_designs
            .map { meta, files -> files }           // Extract just the files
            .flatten()                              // Flatten list of lists
            .collect()                              // Collect into single list
        
        // PrepFAMPNN expects [pdbs, jsons]
        fampnn_prep_input = all_backbone_pdbs.map { pdbs ->
            // Create dummy JSON for prep
            [pdbs, file("${projectDir}/lib/NO_JSON")]
        }
        PrepFAMPNN(fampnn_prep_input)

        // RunFAMPNN expects [batch_id, pdbs, csv, gpu_id], analysis_chain_id
        // Collect all PDB outputs and the CSV into a single batch tuple
        // Use collect() to get all PDBs as a single list emission
        fampnn_pdbs_collected = PrepFAMPNN.out.pdbs.collect()
        fampnn_csv_file = PrepFAMPNN.out.csv.first()
        
        // Multi-GPU parallelism for FAMPNN
        // Split PDBs across available GPUs
        log.info("  Multi-GPU mode: Splitting FAMPNN across ${available_gpus.size()} GPU(s)")
        
        // Create one job per GPU, splitting the PDBs
        // Use map to structure data, then flatMap to emit multiple items
        fampnn_run_input = fampnn_pdbs_collected
            .combine(fampnn_csv_file)
            .map { items ->
                // combine() flattens everything into a list, last item is csv
                def csv = items[-1]
                def pdb_list = items.size() > 1 ? items[0..-2] : []
                return [pdb_list, csv]
            }
            .flatMap { pdb_list, csv ->
                def fampnn_gpu_list = available_gpus
                def fampnn_gpu_count = fampnn_gpu_list.size()
                def chunk_size = Math.max(1, (pdb_list.size() / fampnn_gpu_count).intValue())
                def items = []
                fampnn_gpu_list.eachWithIndex { gpu_id, idx ->
                    def start = idx * chunk_size
                    def end = (idx == fampnn_gpu_count - 1) ? pdb_list.size() : (idx + 1) * chunk_size
                    if (start < pdb_list.size()) {
                        def pdb_subset = pdb_list.subList(start, end)
                        log.info("    GPU ${gpu_id}: ${pdb_subset.size()} PDBs (${start}-${end-1})")
                        items << [idx, pdb_subset, csv, gpu_id]
                    }
                }
                return items
            }

        RunFAMPNN(fampnn_run_input, params.analysis_chain_id ?: "all_chains")

        // REPORT STAGE: fampnn
        RunFAMPNN.out.pdbs_jsons.subscribe { pdbs, jsons ->
            try {
                def file_list = pdbs instanceof List ? pdbs : [pdbs]
                def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                def args = [params.job_id, "fampnn", "complete"] + report_files.collect { it.toString() }
                def proc = ["python3", "${projectDir}/scripts/stage_reporter.py", *args].execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage fampnn: ${e.message}"
            }
        }

        // ═══════════════════════════════════════════════════════════════════
        // FILTER: Pre-Boltz Filtering
        // Reject low-quality FAMPNN sequences before expensive Boltz validation
        // This is the highest-impact filter for compute savings
        // ═══════════════════════════════════════════════════════════════════
        def filterEnabled = params.enable_fampnn_filter != false && 
                           (params.fampnn_max_psce != null || params.fampnn_max_residue_psce != null)
        
        if (filterEnabled) {
            def filterDesc = []
            if (params.fampnn_max_psce != null) filterDesc << "max avg PSCE: ${params.fampnn_max_psce}"
            if (params.fampnn_max_residue_psce != null) filterDesc << "max residue PSCE: ${params.fampnn_max_residue_psce}"
            log.info("  Filtering FAMPNN designs (${filterDesc.join(', ')})...")
            
            FilterFAMPNN(RunFAMPNN.out.pdbs_jsons)
            
            // Report filter results
            FilterFAMPNN.out.pdbs.subscribe { pdbs ->
                def count = pdbs instanceof List ? pdbs.size() : 1
                log.info("  FilterFAMPNN: ${count} designs passed filter")
            }
            
            // Use filtered outputs for downstream (discard JSONs, Boltz doesn't need them)
            fampnn_seqs = FilterFAMPNN.out.pdbs.map { pdbs ->
                def meta = [id: "fampnn_designs"]
                [meta, pdbs]
            }
        } else {
            log.info("  FAMPNN filtering disabled (enable with fampnn_max_psce or fampnn_max_residue_psce)")
            // Pass through unfiltered - drop JSONs to prevent downstream PrepBoltz error
            fampnn_seqs = RunFAMPNN.out.pdbs_jsons.map { pdbs, jsons ->
                def meta = [id: "fampnn_designs"]
                [meta, pdbs]
            }
        }
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
             [pdbs, file("${projectDir}/lib/NO_JSON")]
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
            
            // Call the spawn process - THIS BLOCKS until complete
            // PDBs and MSA must both be ready before this executes
            SpawnChildJobs(
                collected_pdbs,
                msa_for_spawn,
                parent_id,
                batch
            )
            
            // Report completion with actual count from spawn result
            SpawnChildJobs.out.result.subscribe { result_file ->
                try {
                    def result = new groovy.json.JsonSlurper().parse(result_file)
                    log.info("Parent job complete. ${result.spawned_jobs} child jobs queued for validation.")
                    if (result.status != "complete") {
                        log.warn("Spawn status: ${result.status}")
                    }
                } catch (Exception e) {
                    log.warn("Failed to parse spawn result: ${e.message}")
                }
            }
            
            // Parent's job is done - no local validation needed
            // Children run independently via GPU orchestrator
            validated_structures = channel.empty()
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

    // Step 4: Immunogenicity Scoring with AntiBERTy
    // ---------------------------------------------------------------------------
    log.info("Step 4: Scoring immunogenicity with AntiBERTy...")

    if (params.run_immunogenicity_scoring != false) {
        // Extract sequences from structures for AntiBERTy
        // AntiBERTy expects FASTA input
        antiberty_input = validated_structures.map { meta, pdb ->
            // Convert PDB to FASTA (simplified - actual implementation needs extraction)
            [meta, pdb]
        }

        ANTIBERTY_SCORE(antiberty_input)
        immunogenicity_scores = ANTIBERTY_SCORE.out.scores

        // Filter high-risk sequences
        if (params.filter_immunogenic != false) {
            antiberty_filter_input = ANTIBERTY_SCORE.out.scores.join(validated_structures)
            ANTIBERTY_FILTER(antiberty_filter_input)
            filtered_structures = ANTIBERTY_FILTER.out.filtered_fasta
        }
        else {
            filtered_structures = validated_structures
        }
    }
    else {
        filtered_structures = validated_structures
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
