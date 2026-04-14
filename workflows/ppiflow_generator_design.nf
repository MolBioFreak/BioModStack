#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { IdentifyAnchorResidues ; RunPartialFlow ; ScorePartialFlowImprovement ; FilterByMaturation } from '../modules/ppiflow.nf'

[
    framework_type: 'nanobody',
    antibody_chains: 'H',
    ppiflow_seed_input_dir: '',
    selected_input_dir: '',
    ppiflow_mode: 'backbone_refine',
    stage_family: 'ppiflow',
    stage_mode: 'generator_backbone_refine',
    ppiflow_stage_mode: 'generator_backbone_refine',
    ppiflow_require_anchors: true,
    ppiflow_rotamer_enrichment_enabled: true,
    ppiflow_rotamer_shell_distance: params.get('ppiflow_rotamer_shell_cutoff') ?: 20.0,
    ppiflow_region_mode: params.get('ppiflow_backbone_region_mode') ?: 'selected_cdrs',
    ppiflow_selected_loops: params.get('ppiflow_backbone_loop_scope') ?: '',
    cdr_positions_by_loop: [:],
    manual_cdr_definitions: [],
    interactive_swa: false,
    interactive_gating: false,
    interactive_gate_stage: 'post_ppiflow_generator',
    interactive_gate_continue: false,
].each { key, value ->
    if (!params.containsKey(key)) {
        params[key] = value
    }
}

def parsePpiFlowBackboneManifest(manifestFile) {
    if (manifestFile == null) {
        return []
    }
    def parsed = new groovy.json.JsonSlurper().parse(new File(manifestFile.toString()))
    if (!(parsed instanceof List)) {
        return []
    }
    return parsed
        .findAll { entry -> entry instanceof Map && entry.path }
        .collect { entry -> [file(entry.path.toString()), entry] }
}

def resolveAnchorCount(anchorsJson) {
    if (anchorsJson == null) {
        return 0
    }
    try {
        def parsed = new groovy.json.JsonSlurper().parse(new File(anchorsJson.toString()))
        def count = parsed instanceof Map ? parsed.anchor_count : 0
        return count instanceof Number ? count.intValue() : (count?.toString()?.isInteger() ? count.toString().toInteger() : 0)
    }
    catch (Throwable ignored) {
        return 0
    }
}

def resolveSeedStructureFiles() {
    def candidates = []
    def seedInputDir = params.get('ppiflow_seed_input_dir')
    def selectedInputDir = params.get('selected_input_dir')
    def seedComplexPath = params.get('ppiflow_seed_complex_path')
    def addPath = { raw ->
        def text = raw?.toString()?.trim()
        if (!text) {
            return
        }
        def resolved = file(text)
        if (!resolved.exists()) {
            return
        }
        if (resolved.isDirectory()) {
            ['*.pdb', '*.cif', '*.mmcif'].each { pattern ->
                candidates.addAll(resolved.listFiles().findAll { child -> child.name ==~ pattern.replace('*', '.*') })
            }
        }
        else {
            candidates << resolved
        }
    }

    addPath(seedInputDir)
    addPath(selectedInputDir)
    addPath(seedComplexPath)

    def normalized = candidates
        .collect { it.toString() }
        .unique()
        .sort()
        .collect { file(it) }
    return normalized
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

process CollectPPIFlowGeneratorRaw {
    label 'process_low'

    publishDir "${params.out_dir}/collected/ppiflow_generator_raw", mode: 'copy', pattern: "raw_output/*", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path raw_pdbs
    path score_jsons
    path anchor_jsons
    path interface_jsons
    path rotamer_jsons
    path enriched_pdbs
    path ppiflow_positions_files
    path cdr_positions_files
    path cdr_positions_by_loop_jsons

    output:
    path "raw_output/*.pdb", emit: pdbs, optional: true
    path "raw_output/*.json", emit: jsons, optional: true

    script:
    """
    mkdir -p raw_output
    for file in ${raw_pdbs} ${score_jsons} ${anchor_jsons} ${interface_jsons} ${rotamer_jsons} ${enriched_pdbs} ${ppiflow_positions_files} ${cdr_positions_files} ${cdr_positions_by_loop_jsons}; do
        if [ -f "\$file" ]; then
            cp -f "\$file" raw_output/
        fi
    done
    """
}

process CollectPPIFlowGeneratorFiltered {
    label 'process_low'

    publishDir "${params.out_dir}/collected/ppiflow_generator_filtered", mode: 'copy', pattern: "filtered_output/*", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path filtered_pdbs
    path filter_reports
    path score_jsons
    path anchor_jsons
    path interface_jsons
    path rotamer_jsons
    path enriched_pdbs
    path ppiflow_positions_files
    path cdr_positions_files
    path cdr_positions_by_loop_jsons

    output:
    path "filtered_output/*.pdb", emit: pdbs, optional: true
    path "filtered_output/*.json", emit: jsons, optional: true

    script:
    """
    mkdir -p filtered_output
    for file in ${filtered_pdbs} ${filter_reports} ${score_jsons} ${anchor_jsons} ${interface_jsons} ${rotamer_jsons} ${enriched_pdbs} ${ppiflow_positions_files} ${cdr_positions_files} ${cdr_positions_by_loop_jsons}; do
        if [ -f "\$file" ]; then
            cp -f "\$file" filtered_output/
        fi
    done
    """
}

workflow PPIFLOW_GENERATOR_DESIGN {
    main:
        def seedStructures = resolveSeedStructureFiles()
        if (seedStructures.isEmpty()) {
            error("PPIFlow generator requires --ppiflow_seed_complex_path or --ppiflow_seed_input_dir")
        }

        if (!params.containsKey('maturation_redesign_enabled')) {
            params.maturation_redesign_enabled = false
        }

        def strictAnchorRequirement = params.ppiflow_require_anchors != null ? params.ppiflow_require_anchors : false
        def interactiveGateEnabled = params.get('interactive_gating') == true || params.get('interactive_swa') == true
        def shouldPauseAfterGenerator = interactiveGateEnabled &&
            (params.get('interactive_gate_stage') ?: 'post_ppiflow_generator') == 'post_ppiflow_generator' &&
            params.get('interactive_gate_continue') != true

        def seed_inputs = Channel
            .from(seedStructures)
            .map { structure_path ->
                def stagedPath = structure_path instanceof Path ? file(structure_path.toString()) : file(structure_path.toString())
                def meta = [id: stagedPath.baseName]
                tuple(meta, stagedPath)
            }

        IdentifyAnchorResidues(seed_inputs)

        def usable_anchor_inputs = IdentifyAnchorResidues.out.anchor_inputs.filter { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json ->
            def anchorCount = resolveAnchorCount(anchors_json)
            if (strictAnchorRequirement && anchorCount <= 0) {
                log.warn("PPIFlow generator skipping ${meta.id} because strict anchor selection produced zero anchors")
                return false
            }
            return true
        }

        RunPartialFlow(usable_anchor_inputs)

        def anchor_lookup = usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json ->
            tuple(meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json)
        }

        def partial_backbones = RunPartialFlow.out.backbones.flatMap { meta, _backbone_dir, manifest_json ->
            def pdbList = parsePpiFlowBackboneManifest(manifest_json)
            pdbList.collect { backbone_pdb, manifestEntry ->
                def sampleMeta = new LinkedHashMap(meta)
                sampleMeta.parent_id = meta.id
                sampleMeta.id = backbone_pdb.baseName
                sampleMeta.sample_index = manifestEntry.sample_index
                tuple(sampleMeta, backbone_pdb)
            }
        }

        def partial_score_inputs = partial_backbones
            .map { meta, backbone_pdb -> tuple(meta.parent_id ?: meta.id, meta, backbone_pdb) }
            .combine(anchor_lookup.map { meta, original_pdb, _enriched_pdb, _anchors_json, ppiflow_positions, _cdr_positions, cdr_positions_by_loop_json ->
                tuple(meta.id, original_pdb, ppiflow_positions, cdr_positions_by_loop_json)
            }, by: 0)
            .map { _parentId, meta, backbone_pdb, original_pdb, ppiflow_positions, cdr_positions_by_loop_json ->
                tuple(meta, original_pdb, backbone_pdb, ppiflow_positions, cdr_positions_by_loop_json)
            }

        ScorePartialFlowImprovement(partial_score_inputs)

        def partial_scored = ScorePartialFlowImprovement.out.scores
            .join(partial_backbones)
            .map { meta, score_json, backbone_pdb ->
                tuple(meta.parent_id ?: meta.id, meta, backbone_pdb, score_json)
            }

        def filter_inputs = partial_scored
            .groupTuple(by: 0)
            .map { _parentId, metaList, maturedPdbList, scoreJsonList ->
                def representativeMeta = (metaList instanceof List && !metaList.isEmpty()) ? metaList[0] : metaList
                tuple(representativeMeta, maturedPdbList, scoreJsonList)
            }

        FilterByMaturation(filter_inputs)

        CollectPPIFlowGeneratorRaw(
            partial_backbones.map { meta, backbone_pdb -> backbone_pdb }.collect(),
            ScorePartialFlowImprovement.out.scores.map { meta, score_json -> score_json }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> anchors_json }.collect(),
            IdentifyAnchorResidues.out.interface_scores.map { meta, interface_score_json -> interface_score_json }.collect(),
            IdentifyAnchorResidues.out.rotamer_enrichment.map { meta, rotamer_json -> rotamer_json }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> enriched_pdb }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> ppiflow_positions }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> cdr_positions }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> cdr_positions_by_loop_json }.collect(),
        )

        CollectPPIFlowGeneratorFiltered(
            FilterByMaturation.out.pdbs.collect(),
            FilterByMaturation.out.filter_reports.collect(),
            ScorePartialFlowImprovement.out.scores.map { meta, score_json -> score_json }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> anchors_json }.collect(),
            IdentifyAnchorResidues.out.interface_scores.map { meta, interface_score_json -> interface_score_json }.collect(),
            IdentifyAnchorResidues.out.rotamer_enrichment.map { meta, rotamer_json -> rotamer_json }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> enriched_pdb }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> ppiflow_positions }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> cdr_positions }.collect(),
            usable_anchor_inputs.map { meta, original_pdb, enriched_pdb, anchors_json, ppiflow_positions, cdr_positions, cdr_positions_by_loop_json -> cdr_positions_by_loop_json }.collect(),
        )

        if (shouldPauseAfterGenerator) {
            ppiflow_gate_trigger = partial_backbones.collect().map { items -> items instanceof Collection ? items.size() : 0 }
            OpenInteractiveGate(
                params.job_id ?: "unknown",
                "post_ppiflow_generator",
                ppiflow_gate_trigger,
                params.out_dir ? "${params.out_dir}/collected/ppiflow_generator_filtered" : "",
                params.out_dir ? "${params.out_dir}/collected/ppiflow_generator_raw" : "",
                params.out_dir ? "${params.out_dir}/collected/ppiflow_generator_filtered" : "",
                params.get('framework_type') ?: "nanobody",
                params.get('antibody_chains') ?: "H",
                params.get('structure_validator') ?: "boltz2",
            )
        }

    emit:
        raw_pdbs = CollectPPIFlowGeneratorRaw.out.pdbs
        filtered_pdbs = CollectPPIFlowGeneratorFiltered.out.pdbs
}
