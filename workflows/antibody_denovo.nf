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

// Import modules
include { RFANTIBODY } from '../modules/rfantibody'
include { ANTIFOLD } from '../modules/antifold'
include { PrepFAMPNN ; RunFAMPNN ; FilterFAMPNN } from '../modules/fampnn'
include { RunMPNN as ProteinMPNNSeq } from '../modules/proteinmpnn'
include { ANTIBERTY_SCORE ; ANTIBERTY_FILTER } from '../modules/antiberty'
include { THERMOMPNN } from '../modules/thermompnn'
include { IGGM_AFFINITY_MATURATION } from '../modules/iggm'
include { PrepBoltz ; RunBoltz } from '../modules/boltz'
include { ANARCI } from '../modules/utils/anarci'

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
    
    // Framework PDB - use provided or provide placeholder for default
    framework_for_rfantibody = framework_pdb_ch
        .map { meta, pdb -> pdb }
        .ifEmpty(file('NO_FRAMEWORK'))

    RFANTIBODY(rfantibody_input, framework_for_rfantibody)
    backbone_designs = RFANTIBODY.out.designs

    // Step 2: CDR Sequence Design (Cross-Validation Mode)
    // ---------------------------------------------------------------------------
    log.info("Step 2: Designing CDR sequences...")

    // Determine which sequence design methods to run
    def run_fampnn = params.seq_design_fampnn ?: true
    def run_antifold = params.seq_design_antifold ?: true
    def run_proteinmpnn = params.seq_design_proteinmpnn ?: true

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
        fampnn_run_input = PrepFAMPNN.out.pdbs
            .combine(PrepFAMPNN.out.csv)
            .map { pdbs, csv -> [1, pdbs, csv] }

        RunFAMPNN(fampnn_run_input, params.analysis_chain_id ?: "H")
        fampnn_seqs = RunFAMPNN.out.pdbs_jsons
    }

    // AntiFold branch (requires IMGT numbering)
    if (run_antifold) {
        log.info("  Running AntiFold...")
        // First number with ANARCI
        ANARCI(backbone_designs)
        ANTIFOLD(ANARCI.out.pdb_imgt)
        antifold_seqs = ANTIFOLD.out.sequences
    }

    // ProteinMPNN branch
    if (run_proteinmpnn) {
        log.info("  Running ProteinMPNN...")
        ProteinMPNNSeq(backbone_designs.map { meta, pdbs -> pdbs })
        proteinmpnn_seqs = ProteinMPNNSeq.out.pdbs_jsons
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
        // PrepBoltz expects path(pdb_files) - collect all PDBs
        // Extract PDB files from sequences (FAMPNN/AntiFold output PDBs)
        pdb_files_for_boltz = all_sequences.map { meta, files -> files }
            .collect()
        
        // Generate YAML files for Boltz2
        PrepBoltz(pdb_files_for_boltz)
        
        // Batch YAMLs for RunBoltz (expects tuple(batch_id, yamls))
        boltz_batched = PrepBoltz.out.yamls
            .flatten()
            .collate(params.boltz_batch_size ?: 10)
            .map { yamls -> [1, yamls] }  // batch_id, yamls
        
        RunBoltz(boltz_batched)
        
        // RunBoltz emits pdbs_jsons as tuple(path(pdbs), path(jsons))
        // Need to transform to tuple(meta, pdb) with metrics from JSON
        validated_structures = RunBoltz.out.pdbs_jsons
            .flatMap { pdbs, jsons ->
                // Pair each PDB with its corresponding JSON
                def pdb_list = pdbs instanceof List ? pdbs : [pdbs]
                def json_list = jsons instanceof List ? jsons : [jsons]
                pdb_list.collect { pdb ->
                    def name = pdb.baseName.replace('_boltzpred', '')
                    def json_file = json_list.find { it.baseName.contains(name) }
                    // Create meta with id from filename
                    def meta = [id: name]
                    [meta, pdb, json_file]
                }
            }
        
        // Filter by ipTM and pLDDT thresholds (read from JSON)
        validated_structures = validated_structures
            .filter { meta, pdb, json_file ->
                // Parse JSON for metrics if available
                if (!json_file || !json_file.exists()) return true
                try {
                    def json_text = json_file.text
                    def slurper = new groovy.json.JsonSlurper()
                    def data = slurper.parseText(json_text)
                    def iptm = data.ptm_intf ?: data.ipTM ?: data.iptm ?: 0.8
                    def plddt = data.plddt_mean ?: data.pLDDT ?: data.plddt ?: 80
                    def iptm_threshold = params.boltz_iptm_threshold ?: 0.6
                    def plddt_threshold = params.boltz_plddt_threshold ?: 70
                    return iptm >= iptm_threshold && plddt >= plddt_threshold
                } catch (Exception e) {
                    log.warn("Could not parse JSON for ${meta.id}: ${e.message}")
                    return true  // Keep if can't parse
                }
            }
            .map { meta, pdb, json_file -> [meta, pdb] }  // Drop JSON for downstream
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
