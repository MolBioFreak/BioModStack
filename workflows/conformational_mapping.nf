#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

import groovy.json.JsonSlurper

include { CONFORMATIONAL_MAPPING_CONFORNETS } from '../modules/conformational_mapping_confornets.nf'

params.cm_request_path = null

process CM_PROTENIX_UNIMPLEMENTED {
    tag "cm-protenix:${target_meta.target_id}"

    input:
    tuple val(target_meta), path(request_json), val(staged_assets)

    script:
    """
    echo 'Canonical Protenix conformational mapping is not implemented in Phase 3.' >&2
    exit 64
    """
}

process CM_IMPORT_UNIMPLEMENTED {
    tag "cm-import:${target_meta.target_id}"

    input:
    tuple val(target_meta), path(request_json), val(staged_assets)

    script:
    """
    echo 'Canonical conformational import is not implemented in Phase 3.' >&2
    exit 64
    """
}

workflow {
    if (!params.cm_request_path) {
        error '--cm_request_path is required'
    }
    def requestPath = file(params.cm_request_path, checkIfExists: true)
    def request = new JsonSlurper().parse(requestPath)
    if (request.schema_name != 'cm_request' || request.schema_version != 1) {
        error 'Expected cm_request schema version 1'
    }
    if (!(request.targets instanceof List) || request.targets.isEmpty()) {
        error 'cm_request targets must be nonempty'
    }
    if (!(request.ordered_seeds instanceof List) || request.ordered_seeds.isEmpty()) {
        error 'cm_request ordered_seeds must be nonempty'
    }

    def targetTuples = request.targets.collect { target ->
        if (!(target.target_id instanceof String) || target.target_id.isEmpty()) {
            error 'Every target requires target_id'
        }
        tuple(
            [request_id: request.request_id, backend: request.backend, target_id: target.target_id, target_order: target.target_order],
            requestPath,
            null,
        )
    }
    def dispatch = Channel.fromList(targetTuples)

    switch (request.backend) {
        case 'protenix_v2_ensemble':
            CM_PROTENIX_UNIMPLEMENTED(dispatch)
            break
        case 'confornets':
            def coordinatePlanPath = file(
                "${requestPath.parent}/cm_coordinate_plan_v1.json",
                checkIfExists: true,
            )
            def requestRootPath = file(
                requestPath.parent.toString(),
                checkIfExists: true,
            )
            def confornetsTuples = request.targets.collect { target ->
                tuple(
                    [request_id: request.request_id, backend: request.backend, target_id: target.target_id, target_order: target.target_order],
                    requestRootPath,
                )
            }
            CONFORMATIONAL_MAPPING_CONFORNETS(Channel.fromList(confornetsTuples))
            break
        case 'external_import':
            CM_IMPORT_UNIMPLEMENTED(dispatch)
            break
        default:
            error "Unknown conformational-mapping backend: ${request.backend}"
    }
}
