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
    RunShapeCaliby
    EvaluateShapeCandidate
    RunShapeBoltzValidator
    RunShapeProtenixValidator
    AggregateShapeValidatorEvidence
    AttachShapePostRefold
    BuildShapeSkipBundle
    BuildShapeResult
} from '../modules/shape_blueprint'
include { ESMFold2Predict } from '../modules/esmfold2_experimental'


def loadShapeSequenceRecords(bundle, engine) {
    def payload = new groovy.json.JsonSlurper().parse(new File(bundle.toString(), 'sequence_records.json'))
    if (!(payload.schema in ['bms_shape_sequences_v1', 'bms_shape_sequences_v2']) || !(payload.records instanceof Collection)) {
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
    if (sequenceEnabled && !(sequenceEngine in ['proteinmpnn', 'fampnn', 'caliby_experimental'])) {
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
    if (validatorSuite.unique(false).size() != validatorSuite.size()) {
        error "Shape validator suite contains duplicates"
    }

    ValidateShapeBundle(requestFile, manifestFile, verticesFile, facesFile, pointsFile, sdfFile)
    PlanRFD3Batches(requestFile)
    RunShapeRFD3(
        PlanRFD3Batches.out.batch_requests.flatten(),
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
            RunShapeProteinMPNN(shapeBackbones, sequenceCount, seed, requestFile)
        } else if (sequenceEngine == 'fampnn') {
            RunShapeFAMPNN(shapeBackbones, sequenceCount, seed, requestFile)
        } else {
            RunShapeCaliby(shapeBackbones, sequenceCount, seed, requestFile)
        }
        def sequenceBundles = sequenceEngine == 'proteinmpnn'
            ? RunShapeProteinMPNN.out.bundle
            : sequenceEngine == 'fampnn' ? RunShapeFAMPNN.out.bundle : RunShapeCaliby.out.bundle
        def shapeSequences = sequenceBundles.flatMap { bundle ->
            loadShapeSequenceRecords(bundle, sequenceEngine)
        }
        ESMFold2Predict(shapeSequences.map { producerMeta, sequence, name, source ->
            if (shapeRequest.schema == 'bms_shape_design_request_v3') {
                producerMeta = producerMeta + [shape_settings: shapeRequest.validator_settings.esmfold2]
            }
            tuple(producerMeta, sequence, name)
        })
        // Manifest rows bind the actual native sample to its exact two files.
        // Preserve the established native sample-000 baseline; the full
        // native manifest is retained by the validator suite as alternate samples.
        def esmSamples = ESMFold2Predict.out.shape_bundle.flatMap { name, bundle ->
            def manifest = new groovy.json.JsonSlurper().parse(new File(bundle.toString(), 'manifest.json'))
            manifest.samples.findAll { sample -> sample.sample_id.toString() == name.toString() + "_000" }.collect { sample ->
                tuple(name, sample.sample_id.toString(), bundle.resolve(sample.cif.toString()), bundle.resolve(sample.metrics.toString()))
            }
        }
        def nativeValidatorInputs = shapeSequences.map { producerMeta, sequence, name, source -> tuple(name, sequence) }
        def suiteInputs = esmSamples.combine(nativeValidatorInputs, by: 0).map { name, sampleId, structure, metrics, sequence ->
            tuple(name, sampleId, structure, metrics, sequence, [])
        }
        if ('boltz2' in validatorSuite) {
            RunShapeBoltzValidator(nativeValidatorInputs, seed, requestFile)
            suiteInputs = suiteInputs.combine(RunShapeBoltzValidator.out.evidence, by: 0)
                .map { name, sampleId, structure, metrics, sequence, peers, evidence ->
                    tuple(name, sampleId, structure, metrics, sequence, peers + [evidence])
                }
        }
        if ('protenix_v2' in validatorSuite) {
            RunShapeProtenixValidator(nativeValidatorInputs, seed, requestFile)
            suiteInputs = suiteInputs.combine(RunShapeProtenixValidator.out.evidence, by: 0)
                .map { name, sampleId, structure, metrics, sequence, peers, evidence ->
                    tuple(name, sampleId, structure, metrics, sequence, peers + [evidence])
                }
        }
        AggregateShapeValidatorEvidence(suiteInputs.combine(ESMFold2Predict.out.shape_bundle, by: 0), validatorSuite, seed, requestFile)
        def evaluatedInputs = esmSamples.combine(
            shapeSequences.map { producerMeta, sequence, name, source -> tuple(name, source) }, by: 0
        ).map { name, sampleId, structure, metrics, source -> tuple(sampleId, name, structure, metrics, source) }
        EvaluateShapeCandidate(evaluatedInputs, requestFile, manifestFile, pointsFile, sdfFile)
        def sequenceSources = sequenceBundles.flatMap { bundle ->
            def payload = new groovy.json.JsonSlurper().parse(new File(bundle.toString(), 'sequence_records.json'))
            payload.records.collect { record -> tuple(record.sequence_name.toString(), bundle) }
        }
        def sampleSources = esmSamples.combine(sequenceSources, by: 0)
            .map { name, sampleId, structure, metrics, bundle -> tuple(sampleId, bundle) }
        def attachInputs = EvaluateShapeCandidate.out.bundle
            .join(AggregateShapeValidatorEvidence.out.evidence, failOnDuplicate: true, failOnMismatch: true)
            .join(sampleSources, failOnDuplicate: true, failOnMismatch: true)
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
