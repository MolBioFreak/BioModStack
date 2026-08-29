#!/usr/bin/env nextflow
nextflow.enable.dsl = 2


include { CONFORMATIONAL_MAPPING_CONFORNETS } from '../modules/conformational_mapping_confornets.nf'
include { CONFORMATIONAL_MAPPING_PROTENIX } from '../modules/conformational_mapping_protenix.nf'
include { CONFORMATIONAL_MAPPING_IMPORT } from '../modules/conformational_mapping_import.nf'
include { PrepareConformationalMappingFrustraMPNNV2; CanonicalConformationalAnalysisPlaneV2 } from '../modules/conformational_mapping_frustrampnn.nf'
include { SchedulerFrustraMPNNParentFanout } from '../modules/frustrampnn_parent_fanout.nf'

params.cm_request_path = null

workflow {
    if (!params.cm_request_path) {
        error '--cm_request_path is required'
    }
    if (!params.job_id) {
        error '--job_id is required for canonical FrustraMPNN parent binding'
    }
    def requestPath = file(params.cm_request_path, checkIfExists: true)
    def request = new groovy.json.JsonSlurper().parse(requestPath)
    if (request.schema_name != 'cm_request' || request.schema_version != 1) {
        error 'Expected cm_request schema version 1'
    }
    if (request.frustrampnn_requiredness != 'required') {
        error 'Conformational Mapping requires canonical FrustraMPNN for every candidate'
    }
    if (!(request.frustrampnn_settings instanceof Map)) {
        error 'cm_request requires canonical frustrampnn_settings'
    }
    if (!(request.targets instanceof List) || request.targets.isEmpty()) {
        error 'cm_request targets must be nonempty'
    }
    if (!(request.ordered_seeds instanceof List) || request.ordered_seeds.isEmpty()) {
        error 'cm_request ordered_seeds must be nonempty'
    }

    request.targets.each { target ->
        if (!(target.target_id instanceof String) || target.target_id.isEmpty()) {
            error 'Every target requires target_id'
        }
    }
    def requestRootPath = file(requestPath.parent.toString(), checkIfExists: true)
    def requestTuples = Channel.value(tuple(request.request_id, requestRootPath))
    canonicalInputs = Channel.empty()

    if (request.backend == 'protenix_v2_ensemble') {
        CONFORMATIONAL_MAPPING_PROTENIX(requestTuples)
        canonicalInputs = CONFORMATIONAL_MAPPING_PROTENIX.out.canonical.map {
            request_id, canonical_dir ->
            tuple(request_id, 'canonical_protenix', requestRootPath, canonical_dir)
        }
    }
    else if (request.backend == 'confornets') {
        def confornetsTuples = request.targets.collect { target ->
            tuple(
                [request_id: request.request_id, backend: request.backend, target_id: target.target_id, target_order: target.target_order],
                requestRootPath,
            )
        }
        CONFORMATIONAL_MAPPING_CONFORNETS(Channel.fromList(confornetsTuples))
        canonicalInputs = CONFORMATIONAL_MAPPING_CONFORNETS.out.canonical_dir.map {
            canonical_dir ->
            tuple(request.request_id, 'canonical_confornets', requestRootPath, canonical_dir)
        }
    }
    else if (request.backend == 'external_import') {
        CONFORMATIONAL_MAPPING_IMPORT(requestTuples)
        canonicalInputs = CONFORMATIONAL_MAPPING_IMPORT.out.canonical.map {
            request_id, canonical_dir ->
            tuple(request_id, 'canonical_import', requestRootPath, canonical_dir)
        }
    }
    else {
        error "Unknown conformational-mapping backend: ${request.backend}"
    }

    PrepareConformationalMappingFrustraMPNNV2(canonicalInputs)

    schedulerCandidates = PrepareConformationalMappingFrustraMPNNV2.out.prepared.flatMap {
        request_id, backend_dir, prepared_dir, preparation_manifest ->
        def preparation = new groovy.json.JsonSlurper().parse(preparation_manifest)
        if (preparation.requiredness != 'required') {
            error 'CM FrustraMPNN preparation must be required'
        }
        if (preparation.expected_cardinality != preparation.candidates.size()) {
            error 'CM FrustraMPNN preparation cardinality is incomplete'
        }
        preparation.candidates.collect { candidate ->
            def candidateDir = prepared_dir.resolve(candidate.candidate_id.toString())
            def componentRequest = new groovy.json.JsonSlurper().parse(
                candidateDir.resolve('workflow_component_request_v3.json')
            )
            tuple([
                candidate_id: componentRequest.candidate_id,
                parent_job_id: params.job_id.toString(),
                parent_workflow_id: 'conformational_mapping',
                producer_stage: componentRequest.source_artifact.producer_stage,
                producer_candidate_key: "conformational_mapping/${componentRequest.candidate_id}.pdb",
                requiredness: 'required',
            ], candidateDir.resolve('canonical_source.pdb'))
        }
    }
    def schedulerSettings = new LinkedHashMap(request.frustrampnn_settings as Map)
    def schedulerSettingsOrigin = schedulerSettings.remove('settings_value_origin').toString()
    SchedulerFrustraMPNNParentFanout(
        schedulerCandidates,
        Channel.value(params.job_id.toString()),
        Channel.value('conformational_mapping'),
        Channel.value(groovy.json.JsonOutput.toJson(schedulerSettings)),
        Channel.value(schedulerSettingsOrigin),
    )

    preparationManifest = PrepareConformationalMappingFrustraMPNNV2.out.prepared.map {
        request_id, backend_dir, prepared_dir, preparation_manifest -> preparation_manifest
    }
    requiredResultBundles = SchedulerFrustraMPNNParentFanout.out.result_bundles.collect()

    CanonicalConformationalAnalysisPlaneV2(
        canonicalInputs,
        preparationManifest,
        requiredResultBundles,
        SchedulerFrustraMPNNParentFanout.out.receipt,
    )
}
