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
// 0 = use all

include { IdentifyAnchorResidues ; RunPartialFlow ; PrepMaturationRedesign ; RunMaturationFAMPNN ; ScoreMaturationImprovement ; ScorePartialFlowImprovement ; FilterByMaturation } from '../modules/ppiflow.nf'
include { ANARCII } from '../modules/utils/anarci'

workflow MATURATION_CHILD {
    take:
    pdb_list // List of PDB file paths

    main:
    // Create per-PDB input channel
    def anchor_inputs = Channel
        .from(pdb_list)
        .map { pdb ->
            def meta = [id: pdb.baseName]
            tuple(meta, pdb)
        }

    // Step 1: Identify anchor residues for partial flow
    IdentifyAnchorResidues(anchor_inputs)

    // Step 2: Run partial flow backbone maturation
    RunPartialFlow(IdentifyAnchorResidues.out.anchor_inputs)

    // Build lookup channels for joining
    def anchor_lookup = IdentifyAnchorResidues.out.anchor_inputs.map { meta, original_pdb, anchors_json, cdr_positions ->
        tuple(meta, original_pdb, anchors_json, cdr_positions)
    }
    def anchor_original_lookup = anchor_lookup.map { meta, original_pdb, _anchors_json, _cdr_positions -> tuple(meta, original_pdb) }
    def anchor_redesign_lookup = anchor_lookup.map { meta, _original_pdb, anchors_json, cdr_positions -> tuple(meta, anchors_json, cdr_positions) }

    // Step 3: Score partial flow improvement
    def partial_score_inputs = RunPartialFlow.out.backbones
        .join(anchor_original_lookup)
        .map { meta, backbone_pdb, original_pdb ->
            tuple(meta, original_pdb, backbone_pdb)
        }

    ScorePartialFlowImprovement(partial_score_inputs)

    // Step 4: ANARCI CDR loop positions for selective redesign
    ANARCII(RunPartialFlow.out.backbones)
    def cdr_loop_lookup = ANARCII.out.cdr_positions.map { meta, cdr_positions_by_loop -> tuple(meta, cdr_positions_by_loop) }

    // Parse scores and optionally select top N
    def partial_scored = ScorePartialFlowImprovement.out.scores
        .join(RunPartialFlow.out.backbones)
        .map { meta, score_json, backbone_pdb ->
            def score = 0.0
            try {
                score = new groovy.json.JsonSlurper().parse(score_json).delta_interface_score ?: 0.0
            }
            catch (Exception e) {
                score = 0.0
            }
            tuple(meta, backbone_pdb, score_json, score)
        }

    def redesign_top_n = params.maturation_redesign_top_n ?: 0
    def partial_selected = partial_scored
    if (redesign_top_n > 0) {
        partial_selected = partial_scored
            .collect()
            .flatMap { items ->
                def sorted = items.sort { a, b -> a[3] <=> b[3] }
                return sorted.take(redesign_top_n)
            }
    }

    def redesign_enabled = params.maturation_redesign_enabled != false

    def final_matured
    def final_scores

    if (redesign_enabled) {
        // Step 5: Prepare and run FAMPNN redesign on matured backbones
        def redesign_inputs = partial_selected
            .map { meta, backbone_pdb, score_json, score -> tuple(meta, backbone_pdb) }
            .join(
                anchor_redesign_lookup.join(cdr_loop_lookup).map { meta, anchors_json, cdr_positions, cdr_positions_by_loop ->
                    tuple(meta, anchors_json, cdr_positions, cdr_positions_by_loop)
                }
            )
            .map { meta, backbone_pdb, anchors_json, cdr_positions, cdr_positions_by_loop ->
                tuple(meta, backbone_pdb, anchors_json, cdr_positions, cdr_positions_by_loop)
            }

        PrepMaturationRedesign(redesign_inputs)
        RunMaturationFAMPNN(PrepMaturationRedesign.out.prep)

        final_matured = RunMaturationFAMPNN.out.redesigned.map { meta, matured_pdb, matured_json ->
            tuple(meta, matured_pdb)
        }

        // Score final improvement
        def score_inputs = RunMaturationFAMPNN.out.redesigned
            .join(anchor_original_lookup)
            .map { meta, matured_pdb, matured_json, original_pdb ->
                tuple(meta, original_pdb, matured_pdb)
            }

        ScoreMaturationImprovement(score_inputs)
        final_scores = ScoreMaturationImprovement.out.scores
    }
    else {
        final_matured = partial_selected.map { meta, backbone_pdb, score_json, score -> tuple(meta, backbone_pdb) }
        final_scores = ScorePartialFlowImprovement.out.scores
    }

    // Step 6: Filter by maturation improvement
    def filter_inputs = final_scores
        .join(final_matured)
        .map { meta, score_json, matured_pdb ->
            tuple(meta, matured_pdb, score_json)
        }

    FilterByMaturation(filter_inputs)

    emit:
    matured_pdbs = FilterByMaturation.out.pdbs
    scores = FilterByMaturation.out.filter_reports
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
