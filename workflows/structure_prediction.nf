#!/usr/bin/env nextflow
/**
 * Structure Prediction Workflow
 * 
 * Standalone entry point for sequence-to-structure prediction using Boltz-2,
 * Protenix, or ESMFold2. NVIDIA Fold-CP uses its pinned OEM entry point.
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
import java.util.Arrays

include { structure_prediction_wf } from '../modules/structure_prediction.nf'
include { SchedulerFrustraMPNNParentFanout } from '../modules/frustrampnn_parent_fanout.nf'

// Workflow-specific param defaults
params.sequence_input = null
params.sequence_name = 'predicted'
params.pred_method = 'boltz'
params.num_parallel_jobs = 1
params.run_frustrampnn = true
params.frustrampnn_requiredness = 'required'
params.frustrampnn_settings_value_origin = null

final int FRUSTRAMPNN_SETTINGS_MAX_BYTES = 64 * 1024

def requireExactSettingsObject(value, Set<String> expectedKeys, String location) {
    if (!(value instanceof Map)) {
        throw new IllegalArgumentException("${location} must be an object")
    }
    def keys = value.keySet().collect { key ->
        if (!(key instanceof CharSequence)) {
            throw new IllegalArgumentException("${location} keys must be strings")
        }
        key.toString()
    } as Set
    if (keys != expectedKeys) {
        def missing = (expectedKeys - keys).sort()
        def unknown = (keys - expectedKeys).sort()
        throw new IllegalArgumentException(
            "${location} fields are not exact; missing=${missing}, unknown=${unknown}"
        )
    }
}

def requireCompleteFrustraMPNNSettings(value) {
    requireExactSettingsObject(value, [
        'schema_name', 'schema_version', 'batching_enabled', 'structures_per_job',
        'protein_selection',
        'source_structure', 'classification_policy',
    ] as Set, 'frustrampnn_settings')
    if (value.schema_name != 'frustrampnn_settings' || value.schema_version != 2 ||
        !(value.batching_enabled instanceof Boolean) ||
        !(value.structures_per_job instanceof Integer) ||
        value.structures_per_job < 1 || value.structures_per_job > 250) {
        throw new IllegalArgumentException('frustrampnn_settings v2 batching authority is invalid')
    }
    requireExactSettingsObject(value.protein_selection, [
        'mode', 'entities', 'regions', 'residues',
    ] as Set, 'frustrampnn_settings.protein_selection')
    requireExactSettingsObject(value.source_structure, [
        'selected_model_number', 'preferred_altloc',
    ] as Set, 'frustrampnn_settings.source_structure')
    requireExactSettingsObject(value.classification_policy, [
        'mode', 'high_max', 'minimal_min',
    ] as Set, 'frustrampnn_settings.classification_policy')
    if (!(value.protein_selection.entities instanceof Collection) ||
        !(value.protein_selection.regions instanceof Collection) ||
        !(value.protein_selection.residues instanceof Collection)) {
        throw new IllegalArgumentException(
            'frustrampnn_settings protein selectors must be arrays'
        )
    }
    value.protein_selection.entities.eachWithIndex { selector, index ->
        requireExactSettingsObject(selector, [
            'entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id',
        ] as Set, "frustrampnn_settings.protein_selection.entities[${index}]")
    }
    value.protein_selection.regions.eachWithIndex { selector, index ->
        requireExactSettingsObject(selector, [
            'entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id',
            'sequence_start', 'sequence_end',
        ] as Set, "frustrampnn_settings.protein_selection.regions[${index}]")
    }
    value.protein_selection.residues.eachWithIndex { selector, index ->
        requireExactSettingsObject(selector, [
            'entity_instance_id', 'source_entity_id', 'label_asym_id', 'auth_asym_id',
            'auth_seq_id', 'insertion_code', 'sequence_index',
        ] as Set, "frustrampnn_settings.protein_selection.residues[${index}]")
    }
    return value
}

def canonicalJsonValue(value) {
    if (value instanceof Map) {
        def ordered = new TreeMap<String, Object>()
        value.each { key, item ->
            if (!(key instanceof CharSequence)) {
                throw new IllegalArgumentException('FrustraMPNN settings keys must be strings')
            }
            ordered[key.toString()] = canonicalJsonValue(item)
        }
        return ordered
    }
    if (value instanceof Collection) {
        return value.collect { item -> canonicalJsonValue(item) }
    }
    if (value == null || value instanceof CharSequence || value instanceof Boolean) {
        return value
    }
    if (value instanceof Number) {
        if ((value instanceof Double || value instanceof Float) &&
            !Double.isFinite(value.doubleValue())) {
            throw new IllegalArgumentException('FrustraMPNN settings numbers must be finite')
        }
        return value
    }
    throw new IllegalArgumentException(
        "FrustraMPNN settings contain unsupported value type ${value.getClass().getName()}"
    )
}

def canonicalJsonBytes(value) {
    JsonOutput.toJson(canonicalJsonValue(value)).getBytes('UTF-8')
}

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

def producerIdentitySha256(producerMeta) {
    def outputKey = producerMeta.producer_output_key?.toString()
    if (!outputKey) {
        throw new IllegalArgumentException('structure prediction producer output key is missing')
    }
    def neutralKey = outputKey.replaceFirst(/(?i)\.(?:pdb|ent|cif|mmcif)$/, '')
    canonicalJsonBytes([
        producer_method: producerMeta.producer_method,
        producer_sample: producerMeta.producer_sample,
        producer_rank: producerMeta.producer_rank,
        producer_output_key: neutralKey,
    ]).with { bytes -> sha256Hex(bytes) }
}

process PrepareStructurePredictionFrustraMPNNCandidate {
    tag "frustrampnn-source:${candidate_meta.producer_stage}:${candidate_meta.producer_candidate_key}"
    stageInMode 'copy'
    publishDir { "${params.out_dir}/${new File(candidate_meta.producer_candidate_key.toString()).parent}" },
        mode: 'copy', pattern: 'canonical_source.pdb', saveAs: { new File(candidate_meta.producer_candidate_key.toString()).name }

    input:
    tuple val(candidate_meta), path(predicted_structure), val(settings_base64), \
        val(settings_sha256), val(settings_value_origin)

    output:
    tuple path('workflow_component_request_v3.json'), path('canonical_source.pdb'), \
        path('frustrampnn_structure_map_v1.json'), emit: prepared

    script:
    def metadataBase64 = JsonOutput.toJson(candidate_meta).getBytes('UTF-8').encodeBase64().toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_frustrampnn_candidate.py' \
      --source '${predicted_structure}' \
      --output-pdb canonical_source.pdb \
      --request workflow_component_request_v3.json \
      --metadata-base64 '${metadataBase64}' \
      --request-version 3 \
      --structure-map frustrampnn_structure_map_v1.json \
      --settings-base64 '${settings_base64}' \
      --settings-sha256 '${settings_sha256}' \
      --settings-value-origin '${settings_value_origin}'
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
            if (!(params.frustrampnn_settings instanceof CharSequence)) {
                error('Enabled FrustraMPNN requires complete typed --frustrampnn_settings')
            }
            if (!(params.frustrampnn_settings_value_origin instanceof CharSequence) ||
                !(params.frustrampnn_settings_value_origin.toString() in [
                    'bms_default', 'operator_request',
                ])) {
                error(
                    'Enabled FrustraMPNN requires --frustrampnn_settings_value_origin ' +
                    'to be bms_default or operator_request'
                )
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
            def canonical_candidates = structure_prediction_wf.out.canonical_structures.map { producer_meta, predicted ->
                def method = producer_meta.producer_method?.toString()
                def artifactKey = producer_meta.producer_artifact_key?.toString()
                if (!(method ==~ /[a-z0-9][a-z0-9_-]*/)) {
                    error("structure prediction emitted an invalid producer method")
                }
                if (!(artifactKey ==~ /[A-Za-z0-9][A-Za-z0-9._-]*/)) {
                    error("structure prediction emitted an unsafe producer artifact key")
                }
                def producerIdentity = producerIdentitySha256(producer_meta)
                def producerKey = "frustrampnn/sources/${method}/${artifactKey}.${producerIdentity.take(16)}.normalized.pdb"
                tuple([
                    parent_job_id: params.job_id.toString(),
                    parent_workflow_id: 'structure_prediction',
                    producer_stage: "structure_prediction:${method}",
                    producer_candidate_key: producerKey,
                    requiredness: requiredness,
                    producer_method: method,
                    producer_sample: producer_meta.producer_sample,
                    producer_rank: producer_meta.producer_rank,
                    producer_output_key: producer_meta.producer_output_key,
                    producer_identity_sha256: producerIdentity,
                    producer_artifact_sha256: producer_meta.producer_artifact_sha256,
                    source_format: producer_meta.source_format,
                ], predicted)
            }
            SchedulerFrustraMPNNParentFanout(
                canonical_candidates,
                Channel.value(params.job_id.toString()),
                Channel.value('structure_prediction'),
                Channel.value(params.frustrampnn_settings.toString()),
                Channel.value(settingsValueOrigin),
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

    if (!(params.pred_method in ['boltz', 'protenix', 'esmfold2', 'boltz_protenix'])) {
        error("--pred_method must be one of: boltz, protenix, esmfold2, boltz_protenix")
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
