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

def parseFastaRecords(fasta_file) {
    def records = []
    def currentId = null
    def sequence = new StringBuilder()

    fasta_file.eachLine { line ->
        def trimmed = line?.trim()
        if (!trimmed) {
            return
        }
        if (trimmed.startsWith('>')) {
            if (currentId != null) {
                records << [id: currentId, sequence: sequence.toString()]
            }
            currentId = trimmed.substring(1).trim()
            sequence = new StringBuilder()
        } else {
            sequence.append(trimmed)
        }
    }

    if (currentId != null) {
        records << [id: currentId, sequence: sequence.toString()]
    }

    return records
}

// Import modules
include { RFANTIBODY } from '../modules/rfantibody'
include { ANTIFOLD } from '../modules/antifold'
include { PrepFAMPNN ; RunFAMPNN ; FilterFAMPNN } from '../modules/fampnn'
include { PrepMPNN ; RunMPNN as ProteinMPNNSeq } from '../modules/proteinmpnn'
include { ANTIBERTY_SCORE ; ANTIBERTY_FILTER_STRUCTURES } from '../modules/antiberty'
include { THERMOMPNN } from '../modules/thermompnn'
include { MergeComplex ; AF2_BACKPROP } from '../modules/af2_backprop'
include { IGGM_AFFINITY_MATURATION } from '../modules/iggm'
include { PrepBoltz ; PrepBoltzWithMSA ; RunBoltz } from '../modules/boltz'
include { GenerateLocalMSA ; BoltzFromSequenceWithMSA } from '../modules/structure_prediction'
include { ANARCII } from '../modules/utils/anarci'
include { PredictTargetComplex } from '../modules/predict_target_complex'
include { OpenMMRelaxation ; OpenMMScore } from '../modules/openmm'
include { FrustrampnnQC ; AggregateFrustrationReports } from '../modules/frustrampnn'
include { BatchProtenixValidation } from '../modules/antibody_batch'

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
    def customLoopSpec = params.get('rfantibody_design_loops_custom')
    def loopLengthSpec = params.get('rfantibody_loop_length_ranges')
    def params_json = groovy.json.JsonOutput.toJson([
        rfantibody_diffusion_steps: params.rfantibody_diffusion_steps ?: 50,
        rfantibody_noise_scale_ca: params.rfantibody_noise_scale_ca ?: 1.0,
        rfantibody_noise_scale_frame: params.rfantibody_noise_scale_frame ?: 1.0,
        rfantibody_guide_scale: params.rfantibody_guide_scale ?: 10,
        rfantibody_ckpt_override: params.rfantibody_ckpt_override,
        rfantibody_debug_repo_overlay: params.rfantibody_debug_repo_overlay ?: false,
        // Pass UI CDR loop selection - prefer custom UI index over general string flag if available
        antibody_design_loops: customLoopSpec ?: (params.antibody_design_loops ?: ''),
        rfantibody_loop_length_ranges: loopLengthSpec,
        antibody_chains: params.antibody_chains ?: 'H,L',
        pinned_gpus: params.pinned_gpus
    ])
    def frameworkArg = params.framework_pdb ? "--framework_pdb \"${params.framework_pdb}\" \\\n        " : ""
    """
    python3 ${params.code_root}/scripts/spawn_rfantibody_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --total_designs ${total_designs} \\
        --designs_per_job ${designs_per_job} \\
        --target_pdb "\$(readlink -f ${target_pdb})" \\
        --epitope_residues "${epitope_residues}" \\
        --framework_type "${framework_type}" \\
        ${frameworkArg}\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_rfa_result.json \\
        2>&1 | tee spawn_rfa.log
    """
}

process NormalizeTargetPDB {
    label 'process_low'

    publishDir "${params.out_dir}/input", mode: 'copy', pattern: "normalized_target.pdb"

    input:
    tuple val(meta), path(target_pdb)

    output:
    tuple val(meta), path("normalized_target.pdb"), emit: normalized

    script:
    def chainArg = params.antigen_chains ? "--chains \"${params.antigen_chains}\" \\\n        " : ""
    def modelArg = params.target_model_number ? "--model-number ${params.target_model_number} \\\n        " : ""
    def firstModelArg = params.target_model_number ? "" : "--first-model-only \\\n        "
    """
    python3 ${params.code_root}/scripts/normalize_target_pdb.py \\
        --input "\$(readlink -f ${target_pdb})" \\
        --output normalized_target.pdb \\
        ${firstModelArg}\
        ${modelArg}\
        ${chainArg}\
        2>&1 | tee normalize_target.log
    """
}

process StageRFantibodyBackbones {
    label 'process_low'

    publishDir "${params.out_dir}/collected/rfantibody_raw", mode: 'copy', pattern: "staged_output/*.pdb", saveAs: { fn -> fn.replace('staged_output/', '') }

    input:
    path pdb_files

    output:
    path "staged_output", emit: dir
    path "staged_output/*.pdb", emit: pdbs, optional: true
    path "rfantibody_stage_summary.json", emit: summary

    script:
    """
    set -euo pipefail
    mkdir -p staged_output
    count=0
    for pdb in ${pdb_files}; do
        [ -f "\$pdb" ] || continue
        base="\$(basename "\$pdb")"
        dest="staged_output/\$base"
        if [ -e "\$dest" ]; then
            dest="staged_output/\${count}_\$base"
        fi
        cp "\$pdb" "\$dest"
        count=\$((count + 1))
    done
    cat > rfantibody_stage_summary.json <<EOF
{
  "total_designs": \$count
}
EOF
    """
}

process ScreenRFantibodyBackbones {
    label 'process_low'

    publishDir "${params.out_dir}/run/rfantibody_screen", mode: 'copy', pattern: '*.log'
    publishDir "${params.out_dir}/run/rfantibody_screen", mode: 'copy', pattern: 'screening_summary.json'
    publishDir "${params.out_dir}/collected/rfantibody_filtered", mode: 'copy', pattern: 'screened_output/*.pdb', saveAs: { fn -> fn.replace('screened_output/', '') }
    publishDir "${params.out_dir}/collected/rfantibody_filtered", mode: 'copy', pattern: 'screened_output/*.json', saveAs: { fn -> fn.replace('screened_output/', '') }
    publishDir "${params.out_dir}/collected/rfantibody_filtered", mode: 'copy', pattern: 'screened_output/*.csv', saveAs: { fn -> fn.replace('screened_output/', '') }

    input:
    path staged_dir
    val epitope_residues
    val antibody_chains
    val target_chain
    path reference_target_pdb

    output:
    path "screened_output", emit: dir
    path "screened_output/*.pdb", emit: pdbs, optional: true
    path "screened_output/*.json", emit: jsons, optional: true
    path "screened_output/*.csv", emit: csvs, optional: true
    path "screening_summary.json", emit: summary
    path "screen_rfantibody_${task.index}.log", emit: log

    script:
    def minContactsArg = params.rfantibody_min_epitope_contacts != null ? "--min-epitope-contacts ${params.rfantibody_min_epitope_contacts}" : ""
    def maxDistanceArg = params.rfantibody_max_epitope_distance != null ? "--max-epitope-distance ${params.rfantibody_max_epitope_distance}" : ""
    def contactCutoffArg = params.rfantibody_contact_distance_threshold != null ? "--contact-distance-threshold ${params.rfantibody_contact_distance_threshold}" : ""
    def minTargetContactsArg = params.rfantibody_min_target_contacts != null ? "--min-target-contacts ${params.rfantibody_min_target_contacts}" : ""
    def maxTargetDistanceArg = params.rfantibody_max_target_distance != null ? "--max-target-distance ${params.rfantibody_max_target_distance}" : ""
    def maxEpitopeCentroidArg = params.rfantibody_max_epitope_centroid_distance != null ? "--max-epitope-centroid-distance ${params.rfantibody_max_epitope_centroid_distance}" : ""
    def targetContactCutoffArg = params.rfantibody_target_contact_distance_threshold != null ? "--target-contact-distance-threshold ${params.rfantibody_target_contact_distance_threshold}" : ""
    def targetChainArg = target_chain ? "--target-chain \"${target_chain}\"" : ""
    """
    python3 ${params.code_root}/scripts/screen_rfantibody_backbones.py \\
        --pdb-dir "\$(readlink -f ${staged_dir})" \\
        --output-dir screened_output \\
        --summary-json screening_summary.json \\
        --epitope-residues "${epitope_residues ?: ''}" \\
        --antibody-chains "${antibody_chains ?: ''}" \\
        --reference-target-pdb "\$(readlink -f ${reference_target_pdb})" \\
        ${targetChainArg} \\
        ${minContactsArg} \\
        ${maxDistanceArg} \\
        ${contactCutoffArg} \\
        ${minTargetContactsArg} \\
        ${maxTargetDistanceArg} \\
        ${maxEpitopeCentroidArg} \\
        ${targetContactCutoffArg} \\
        2>&1 | tee screen_rfantibody_${task.index}.log
    """
}

process CheckRFantibodyYield {
    label 'process_low'

    input:
    val candidate_count

    output:
    path "rfantibody_yield_guard.ok", emit: ok

    script:
    def reportJson = groovy.json.JsonOutput.prettyPrint(
        groovy.json.JsonOutput.toJson([
            status: "completed_zero_yield",
            reason: "No RFantibody backbones survived coarse screening or backbone generation failed upstream",
            min_epitope_contacts: params.rfantibody_min_epitope_contacts,
            max_epitope_distance: params.rfantibody_max_epitope_distance,
            min_target_contacts: params.rfantibody_min_target_contacts,
            max_target_distance: params.rfantibody_max_target_distance,
            max_epitope_centroid_distance: params.rfantibody_max_epitope_centroid_distance,
            recommendation: "Inspect RFantibody review artifacts, relax the coarse screen, or pause after RFantibody to review backbones manually before FAMPNN."
        ])
    )
    """
    set -euo pipefail
    if [ "${candidate_count}" -le 0 ]; then
        mkdir -p "${params.out_dir}"
        cat > "${params.out_dir}/rfantibody_zero_yield_report.json" <<'JSON'
${reportJson}
JSON
        echo "ZERO-YIELD: no RFantibody backbones passed coarse screening" >&2
        exit 1
    fi
    touch rfantibody_yield_guard.ok
    """
}

process CheckZeroYield {
    label 'process_low'

    input:
    val candidate_count

    output:
    path "zero_yield_guard.ok", emit: ok

    script:
    def structureValidator = (params.structure_validator ?: 'boltz2').toString().toLowerCase()
    def validationLabel = structureValidator == 'protenix' ? 'Protenix' : 'Boltz2'
    def reportJson = groovy.json.JsonOutput.prettyPrint(
        groovy.json.JsonOutput.toJson([
            status: "completed_zero_yield",
            reason: "No sequences survived upstream filtering or upstream child jobs failed before ${validationLabel} validation",
            structure_validator: structureValidator,
            fampnn_psce_threshold: params.fampnn_psce_threshold ?: "default",
            fampnn_temperature: params.fampnn_temperature ?: "default",
            recommendation: "Check RFantibody/FAMPNN child logs, confirm target antigen preprocessing, or relax FAMPNN filtering"
        ])
    )
    """
    set -euo pipefail
    if [ "${candidate_count}" -le 0 ]; then
        mkdir -p "${params.out_dir}"
        cat > "${params.out_dir}/zero_yield_report.json" <<'JSON'
${reportJson}
JSON
        echo "ZERO-YIELD: no designs reached ${validationLabel} validation" >&2
        exit 1
    fi
    touch zero_yield_guard.ok
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
        fampnn_checkpoint: params.fampnn_checkpoint,
        fampnn_checkpoint_path: params.fampnn_checkpoint_path,
        fampnn_temperature: params.fampnn_temperature ?: 0.0001,
        fampnn_num_steps: params.fampnn_num_steps ?: 500,
        fampnn_psce_threshold: params.fampnn_psce_threshold ?: 0.15,
        fampnn_constraint_mode: params.fampnn_constraint_mode,
        rfantibody_design_loops_custom: params.get('rfantibody_design_loops_custom'),
        rfantibody_loop_length_ranges: params.get('rfantibody_loop_length_ranges'),
        pinned_gpus: params.pinned_gpus
    ])
    """
    python3 ${params.code_root}/scripts/spawn_fampnn_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir "${pdb_dir}" \\
        --pdbs_per_job ${pdbs_per_job} \\
        --seqs_per_design ${seqs_per_design} \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
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
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
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
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output child_outputs.json
    """
}

process CollectFAMPNNOutputs {
    label 'process_low'
    
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "job*.pdb"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "job*.json"
    
    input:
    path child_outputs_json
    val stage_name
    
    output:
    tuple path("job*.pdb"), path("job*.json"), emit: outputs
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
// PPIFlow maturation spawn/wait/collect helpers
// =============================================================================
process StageMaturationInputs {
    label 'process_low'

    publishDir "${params.out_dir}/ppiflow/input_pdbs", mode: 'copy', pattern: "*.pdb"

    input:
    path pdbs

    output:
    path "input_pdbs", emit: pdb_dir

    script:
    """
    mkdir -p input_pdbs
    cp ${pdbs} input_pdbs/ 2>/dev/null || true
    """
}

process SpawnMaturationJobs {
    label 'process_low'

    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"

    input:
    path pdb_dir
    val designs_per_job
    val parent_job_id
    val batch_name
    val stage_name

    output:
    path "spawn_maturation_result.json", emit: result

    script:
    def params_json = groovy.json.JsonOutput.toJson([
        framework_type: params.framework_type,
        framework_pdb: params.framework_pdb,
        antibody_chains: params.antibody_chains,
        antigen_chains: params.antigen_chains,
        epitope_residues: params.epitope_residues ?: "",
        ppiflow_start_t: params.ppiflow_start_t ?: 0.8,
        ppiflow_samples_per_target: params.ppiflow_samples_per_target ?: 1,
        ppiflow_retry_limit: params.ppiflow_retry_limit ?: 10,
        ppiflow_config: params.ppiflow_config,
        ppiflow_checkpoint: params.ppiflow_checkpoint,
        ppiflow_checkpoint_path: params.ppiflow_checkpoint_path,
        ppiflow_weights_dir: params.ppiflow_weights_dir,
        ppiflow_antigen_chain: params.ppiflow_antigen_chain,
        ppiflow_heavy_chain: params.ppiflow_heavy_chain,
        ppiflow_light_chain: params.ppiflow_light_chain,
        maturation_anchor_threshold: params.maturation_anchor_threshold ?: -5.0,
        maturation_anchor_distance_cutoff: params.maturation_anchor_distance_cutoff ?: 8.0,
        maturation_min_improvement: params.maturation_min_improvement ?: -1.0,
        maturation_filter_percentile: params.maturation_filter_percentile,
        maturation_redesign_temp: params.maturation_redesign_temp,
        maturation_redesign_steps: params.maturation_redesign_steps,
        maturation_design_mode: params.maturation_design_mode,
        maturation_redesign_enabled: params.maturation_redesign_enabled,
        maturation_redesign_top_n: params.maturation_redesign_top_n,
        fampnn_checkpoint: params.fampnn_checkpoint,
        fampnn_checkpoint_path: params.fampnn_checkpoint_path,
        fampnn_temperature: params.fampnn_temperature,
        fampnn_num_steps: params.fampnn_num_steps,
        fampnn_psce_threshold: params.fampnn_psce_threshold,
        fampnn_exclude_cys: params.fampnn_exclude_cys,
        fampnn_repack_last: params.fampnn_repack_last,
        fampnn_seq_only: params.fampnn_seq_only,
        fampnn_extra_config: params.fampnn_extra_config,
        thermompnn_max_ddg: params.thermompnn_max_ddg,
        rfantibody_design_loops_custom: params.get('rfantibody_design_loops_custom'),
        rfantibody_loop_length_ranges: params.get('rfantibody_loop_length_ranges'),
        pinned_gpus: params.pinned_gpus
    ])
    """
    python3 ${params.code_root}/scripts/spawn_maturation_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir "${pdb_dir}" \\
        --designs_per_job ${designs_per_job} \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --stage "${stage_name}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_maturation_result.json \\
        2>&1 | tee spawn_maturation.log
    """
}

process WaitForMaturationChildren {
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

process CollectMaturationOutputs {
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
    python3 ${params.code_root}/scripts/collect_maturation_outputs.py \\
        --child_outputs_json "${child_outputs_json}" \\
        --stage_name "${stage_name}" \\
        --manifest collection_manifest.json
    """
}

process StageValidatedMaturationInputs {
    label 'process_low'

    publishDir "${params.out_dir}/ppiflow/validated_input_pdbs", mode: 'copy', pattern: "*.pdb"

    input:
    path pdbs

    output:
    path "input_pdbs", emit: pdb_dir

    script:
    """
    mkdir -p input_pdbs
    cp ${pdbs} input_pdbs/ 2>/dev/null || true
    """
}

process SpawnValidatedMaturationJobs {
    label 'process_low'

    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"

    input:
    path pdb_dir
    val designs_per_job
    val parent_job_id
    val batch_name
    val stage_name

    output:
    path "spawn_validated_maturation_result.json", emit: result

    script:
    def params_json = groovy.json.JsonOutput.toJson([
        framework_type: params.framework_type,
        framework_pdb: params.framework_pdb,
        antibody_chains: params.antibody_chains,
        antigen_chains: params.antigen_chains,
        epitope_residues: params.epitope_residues ?: "",
        ppiflow_start_t: params.ppiflow_start_t ?: 0.8,
        ppiflow_samples_per_target: params.ppiflow_samples_per_target ?: 1,
        ppiflow_retry_limit: params.ppiflow_retry_limit ?: 10,
        ppiflow_config: params.ppiflow_config,
        ppiflow_checkpoint: params.ppiflow_checkpoint,
        ppiflow_checkpoint_path: params.ppiflow_checkpoint_path,
        ppiflow_weights_dir: params.ppiflow_weights_dir,
        ppiflow_antigen_chain: params.ppiflow_antigen_chain,
        ppiflow_heavy_chain: params.ppiflow_heavy_chain,
        ppiflow_light_chain: params.ppiflow_light_chain,
        maturation_anchor_threshold: params.maturation_anchor_threshold ?: -5.0,
        maturation_anchor_distance_cutoff: params.maturation_anchor_distance_cutoff ?: 8.0,
        maturation_min_improvement: params.maturation_min_improvement ?: -1.0,
        maturation_filter_percentile: params.maturation_filter_percentile,
        maturation_redesign_temp: params.maturation_redesign_temp,
        maturation_redesign_steps: params.maturation_redesign_steps,
        maturation_design_mode: params.maturation_design_mode,
        maturation_redesign_enabled: params.maturation_redesign_enabled,
        maturation_redesign_top_n: params.maturation_redesign_top_n,
        fampnn_psce_threshold: params.fampnn_psce_threshold,
        fampnn_exclude_cys: params.fampnn_exclude_cys,
        fampnn_repack_last: params.fampnn_repack_last,
        fampnn_seq_only: params.fampnn_seq_only,
        fampnn_extra_config: params.fampnn_extra_config,
        rfantibody_design_loops_custom: params.get('rfantibody_design_loops_custom'),
        rfantibody_loop_length_ranges: params.get('rfantibody_loop_length_ranges'),
        pinned_gpus: params.pinned_gpus
    ])
    """
    python3 ${params.code_root}/scripts/spawn_maturation_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir "${pdb_dir}" \\
        --designs_per_job ${designs_per_job} \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --stage "${stage_name}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_validated_maturation_result.json \\
        2>&1 | tee spawn_validated_maturation.log
    """
}

process WaitForValidatedMaturationChildren {
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

process CollectValidatedMaturationOutputs {
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
    python3 ${params.code_root}/scripts/collect_maturation_outputs.py \\
        --child_outputs_json "${child_outputs_json}" \\
        --stage_name "${stage_name}" \\
        --manifest collection_manifest.json
    """
}

process StageStructureValidationArtifacts {
    label 'process_low'

    publishDir "${params.out_dir}/collected/structure_validation", mode: 'copy', pattern: "validation_artifacts/*"

    input:
    val pdbs

    output:
    path "validation_artifacts", emit: dir

    script:
    def pdbList = (pdbs instanceof Collection ? pdbs : [pdbs]).collect { it.toString() }.join('\n')
    """
    mkdir -p validation_artifacts
    cat > pdbs.list <<'EOF'
${pdbList}
EOF
    while IFS= read -r pdb; do
        [ -n "\$pdb" ] || continue
        [ -f "\$pdb" ] || continue
        base="\$(basename "\$pdb")"
        stem="\${base%.*}"
        cp "\$pdb" "validation_artifacts/\$base"
        for ext in json cif; do
            sibling="\${pdb%.*}.\$ext"
            if [ -f "\$sibling" ]; then
                cp "\$sibling" "validation_artifacts/\${stem}.\$ext"
            fi
        done
    done < pdbs.list
    """
}

process OpenInteractiveGate {
    label 'process_low'

    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: "*.json"

    input:
    val job_id
    val stage_name
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

// =============================================================================
// ANARCII Polishing (trigger API annotation)
// =============================================================================
process TriggerANARCIIAnnotationPostFAMPNNGate {
    label 'process_low'

    input:
    val job_id
    val include_children

    output:
    path "anarcii_trigger.log", emit: log

    script:
    """
    python3 ${params.code_root}/scripts/trigger_anarcii_annotation.py \\
        --job_id "${job_id}" \\
        --include_children "${include_children}" \\
        --api_url "${params.api_url}" \\
        2>&1 | tee anarcii_trigger.log
    """
}

process TriggerANARCIIAnnotationPostValidationGate {
    label 'process_low'

    input:
    val job_id
    val include_children

    output:
    path "anarcii_trigger.log", emit: log

    script:
    """
    python3 ${params.code_root}/scripts/trigger_anarcii_annotation.py \\
        --job_id "${job_id}" \\
        --include_children "${include_children}" \\
        --api_url "${params.api_url}" \\
        2>&1 | tee anarcii_trigger.log
    """
}

process TriggerANARCIIAnnotationFinal {
    label 'process_low'

    input:
    val job_id
    val include_children

    output:
    path "anarcii_trigger.log", emit: log

    script:
    """
    python3 ${params.code_root}/scripts/trigger_anarcii_annotation.py \\
        --job_id "${job_id}" \\
        --include_children "${include_children}" \\
        --api_url "${params.api_url}" \\
        2>&1 | tee anarcii_trigger.log
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
    
    python3 ${params.code_root}/scripts/spawn_antibody_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir pdb_input \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --msa_path "\$MSA_PERSIST_PATH" \\
        --params_json '${child_params_json}' \\
        --seqs_per_validation_job ${params.seqs_per_validation_job ?: params.seqs_per_boltz_job ?: 10} \\
        --api_url "${params.api_url}" \\
        2>&1 | tee -a spawn.log
    
    SPAWN_EXIT=\${PIPESTATUS[0]}
    
    if [ "\$SPAWN_EXIT" -eq 0 ]; then
        CREATED_CHILDREN=\$(awk 'index(\$0, "[SPAWN] Created ") == 1 {count++} END {print count+0}' spawn.log)
        echo '{"spawned_jobs": '\$CREATED_CHILDREN', "status": "complete", "error": null}' > spawn_result.json
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
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.json"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.cif"
    publishDir "${params.out_dir}", mode: 'copy', pattern: "aggregation_report.json"
    
    input:
    val parent_job_id
    val batch_name
    val expected_child_count
    val child_stage
    
    output:
    path "validated_designs/*.pdb", emit: pdbs, optional: true
    path "validated_designs/*.json", emit: scores, optional: true
    path "aggregation_report.json", emit: report
    
    script:
    """
    #!/bin/bash
    set -euo pipefail
    
    echo "Waiting for ${expected_child_count} child validation jobs to complete..."
    
    mkdir -p validated_designs intermediates/boltz intermediates/scores
    declare -A COPIED_BASENAMES

    choose_dest_name() {
        local child_idx="${'$'}1"
        local filename="${'$'}2"
        local stem="\${filename%.*}"
        local ext=""
        if [ "\$stem" != "\$filename" ]; then
            ext=".\${filename##*.}"
        fi
        local candidate="validated_designs/\$filename"
        if [ ! -e "\$candidate" ]; then
            printf '%s\n' "\$candidate"
            return
        fi
        candidate="validated_designs/\${child_idx}_\$filename"
        if [ ! -e "\$candidate" ]; then
            printf '%s\n' "\$candidate"
            return
        fi
        local counter=2
        while true; do
            candidate="validated_designs/\${child_idx}_\${stem}_\${counter}\${ext}"
            if [ ! -e "\$candidate" ]; then
                printf '%s\n' "\$candidate"
                return
            fi
            counter=\$((counter + 1))
        done
    }
    
    # Wait for all children using the wait script
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${child_stage}" \\
        --batch_name "${batch_name}" \\
        --output wait_result.json \\
        --api_url "${params.api_url}" \\
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
            
            # Search multiple possible locations where validator outputs may be published
            for subdir in "pdb_files/predictions" "pdb_files" "run/boltz/predictions" "run/boltz" "run/protenix/predictions" "run/protenix" ""; do
                search_path="\$child_dir/\$subdir"
                if [ -d "\$search_path" ]; then
                    for pdb in \$search_path/*.pdb; do
                        if [ -f "\$pdb" ]; then
                            basename=\$(basename "\$pdb")
                            if [ -n "\${COPIED_BASENAMES[\$basename]:-}" ]; then
                                continue
                            fi
                            dest_path=\$(choose_dest_name "\$child_idx" "\$basename")
                            cp "\$pdb" "\$dest_path"
                            COPIED_BASENAMES[\$basename]=1
                            TOTAL_PDBS=\$((TOTAL_PDBS + 1))
                        fi
                    done
                    for cif in \$search_path/*.cif; do
                        if [ -f "\$cif" ]; then
                            basename=\$(basename "\$cif")
                            if [ -n "\${COPIED_BASENAMES[\$basename]:-}" ]; then
                                continue
                            fi
                            dest_path=\$(choose_dest_name "\$child_idx" "\$basename")
                            cp "\$cif" "\$dest_path" 2>/dev/null || true
                            COPIED_BASENAMES[\$basename]=1
                        fi
                    done
                    for json_path in \$search_path/*.json; do
                        if [ -f "\$json_path" ]; then
                            basename=\$(basename "\$json_path")
                            if [ -n "\${COPIED_BASENAMES[\$basename]:-}" ]; then
                                continue
                            fi
                            dest_path=\$(choose_dest_name "\$child_idx" "\$basename")
                            cp "\$json_path" "\$dest_path" 2>/dev/null || true
                            COPIED_BASENAMES[\$basename]=1
                        fi
                    done
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
        python3 ${params.code_root}/scripts/result_ingester.py \\
            --job_id "${parent_job_id}" \\
            --results_dir "${params.out_dir}" \\
            --api_url "${params.api_url}" \\
            2>&1 | tee ingest.log || echo "Warning: Ingestion had issues (non-fatal)"
    fi
    
    echo "Aggregation complete: \$TOTAL_PDBS designs ready for analytics"
    """
}

// Initialize missing parameters with defaults to suppress warnings
if (!params.containsKey('framework_pdb')) params.framework_pdb = null
if (!params.containsKey('run_id')) params.run_id = null
if (!params.containsKey('analysis_chain_id')) params.analysis_chain_id = 'all_chains'
if (!params.containsKey('filter_immunogenic')) params.filter_immunogenic = true
if (!params.containsKey('run_immunogenicity_scoring')) params.run_immunogenicity_scoring = false
if (!params.containsKey('run_affinity_maturation')) params.run_affinity_maturation = false
if (!params.containsKey('run_post_boltz_maturation')) params.run_post_boltz_maturation = false
if (!params.containsKey('run_post_validation_maturation')) params.run_post_validation_maturation = params.run_post_boltz_maturation
if (!params.run_post_boltz_maturation && params.run_post_validation_maturation) params.run_post_boltz_maturation = params.run_post_validation_maturation
if (!params.containsKey('structure_validator') || !params.structure_validator) params.structure_validator = 'boltz2'
if (!params.containsKey('exploration_mode') || params.exploration_mode == null) params.exploration_mode = false
if (!params.containsKey('pinned_gpus')) params.pinned_gpus = null
if (!params.containsKey('job_id')) params.job_id = "job_${new java.text.SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date())}"
if (!params.containsKey('job_name')) params.job_name = 'antibody_batch'
if (!params.containsKey('fampnn_constraint_mode')) params.fampnn_constraint_mode = 'antibody'
if (!params.containsKey('rfantibody_design_loops_custom')) params.rfantibody_design_loops_custom = null
if (!params.containsKey('rfantibody_loop_length_ranges')) params.rfantibody_loop_length_ranges = null
if (!params.containsKey('seq_design_fampnn')) params.seq_design_fampnn = null
if (!params.containsKey('seq_design_antifold')) params.seq_design_antifold = null
if (!params.containsKey('seq_design_proteinmpnn')) params.seq_design_proteinmpnn = null
if (!params.containsKey('enable_rfantibody_filter')) params.enable_rfantibody_filter = false
if (!params.containsKey('rfantibody_min_epitope_contacts')) params.rfantibody_min_epitope_contacts = null
if (!params.containsKey('rfantibody_max_epitope_distance')) params.rfantibody_max_epitope_distance = null
if (!params.containsKey('rfantibody_contact_distance_threshold')) params.rfantibody_contact_distance_threshold = 8.0
if (!params.containsKey('rfantibody_min_target_contacts')) params.rfantibody_min_target_contacts = null
if (!params.containsKey('rfantibody_max_target_distance')) params.rfantibody_max_target_distance = null
if (!params.containsKey('rfantibody_max_epitope_centroid_distance')) params.rfantibody_max_epitope_centroid_distance = null
if (!params.containsKey('rfantibody_target_contact_distance_threshold')) params.rfantibody_target_contact_distance_threshold = 12.0
if (!params.containsKey('run_structure_validation')) params.run_structure_validation = true
if (!params.containsKey('interactive_gating')) params.interactive_gating = false
if (!params.containsKey('interactive_swa')) params.interactive_swa = false
if (!params.containsKey('interactive_gate_stage') || !params.interactive_gate_stage) params.interactive_gate_stage = 'post_fampnn'
if (!params.containsKey('interactive_gate_continue')) params.interactive_gate_continue = false
if (!params.containsKey('target_model_number')) params.target_model_number = null

// Orchestrator-based parallelism settings
// 'standard' = Nextflow-internal parallelism (current behavior)
// 'full_orchestrator' = Spawn child jobs via API for per-job GPU assignment
if (!params.containsKey('parallel_mode')) params.parallel_mode = 'standard'
if (!params.containsKey('designs_per_job')) params.designs_per_job = 5
if (!params.containsKey('seqs_per_job')) params.seqs_per_job = 50
if (!params.containsKey('seqs_per_boltz_job') || params.seqs_per_boltz_job == null) params.seqs_per_boltz_job = 10
if (!params.containsKey('seqs_per_validation_job') || params.seqs_per_validation_job == null) params.seqs_per_validation_job = params.seqs_per_boltz_job ?: 10


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
    def framework_path = params.framework_pdb ? file(params.framework_pdb) : file("${params.code_root}/lib/NO_FRAMEWORK")
    framework_for_rfantibody = framework_pdb_ch
        .map { meta, pdb -> pdb }
        .ifEmpty(framework_path)

    if (params.framework_type == 'nanobody' &&
        (!params.antibody_chains || params.antibody_chains.toString().trim() == 'H,L')) {
        params.antibody_chains = 'H'
        log.info("  Nanobody mode detected; defaulting antibody_chains to H for maturation/design stages")
    }
    
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
    def designs_per_job = params.designs_per_job ?: 5
    def planned_child_jobs = Math.ceil(total_designs / (double) designs_per_job).intValue()
    // Use a per-run unique batch key to prevent accidental cross-run child reuse.
    // Reusing a static name (e.g., "antibody_batch") can make fresh jobs skip RFantibody
    // by attaching to completed children from older runs.
    def orchestrator_batch_name = params.job_id
        ? "${params.name ?: 'antibody_batch'}_${params.job_id}"
        : "${params.name ?: 'antibody_batch'}_${workflow.runName}"
    
    // =========================================================================
    // SKIP RFANTIBODY: Load pre-existing backbone PDBs instead of generating
    // =========================================================================
    def skip_rfantibody = params.skip_rfantibody == true || params.rfantibody_input_pdbs != null || params.fampnn_collected_pdbs != null
    def skip_rfantibody_input_dir = params.rfantibody_input_pdbs ?: params.fampnn_collected_pdbs

    if (skip_rfantibody && skip_rfantibody_input_dir) {
        log.info("  SKIP: Loading pre-existing backbone PDBs from ${skip_rfantibody_input_dir}")
        
        // Load backbone PDBs from provided directory
        backbone_designs = Channel.fromPath("${skip_rfantibody_input_dir}/*.pdb")
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
        log.info("  Orchestrator mode: Spawning ${planned_child_jobs} child job(s)")
        
        // Spawn child jobs via API
        SpawnRFantibodyJobs(
            target_pdb_ch.map { meta, pdb -> pdb }.first(),
            epitope_residues ?: "",
            params.framework_type ?: "standard-fv",
            total_designs,
            designs_per_job,
            params.job_id ?: "unknown",
            orchestrator_batch_name
        )
        
        // Wait for all child jobs to complete
        // Depends on spawn completion via SpawnRFantibodyJobs.out.result
        // Pass batch_name for resume support (find children from original run)
        wait_trigger = SpawnRFantibodyJobs.out.result.map { it -> params.job_id ?: "unknown" }
        batch_name = orchestrator_batch_name
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

        // REPORT STAGE: rfantibody (orchestrator path)
        CollectChildOutputs.out.pdbs.subscribe { pdbs ->
            try {
                def file_list = pdbs instanceof List ? pdbs : [pdbs]
                def count = file_list.size()
                log.info("  RFantibody via orchestrator: Collected ${count} PDBs from child jobs")
                def report_files = count > 50 ? file_list[0..49] : file_list
                def args = [params.job_id, "rfantibody", "complete"] + report_files.collect { it.toString() }
                def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage rfantibody: ${e.message}"
            }
        }
        
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
                def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
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

    def interactiveGateEnabled = params.interactive_gating == true || params.interactive_swa == true
    def rfantibodyRawDir = params.out_dir ? "${params.out_dir}/collected/rfantibody_raw" : null
    def rfantibodyFilteredDir = params.out_dir ? "${params.out_dir}/collected/rfantibody_filtered" : null
    def rfantibodyScreenEnabled = params.enable_rfantibody_filter == true
    def shouldPauseAfterRFantibody = !params.skip_rfantibody && interactiveGateEnabled &&
        (params.interactive_gate_stage ?: 'post_fampnn') == 'post_rfantibody' &&
        params.interactive_gate_continue != true
    def shouldScreenRFantibody = !params.fampnn_collected_pdbs && (
        shouldPauseAfterRFantibody ||
        rfantibodyScreenEnabled ||
        params.rfantibody_min_epitope_contacts != null ||
        params.rfantibody_max_epitope_distance != null ||
        params.rfantibody_min_target_contacts != null ||
        params.rfantibody_max_target_distance != null ||
        params.rfantibody_max_epitope_centroid_distance != null
    )

    staged_rfantibody_pdbs = backbone_designs
        .map { meta, files -> files }
        .flatten()
        .collect()

    StageRFantibodyBackbones(staged_rfantibody_pdbs)

    if (shouldScreenRFantibody) {
        ScreenRFantibodyBackbones(
            StageRFantibodyBackbones.out.dir,
            epitope_residues ?: "",
            params.antibody_chains ?: "",
            params.antigen_chains ?: "",
            target_pdb_ch.map { meta, pdb -> pdb }.first()
        )
        rfantibody_ready_dir = ScreenRFantibodyBackbones.out.dir
        rfantibody_candidate_count = ScreenRFantibodyBackbones.out.summary.map { summary_file ->
            def data = new groovy.json.JsonSlurper().parse(summary_file)
            (data.passed_designs ?: 0) as Integer
        }
        rfantibodyCandidateDir = rfantibodyFilteredDir ?: rfantibodyRawDir
    } else {
        rfantibody_ready_dir = StageRFantibodyBackbones.out.dir
        rfantibody_candidate_count = StageRFantibodyBackbones.out.summary.map { summary_file ->
            def data = new groovy.json.JsonSlurper().parse(summary_file)
            (data.total_designs ?: 0) as Integer
        }
        rfantibodyCandidateDir = rfantibodyRawDir
    }

    reviewed_backbone_designs = rfantibody_ready_dir.map { dir ->
        def pdbs = dir.toFile().listFiles()?.findAll { it.name.toLowerCase().endsWith('.pdb') }?.sort { it.name }?.collect { file(it.toString()) } ?: []
        def meta = [id: params.name ?: "antibody"]
        [meta, pdbs]
    }

    if (shouldPauseAfterRFantibody) {
        log.info("Interactive SWA gate: pausing after RFantibody backbone generation at ${rfantibodyCandidateDir}")
        OpenInteractiveGate(
            params.job_id ?: "unknown",
            "post_rfantibody",
            rfantibodyCandidateDir,
            rfantibodyRawDir ?: "",
            shouldScreenRFantibody ? (rfantibodyFilteredDir ?: "") : "",
            params.framework_type ?: "standard-fv",
            params.antibody_chains ?: "",
            params.structure_validator ?: "boltz2"
        )
        final_designs = Channel.empty()
        immunogenicity_scores = Channel.empty()
        stability_scores_early = Channel.empty()
        mutations = Channel.empty()
        backbone_designs = reviewed_backbone_designs
    } else {
        // If a coarse screen ran, enforce non-zero yield even in refinement mode.
        // If screening is disabled for a hand-selected refinement set, trust the user selection.
        if (params.skip_rfantibody && !shouldScreenRFantibody) {
            backbone_designs = reviewed_backbone_designs
        } else {
            CheckRFantibodyYield(rfantibody_candidate_count)
            backbone_designs = reviewed_backbone_designs
                .combine(CheckRFantibodyYield.out.ok)
                .map { meta, pdbs, _guard -> [meta, pdbs] }
        }

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
        def fampnnRawDir = params.out_dir ? "${params.out_dir}/collected/fampnn" : null
        def fampnnFilteredDir = params.out_dir ? "${params.out_dir}/collected/fampnn_filtered" : null
        def fampnnCandidateDir = params.fampnn_collected_pdbs ? params.fampnn_collected_pdbs.toString() : null

        if (!run_fampnn && params.fampnn_collected_pdbs) {
            log.info("  Sequence design skipped: Using pre-collected PDBs from ${params.fampnn_collected_pdbs}")

            pre_collected_pdbs = Channel.fromPath("${params.fampnn_collected_pdbs}/*.pdb")
                .collect()

            pre_collected_pdbs.subscribe { pdbs ->
                log.info("  Sequence design skipped: Loaded ${pdbs.size()} input PDBs")
            }

            fampnn_seqs = pre_collected_pdbs.map { pdbs ->
                def meta = [id: "selected_designs"]
                [meta, pdbs]
            }
        }

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
            fampnnCandidateDir = params.fampnn_collected_pdbs.toString()
            
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
                [pdbs, file("${params.code_root}/lib/empty-meta.jsonl")]
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
                orchestrator_batch_name
            )
            
            // Wait for all FAMPNN children to complete
            // Pass batch_name for resume support (find children from original run)
            // Note: map closure must accept the path argument (even if unused) 
            fampnn_wait_trigger = SpawnFAMPNNJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }
            fampnn_batch_name = orchestrator_batch_name
            
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
            CollectFAMPNNOutputs.out.outputs.subscribe { items ->
                try {
                    def (pdbs, jsons) = items
                    def file_list = pdbs instanceof List ? pdbs : [pdbs]
                    def count = file_list.size()
                    log.info("  FAMPNN via orchestrator: Collected ${count} PDBs from child jobs")
                    def report_files = count > 50 ? file_list[0..49] : file_list
                    def args = [params.job_id, "fampnn", "complete"] + report_files.collect { it.toString() }
                    def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
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
                
                // Collect PDBs + JSONs for filtering
                FilterFAMPNN(CollectFAMPNNOutputs.out.outputs)
                
                FilterFAMPNN.out.pdbs.subscribe { pdbs ->
                    def count = pdbs instanceof List ? pdbs.size() : 1
                    log.info("  FilterFAMPNN: ${count} designs passed filter")
                }
                
                fampnn_seqs = FilterFAMPNN.out.pdbs.map { pdbs ->
                    def meta = [id: "fampnn_designs"]
                    [meta, pdbs]
                }
                fampnnCandidateDir = fampnnFilteredDir ?: fampnnRawDir
            } else {
                log.info("  FAMPNN filtering disabled (enable with fampnn_max_psce or fampnn_max_residue_psce)")
                // Pass through unfiltered
                fampnn_seqs = CollectFAMPNNOutputs.out.outputs.map { pdbs, jsons ->
                    def meta = [id: "fampnn_designs"]
                    [meta, pdbs]
                }
                fampnnCandidateDir = fampnnRawDir
            }
        } // End of else block (standard FAMPNN mode)
    }

    def shouldPauseAfterFampnn = interactiveGateEnabled &&
        (params.interactive_gate_stage ?: 'post_fampnn') == 'post_fampnn' &&
        params.interactive_gate_continue != true &&
        run_fampnn &&
        fampnnCandidateDir

    if (shouldPauseAfterFampnn) {
        log.info("Interactive SWA gate: pausing after FAMPNN candidate collection at ${fampnnCandidateDir}")
        if (params.run_anarcii_post == true) {
            if (!params.job_id) {
                log.warn("ANARCII polishing requested for post-FAMPNN gate but job_id is missing; skipping.")
            } else {
                def includeChildren = params.anarcii_include_children != null ? params.anarcii_include_children : true
                log.info("Step 2.y: Triggering ANARCII CDR annotation before FAMPNN review gate (include_children=${includeChildren})")
                TriggerANARCIIAnnotationPostFAMPNNGate(params.job_id, includeChildren)
            }
        }
        OpenInteractiveGate(
            params.job_id ?: "unknown",
            "post_fampnn",
            fampnnCandidateDir,
            fampnnRawDir ?: "",
            fampnnFilteredDir ?: "",
            params.framework_type ?: "standard-fv",
            params.antibody_chains ?: "",
            params.structure_validator ?: "boltz2"
        )
        validated_structures = Channel.empty()
        stability_scores_early = Channel.empty()
    } else {
        // =====================================================================
        // Step 2.4: PPIFlow Maturation (Interface Rotamer Enrichment + Partial Flow)
        // Applies only to the FAMPNN branch
        // =====================================================================
        maturation_seqs = Channel.empty()
        if (params.run_maturation == true) {
            if (!run_fampnn) {
                log.warn("PPIFlow maturation requested but FAMPNN is disabled; skipping maturation.")
                maturation_seqs = fampnn_seqs
            } else {
                log.info("Step 2.4: Running PPIFlow maturation on FAMPNN outputs...")
                log.info("  Spawning maturation child jobs (${params.maturation_designs_per_job ?: 4} PDBs per job)")

                maturation_inputs = fampnn_seqs
                    .map { meta, pdbs -> pdbs }
                    .flatten()
                    .collect()

                StageMaturationInputs(maturation_inputs)

                SpawnMaturationJobs(
                    StageMaturationInputs.out.pdb_dir,
                    params.maturation_designs_per_job ?: 4,
                    params.job_id ?: "unknown",
                    orchestrator_batch_name,
                    "maturation"
                )

                maturation_wait_trigger = SpawnMaturationJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }
                maturation_batch_name = orchestrator_batch_name

                WaitForMaturationChildren(
                    maturation_wait_trigger,
                    "maturation",
                    30,
                    maturation_batch_name
                )

                CollectMaturationOutputs(
                    WaitForMaturationChildren.out.child_outputs,
                    "maturation"
                )

                CollectMaturationOutputs.out.pdbs.subscribe { pdbs ->
                    try {
                        def file_list = pdbs instanceof List ? pdbs : [pdbs]
                        def count = file_list.size()
                        log.info("  PPIFlow maturation: Collected ${count} PDBs from child jobs")
                        def report_files = count > 50 ? file_list[0..49] : file_list
                        def args = [params.job_id, "maturation", "complete"] + report_files.collect { it.toString() }
                        def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                        proc.waitFor()
                    } catch (Exception e) {
                        println "Warning: Failed to report stage maturation: ${e.message}"
                    }
                }

                maturation_seqs = CollectMaturationOutputs.out.pdbs.map { pdbs ->
                    def meta = [id: "ppiflow_maturation"]
                    [meta, pdbs]
                }
            }
        } else {
            maturation_seqs = fampnn_seqs
        }

        if (run_antifold) {
            log.info("  Running AntiFold...")
            ANARCII(backbone_designs)
            ANTIFOLD(ANARCII.out.pdb_imgt)
            antifold_seqs = ANTIFOLD.out.sequences
        }

        if (run_proteinmpnn) {
            log.info("  Running ProteinMPNN...")
            mpnn_prep_input = backbone_designs.map { meta, pdbs ->
                 [pdbs, file("${params.code_root}/lib/empty-meta.jsonl")]
            }
            PrepMPNN(mpnn_prep_input)
            ProteinMPNNSeq(PrepMPNN.out.pdbs)
            proteinmpnn_seqs = ProteinMPNNSeq.out.pdbs_jsons.map { pdbs, jsons ->
                def meta = [id: "proteinmpnn_designs"]
                [meta, pdbs]
            }
        }

        pdb_designs = maturation_seqs.mix(proteinmpnn_seqs)
        sequence_only_designs = Channel.empty()
        if (run_antifold) {
            if (params.exploration_mode == true) {
                log.warn("AntiFold emits FASTA only. Exploration-mode Boltz children accept PDBs only, so AntiFold candidates are skipped until serial refinement.")
            } else {
                sequence_only_designs = antifold_seqs.flatMap { meta, fasta ->
                    parseFastaRecords(fasta).collect { record ->
                        tuple(record.sequence, record.id ?: "${meta.id}_antifold")
                    }
                }
            }
        }

        pdb_designs_for_boltz = pdb_designs
        if (params.run_thermompnn == true) {
            log.info("Step 2.5: Scoring sequence stability with ThermoMPNN...")

            thermompnn_input = pdb_designs.flatMap { meta, pdbs ->
                def pdb_list = pdbs instanceof List ? pdbs : [pdbs]
                pdb_list.collect { pdb ->
                    def design_meta = [id: pdb.baseName]
                    [design_meta, pdb]
                }
            }

            THERMOMPNN(thermompnn_input)

            THERMOMPNN.out.stability.subscribe { meta, csv ->
                try {
                    def args = [params.job_id, "thermompnn", "complete", csv.toString()]
                    def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage thermompnn: ${e.message}"
                }
            }

            def thermompnn_with_pdb = THERMOMPNN.out.stability
                .join(thermompnn_input.map { meta, pdb -> tuple(meta, pdb) })
                .map { meta, csv, pdb ->
                    tuple(meta, pdb, csv)
                }

            if (params.thermompnn_max_ddg != null) {
                log.info("  Filtering by ThermoMPNN ddG <= ${params.thermompnn_max_ddg}...")

                stable_pdb_designs = thermompnn_with_pdb.filter { meta, pdb, csv ->
                    try {
                        def lines = csv.text.split('\n')
                        if (lines.size() > 1) {
                            def ddg = lines[1].split(',')[1]?.trim()
                            if (ddg && ddg != 'N/A' && ddg != 'ERROR') {
                                return Float.parseFloat(ddg) <= params.thermompnn_max_ddg
                            }
                        }
                    } catch (Exception e) {
                        log.warn("Could not parse ThermoMPNN output for ${meta.id}: ${e.message}")
                    }
                    return true
                }

                pdb_designs_for_boltz = stable_pdb_designs
                    .map { meta, pdb, csv -> pdb }
                    .collect()
                    .map { pdbs ->
                        def meta = [id: "thermompnn_filtered"]
                        [meta, pdbs]
                    }
            }

            stability_scores_early = THERMOMPNN.out.stability
        } else {
            log.info("ThermoMPNN stability scoring disabled (enable with run_thermompnn=true)")
            stability_scores_early = Channel.empty()
        }

        if (params.run_af2_backprop == true) {
            log.info("Step 2.6: Refining CDR sequences with AF2 Backprop...")

            af2_merge_input = pdb_designs_for_boltz
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

            AF2_BACKPROP.out.refined.subscribe { meta, pdb ->
                try {
                    def args = [params.job_id, "af2_backprop", "complete", pdb.toString()]
                    def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage af2_backprop: ${e.message}"
                }
            }

            pdb_designs_for_boltz = AF2_BACKPROP.out.refined
                .map { meta, pdb -> pdb }
                .collect()
                .map { pdbs ->
                    def meta = [id: "af2_refined"]
                    [meta, pdbs]
                }
        }

        def structure_validator = (params.structure_validator ?: 'boltz2').toString().toLowerCase()
        if (!(structure_validator in ['boltz2', 'protenix'])) {
            log.warn("Unknown structure_validator '${structure_validator}', defaulting to boltz2")
            structure_validator = 'boltz2'
        }
        def validation_stage_name = "structure_validation"
        def validation_label = structure_validator == 'protenix' ? 'Protenix' : 'Boltz2'

        log.info("Step 3: Validating structures with ${validation_label}...")

        if (params.run_structure_validation != false) {
            pdb_design_sequences = pdb_designs_for_boltz
                .flatMap { meta, files ->
                    def pdbs = files instanceof List ? files : [files]
                    pdbs.collect { pdb ->
                        def sequence = extractSequenceFromPDB(pdb)
                        tuple(sequence, pdb.baseName, pdb)
                    }
                }

            if (structure_validator == 'protenix' && run_antifold) {
                log.warn("AntiFold emits FASTA only. Protenix validation currently runs on PDB-backed candidates only, so AntiFold sequence-only designs are skipped.")
            }

            design_sequences = (params.exploration_mode == true || structure_validator == 'protenix')
                ? pdb_design_sequences
                : pdb_design_sequences.mix(sequence_only_designs.map { sequence, name -> tuple(sequence, name, null) })

            design_sequence_count = design_sequences.count()
            CheckZeroYield(design_sequence_count)
            design_sequences = design_sequences
                .combine(CheckZeroYield.out.ok)
                .map { sequence, name, pdb, _guard -> tuple(sequence, name, pdb) }

            def msa_file_ch
            if (structure_validator == 'protenix') {
                log.info("Protenix validation uses its built-in MSA/update pipeline; skipping parent GenerateLocalMSA step.")
                msa_file_ch = Channel.value(file("${params.code_root}/lib/NO_MSA"))
            } else {
                first_design_for_msa = design_sequences
                    .map { sequence, name, pdb ->
                        tuple(sequence, name, pdb)
                    }
                    .first()
                    .map { sequence, name, pdb ->
                        tuple(sequence, "antibody_representative")
                    }

                GenerateLocalMSA(first_design_for_msa)
                msa_file_ch = GenerateLocalMSA.out.msa.map { _seq, _name, msa_file -> msa_file }
            }

            if (params.exploration_mode == true) {
                log.info("Exploration Mode: Spawning child jobs for parallel GPU processing...")

                collected_pdbs = pdb_designs_for_boltz
                    .flatMap { meta, files -> files instanceof List ? files : [files] }
                    .collect()

                msa_for_spawn = msa_file_ch

                def parent_id = params.job_id ?: "unknown_${System.currentTimeMillis()}"
                def batch = orchestrator_batch_name

                def child_params = groovy.json.JsonOutput.toJson([
                    structure_validator: structure_validator,
                    boltz_sampling_steps: params.boltz_sampling_steps ?: 200,
                    boltz_recycling_steps: params.boltz_recycling_steps ?: 3,
                    boltz_num_samples: params.boltz_num_samples ?: 1,
                    boltz_use_potentials: params.boltz_use_potentials ?: false,
                    boltz_use_msa: params.boltz_use_msa ?: false,
                    boltz_step_scale: params.boltz_step_scale,
                    protenix_model_weights: params.protenix_model_weights,
                    protenix_seeds: params.protenix_seeds,
                    protenix_n_sample: params.protenix_n_sample,
                    protenix_n_step: params.protenix_n_step,
                    protenix_n_cycle: params.protenix_n_cycle,
                    protenix_use_msa: params.protenix_use_msa,
                    protenix_msa_backend: params.protenix_msa_backend,
                    protenix_use_template: params.protenix_use_template,
                    protenix_enable_cache: params.protenix_enable_cache,
                    protenix_enable_fusion: params.protenix_enable_fusion,
                    protenix_auto_oom_retry: params.protenix_auto_oom_retry,
                    protenix_oom_retry_attempts: params.protenix_oom_retry_attempts,
                    msa_preset: params.msa_preset,
                    msa_use_gpu: params.msa_use_gpu,
                    msa_local_db: params.msa_local_db,
                    msa_cache_dir: params.msa_cache_dir,
                    msa_threads: params.msa_threads,
                    colabfold_api_host: params.colabfold_api_host,
                    msa_gpu_mode: params.msa_gpu_mode,
                    msa_gpu_threshold: params.msa_gpu_threshold,
                    msa_preferred_gpus: params.msa_preferred_gpus,
                    msa_excluded_gpus: params.msa_excluded_gpus,
                    msa_gpu_server_mode: params.msa_gpu_server_mode,
                    msa_gpu_server_wait_timeout: params.msa_gpu_server_wait_timeout,
                    msa_gpu_server_db_load_mode: params.msa_gpu_server_db_load_mode,
                    msa_gpu_server_startup_wait: params.msa_gpu_server_startup_wait,
                    run_thermompnn: params.run_thermompnn ?: false,
                    thermompnn_max_ddg: params.thermompnn_max_ddg,
                    run_immunogenicity_scoring: params.run_immunogenicity_scoring ?: false,
                    pinned_gpus: params.pinned_gpus,
                    fampnn_max_psce: params.fampnn_max_psce,
                    fampnn_max_residue_psce: params.fampnn_max_residue_psce
                ])

                SpawnChildJobs(
                    collected_pdbs,
                    msa_for_spawn,
                    parent_id,
                    batch,
                    child_params
                )

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

                WaitAndAggregateChildResults(
                    parent_id,
                    batch,
                    spawn_child_count,
                    validation_stage_name
                )

                WaitAndAggregateChildResults.out.report.subscribe { report_file ->
                    try {
                        def report = new groovy.json.JsonSlurper().parse(report_file)
                        log.info("Aggregation complete: ${report.total_validated_designs} validated designs collected")
                    } catch (Exception e) {
                        log.warn("Failed to parse aggregation report: ${e.message}")
                    }
                }

                validated_structures = WaitAndAggregateChildResults.out.pdbs
                    .flatten()
                    .map { pdb ->
                        def name = pdb.baseName.replace('_model_0', '').replace('_boltzpred', '')
                        def meta = [id: name]
                        [meta, pdb]
                    }
            } else {
                log.info("Refinement Mode: Running ${validation_label} validation sequentially...")

                if (structure_validator == 'protenix') {
                    collected_validation_pdbs = pdb_design_sequences
                        .map { sequence, name, pdb -> pdb }
                        .collect()

                    msa_for_validation = msa_file_ch
                    BatchProtenixValidation(collected_validation_pdbs, msa_for_validation)

                    BatchProtenixValidation.out.pdbs.subscribe { pdbs ->
                        try {
                            def file_list = pdbs instanceof List ? pdbs : [pdbs]
                            def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                            def args = [params.job_id, validation_stage_name, "complete"] + report_files.collect { it.toString() }
                            def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                            proc.waitFor()
                        } catch (Exception e) {
                            println "Warning: Failed to report stage ${validation_stage_name}: ${e.message}"
                        }
                    }

                    validated_structures = BatchProtenixValidation.out.pdbs
                        .flatten()
                        .map { pdb ->
                            def name = pdb.baseName.replace('_model_0', '').replace('_boltzpred', '')
                            def meta = [id: name]
                            [meta, pdb]
                        }
                } else {
                    boltz_inputs = design_sequences
                        .combine(msa_file_ch)
                        .map { sequence, name, pdb, msa_file ->
                            tuple(sequence, name, msa_file)
                        }

                    BoltzFromSequenceWithMSA(boltz_inputs)

                    BoltzFromSequenceWithMSA.out.pdbs.subscribe { pdbs ->
                        try {
                            def file_list = pdbs instanceof List ? pdbs : [pdbs]
                            def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                            def args = [params.job_id, validation_stage_name, "complete"] + report_files.collect { it.toString() }
                            def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                            proc.waitFor()
                        } catch (Exception e) {
                            println "Warning: Failed to report stage ${validation_stage_name}: ${e.message}"
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
        } else {
            if (run_antifold) {
                log.warn("Structure validation disabled; AntiFold sequence-only outputs are omitted from downstream structure-based stages.")
            }
            validated_structures = pdb_designs_for_boltz
        }

        def shouldPauseAfterStructureValidation = interactiveGateEnabled &&
            (params.interactive_gate_stage ?: 'post_fampnn') == 'post_structure_validation' &&
            params.interactive_gate_continue != true &&
            params.run_structure_validation != false

        if (params.run_structure_validation != false) {
            staged_validation_pdbs = validated_structures
                .map { meta, pdb -> pdb }
                .collect()

            StageStructureValidationArtifacts(staged_validation_pdbs)

            validation_gate_candidate_dir = StageStructureValidationArtifacts.out.dir
                .map { _dir -> "${params.out_dir}/collected/structure_validation" }
        }

        if (shouldPauseAfterStructureValidation) {
            log.info("Interactive SWA gate: pausing after ${validation_label} structure validation")
            if (params.run_anarcii_post == true) {
                if (!params.job_id) {
                    log.warn("ANARCII polishing requested for post-structure-validation gate but job_id is missing; skipping.")
                } else {
                    def includeChildren = params.anarcii_include_children != null ? params.anarcii_include_children : true
                    log.info("Step 3.y: Triggering ANARCII CDR annotation before ${validation_label} review gate (include_children=${includeChildren})")
                    TriggerANARCIIAnnotationPostValidationGate(params.job_id, includeChildren)
                }
            }
            OpenInteractiveGate(
                params.job_id ?: "unknown",
                "post_structure_validation",
                validation_gate_candidate_dir,
                "",
                "",
                params.framework_type ?: "standard-fv",
                params.antibody_chains ?: "",
                structure_validator
            )
            validated_structures = Channel.empty()
        } else if (params.run_post_validation_maturation == true) {
            log.info("Step 3.25: Running PPIFlow maturation on ${validation_label}-validated structures...")
            log.info("  Spawning post-validation maturation child jobs (${params.maturation_designs_per_job ?: 4} PDBs per job)")

            validated_maturation_inputs = validated_structures
                .map { meta, pdb -> pdb }
                .collect()

            StageValidatedMaturationInputs(validated_maturation_inputs)

            SpawnValidatedMaturationJobs(
                StageValidatedMaturationInputs.out.pdb_dir,
                params.maturation_designs_per_job ?: 4,
                params.job_id ?: "unknown",
                "${orchestrator_batch_name}_post_validation",
                "maturation_post_validation"
            )

            validated_maturation_wait_trigger = SpawnValidatedMaturationJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }

            WaitForValidatedMaturationChildren(
                validated_maturation_wait_trigger,
                "maturation_post_validation",
                30,
                "${orchestrator_batch_name}_post_validation"
            )

            CollectValidatedMaturationOutputs(
                WaitForValidatedMaturationChildren.out.child_outputs,
                "maturation_post_validation"
            )

            CollectValidatedMaturationOutputs.out.pdbs.subscribe { pdbs ->
                try {
                    def file_list = pdbs instanceof List ? pdbs : [pdbs]
                    def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                    def args = [params.job_id, "maturation_post_validation", "complete"] + report_files.collect { it.toString() }
                    def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage maturation_post_validation: ${e.message}"
                }
            }

            validated_structures = CollectValidatedMaturationOutputs.out.pdbs
                .flatten()
                .map { pdb ->
                    def meta = [id: pdb.baseName]
                    [meta, pdb]
                }
        }
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
                def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
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
                    def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
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
            ANTIBERTY_FILTER_STRUCTURES(antiberty_filter_input)
            filtered_structures = ANTIBERTY_FILTER_STRUCTURES.out.filtered_pdb
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

        if (params.run_structure_validation != false) {
            log.warn("IgGM affinity maturation completed, but a full post-IgGM Boltz revalidation loop is not yet wired in this workflow.")
        }
        final_designs = matured_designs
    }
    else {
        final_designs = stable_designs
        mutations = Channel.empty()
    }

    // Step 4.x: FrustraMPNN QC (Post-pipeline annotation)
    if (params.run_frustrampnn == true) {
        log.info("Step 4.x: Running FrustraMPNN QC on final candidates...")
        frustrampnn_input = final_designs.flatMap { meta, pdb_or_pdbs ->
            def pdb_list = pdb_or_pdbs instanceof List ? pdb_or_pdbs : [pdb_or_pdbs]
            pdb_list.collect { pdb ->
                def frustra_meta = [id: pdb.baseName]
                tuple(frustra_meta, pdb)
            }
        }
        FrustrampnnQC(frustrampnn_input)
        // Extract just the path from (meta, path) tuples before collecting
        AggregateFrustrationReports(FrustrampnnQC.out.summary.map { meta, summary -> summary }.collect())
    }

    // Step 4.y: ANARCII CDR annotation (post-pipeline polishing)
    if (params.run_anarcii_post == true) {
        if (!params.job_id) {
            log.warn("ANARCII polishing requested but job_id is missing; skipping.")
        } else {
            def includeChildren = params.anarcii_include_children != null ? params.anarcii_include_children : true
            log.info("Step 4.y: Triggering ANARCII CDR annotation (include_children=${includeChildren})")
            TriggerANARCIIAnnotationFinal(params.job_id, includeChildren)
        }
    }
    }

    emit:
    designs = final_designs // Final antibody designs
    immunogenicity = immunogenicity_scores // AntiBERTy PLL scores
    stability = stability_scores_early // ThermoMPNN ddG scores
    mutations = mutations // IgGM suggested mutations
    backbones = backbone_designs // RFantibody backbones after optional coarse screening/review staging
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

    NormalizeTargetPDB(target_ch)
    normalized_target_ch = NormalizeTargetPDB.out.normalized

    // Run workflow
    ANTIBODY_DENOVO(normalized_target_ch, epitope, framework_ch)

    // Collect outputs
    ANTIBODY_DENOVO.out.designs
        .map { meta, pdb -> pdb }
        .flatten()
        .collectFile(name: 'final_designs.txt', storeDir: params.out_dir) { it.name + '\n' }
}
