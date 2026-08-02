#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

import groovy.json.JsonOutput
import groovy.json.JsonSlurper

include { complex_prediction_wf } from '../modules/structure_prediction.nf'
include { CanonicalFrustraMPNN } from '../modules/frustrampnn.nf'

def parseJsonFile(rawPath) {
    return new JsonSlurper().parse(file(rawPath))
}

def sha256File(rawPath) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    rawPath.toFile().withInputStream { stream ->
        byte[] buffer = new byte[1024 * 1024]
        int count
        while ((count = stream.read(buffer)) != -1) {
            digest.update(buffer, 0, count)
        }
    }
    return digest.digest().encodeHex().toString()
}

def sha256Text(value) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    digest.update(value.toString().getBytes('UTF-8'))
    return digest.digest().encodeHex().toString()
}

def detectStructureFormat(rawPath) {
    boolean sawPdbRecord = false
    boolean sawMmcifRecord = false
    int inspected = 0
    rawPath.toFile().withReader('UTF-8') { reader ->
        String line
        while ((line = reader.readLine()) != null && inspected < 10000) {
            inspected += 1
            def trimmed = line.trim()
            if (!trimmed || trimmed.startsWith('#')) continue
            if (trimmed.startsWith('data_') || trimmed == 'loop_' || trimmed.startsWith('_atom_site.')) {
                sawMmcifRecord = true
                break
            }
            if (line.startsWith('ATOM  ') || line.startsWith('HETATM') || line.startsWith('MODEL ')) {
                sawPdbRecord = true
            }
        }
    }
    if (sawMmcifRecord) return 'mmcif'
    if (sawPdbRecord) return 'pdb'
    throw new IllegalArgumentException('complex_prediction emitted an unrecognized structure representation')
}

process PrepareComplexPredictionFrustraMPNNCandidate {
    tag "frustrampnn-complex-source:${candidate_meta.producer_method}:${candidate_meta.producer_artifact_sha256}"
    stageInMode 'copy'

    input:
    tuple val(candidate_meta), path(predicted_structure)

    output:
    tuple val(candidate_meta), path('prepared_request.json'), path('prepared_source.pdb'), emit: prepared

    script:
    def requestMetadata = [
        parent_job_id: candidate_meta.parent_job_id,
        parent_workflow_id: candidate_meta.parent_workflow_id,
        producer_stage: candidate_meta.producer_stage,
        producer_candidate_key: candidate_meta.producer_candidate_key,
        requiredness: candidate_meta.requiredness,
        checkpoint_id: candidate_meta.checkpoint_id,
        producer_method: candidate_meta.producer_method,
        producer_sample: candidate_meta.producer_sample,
        producer_rank: candidate_meta.producer_rank,
        producer_output_key: candidate_meta.producer_output_key,
        producer_identity_sha256: candidate_meta.producer_identity_sha256,
        producer_artifact_sha256: candidate_meta.producer_artifact_sha256,
        source_format: candidate_meta.source_format,
    ]
    def metadataBase64 = JsonOutput.toJson(requestMetadata).getBytes('UTF-8').encodeBase64().toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_frustrampnn_candidate.py' \
      --source '${predicted_structure}' \
      --output-pdb prepared_source.pdb \
      --request prepared_request.json \
      --metadata-base64 '${metadataBase64}'
    """
}

process MaterializeComplexPredictionFrustraMPNNCandidate {
    tag "frustrampnn-complex-materialize:${candidate_meta.producer_method}:${candidate_meta.producer_artifact_sha256}"
    stageInMode 'copy'
    publishDir { "${params.out_dir}/${new File(candidate_meta.producer_candidate_key.toString()).parent}" },
        mode: 'copy', pattern: 'canonical_source.pdb', saveAs: { new File(candidate_meta.producer_candidate_key.toString()).name }

    input:
    tuple val(candidate_meta), path(prepared_request), path(prepared_source)

    output:
    tuple path('workflow_component_request_v1.json'), path('canonical_source.pdb'), emit: prepared

    script:
    """
    set -euo pipefail
    cp -L '${prepared_request}' workflow_component_request_v1.json
    cp -L '${prepared_source}' canonical_source.pdb
    """
}

process ReportComplexPredictionFrustraMPNNNotRequested {
    label 'CPU'
    publishDir "${params.out_dir}/frustrampnn", mode: 'copy', pattern: 'complex_prediction_frustrampnn_terminal_manifest.json'

    input:
    val trigger

    output:
    path 'complex_prediction_frustrampnn_terminal_manifest.json'
    path 'frustrampnn_not_requested.reported'

    script:
    def payload = JsonOutput.toJson([
        schema_name: 'complex_prediction_frustrampnn_terminal_manifest',
        schema_version: 1,
        parent_job_id: params.job_id.toString(),
        parent_workflow_id: 'complex_prediction',
        status: 'not_requested',
        requiredness: 'not_requested',
        candidate_count: 0,
        candidates: [],
        reported_outputs: [],
    ])
    """
    set -euo pipefail
    printf '%s\n' '${payload}' > complex_prediction_frustrampnn_terminal_manifest.json
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' \
      '${params.job_id}' frustrampnn not_requested
    : > frustrampnn_not_requested.reported
    """
}

process PublishComplexPredictionFrustraMPNNCandidate {
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
      --source-bundle '${candidate_bundle}' \
      --allowed-root '${params.out_dir}' \
      --destination '${params.out_dir}/frustrampnn/results/${candidateId}' \
      --marker 'published_${candidateId}.json'
    """
}

process ReportComplexPredictionFrustraMPNNComplete {
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

workflow COMPLEX_PREDICTION {
    main:
        if (!params.complex_json_path && !(params.sequence_batch_json_path && params.complex_batch_dir)) {
            error("complex_prediction requires --complex_json_path or --sequence_batch_json_path with --complex_batch_dir")
        }

        def numParallelJobs = params.num_parallel_jobs ?: 1
        def complex_name = params.sequence_name ?: 'complex_pred'
        def msa_file = params.msa_path ? file(params.msa_path) : file("${params.code_root}/NO_MSA")
        def complex_ch

        println("=" * 60)
        println("Complex Structure Prediction Workflow")
        println("=" * 60)
        if (params.complex_json_path) println("* Complex definition: ${params.complex_json_path}")
        if (params.sequence_batch_json_path) println("* Batch manifest: ${params.sequence_batch_json_path}")
        println("* Predictor: ${params.pred_method ?: 'boltz'}")
        println("* Number of simulations: ${numParallelJobs}")

        if (params.sequence_batch_json_path && params.complex_batch_dir) {
            def batchEntries = parseJsonFile(params.sequence_batch_json_path) as List
            println("* Batch variants: ${batchEntries.size()}")
            if ((params.pred_method ?: 'boltz') == 'protenix') {
                println("* Protenix complex batch mode: one model bootstrap for ${batchEntries.size()} variants")
                complex_ch = Channel.of(
                    tuple(
                        "${complex_name}_batch",
                        file(params.complex_batch_dir),
                        msa_file,
                    )
                )
            } else {
                complex_ch = Channel
                    .from(batchEntries)
                    .map { entry ->
                        tuple(
                            "${entry.name}",
                            file("${entry.complex_json}"),
                            msa_file,
                        )
                    }
            }
        } else {
            def complex_json = file(params.complex_json_path)
            def job_indices = Channel.from(0..<numParallelJobs)
            complex_ch = job_indices.map { idx ->
                def jobName = numParallelJobs > 1 ? "${complex_name}_job${idx}" : complex_name
                tuple(jobName, complex_json, msa_file)
            }
        }

        complex_prediction_wf(complex_ch)

        // Predictor failure/zero yield is terminal: never fabricate an analyzer input.
        actual_candidates = complex_prediction_wf.out.structures.flatten().ifEmpty {
            error('complex_prediction:no_candidates')
        }
        structures = actual_candidates.collect()
        frustrampnn_results = channel.empty()

        if (params.run_frustrampnn == true) {
            def requiredness = params.frustrampnn_requiredness ?: 'required'
            if (requiredness != 'required') {
                error('frustrampnn_requiredness must be required')
            }
            if (!params.job_id) error('Canonical FrustraMPNN parent execution requires --job_id')
            println("Running canonical FrustraMPNN on final complex candidates")
            println("FrustraMPNN scope: protein entities only; ligand/nucleic-acid context is not analyzed")

            raw_candidates = complex_prediction_wf.out.canonical_candidates.map { producer_meta, predicted ->
                def producerMethod = producer_meta.producer_method?.toString()
                def producerSample = producer_meta.containsKey('producer_sample')
                    ? producer_meta.producer_sample
                    : null
                def producerRank = producer_meta.containsKey('producer_rank')
                    ? producer_meta.producer_rank
                    : null
                def producerOutputKey = producer_meta.producer_output_key?.toString()
                def sourceFormat = producer_meta.source_format?.toString()
                def artifactDigest = sha256File(predicted)
                if (!(producerMethod ==~ /[a-z0-9][a-z0-9_-]*/)) {
                    error('complex_prediction emitted an invalid producer method')
                }
                if (!(producerOutputKey && !producerOutputKey.startsWith('/') &&
                    !producerOutputKey.contains('..') && !producerOutputKey.contains('\\'))) {
                    error('complex_prediction emitted an unsafe producer output key')
                }
                if (!(producerSample == null || producerSample instanceof CharSequence)) {
                    error('complex_prediction producer_sample must be a string or unavailable')
                }
                if (!(producerRank == null || (producerRank instanceof Integer && producerRank >= 0))) {
                    error('complex_prediction producer_rank must be a non-negative integer or unavailable')
                }
                if (!(sourceFormat in ['pdb', 'mmcif'])) {
                    error('complex_prediction emitted an invalid source format')
                }
                if (producer_meta.producer_artifact_sha256 != artifactDigest) {
                    error('complex_prediction producer artifact digest disagrees with emitted bytes')
                }
                def representationNeutralOutputKey = producerOutputKey.replaceFirst(
                    /(?i)\.(?:pdb|cif|mmcif)$/, ''
                )
                def identityDomain = JsonOutput.toJson([
                    producer_method: producerMethod,
                    producer_output_key: representationNeutralOutputKey,
                    producer_rank: producerRank,
                    producer_sample: producerSample,
                ])
                def identityDigest = sha256Text(identityDomain)
                def producerKey = "frustrampnn/sources/${producerMethod}/${identityDigest}/${artifactDigest}.normalized.pdb"
                tuple([
                    parent_job_id: params.job_id.toString(),
                    parent_workflow_id: 'complex_prediction',
                    producer_stage: "complex_prediction:${producerMethod}:protein_only",
                    producer_candidate_key: producerKey,
                    producer_method: producerMethod,
                    producer_sample: producerSample,
                    producer_rank: producerRank,
                    producer_output_key: producerOutputKey,
                    producer_identity_sha256: identityDigest,
                    producer_artifact_sha256: artifactDigest,
                    source_format: sourceFormat,
                    analysis_scope: 'protein_only',
                    requiredness: requiredness,
                    checkpoint_id: 'megascale.ckpt',
                ], predicted)
            }
            PrepareComplexPredictionFrustraMPNNCandidate(raw_candidates)

            // Deduplicate representations only inside one explicit producer identity and
            // one normalized-byte equivalence class. Distinct methods/samples/ranks survive.
            deduplicated_candidates = PrepareComplexPredictionFrustraMPNNCandidate.out.prepared
                .map { candidate_meta, prepared_request, prepared_source ->
                    def normalized_sha = sha256File(prepared_source)
                    tuple(candidate_meta.producer_identity_sha256, normalized_sha,
                        candidate_meta, prepared_request, prepared_source)
                }
                .groupTuple(by: [0, 1])
                .map { producer_identity_sha, normalized_sha, candidate_metas, prepared_requests, prepared_sources ->
                    int preferredIndex = 0
                    candidate_metas.eachWithIndex { candidate_meta, index ->
                        def current = candidate_metas[preferredIndex]
                        def candidatePreference = "${candidate_meta.source_format == 'mmcif' ? '0' : '1'}:${candidate_meta.producer_artifact_sha256}"
                        def currentPreference = "${current.source_format == 'mmcif' ? '0' : '1'}:${current.producer_artifact_sha256}"
                        if (candidatePreference < currentPreference) preferredIndex = index
                    }
                    tuple(candidate_metas[preferredIndex], prepared_requests[preferredIndex], prepared_sources[preferredIndex])
                }
            MaterializeComplexPredictionFrustraMPNNCandidate(deduplicated_candidates)
            CanonicalFrustraMPNN(MaterializeComplexPredictionFrustraMPNNCandidate.out.prepared)
            frustrampnn_results = CanonicalFrustraMPNN.out.result
            PublishComplexPredictionFrustraMPNNCandidate(CanonicalFrustraMPNN.out.result)
            ReportComplexPredictionFrustraMPNNComplete(
                PublishComplexPredictionFrustraMPNNCandidate.out.marker.collect()
            )
        } else {
            if (!params.job_id) error('FrustraMPNN not-requested reporting requires --job_id')
            ReportComplexPredictionFrustraMPNNNotRequested(Channel.value(true))
        }

    emit:
        structures
        frustrampnn_results
}

workflow {
    COMPLEX_PREDICTION()
}
