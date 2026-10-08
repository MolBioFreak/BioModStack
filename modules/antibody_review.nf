nextflow.enable.dsl = 2

include { ANARCII as ReviewANARCII } from './utils/anarci'
include { ANARCII as FinalANARCII } from './utils/anarci'

process SealAntibodyReview {
    label 'process_low'
    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: 'checkpoint', saveAs: { stage.toString() }
    input:
    val job_id
    val stage
    path structures
    path annotations
    path native_artifacts
    val settings
    output:
    path 'checkpoint', emit: checkpoint
    script:
    def manifest = groovy.json.JsonOutput.toJson([
        job_id: job_id, stage: stage, sources: (structures instanceof Collection ? structures : [structures]).collect { it.toString() },
        annotations: (annotations instanceof Collection ? annotations : [annotations]).collect { it.toString() },
        native_artifacts: (native_artifacts instanceof Collection ? native_artifacts : [native_artifacts]).collect { it.toString() }, settings: settings])
    def encoded = manifest.getBytes('UTF-8').encodeBase64().toString()
    """
    printf '%s' '${encoded}' | base64 --decode > review_manifest.json
    python3 ${params.code_root}/scripts/antibody_checkpoint.py seal --manifest review_manifest.json --output checkpoint
    """
}

process PublishAntibodyAnnotations {
    label 'process_low'
    publishDir "${params.out_dir}/annotations/anarcii", mode: 'copy'
    input:
    path annotations
    output:
    path 'native_annotations/*', emit: annotations
    script:
    """
    mkdir native_annotations
    cp ${annotations} native_annotations/
    """
}

workflow OpenInteractiveGate {
    take:
    job_id
    stage_name
    gate_trigger
    candidate_dir
    raw_dir
    filtered_dir
    framework_type
    antibody_chains
    structure_validator
    main:
    // Consume the task-output channel, not asynchronously published host paths.
    candidate_channel = candidate_dir
    review_structures = candidate_channel.collect().map { candidate ->
        def values = candidate instanceof Collection ? candidate.flatten() : [candidate]
        def pdbs = values.collectMany { value ->
            def path = file(value.toString())
            path.isDirectory() ? path.toFile().listFiles().findAll { it.name.endsWith('.pdb') }.collect { file(it.toString()) } : [path]
        }.sort { it.name }
        if (!pdbs) error('antibody_denovo:empty_review_set')
        pdbs
    }
    // Native confidence, design metrics, RF metadata and aligned-error bytes
    // remain review artifacts; ANARCII is not a replacement for these analyses.
    native_analysis = review_structures.map { pdbs ->
        pdbs.collectMany { pdb ->
            def stem = pdb.baseName.toString()
            pdb.parent.toFile().listFiles().findAll { candidate ->
                candidate.isFile() && candidate.name.startsWith(stem + '.') &&
                (candidate.name ==~ /.*\.(json|trb|cif|npz|csv|txt)$/)
            }.collect { file(it.toString()) }
        }.unique { it.toString() }.sort { it.name }
    }
    ReviewANARCII(review_structures.flatten().map { pdb -> tuple([id: pdb.baseName], pdb) })
    annotation_files = ReviewANARCII.out.cdrs.map { meta, path -> path }
        .mix(ReviewANARCII.out.cdr_positions.map { meta, path -> path }).collect()
    def checkpointSettings = params.antibody_checkpoint_settings
    if (!(checkpointSettings instanceof Map) || checkpointSettings.isEmpty()) {
        error('antibody_denovo:checkpoint_requires_complete_effective_settings')
    }
    SealAntibodyReview(job_id, stage_name, review_structures, annotation_files, native_analysis, checkpointSettings)
    emit:
    report = SealAntibodyReview.out.checkpoint
}

workflow AnnotateTerminalAntibody {
    take:
    structures
    main:
    FinalANARCII(structures.flatMap { meta, pdbs ->
        (pdbs instanceof Collection ? pdbs : [pdbs]).collect { pdb -> tuple(meta + [id: pdb.baseName], pdb) }
    })
    annotations = FinalANARCII.out.cdrs.map { meta, path -> path }
        .mix(FinalANARCII.out.cdr_positions.map { meta, path -> path }).collect()
        .filter { it.size() > 0 }
    PublishAntibodyAnnotations(annotations)
    emit:
    receipt = PublishAntibodyAnnotations.out.annotations
}
