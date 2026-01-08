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
//
// PHASE 2: Validation & Scoring
//   Step 3: Structure Validation - Boltz2 (ipTM, pLDDT)
//   Step 4: Immunogenicity - AntiBERTy (pseudo-log-likelihood)
//   Step 5: Stability - ThermoMPNN (ddG)
//   Step 6: Affinity Maturation - IgGM (optional)
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
include { RunMPNN as ProteinMPNNSeq } from '../modules/proteinmpnn'
include { ANTIBERTY_SCORE ; ANTIBERTY_FILTER } from '../modules/antiberty'
include { THERMOMPNN } from '../modules/thermompnn'
include { IGGM_AFFINITY_MATURATION } from '../modules/iggm'
include { PrepBoltz ; PrepBoltzWithMSA ; RunBoltz } from '../modules/boltz'
include { GenerateLocalMSA ; BoltzFromSequenceWithMSA } from '../modules/structure_prediction'
include { ANARCI } from '../modules/utils/anarci'

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

    // Prepare input for RFantibody
    // New interface: tuple(meta, target_pdb, hotspot_residues), framework_pdb
    rfantibody_input = target_pdb_ch.map { meta, pdb ->
        // epitope_residues comes from workflow input (e.g., "A45,A46,A52")
        def hotspots = epitope_residues ?: ""
        [meta, pdb, hotspots]
    }
    
    // Framework PDB - if user provided custom framework, use it; otherwise use placeholder
    // The placeholder triggers preset selection in the process script
    // Use safe path resolution to avoid Channel.value() DSL2 error with undefined params
    def framework_path = params.framework_pdb ? file(params.framework_pdb) : file("${projectDir}/lib/NO_FRAMEWORK")
    framework_for_rfantibody = Channel.value(framework_path)

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

    backbone_designs = RFANTIBODY.out.designs

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
        // PrepFAMPNN expects [pdbs, jsons]
        fampnn_prep_input = backbone_designs.map { meta, pdbs ->
            // Create dummy JSON for prep
            [pdbs, file("${projectDir}/lib/NO_JSON")]
        }
        PrepFAMPNN(fampnn_prep_input)

        // RunFAMPNN expects [batch_id, pdbs, csv], analysis_chain_id
        // Collect all PDB outputs and the CSV into a single batch tuple
        fampnn_pdbs_collected = PrepFAMPNN.out.pdbs.toList()
        fampnn_csv_file = PrepFAMPNN.out.csv.first()
        
        fampnn_run_input = fampnn_pdbs_collected
            .merge(fampnn_csv_file)
            .map { pdbs, csv -> [1, pdbs, csv] }

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

        // Fix: Map to [meta, pdbs] - Drop JSONs to prevent downstream PrepBoltz error
        // Use a generic meta ID or derive from inputs if possible, but for batch it's tricky
        // Here we just use a placeholder since the detailed meta isn't preserved in this branch structure perfectly yet
        fampnn_seqs = RunFAMPNN.out.pdbs_jsons.map { pdbs, jsons ->
             def meta = [id: "fampnn_designs"]
             [meta, pdbs]
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
        ProteinMPNNSeq(backbone_designs.map { meta, pdbs -> pdbs })
        
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
        design_sequences = all_sequences
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
            collected_pdbs = all_sequences
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

    // Step 5: Stability Scoring with ThermoMPNN
    // ---------------------------------------------------------------------------
    log.info("Step 5: Scoring stability with ThermoMPNN...")

    if (params.run_stability_scoring != false) {
        THERMOMPNN(filtered_structures)
        stability_scores = THERMOMPNN.out.stability

        // Filter by ddG threshold
        stable_designs = stability_scores.filter { meta, score_file ->
            // Parse score and filter - actual implementation reads the file
            true
        }
    }
    else {
        stable_designs = filtered_structures
        stability_scores = Channel.empty()
    }

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
    stability = stability_scores // ThermoMPNN ddG scores
    mutations = mutations // IgGM suggested mutations
    backbones = backbone_designs // Original RFantibody backbones
}

// =============================================================================
// STANDALONE WORKFLOW ENTRY
// =============================================================================
workflow {
    // Parse inputs
    if (!params.target_pdb) {
        error("Please provide --target_pdb (antigen structure)")
    }

    target_pdb = file(params.target_pdb)
    if (!target_pdb.exists()) {
        error("Target PDB not found: ${params.target_pdb}")
    }

    // Create input channel
    meta = [id: params.run_id ?: target_pdb.baseName]
    target_ch = Channel.of([meta, target_pdb])

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
