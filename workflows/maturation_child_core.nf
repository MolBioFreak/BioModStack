#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { IdentifyAnchorResidues ; RunPartialFlow ; PrepMaturationRedesign ; RunMaturationFAMPNN ; ScoreMaturationImprovement ; ScorePartialFlowImprovement ; FilterByMaturation } from '../modules/ppiflow.nf'
include { ANARCII } from '../modules/utils/anarci'

workflow MATURATION_CHILD_CORE {
    take:
    pdb_list

    main:
    def normalizeLoopSpec = { raw ->
        if (raw == null) {
            return null
        }
        def values = raw instanceof Collection ? raw : raw.toString().replace('[', '').replace(']', '').split(',')
        def normalized = values.collect { it.toString().trim().toUpperCase() }.findAll { it }
        normalized ? normalized.join(',') : null
    }
    def normalizeRegionMode = { raw ->
        def value = (raw ?: 'selected_cdrs').toString().trim().toLowerCase()
        if (value in ['all_cdrs']) return 'all_cdrs'
        if (value in ['framework', 'framework_only']) return 'framework_only'
        if (value in ['all_antibody', 'whole_antibody', 'full_antibody']) return 'all_antibody'
        return 'selected_cdrs'
    }
    def selectedLoopsSpec = normalizeLoopSpec(params.ppiflow_selected_loops ?: params.maturation_selected_loops ?: params.selected_cdr_loops)
    def ppiflowRegionMode = normalizeRegionMode(params.ppiflow_region_mode ?: params.ppiflow_maturation_region_mode ?: params.ppiflow_backbone_region_mode)
    params.ppiflow_region_mode = ppiflowRegionMode
    if (selectedLoopsSpec) {
        params.ppiflow_selected_loops = selectedLoopsSpec
    }
    def selectedLoopSet = (ppiflowRegionMode == 'selected_cdrs' && selectedLoopsSpec)
        ? selectedLoopsSpec.split(',')*.trim().findAll { it }.collect { it.toUpperCase() } as Set
        : [] as Set
    def ppiflowMode = (params.ppiflow_mode ?: params.maturation_stage_name ?: 'maturation').toString().toLowerCase()
    def runRedesign = (params.maturation_redesign_enabled != false) && ppiflowMode != 'backbone_refine'

    def anchor_inputs = Channel
        .from(pdb_list)
        .map { pdb ->
            def meta = [id: pdb.baseName]
            tuple(meta, pdb)
        }

    IdentifyAnchorResidues(anchor_inputs)
    RunPartialFlow(IdentifyAnchorResidues.out.anchor_inputs)

    def anchor_lookup = IdentifyAnchorResidues.out.anchor_inputs.map { meta, original_pdb, anchors_json, ppiflow_positions, cdr_positions ->
        tuple(meta, original_pdb, anchors_json, ppiflow_positions, cdr_positions)
    }
    def anchor_original_lookup = anchor_lookup.map { meta, original_pdb, _anchors_json, _ppiflow_positions, _cdr_positions -> tuple(meta.id, original_pdb) }
    def anchor_redesign_lookup = anchor_lookup.map { meta, _original_pdb, anchors_json, _ppiflow_positions, cdr_positions -> tuple(meta.id, anchors_json, cdr_positions) }

    // Fan out multi-sample PPIFlow outputs so downstream tasks operate on one
    // matured backbone at a time instead of a space-joined file list.
    def partial_backbones = RunPartialFlow.out.backbones.flatMap { meta, backbone_pdbs ->
        def pdbList = backbone_pdbs instanceof Collection ? backbone_pdbs : [backbone_pdbs]
        pdbList.collect { backbone_pdb ->
            def sampleMeta = new LinkedHashMap(meta)
            sampleMeta.parent_id = meta.id
            sampleMeta.id = backbone_pdb.baseName
            tuple(sampleMeta, backbone_pdb)
        }
    }

    def partial_score_inputs = partial_backbones
        .map { meta, backbone_pdb -> tuple(meta.parent_id ?: meta.id, meta, backbone_pdb) }
        .join(anchor_original_lookup)
        .map { _parentId, meta, backbone_pdb, original_pdb ->
            tuple(meta, original_pdb, backbone_pdb)
        }

    ScorePartialFlowImprovement(partial_score_inputs)

    def cdr_loop_lookup = Channel.empty()
    if (runRedesign) {
        ANARCII(partial_backbones)
        cdr_loop_lookup = ANARCII.out.cdr_positions.map { meta, cdr_positions_by_loop ->
            def filtered = cdr_positions_by_loop
            if (selectedLoopSet) {
                def parsed = new groovy.json.JsonSlurper().parse(new File(cdr_positions_by_loop.toString()))
                if (parsed instanceof Map) {
                    parsed = parsed.findAll { loop_id, _positions ->
                        selectedLoopSet.contains(loop_id.toString().toUpperCase())
                    }
                }
                def tmpFile = File.createTempFile("${meta.id}_selected_cdr_positions_", ".json")
                tmpFile.deleteOnExit()
                tmpFile.text = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(parsed))
                filtered = file(tmpFile.toString())
            }
            tuple(meta.id, filtered)
        }
    }

    def partial_scored = ScorePartialFlowImprovement.out.scores
        .join(partial_backbones)
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
                sorted.take(redesign_top_n)
            }
    }

    def redesign_enabled = params.maturation_redesign_enabled != false

    def final_matured
    def final_scores

    if (redesign_enabled && runRedesign) {
        def redesign_inputs = partial_selected
            .map { meta, backbone_pdb, _score_json, _score -> tuple(meta.parent_id ?: meta.id, meta, backbone_pdb) }
            .join(anchor_redesign_lookup)
            .map { _parentId, meta, backbone_pdb, anchors_json, cdr_positions ->
                tuple(meta.id, meta, backbone_pdb, anchors_json, cdr_positions)
            }
            .join(cdr_loop_lookup)
            .map { _sampleId, meta, backbone_pdb, anchors_json, cdr_positions, cdr_positions_by_loop ->
                tuple(meta, backbone_pdb, anchors_json, cdr_positions, cdr_positions_by_loop)
            }

        PrepMaturationRedesign(redesign_inputs)
        RunMaturationFAMPNN(PrepMaturationRedesign.out.prep)

        final_matured = RunMaturationFAMPNN.out.redesigned.map { meta, matured_pdb, _matured_json ->
            tuple(meta, matured_pdb)
        }

        def score_inputs = RunMaturationFAMPNN.out.redesigned
            .map { meta, matured_pdb, _matured_json -> tuple(meta.parent_id ?: meta.id, meta, matured_pdb) }
            .join(anchor_original_lookup)
            .map { _parentId, meta, matured_pdb, original_pdb ->
                tuple(meta, original_pdb, matured_pdb)
            }

        ScoreMaturationImprovement(score_inputs)
        final_scores = ScoreMaturationImprovement.out.scores
    }
    else {
        final_matured = partial_selected.map { meta, backbone_pdb, _score_json, _score -> tuple(meta, backbone_pdb) }
        final_scores = ScorePartialFlowImprovement.out.scores
    }

    def filter_inputs = final_scores.join(final_matured)
        .map { meta, score_json, matured_pdb ->
            tuple(meta, matured_pdb, score_json)
        }

    FilterByMaturation(filter_inputs)

    emit:
    matured_pdbs = FilterByMaturation.out.pdbs
    scores = FilterByMaturation.out.filter_reports
}
