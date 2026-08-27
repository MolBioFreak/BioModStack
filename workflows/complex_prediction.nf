#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.util.Arrays

params.frustrampnn_settings_value_origin = params.frustrampnn_settings_value_origin ?: null

def FRUSTRAMPNN_SETTINGS_MAX_BYTES = 64 * 1024

def requireExactSettingsObject(value, Set<String> expectedKeys, String location) {
    if (!(value instanceof Map)) throw new IllegalArgumentException("${location} must be an object")
    def keys = value.keySet().collect { key ->
        if (!(key instanceof CharSequence)) throw new IllegalArgumentException("${location} keys must be strings")
        key.toString()
    } as Set
    if (keys != expectedKeys) {
        throw new IllegalArgumentException(
            "${location} fields are not exact; missing=${expectedKeys - keys}, unknown=${keys - expectedKeys}"
        )
    }
}

def requireCompleteFrustraMPNNSettings(value) {
    requireExactSettingsObject(value, ['schema_name', 'schema_version', 'batching_enabled', 'structures_per_job', 'protein_selection', 'source_structure', 'classification_policy'] as Set, 'frustrampnn_settings')
    if (value.schema_name != 'frustrampnn_settings' || value.schema_version != 2 || !(value.batching_enabled instanceof Boolean) || !(value.structures_per_job instanceof Integer) || value.structures_per_job < 1 || value.structures_per_job > 250) {
        throw new IllegalArgumentException('frustrampnn_settings v2 batching authority is invalid')
    }
    requireExactSettingsObject(value.protein_selection, ['mode', 'entities', 'regions', 'residues'] as Set, 'frustrampnn_settings.protein_selection')
    requireExactSettingsObject(value.source_structure, ['selected_model_number', 'preferred_altloc'] as Set, 'frustrampnn_settings.source_structure')
    requireExactSettingsObject(value.classification_policy, ['mode', 'high_max', 'minimal_min'] as Set, 'frustrampnn_settings.classification_policy')
    if (!(value.protein_selection.entities instanceof Collection) || !(value.protein_selection.regions instanceof Collection) || !(value.protein_selection.residues instanceof Collection)) {
        throw new IllegalArgumentException('frustrampnn_settings protein selectors must be arrays')
    }
    value.protein_selection.entities.eachWithIndex { selector, index ->
        requireExactSettingsObject(selector, ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id'] as Set, "frustrampnn_settings.protein_selection.entities[${index}]")
    }
    value.protein_selection.regions.eachWithIndex { selector, index ->
        requireExactSettingsObject(selector, ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'sequence_start', 'sequence_end'] as Set, "frustrampnn_settings.protein_selection.regions[${index}]")
    }
    value.protein_selection.residues.eachWithIndex { selector, index ->
        requireExactSettingsObject(selector, ['entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index'] as Set, "frustrampnn_settings.protein_selection.residues[${index}]")
    }
    return value
}

def canonicalJsonValue(value) {
    if (value instanceof Map) {
        def ordered = new TreeMap<String, Object>()
        value.each { key, item ->
            if (!(key instanceof CharSequence)) throw new IllegalArgumentException('FrustraMPNN settings keys must be strings')
            ordered[key.toString()] = canonicalJsonValue(item)
        }
        return ordered
    }
    if (value instanceof Collection) return value.collect { item -> canonicalJsonValue(item) }
    if (value == null || value instanceof CharSequence || value instanceof Boolean) return value
    if (value instanceof Number) {
        if ((value instanceof Double || value instanceof Float) && !Double.isFinite(value.doubleValue())) {
            throw new IllegalArgumentException('FrustraMPNN settings numbers must be finite')
        }
        return value
    }
    throw new IllegalArgumentException("FrustraMPNN settings contain unsupported value type ${value.getClass().getName()}")
}

def canonicalJsonBytes(value) { JsonOutput.toJson(canonicalJsonValue(value)).getBytes('UTF-8') }

def sha256Hex(byte[] payload) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    digest.update(payload)
    digest.digest().encodeHex().toString()
}

def requestedFrustraMPNNSettingsHashPayload(value, String settingsValueOrigin) {
    def payload = canonicalJsonValue(value) as Map
    payload['settings_value_origin'] = settingsValueOrigin
    def selection = payload['protein_selection']
    if (selection instanceof Map && selection['regions'] instanceof Collection && selection['regions'].isEmpty()) {
        def compatibleSelection = new TreeMap<String, Object>()
        compatibleSelection.putAll(selection)
        compatibleSelection.remove('regions')
        payload['protein_selection'] = compatibleSelection
    }
    return payload
}

include { complex_prediction_wf } from '../modules/structure_prediction.nf'
include { CanonicalFrustraMPNNV2 } from '../modules/frustrampnn.nf'

def parseJsonFile(rawPath) {
    return new JsonSlurper().parse(file(rawPath))
}

def sha256File(rawPath) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    rawPath.toFile().withInputStream { stream ->
        stream.eachByte(1024 * 1024) { buffer, count ->
            digest.update(buffer, 0, count as int)
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
    tuple val(candidate_meta), path(predicted_structure), val(settings_base64), \
        val(settings_sha256), val(settings_value_origin)

    output:
    tuple val(candidate_meta), path('prepared_request.json'), path('prepared_source.pdb'), \
        path('prepared_structure_map.json'), emit: prepared

    script:
    def requestMetadata = [
        parent_job_id: candidate_meta.parent_job_id,
        parent_workflow_id: candidate_meta.parent_workflow_id,
        producer_stage: candidate_meta.producer_stage,
        producer_candidate_key: candidate_meta.producer_candidate_key,
        requiredness: candidate_meta.requiredness,
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
      --metadata-base64 '${metadataBase64}' \
      --request-version 3 \
      --structure-map prepared_structure_map.json \
      --settings-base64 '${settings_base64}' \
      --settings-sha256 '${settings_sha256}' \
      --settings-value-origin '${settings_value_origin}'
    """
}

process MaterializeComplexPredictionFrustraMPNNCandidate {
    tag "frustrampnn-complex-materialize:${candidate_meta.producer_method}:${candidate_meta.producer_artifact_sha256}"
    stageInMode 'copy'
    publishDir { "${params.out_dir}/${new File(candidate_meta.producer_candidate_key.toString()).parent}" },
        mode: 'copy', pattern: 'canonical_source.pdb', saveAs: { new File(candidate_meta.producer_candidate_key.toString()).name }

    input:
    tuple val(candidate_meta), path(prepared_request), path(prepared_source), path(prepared_structure_map)

    output:
    tuple path('workflow_component_request_v3.json'), path('canonical_source.pdb'), \
        path('frustrampnn_structure_map_v1.json'), emit: prepared

    script:
    """
    set -euo pipefail
    cp -L '${prepared_request}' workflow_component_request_v3.json
    cp -L '${prepared_source}' canonical_source.pdb
    cp -L '${prepared_structure_map}' frustrampnn_structure_map_v1.json
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
    stageInMode 'copy'

    input:
    path published_markers

    output:
    path 'frustrampnn_complete.reported'

    script:
    """
    set -euo pipefail
    mapfile -t outputs < <('${params.api_python}' \
      '${params.code_root}/scripts/validate_frustrampnn_publication_markers.py' \
      --job-root '${params.out_dir}' \
      published_*.json)
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
            if (!(params.frustrampnn_settings instanceof CharSequence)) {
                error('Enabled FrustraMPNN requires complete typed --frustrampnn_settings')
            }
            if (!(params.frustrampnn_settings_value_origin instanceof CharSequence) ||
                !(params.frustrampnn_settings_value_origin.toString() in ['bms_default', 'operator_request'])) {
                error('Enabled FrustraMPNN requires canonical --frustrampnn_settings_value_origin')
            }
            def settingsValueOrigin = params.frustrampnn_settings_value_origin.toString()
            def settingsBytes = params.frustrampnn_settings.toString().getBytes('UTF-8')
            if (settingsBytes.length > FRUSTRAMPNN_SETTINGS_MAX_BYTES) {
                error("frustrampnn_settings exceeds ${FRUSTRAMPNN_SETTINGS_MAX_BYTES} byte limit")
            }
            def rawSettings
            try {
                rawSettings = new JsonSlurper().parseText(params.frustrampnn_settings.toString())
                requireCompleteFrustraMPNNSettings(rawSettings)
            } catch (Exception exc) {
                error("Enabled FrustraMPNN requires complete typed --frustrampnn_settings: ${exc.message}")
            }
            def canonicalSettingsBytes = canonicalJsonBytes(rawSettings)
            if (!Arrays.equals(settingsBytes, canonicalSettingsBytes)) {
                error('frustrampnn_settings must be exact compact canonical JSON')
            }
            def settingsBase64 = settingsBytes.encodeBase64().toString()
            def settingsSha256 = sha256Hex(canonicalJsonBytes(
                requestedFrustraMPNNSettingsHashPayload(rawSettings, settingsValueOrigin)
            ))
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
                ], predicted, settingsBase64, settingsSha256, settingsValueOrigin)
            }
            PrepareComplexPredictionFrustraMPNNCandidate(raw_candidates)

            // Deduplicate representations only inside one explicit producer identity and
            // one normalized-byte equivalence class. Distinct methods/samples/ranks survive.
            deduplicated_candidates = PrepareComplexPredictionFrustraMPNNCandidate.out.prepared
                .map { candidate_meta, prepared_request, prepared_source, prepared_structure_map ->
                    def normalized_sha = sha256File(prepared_source)
                    tuple(candidate_meta.producer_identity_sha256, normalized_sha,
                        candidate_meta, prepared_request, prepared_source, prepared_structure_map)
                }
                .groupTuple(by: [0, 1])
                .map { producer_identity_sha, normalized_sha, candidate_metas, prepared_requests, prepared_sources, prepared_structure_maps ->
                    int preferredIndex = 0
                    candidate_metas.eachWithIndex { candidate_meta, index ->
                        def current = candidate_metas[preferredIndex]
                        def candidatePreference = "${candidate_meta.source_format == 'mmcif' ? '0' : '1'}:${candidate_meta.producer_artifact_sha256}"
                        def currentPreference = "${current.source_format == 'mmcif' ? '0' : '1'}:${current.producer_artifact_sha256}"
                        if (candidatePreference < currentPreference) preferredIndex = index
                    }
                    tuple(candidate_metas[preferredIndex], prepared_requests[preferredIndex], prepared_sources[preferredIndex], prepared_structure_maps[preferredIndex])
                }
            MaterializeComplexPredictionFrustraMPNNCandidate(deduplicated_candidates)
            CanonicalFrustraMPNNV2(MaterializeComplexPredictionFrustraMPNNCandidate.out.prepared)
            frustrampnn_results = CanonicalFrustraMPNNV2.out.result
            PublishComplexPredictionFrustraMPNNCandidate(CanonicalFrustraMPNNV2.out.result)
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
