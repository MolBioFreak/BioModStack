nextflow.enable.dsl = 2

include {
    ValidateShapeBundle
    PlanRFD3Batches
    RunShapeRFD3
    AdmitRFD3InitialCandidate
    PrepareShapeBackbone
    BuildRFD3Aggregate
    RunShapeProteinMPNN
    RunShapeFAMPNN
    EvaluateShapeCandidate
    RunShapeValidatorEvidence
    AttachShapePostRefold
    BuildShapeSkipBundle
    BuildShapeResult
} from '../modules/shape_blueprint'
include { ESMFold2Predict } from '../modules/esmfold2_experimental'


def loadShapeSequenceRecords(bundle, engine) {
    def payload = new groovy.json.JsonSlurper().parse(new File(bundle.toString(), 'sequence_records.json'))
    if (payload.schema != 'bms_shape_sequences_v1' || !(payload.records instanceof Collection)) {
        error "${engine} emitted an invalid Shape sequence record bundle"
    }
    payload.records.collect { record ->
        def sequence = record.sequence as String
        def name = record.sequence_name as String
        def source = bundle.resolve(record.source_backbone as String)
        def sourceSha = record.backbone_sha256 as String
        if (!sequence || !name || !source.isFile() || !sourceSha) {
            error "${engine} emitted an incomplete Shape sequence record"
        }
        tuple([
            producer_method: engine,
            producer_artifact_id: name,
            source_backbone_sha256: sourceSha,
        ], sequence, name, source)
    }
}


def shapeFile(pathValue) {
    return file(pathValue.toString(), checkIfExists: true)
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
    if (!params.job_id) {
        error "--job_id is required for terminal Shape result publication"
    }

    def requestFile = shapeFile(params.shape_request_path)
    def manifestFile = shapeFile(params.shape_geometry_manifest_path)
    def verticesFile = shapeFile(params.shape_vertices_path)
    def facesFile = shapeFile(params.shape_faces_path)
    def pointsFile = shapeFile(params.shape_points_path)
    def sdfFile = shapeFile(params.shape_sdf_path)
    def shapeRequest = new groovy.json.JsonSlurper().parse(new File(params.shape_request_path as String))
    def sequencePolicy = (shapeRequest.sequence_policy ?: 'auto').toString()
    def requestedEngine = shapeRequest.sequence_engine?.toString()
    def sequenceEngine = requestedEngine ?: (sequencePolicy == 'auto' ? 'proteinmpnn' : null)
    def sequenceCount = (shapeRequest.sequences_per_backbone ?: 0) as Integer
    def sequenceEnabled = sequencePolicy != 'skip' && sequenceCount > 0
    def validatorSuite = (shapeRequest.validator_suite ?: []).collect { it.toString() }
    def seed = (shapeRequest.seed ?: 0) as Integer

    if (!(sequencePolicy in ['auto', 'skip', 'external'])) {
        error "unsupported Shape sequence policy: ${sequencePolicy}"
    }
    if (sequenceEnabled && !(sequenceEngine in ['proteinmpnn', 'fampnn'])) {
        error "Shape sequence engine ${sequenceEngine} is not implemented for ordinary-protein RFD3"
    }
    if (sequencePolicy == 'external' && !sequenceEnabled) {
        error "external Shape sequence policy requires sequences_per_backbone > 0"
    }
    if (sequencePolicy == 'skip' && sequenceCount != 0) {
        error "sequence_policy=skip requires sequences_per_backbone=0"
    }
    if (validatorSuite.any { !(it in ['boltz2', 'esmfold2', 'protenix_v2']) }) {
        error "Shape validator suite contains an unsupported validator"
    }

    ValidateShapeBundle(requestFile, manifestFile, verticesFile, facesFile, pointsFile, sdfFile)
    PlanRFD3Batches(requestFile)
    RunShapeRFD3(
        PlanRFD3Batches.out.batch_requests,
        manifestFile,
        pointsFile,
        sdfFile,
        ValidateShapeBundle.out.receipt,
    )
    AdmitRFD3InitialCandidate(
        RunShapeRFD3.out.structures.flatten(),
        requestFile,
        manifestFile,
        pointsFile,
        sdfFile,
    )

    def acceptedInitial = AdmitRFD3InitialCandidate.out.admitted.filter { candidate, admission ->
        new groovy.json.JsonSlurper().parse(admission).status == 'accepted'
    }
    def admissionRecords = AdmitRFD3InitialCandidate.out.admitted.map { candidate, admission -> admission }.collect()
    BuildRFD3Aggregate(PlanRFD3Batches.out.plan, admissionRecords)
    PrepareShapeBackbone(acceptedInitial)

    def candidateBundles
    if (sequenceEnabled) {
        def shapeBackbones = PrepareShapeBackbone.out.backbone.map { candidateId, backboneBundle ->
            tuple(candidateId, backboneBundle.resolve('shape_backbone.pdb'))
        }
        if (sequenceEngine == 'proteinmpnn') {
            RunShapeProteinMPNN(shapeBackbones, sequenceCount, seed)
        } else {
            RunShapeFAMPNN(shapeBackbones, sequenceCount, seed)
        }
        def sequenceBundles = sequenceEngine == 'proteinmpnn'
            ? RunShapeProteinMPNN.out.bundle
            : RunShapeFAMPNN.out.bundle
        def shapeSequences = sequenceBundles.flatMap { bundle ->
            loadShapeSequenceRecords(bundle, sequenceEngine)
        }
        ESMFold2Predict(shapeSequences.map { producerMeta, sequence, name, source ->
            tuple(producerMeta, sequence, name)
        })
        def validatorInputs = ESMFold2Predict.out.shape_result.join(
            shapeSequences.map { producerMeta, sequence, name, source -> tuple(name, sequence) }
        )
        RunShapeValidatorEvidence(validatorInputs, validatorSuite, seed)
        def evaluatedInputs = ESMFold2Predict.out.shape_result.join(
            shapeSequences.map { producerMeta, sequence, name, source -> tuple(name, source) }
        )
        EvaluateShapeCandidate(
            evaluatedInputs,
            requestFile,
            manifestFile,
            pointsFile,
            sdfFile,
        )
        def attachInputs = EvaluateShapeCandidate.out.bundle.join(RunShapeValidatorEvidence.out.evidence)
        AttachShapePostRefold(attachInputs, requestFile, manifestFile, pointsFile, sdfFile)
        candidateBundles = AttachShapePostRefold.out.bundle.map { sequenceName, bundle -> bundle }
    } else {
        BuildShapeSkipBundle(PrepareShapeBackbone.out.backbone, requestFile)
        candidateBundles = BuildShapeSkipBundle.out.bundle
    }

    BuildShapeResult(
        candidateBundles.collect(),
        requestFile,
        BuildRFD3Aggregate.out.aggregate,
        params.job_id.toString(),
    )
}
