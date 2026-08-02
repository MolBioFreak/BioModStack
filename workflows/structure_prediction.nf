#!/usr/bin/env nextflow
/**
 * Structure Prediction Workflow
 * 
 * Standalone entry point for sequence-to-structure prediction using Boltz-2,
 * RF3, Protenix, or ESMFold2.
 * 
 * Usage:
 *   nextflow run workflows/structure_prediction.nf -c nextflow.config \
 *     --sequence_input "MKTLLILAVVAAALA..." \
 *     --sequence_name "my_protein" \
 *     --pred_method boltz
 */

nextflow.enable.dsl = 2

import groovy.json.JsonOutput
import groovy.json.JsonSlurper

include { structure_prediction_wf } from '../modules/structure_prediction.nf'
include { CanonicalFrustraMPNN } from '../modules/frustrampnn.nf'

// Workflow-specific param defaults
params.sequence_input = null
params.sequence_name = 'predicted'
params.pred_method = 'boltz'
params.num_parallel_jobs = 1
params.run_frustrampnn = true
params.frustrampnn_requiredness = 'required'

process PrepareStructurePredictionFrustraMPNNCandidate {
    tag "frustrampnn-source:${candidate_meta.producer_stage}:${candidate_meta.producer_candidate_key}"
    stageInMode 'copy'
    publishDir { "${params.out_dir}/${new File(candidate_meta.producer_candidate_key.toString()).parent}" },
        mode: 'copy', pattern: 'canonical_source.pdb', saveAs: { new File(candidate_meta.producer_candidate_key.toString()).name }

    input:
    tuple val(candidate_meta), path(predicted_structure)

    output:
    tuple path('workflow_component_request_v1.json'), path('canonical_source.pdb'), emit: prepared

    script:
    def metadataBase64 = JsonOutput.toJson(candidate_meta).getBytes('UTF-8').encodeBase64().toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_frustrampnn_candidate.py' \
      --source '${predicted_structure}' \
      --output-pdb canonical_source.pdb \
      --request workflow_component_request_v1.json \
      --metadata-base64 '${metadataBase64}'
    """
}

process ReportStructurePredictionFrustraMPNNNotRequested {
    label 'CPU'
    publishDir "${params.out_dir}/frustrampnn", mode: 'copy', pattern: 'structure_prediction_frustrampnn_terminal_manifest.json'

    input:
    val trigger

    output:
    path 'structure_prediction_frustrampnn_terminal_manifest.json'
    path 'frustrampnn_not_requested.reported'

    script:
    def payload = JsonOutput.toJson([
        schema_name: 'structure_prediction_frustrampnn_terminal_manifest',
        schema_version: 1,
        parent_job_id: params.job_id.toString(),
        parent_workflow_id: 'structure_prediction',
        status: 'not_requested',
        requiredness: 'not_requested',
        candidate_count: 0,
        candidates: [],
        reported_outputs: [],
    ])
    """
    set -euo pipefail
    printf '%s\n' '${payload}' > structure_prediction_frustrampnn_terminal_manifest.json
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' \
      '${params.job_id}' frustrampnn not_requested
    : > frustrampnn_not_requested.reported
    """
}

process PublishStructurePredictionFrustraMPNNCandidate {
    label 'CPU'
    stageInMode 'copy'
    input:
    tuple val(result_meta), path(candidate_bundle), path(result_manifest)
    output:
    path 'published_*.json', emit: marker
    script:
    def candidateId = result_meta.candidate_id.toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/publish_frustrampnn_bundle.py' \
      --source-bundle '${candidate_bundle}' --allowed-root '${params.out_dir}' \
      --destination '${params.out_dir}/frustrampnn/results/${candidateId}' \
      --marker 'published_${candidateId}.json'
    """
}

process ReportStructurePredictionFrustraMPNNComplete {
    label 'CPU'
    input:
    path published_markers
    output:
    path 'frustrampnn_complete.reported'
    script:
    """
    set -euo pipefail
    mapfile -t outputs < <('${params.api_python}' - <<'PY'
import json, pathlib
for marker in sorted(pathlib.Path('.').glob('published_*.json')):
    payload = json.loads(marker.read_text(encoding='utf-8'))
    if set(payload) != {'manifest', 'result', 'source'}:
        raise SystemExit('invalid FrustraMPNN publication marker')
    print(payload['result'])
    print(payload['manifest'])
    print(payload['source'])
PY
    )
    test \"\${#outputs[@]}\" -gt 0
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' --job-root-relative \
      '${params.job_id}' frustrampnn complete \"\${outputs[@]}\"
    : > frustrampnn_complete.reported
    """
}

workflow STRUCTURE_PREDICTION {
    take:
        sequence_ch  // Channel of [sequence, name]
    
    main:
        structure_prediction_wf(sequence_ch)
        
        structure_stream = structure_prediction_wf.out.structures.flatten()
        structures = structure_stream.collect()
        
        if (params.run_frustrampnn != false) {
            def requiredness = params.frustrampnn_requiredness ?: 'required'
            if (requiredness != 'required') {
                error('frustrampnn_requiredness must be required')
            }
            if (!params.job_id) error('Canonical FrustraMPNN parent execution requires --job_id')
            def canonical_candidates = structure_prediction_wf.out.canonical_structures.map { producer_meta, predicted ->
                def method = producer_meta.producer_method?.toString()
                def artifactKey = producer_meta.producer_artifact_key?.toString()
                if (!(method ==~ /[a-z0-9][a-z0-9_-]*/)) {
                    error("structure prediction emitted an invalid producer method")
                }
                if (!(artifactKey ==~ /[A-Za-z0-9][A-Za-z0-9._-]*/)) {
                    error("structure prediction emitted an unsafe producer artifact key")
                }
                def producerKey = "frustrampnn/sources/${method}/${artifactKey}.normalized.pdb"
                tuple([
                    parent_job_id: params.job_id.toString(),
                    parent_workflow_id: 'structure_prediction',
                    producer_stage: "structure_prediction:${method}",
                    producer_candidate_key: producerKey,
                    requiredness: requiredness,
                    checkpoint_id: 'megascale.ckpt'
                ], predicted)
            }
            PrepareStructurePredictionFrustraMPNNCandidate(canonical_candidates)
            CanonicalFrustraMPNN(PrepareStructurePredictionFrustraMPNNCandidate.out.prepared)
            PublishStructurePredictionFrustraMPNNCandidate(CanonicalFrustraMPNN.out.result)
            ReportStructurePredictionFrustraMPNNComplete(
                PublishStructurePredictionFrustraMPNNCandidate.out.marker.collect()
            )
        } else {
            if (!params.job_id) error('FrustraMPNN not-requested reporting requires --job_id')
            ReportStructurePredictionFrustraMPNNNotRequested(Channel.value(true))
        }
    
    emit:
        structures
}

// Entry point for direct invocation
workflow {
    // Validate inputs
    def esmComplexComponents = params.esmf_complex_components ?: params.complex_components ?: []
    if (!params.sequence_input && !(params.pred_method == 'esmfold2' && esmComplexComponents instanceof Collection && !esmComplexComponents.isEmpty())) {
        error("--sequence_input is required. ESMFold2 complex jobs may instead provide --complex_components.")
    }

    if (!(params.pred_method in ['boltz', 'rf3', 'protenix', 'esmfold2', 'both', 'all'])) {
        error("--pred_method must be one of: boltz, rf3, protenix, esmfold2, both, all")
    }
    
    def seq = params.sequence_input ?: ''
    def name = params.sequence_name ?: 'predicted'
    def numJobs = params.num_parallel_jobs ?: 1
    
    println("=" * 60)
    println("Structure Prediction Workflow")
    println("=" * 60)
    println("* Sequence: ${seq ? seq.take(50) + (seq.length() > 50 ? '...' : '') : '[complex components]'}")
    println("* Name: ${name}")
    println("* Predictor: ${params.pred_method}")
    println("* Parallel jobs: ${numJobs}")
    println("* Frustration analysis (FrustraMPNN): ${params.run_frustrampnn}")
    println("=" * 60)
    
    // Create parallel job channels
    def job_indices = Channel.from(0..<numJobs)
    def input_ch = job_indices.map { idx ->
        def jobName = numJobs > 1 ? "${name}_job${idx}" : name
        tuple(seq, jobName)
    }
    
    STRUCTURE_PREDICTION(input_ch)
}
