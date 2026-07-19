#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

import groovy.json.JsonSlurper

include { CONFORMATIONAL_MAPPING_CONFORNETS } from '../modules/conformational_mapping_confornets.nf'
include { CONFORMATIONAL_MAPPING_PROTENIX } from '../modules/conformational_mapping_protenix.nf'
include { CONFORMATIONAL_MAPPING_IMPORT } from '../modules/conformational_mapping_import.nf'
include { CONFORMATIONAL_MAPPING_ANALYSIS_PLANE as PROTENIX_ANALYSIS_PLANE } from '../modules/conformational_mapping_frustrampnn.nf'
include { CONFORMATIONAL_MAPPING_ANALYSIS_PLANE as CONFORNETS_ANALYSIS_PLANE } from '../modules/conformational_mapping_frustrampnn.nf'
include { CONFORMATIONAL_MAPPING_ANALYSIS_PLANE as IMPORT_ANALYSIS_PLANE } from '../modules/conformational_mapping_frustrampnn.nf'

params.cm_request_path = null

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

    request.targets.each { target ->
        if (!(target.target_id instanceof String) || target.target_id.isEmpty()) {
            error 'Every target requires target_id'
        }
    }
    def requestRootPath = file(requestPath.parent.toString(), checkIfExists: true)
    def requestTuples = Channel.value(tuple(request.request_id, requestRootPath))
    // The pinned FrustraMPNN image is self-contained; do not require or stage a
    // second host checkpoint that can drift from the image-embedded model.
    def frustrationCheckpoint = '/opt/frustrampnn_weights/megascale.ckpt'

    switch (request.backend) {
        case 'protenix_v2_ensemble':
            CONFORMATIONAL_MAPPING_PROTENIX(requestTuples)
            PROTENIX_ANALYSIS_PLANE(
                CONFORMATIONAL_MAPPING_PROTENIX.out.canonical.map { request_id, canonical_dir ->
                    tuple(request_id, 'canonical_protenix', requestRootPath, canonical_dir, frustrationCheckpoint)
                }
            )
            break
        case 'confornets':
            def confornetsTuples = request.targets.collect { target ->
                tuple(
                    [request_id: request.request_id, backend: request.backend, target_id: target.target_id, target_order: target.target_order],
                    requestRootPath,
                )
            }
            CONFORMATIONAL_MAPPING_CONFORNETS(Channel.fromList(confornetsTuples))
            CONFORNETS_ANALYSIS_PLANE(
                CONFORMATIONAL_MAPPING_CONFORNETS.out.canonical_dir.map { canonical_dir ->
                    tuple(request.request_id, 'canonical_confornets', requestRootPath, canonical_dir, frustrationCheckpoint)
                }
            )
            break
        case 'external_import':
            CONFORMATIONAL_MAPPING_IMPORT(requestTuples)
            IMPORT_ANALYSIS_PLANE(
                CONFORMATIONAL_MAPPING_IMPORT.out.canonical.map { request_id, canonical_dir ->
                    tuple(request_id, 'canonical_import', requestRootPath, canonical_dir, frustrationCheckpoint)
                }
            )
            break
        default:
            error "Unknown conformational-mapping backend: ${request.backend}"
    }
}
