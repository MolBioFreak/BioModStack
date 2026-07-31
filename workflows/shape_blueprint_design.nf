nextflow.enable.dsl = 2

include { ValidateShapeBundle; RunShapeRFD3; RunShapeProteinMPNN; RunShapeFAMPNN; EvaluateShapeCandidate; BuildShapeResult } from '../modules/shape_blueprint'
include { FilterRFD3 } from '../modules/rfd3'
include { ESMFold2Predict } from '../modules/esmfold2_experimental'

workflow {
    if (!params.shape_request_path) {
        error "--shape_request_path is required"
    }
    if (!params.shape_geometry_manifest_path) {
        error "--shape_geometry_manifest_path is required"
    }
    if (!params.shape_vertices_path || !params.shape_faces_path || !params.shape_points_path) {
        error "canonical Shape vertices, faces, and points are required"
    }

    ValidateShapeBundle(
        file(params.shape_request_path, checkIfExists: true),
        file(params.shape_geometry_manifest_path, checkIfExists: true),
        file(params.shape_vertices_path, checkIfExists: true),
        file(params.shape_faces_path, checkIfExists: true),
        file(params.shape_points_path, checkIfExists: true),
        file(params.shape_sdf_path, checkIfExists: true),
    )
    RunShapeRFD3(
        file(params.shape_request_path, checkIfExists: true),
        file(params.shape_geometry_manifest_path, checkIfExists: true),
        file(params.shape_points_path, checkIfExists: true),
        file(params.shape_sdf_path, checkIfExists: true),
        ValidateShapeBundle.out.receipt,
    )
    FilterRFD3(RunShapeRFD3.out.structures_metadata)

    shape_backbones = FilterRFD3.out.structures_metadata.map { pdbs, _metadata -> pdbs }.flatten()
    RunShapeProteinMPNN(
        shape_backbones,
        params.shape_sequences_per_backbone as Integer,
        params.shape_seed as Integer,
    )
    RunShapeFAMPNN(
        shape_backbones,
        params.shape_sequences_per_backbone as Integer,
        params.shape_seed as Integer,
    )

    proteinmpnn_sequences = RunShapeProteinMPNN.out.bundle.flatMap { bundle ->
        def payload = new groovy.json.JsonSlurper().parse(new File(bundle.toString(), 'sequence_records.json'))
        payload.records.collect { record -> tuple(record.sequence as String, record.sequence_name as String, bundle.resolve(record.source_backbone as String)) }
    }
    fampnn_sequences = RunShapeFAMPNN.out.bundle.flatMap { bundle ->
        def payload = new groovy.json.JsonSlurper().parse(new File(bundle.toString(), 'sequence_records.json'))
        payload.records.collect { record -> tuple(record.sequence as String, record.sequence_name as String, bundle.resolve(record.source_backbone as String)) }
    }
    shape_sequences = proteinmpnn_sequences.mix(fampnn_sequences)
    ESMFold2Predict(shape_sequences.map { sequence, name, _source -> tuple(sequence, name) })
    sequence_sources = shape_sequences.map { _sequence, name, source -> tuple(name, source) }
    evaluated_inputs = ESMFold2Predict.out.shape_result.join(sequence_sources)
    EvaluateShapeCandidate(
        evaluated_inputs,
        file(params.shape_request_path, checkIfExists: true),
        file(params.shape_geometry_manifest_path, checkIfExists: true),
        file(params.shape_points_path, checkIfExists: true),
        file(params.shape_sdf_path, checkIfExists: true),
    )
    BuildShapeResult(
        EvaluateShapeCandidate.out.bundle.toList(),
        file(params.shape_request_path, checkIfExists: true),
        params.job_id as String,
    )
}
