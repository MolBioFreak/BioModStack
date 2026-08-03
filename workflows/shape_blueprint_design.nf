nextflow.enable.dsl = 2

include { ValidateShapeBundle; PlanRFD3Batches; RunShapeRFD3; AdmitRFD3InitialCandidate; BuildRFD3Aggregate; RunShapeProteinMPNN; RunShapeFAMPNN; EvaluateShapeCandidate; BuildShapeResult } from '../modules/shape_blueprint'
include { FilterRFD3 } from '../modules/rfd3'
include { ESMFold2Predict } from '../modules/esmfold2_experimental'


def loadShapeSequenceRecords(bundle, engine) {
    def payload = new groovy.json.JsonSlurper().parse(new File(bundle.toString(), 'sequence_records.json'))
    payload.records.collect { record ->
        def sequence = record.sequence as String
        def name = record.sequence_name as String
        def source = bundle.resolve(record.source_backbone as String)
        tuple([producer_method: engine, producer_artifact_id: name], sequence, name, source)
    }
}


workflow {
    if (!params.shape_request_path) {
        error "--shape_request_path is required"
    }
    if (!params.shape_geometry_manifest_path) {
        error "--shape_geometry_manifest_path is required"
    }
    if (!params.shape_vertices_path || !params.shape_faces_path || !params.shape_points_path || !params.shape_sdf_path) {
        error "canonical Shape vertices, faces, points, and SDF are required"
    }

    ValidateShapeBundle(
        file(params.shape_request_path, checkIfExists: true),
        file(params.shape_geometry_manifest_path, checkIfExists: true),
        file(params.shape_vertices_path, checkIfExists: true),
        file(params.shape_faces_path, checkIfExists: true),
        file(params.shape_points_path, checkIfExists: true),
        file(params.shape_sdf_path, checkIfExists: true),
    )
    PlanRFD3Batches(
        file(params.shape_request_path, checkIfExists: true),
    )
    RunShapeRFD3(
        PlanRFD3Batches.out.batch_requests,
        file(params.shape_geometry_manifest_path, checkIfExists: true),
        file(params.shape_points_path, checkIfExists: true),
        file(params.shape_sdf_path, checkIfExists: true),
        ValidateShapeBundle.out.receipt,
    )
    AdmitRFD3InitialCandidate(
        RunShapeRFD3.out.structures.flatten(),
        file(params.shape_request_path, checkIfExists: true),
        file(params.shape_geometry_manifest_path, checkIfExists: true),
        file(params.shape_points_path, checkIfExists: true),
        file(params.shape_sdf_path, checkIfExists: true),
    )
    accepted_initial = AdmitRFD3InitialCandidate.out.admitted.filter { candidate, admission ->
        new groovy.json.JsonSlurper().parse(admission).status == 'accepted'
    }
    initial_admission_records = AdmitRFD3InitialCandidate.out.admitted.map { candidate, admission -> admission }.collect()
    BuildRFD3Aggregate(
        PlanRFD3Batches.out.plan,
        initial_admission_records,
    )
    FilterRFD3(accepted_initial)

    shape_backbones = FilterRFD3.out.structures_metadata.map { pdbs, _metadata -> pdbs }.flatten()
    def shape_request = new groovy.json.JsonSlurper().parse(new File(params.shape_request_path as String))
    def sequence_policy = (shape_request.sequence_policy ?: 'auto') as String
    def requested_engine = shape_request.sequence_engine as String
    def sequence_engine = requested_engine ?: (sequence_policy == 'auto' ? 'proteinmpnn' : null)
    def sequence_count = (shape_request.sequences_per_backbone ?: 0) as Integer
    def sequence_enabled = sequence_policy != 'skip' && sequence_count > 0
    if (sequence_enabled && !(sequence_engine in ['proteinmpnn', 'fampnn'])) {
        error "Shape sequence engine ${sequence_engine} is not implemented for the ordinary-protein RFD3 lane"
    }
    if (sequence_enabled && sequence_engine == 'proteinmpnn') {
        RunShapeProteinMPNN(
            shape_backbones,
            sequence_count,
            (shape_request.seed ?: 0) as Integer,
        )
    }
    if (sequence_enabled && sequence_engine == 'fampnn') {
        RunShapeFAMPNN(
            shape_backbones,
            sequence_count,
            (shape_request.seed ?: 0) as Integer,
        )
    }
    if (sequence_enabled) {
        if (sequence_engine == 'proteinmpnn') {
            shape_sequences = RunShapeProteinMPNN.out.bundle.flatMap { bundle ->
                loadShapeSequenceRecords(bundle, 'proteinmpnn')
            }
        } else {
            shape_sequences = RunShapeFAMPNN.out.bundle.flatMap { bundle ->
                loadShapeSequenceRecords(bundle, 'fampnn')
            }
        }
        ESMFold2Predict(shape_sequences.map { producer_meta, sequence, name, _source -> tuple(producer_meta, sequence, name) })
        sequence_sources = shape_sequences.map { _producer_meta, _sequence, name, source -> tuple(name, source) }
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
}
