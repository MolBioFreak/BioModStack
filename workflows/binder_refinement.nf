nextflow.enable.dsl = 2

include { IdentifyAnchorResidues; RunPartialFlow; RunMaturationFAMPNN } from '../modules/ppiflow.nf'

process PrepareBinderRefinementRegions {
    label 'CPU'
    input:
    tuple val(meta), path(pdb)
    output:
    tuple val(meta), path(pdb), path("${meta.id}_seed.pdb"), path("${meta.id}_anchors.json"), path("${meta.id}_ppiflow_positions.txt"), path("${meta.id}_cdr_positions.txt"), path("${meta.id}_cdr_positions_by_loop.json"), emit: regions
    script:
    // Redesign consumes generic sequence masks, not a partial-flow region.
    // Keep the tuple transport without resolving unused CDRs or requiring ANARCII.
    if (params.get('maturation_flow_enabled') != true) {
        return """
        cp '${pdb}' '${meta.id}_seed.pdb'
        printf '{"anchors":[],"analysis_status":"not_run","anchor_selection_method":"not_run"}' > '${meta.id}_anchors.json'
        touch '${meta.id}_ppiflow_positions.txt' '${meta.id}_cdr_positions.txt'
        printf '{}' > '${meta.id}_cdr_positions_by_loop.json'
        """
    }
    def loops = groovy.json.JsonOutput.toJson(params.get('cdr_positions_by_loop') ?: [:])
    def manual = groovy.json.JsonOutput.toJson(params.get('manual_cdr_definitions') ?: [])
    """
    cp '${pdb}' '${meta.id}_seed.pdb'
    cat > loops.json <<'JSON'
${loops}
JSON
    cat > manual.json <<'JSON'
${manual}
JSON
    python3 '${params.code_root}/scripts/prepare_maturation_regions.py' \\
        --pdb '${pdb}' --prefix '${meta.id}' --chains '${params.binder_chains}' \\
        --region '${params.get('ppiflow_region_mode') ?: 'all_antibody'}' \\
        --loops '${params.get('ppiflow_selected_loops') ?: ''}' \\
        --loop-json loops.json --manual-json manual.json
    """
}

process PrepareBinderRedesign {
    label 'pyrosetta_tools'
    input:
    tuple val(meta), path(pdb), path(anchors)
    output:
    tuple val(meta), path('fampnn_input/*.pdb'), path('fampnn.csv'), path('transport'), emit: prep
    script:
    def request = [sequence_design_mode: 'binder_design', design_chain: params.binder_chains,
                   target_chain: params.target_chains, fixed_positions: params.get('fixed_positions'),
                   fampnn_fix_target_sidechains: params.get('fampnn_fix_target_sidechains')]
    def encoded = groovy.json.JsonOutput.toJson(request).bytes.encodeBase64().toString()
    """
    mkdir selected transport
    cp '${pdb}' selected/
    python3 '${params.code_root}/scripts/prep_fampnn_designs.py' \\
        --input_dir selected --out_dir fampnn_input --generic_identity
    python3 '${params.code_root}/scripts/prep_binder_fampnn_constraints.py' \\
        --input-dir selected --prepared-dir fampnn_input --out-csv fampnn.csv \\
        --request-base64 '${encoded}' --anchors '${anchors}'
    """
}

process PublishBinderRefinement {
    label 'CPU'
    publishDir "${params.out_dir}/collected/binder_refinement", mode: 'copy'
    input:
    tuple val(meta), path(pdb)
    output:
    tuple val(meta), path('published/*.pdb'), emit: pdbs
    path('published/*.json'), emit: metadata
    script:
    def encoded = groovy.json.JsonOutput.toJson(meta).bytes.encodeBase64().toString()
    """
    python3 '${params.code_root}/scripts/publish_binder_refinement.py' \\
        --pdb '${pdb}' --meta-base64 '${encoded}' --output-dir published
    """
}

// Selection and source snapshots belong to the API. This workflow only composes
// explicitly selected native operations; the historical child keeps its defaults.
workflow {
    // CLI transports these two typed mask settings as JSON; params-file callers
    // already supply the native Map/List values.
    ['cdr_positions_by_loop', 'manual_cdr_definitions'].each { key ->
        if (params.get(key) instanceof CharSequence) {
            params[key] = new groovy.json.JsonSlurper().parseText(params.get(key).toString())
        }
    }
    def repack = params.get('maturation_repack_enabled') == true
    def anchors = params.get('maturation_anchors_enabled') == true
    def flow = params.get('maturation_flow_enabled') == true
    def redesign = params.get('maturation_redesign_enabled') == true
    params.maturation_repack_enabled = repack
    params.maturation_anchors_enabled = anchors
    params.maturation_flow_enabled = flow
    params.maturation_redesign_enabled = redesign
    // Included modules consume binder_chains/target_chains directly: workflow
    // assignments do not update their include-time parameter scope in DSL2.
    // Native legacy region names are retained, not relabeled as new biology.
    params.ppiflow_region_mode = params.get('ppiflow_region_mode') ?: 'all_antibody'
    def sources = params.get('source_identity_json') ? new groovy.json.JsonSlurper().parse(file(params.source_identity_json)) : []
    def byName = sources.collectEntries { row -> [(row.staged_name.toString()): row] }
    def selected = Channel.fromList(params.pdb_paths.toString().split(',').findAll { it }.collect { file(it) })
        .map { pdb ->
            def source = byName[pdb.name]
            def sourceMeta = source?.source_meta instanceof Map ? source.source_meta : [:]
            tuple([id: pdb.baseName, source_staged_name: pdb.name, source_meta: sourceMeta,
                   source_document_id: sourceMeta.id, source_structure_state: sourceMeta.structure_state ?: sourceMeta.target_state], pdb)
        }
    def prepared = Channel.empty()
    if (repack || anchors) {
        IdentifyAnchorResidues(selected)
        prepared = IdentifyAnchorResidues.out.anchor_inputs
    } else if (flow || redesign) {
        PrepareBinderRefinementRegions(selected)
        prepared = PrepareBinderRefinementRegions.out.regions
    }
    def candidates = selected
    if (repack) {
        candidates = prepared.map { meta, original, enriched, a, p, c, l ->
            tuple(meta + [id: enriched.baseName, parent_id: meta.id, validation_status: 'unvalidated', terminal_producer: 'pyrosetta_repack'], enriched)
        }
    }
    if (flow) {
        // An off anchor stage provides an explicitly empty fixed-anchor mask.
        // No score, anchor-count exclusion or implicit redesign is added here.
        RunPartialFlow(prepared)
        candidates = RunPartialFlow.out.backbones.flatMap { meta, directory, manifest ->
            new groovy.json.JsonSlurper().parse(manifest).collect { row ->
                tuple(meta + [id: file(row.path).baseName, parent_id: meta.id, sample_index: row.sample_index,
                              validation_status: 'unvalidated', terminal_producer: 'ppiflow'], file(row.path))
            }
        }
    }
    if (redesign) {
        def masks = prepared.map { meta, original, enriched, a, p, c, l -> tuple(meta.id, a, c, l) }
        def redesignInputs = candidates.map { meta, pdb -> tuple(meta.parent_id ?: meta.id, meta, pdb) }
            .combine(masks, by: 0)
            .map { key, meta, pdb, a, c, l -> tuple(meta, pdb, a) }
        PrepareBinderRedesign(redesignInputs)
        RunMaturationFAMPNN(PrepareBinderRedesign.out.prep)
        candidates = RunMaturationFAMPNN.out.redesigned.flatMap { meta, pdbs, jsons ->
            (pdbs instanceof List ? pdbs : [pdbs]).collect { pdb ->
                tuple(meta + [id: pdb.baseName, parent_id: meta.id, validation_status: 'unvalidated', terminal_producer: 'fampnn'], pdb)
            }
        }
    }
    PublishBinderRefinement(candidates)
}
