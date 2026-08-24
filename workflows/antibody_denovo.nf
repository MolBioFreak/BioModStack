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
            "${location} fields are not exact; missing=${(expectedKeys - keys).sort()}, unknown=${(keys - expectedKeys).sort()}"
        )
    }
}

def requireCompleteFrustraMPNNSettings(value) {
    requireExactSettingsObject(value, ['schema_name', 'schema_version', 'protein_selection', 'source_structure', 'classification_policy'] as Set, 'frustrampnn_settings')
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


def extractSequenceFromPDB(pdb_file) {
    def aa_codes = [
        'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
        'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
        'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
        'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V'
    ]
    
    def chain_sequences = [:] as LinkedHashMap  // Preserve chain order
    def seen_residues = [:] as Map  // Per-chain residue tracking
    
    try {
        pdb_file.eachLine { line ->
            if (line.startsWith('ATOM') && line.length() >= 26 && line.substring(12, 16).trim() == 'CA') {
                def resName = line.substring(17, 20).trim()
                def resNum = line.substring(22, 26).trim()
                def chain = line.substring(21, 22)
                def key = "${chain}_${resNum}"
                
                if (!seen_residues.containsKey(chain)) {
                    seen_residues[chain] = [] as Set
                    chain_sequences[chain] = []
                }
                
                if (!seen_residues[chain].contains(key) && aa_codes.containsKey(resName)) {
                    seen_residues[chain].add(key)
                    chain_sequences[chain] << aa_codes[resName]
                }
            }
        }
    } catch (Exception e) {
        return "AAAA"
    }
    
    def result = chain_sequences.values().collect { it.join('') }.join(':')
    return result ?: "AAAA"
}

def parseFastaRecords(fasta_file) {
    def records = []
    def currentId = null
    def sequence = new StringBuilder()

    fasta_file.eachLine { line ->
        def trimmed = line?.trim()
        if (!trimmed) {
            return
        }
        if (trimmed.startsWith('>')) {
            if (currentId != null) {
                records << [id: currentId, sequence: sequence.toString()]
            }
            currentId = trimmed.substring(1).trim()
            sequence = new StringBuilder()
        } else {
            sequence.append(trimmed)
        }
    }

    if (currentId != null) {
        records << [id: currentId, sequence: sequence.toString()]
    }

    return records
}

def normalizeGpuCsvValue(raw) {
    if (raw == null) {
        return ''
    }
    if (raw instanceof Collection) {
        return raw.collect { value -> value?.toString()?.trim() }.findAll { value -> value }.join(',')
    }
    def text = raw.toString().trim()
    if (text.startsWith('[') && text.endsWith(']') && text.length() >= 2) {
        text = text.substring(1, text.length() - 1)
    }
    return text
}

def normalizeLoopSelectionValue(raw) {
    if (raw == null) {
        return null
    }
    def values = raw instanceof Collection ? raw : raw.toString().replace('[', '').replace(']', '').split(',')
    def normalized = values.collect { value -> value.toString().trim().toUpperCase() }.findAll { value -> value }
    return normalized ? normalized.join(',') : null
}

def normalizePpiFlowRegionModeValue(raw) {
    def value = (raw ?: 'selected_cdrs').toString().trim().toLowerCase()
    if (value in ['all_cdrs']) {
        return 'all_cdrs'
    }
    if (value in ['framework', 'framework_only']) {
        return 'framework_only'
    }
    if (value in ['all_antibody', 'whole_antibody', 'full_antibody']) {
        return 'all_antibody'
    }
    return 'selected_cdrs'
}

def normalizeProtenixMsaBackendValue(raw) {
    def value = (raw ?: 'auto').toString().trim().toLowerCase()
    if (value in ['local', 'colabfold_api']) {
        return value
    }
    return 'auto'
}

def resolveValidationBatchPlanValue(params, validatorRaw, useMsaRaw) {
    def requested = params.seqs_per_validation_job ?: params.seqs_per_boltz_job ?: 10
    int requestedSize
    try {
        requestedSize = requested.toString().toInteger()
    } catch (Exception ignored) {
        requestedSize = 10
    }
    requestedSize = Math.max(1, requestedSize)

    def validator = (validatorRaw ?: 'boltz2').toString().trim().toLowerCase()
    def useMsa = (useMsaRaw == true || useMsaRaw == 'true')
    def effectiveSize = requestedSize
    def reason = null

    if (validator == 'protenix' && useMsa) {
        def backend = normalizeProtenixMsaBackendValue(params.protenix_msa_backend)
        def rawCap = backend == 'local'
            ? (params.protenix_local_msa_max_seqs_per_validation_job ?: 1)
            : (params.protenix_msa_max_seqs_per_validation_job ?: 1)
        int cap
        try {
            cap = rawCap.toString().toInteger()
        } catch (Exception ignored) {
            cap = 1
        }
        cap = Math.max(1, cap)
        effectiveSize = Math.min(requestedSize, cap)
        if (effectiveSize < requestedSize) {
            reason = "Protenix MSA batching override: ${requestedSize} -> ${effectiveSize} seqs/job (${backend} backend)"
        }
    }

    return [requestedSize, effectiveSize, reason]
}

def normalizeArtifactPathsList(raw) {
    if (raw == null) {
        return []
    }
    def values = raw instanceof Collection ? raw : [raw]
    return values.findAll { value -> value != null }.collect { value -> value.toString() }
}

def buildValidationArtifactManifestJson(Map<String, ?> artifacts) {
    return groovy.json.JsonOutput.toJson(
        artifacts.collectEntries { key, value ->
            [(key): normalizeArtifactPathsList(value)]
        }
    )
}

def antibodySha256(rawPath) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    rawPath.toFile().withInputStream { stream ->
        stream.eachByte(1024 * 1024) { buffer, count ->
            digest.update(buffer, 0, count as int)
        }
    }
    return digest.digest().encodeHex().toString()
}

def antibodyStableProducerKey(rawValue, String field) {
    def value = rawValue?.toString()?.trim()
    if (!(value ==~ /[A-Za-z0-9][A-Za-z0-9._-]*/)) {
        throw new IllegalArgumentException("antibody_denovo:invalid_${field}")
    }
    return value
}

def antibodyCandidateId(parentJobId, producerStage, producerCandidateKey) {
    def domain = [
        'bms.frustrampnn.parent-candidate.v1',
        parentJobId?.toString()?.trim(),
        'antibody_denovo',
        producerStage?.toString()?.trim(),
        producerCandidateKey?.toString()?.trim(),
    ]
    if (domain.tail().any { !it || it.contains('\u0000') }) {
        throw new IllegalArgumentException('antibody_denovo:invalid_candidate_identity')
    }
    def digest = java.security.MessageDigest.getInstance('SHA-256')
        .digest(domain.join('\u0000').getBytes('UTF-8')).encodeHex().toString()[0..<32]
    return "${digest[0..<8]}-${digest[8..<12]}-${digest[12..<16]}-${digest[16..<20]}-${digest[20..<32]}"
}

/*
 * Single final-candidate adapter. Producer identity comes only from tuple
 * metadata. Paths, basenames, arrival order, and content hashes authenticate
 * bytes but never mint producer coordinates.
 */
def antibodyTerminalCandidate(rawMeta, rawPath, String terminalStage, String terminalMethod) {
    if (!(rawMeta instanceof Map)) {
        throw new IllegalArgumentException('antibody_denovo:missing_producer_metadata')
    }
    def producer = new LinkedHashMap(rawMeta as Map)
    def artifactAuthority = producer.producer_artifact_key ?:
        producer.producer_candidate_key ?: producer.artifact_key ?: producer.id
    def artifactKey = antibodyStableProducerKey(artifactAuthority, 'producer_artifact_key')
    def sampleAuthority = producer.containsKey('producer_sample') ? producer.producer_sample : artifactKey
    def producerSample = sampleAuthority == null ? null : antibodyStableProducerKey(sampleAuthority, 'producer_sample')
    def producerRank = producer.containsKey('producer_rank') ? producer.producer_rank : null
    if (producerRank != null && (!(producerRank instanceof Integer) || (producerRank as int) < 0)) {
        throw new IllegalArgumentException('antibody_denovo:invalid_producer_rank')
    }
    def method = producer.producer_method?.toString()?.trim() ?: terminalMethod
    if (!(method ==~ /[a-z0-9][a-z0-9_-]*/)) {
        throw new IllegalArgumentException('antibody_denovo:invalid_producer_method')
    }
    def outputKey = producer.producer_output_key?.toString()?.trim()
    if (!outputKey) outputKey = "antibody_denovo/${artifactKey}.pdb"
    if (outputKey.startsWith('/') || outputKey.contains('\\') || outputKey.contains('//') ||
        outputKey.split('/').any { it in ['', '.', '..'] } || !(outputKey.toLowerCase().endsWith('.pdb'))) {
        throw new IllegalArgumentException('antibody_denovo:invalid_producer_output_key')
    }
    def identityDomain = new TreeMap([
        producer_method: method,
        producer_output_key: outputKey.replaceFirst(/(?i)\.pdb$/, ''),
        producer_rank: producerRank,
        producer_sample: producerSample,
    ])
    def identityDigest = java.security.MessageDigest.getInstance('SHA-256')
        .digest(JsonOutput.toJson(identityDomain).getBytes('UTF-8')).encodeHex().toString()
    def artifactDigest = antibodySha256(rawPath)
    def producerStage = "antibody_denovo:${terminalStage}"
    def producerKey = "frustrampnn/sources/antibody_denovo/${identityDigest}/${artifactDigest}.normalized.pdb"
    def parentJobId = params.job_id?.toString()?.trim()
    if (!parentJobId) throw new IllegalArgumentException('antibody_denovo:missing_parent_job_id')
    def transformationLineage = producer.transformation_lineage instanceof Collection
        ? new ArrayList(producer.transformation_lineage as Collection)
        : []
    if (!transformationLineage.contains(terminalStage)) transformationLineage << terminalStage
    return tuple([
        candidate_id: antibodyCandidateId(parentJobId, producerStage, producerKey),
        parent_job_id: parentJobId,
        parent_workflow_id: 'antibody_denovo',
        producer_stage: producerStage,
        producer_candidate_key: producerKey,
        producer_artifact_key: artifactKey,
        producer_method: method,
        producer_sample: producerSample,
        producer_rank: producerRank,
        producer_output_key: outputKey,
        producer_identity_sha256: identityDigest,
        producer_artifact_sha256: artifactDigest,
        source_format: 'pdb',
        requiredness: 'required',
        checkpoint_id: 'megascale.ckpt',
        child_job_id: producer.child_job_id ?: producer.producer_child_job_id,
        iteration_id: producer.iteration_id ?: producer.iteration,
        iteration_rank: producer.iteration_rank,
        maturation_cycle: producer.maturation_cycle,
        maturation_variant: producer.maturation_variant,
        transformation_lineage: transformationLineage,
    ], rawPath)
}

def ensureParamDefault(params, key, value) {
    if (!params.containsKey(key)) {
        params[key] = value
    }
}

def paramValueOrDefault(params, String key, defaultValue) {
    if (!params.containsKey(key) || params[key] == null) {
        return defaultValue
    }
    def value = params[key]
    if (value instanceof CharSequence && value.toString().trim() == '') {
        return defaultValue
    }
    return value
}

def initializeAntibodyDenovoParams(params) {
    ensureParamDefault(params, 'framework_pdb', null)
    ensureParamDefault(params, 'run_id', null)
    ensureParamDefault(params, 'analysis_chain_id', 'all_chains')
    ensureParamDefault(params, 'filter_immunogenic', true)
    ensureParamDefault(params, 'run_immunogenicity_scoring', false)
    ensureParamDefault(params, 'run_affinity_maturation', false)
    ensureParamDefault(params, 'run_post_boltz_maturation', false)
    ensureParamDefault(params, 'run_post_validation_maturation', params.run_post_boltz_maturation)
    if (!params.run_post_boltz_maturation && params.run_post_validation_maturation) {
        params.run_post_boltz_maturation = params.run_post_validation_maturation
    }
    if (!params.containsKey('structure_validator') || !params.structure_validator) {
        params.structure_validator = 'boltz2'
    }
    if (!params.containsKey('exploration_mode') || params.exploration_mode == null) {
        params.exploration_mode = false
    }
    ensureParamDefault(params, 'pinned_gpus', null)
    if (!params.containsKey('job_id')) {
        params.job_id = "job_${new java.text.SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date())}"
    }
    ensureParamDefault(params, 'job_name', 'antibody_batch')
    ensureParamDefault(params, 'fampnn_constraint_mode', 'antibody')
    ensureParamDefault(params, 'rfantibody_design_loops_custom', null)
    ensureParamDefault(params, 'rfantibody_loop_length_ranges', null)
    ensureParamDefault(params, 'seq_design_fampnn', null)
    ensureParamDefault(params, 'seq_design_antifold', null)
    ensureParamDefault(params, 'seq_design_proteinmpnn', null)
    ensureParamDefault(params, 'seq_design_caliby', false)
    ensureParamDefault(params, 'enable_rfantibody_filter', false)
    ensureParamDefault(params, 'rfantibody_min_epitope_contacts', null)
    ensureParamDefault(params, 'rfantibody_max_epitope_distance', null)
    ensureParamDefault(params, 'rfantibody_contact_distance_threshold', 8.0)
    ensureParamDefault(params, 'rfantibody_min_target_contacts', null)
    ensureParamDefault(params, 'rfantibody_max_target_distance', null)
    ensureParamDefault(params, 'rfantibody_max_epitope_centroid_distance', null)
    ensureParamDefault(params, 'rfantibody_target_contact_distance_threshold', 12.0)
    ensureParamDefault(params, 'run_structure_validation', true)
    ensureParamDefault(params, 'interactive_gating', false)
    ensureParamDefault(params, 'interactive_swa', false)
    if (!params.containsKey('interactive_gate_stage') || !params.interactive_gate_stage) {
        params.interactive_gate_stage = 'post_fampnn'
    }
    ensureParamDefault(params, 'interactive_gate_continue', false)
    ensureParamDefault(params, 'protenix_allow_cpu_msa_fallback', false)
    ensureParamDefault(params, 'protenix_msa_max_seqs_per_validation_job', 1)
    ensureParamDefault(params, 'protenix_local_msa_max_seqs_per_validation_job', 1)
    ensureParamDefault(params, 'protenix_local_msa_timeout_seconds', 900)
    ensureParamDefault(params, 'target_model_number', null)
    ensureParamDefault(params, 'maturation_selected_loops', null)
    ensureParamDefault(params, 'selected_cdr_loops', null)
    ensureParamDefault(params, 'ppiflow_cdr_loops', null)
    ensureParamDefault(params, 'ppiflow_stage', null)
    ensureParamDefault(params, 'msa_sensitivity', null)
    ensureParamDefault(params, 'msa_min_depth_warning', null)
    ensureParamDefault(params, 'msa_min_depth_fail', null)

    if (!params.containsKey('ppiflow_selected_loops')) {
        params.ppiflow_selected_loops = normalizeLoopSelectionValue(
            params.get('maturation_selected_loops') ?: params.get('selected_cdr_loops') ?: params.get('ppiflow_cdr_loops')
        )
    } else {
        params.ppiflow_selected_loops = normalizeLoopSelectionValue(params.ppiflow_selected_loops)
    }
    def ppiflowGlobalRegionMode = params.containsKey('ppiflow_region_mode') ? params.get('ppiflow_region_mode') : null
    def ppiflowBackboneRegionMode = normalizePpiFlowRegionModeValue(
        paramValueOrDefault(params, 'ppiflow_backbone_region_mode', ppiflowGlobalRegionMode)
    )
    def ppiflowMaturationRegionMode = normalizePpiFlowRegionModeValue(
        paramValueOrDefault(params, 'ppiflow_maturation_region_mode', ppiflowGlobalRegionMode)
    )
    ensureParamDefault(params, 'cdr_positions_by_loop', [:])
    ensureParamDefault(params, 'manual_cdr_definitions', [])

    def ppiflowBackboneLoopScope = normalizeLoopSelectionValue(paramValueOrDefault(params, 'ppiflow_backbone_loop_scope', null)) ?: params.ppiflow_selected_loops
    def ppiflowMaturationLoopScope = normalizeLoopSelectionValue(paramValueOrDefault(params, 'ppiflow_maturation_loop_scope', null)) ?: params.ppiflow_selected_loops

    ensureParamDefault(params, 'run_ppiflow_backbone_refine', false)
    ensureParamDefault(params, 'run_ppiflow_maturation', params.run_maturation)
    ensureParamDefault(params, 'run_maturation', params.run_ppiflow_maturation ?: false)

    ensureParamDefault(params, 'parallel_mode', 'standard')
    ensureParamDefault(params, 'designs_per_job', 5)
    ensureParamDefault(params, 'seqs_per_job', 50)
    if (!params.containsKey('seqs_per_boltz_job') || params.seqs_per_boltz_job == null) {
        params.seqs_per_boltz_job = 10
    }
    if (!params.containsKey('seqs_per_validation_job') || params.seqs_per_validation_job == null) {
        params.seqs_per_validation_job = params.seqs_per_boltz_job ?: 10
    }

    def selectedInputArtifactClass = (params.selected_input_artifact_class ?: '').toString().trim().toLowerCase()
    def selectedInputDir = params.selected_input_dir ?: params.rfantibody_input_pdbs ?: params.fampnn_collected_pdbs
    def selectedInputIsSequenceConditioned = (
        selectedInputArtifactClass in ['sequence_designed_complex', 'validated_complex', 'post_validation_refined_complex']
        || (!selectedInputArtifactClass && params.fampnn_collected_pdbs != null)
    )

    return [
        ppiflowBackboneLoopScope: ppiflowBackboneLoopScope,
        ppiflowMaturationLoopScope: ppiflowMaturationLoopScope,
        ppiflowBackboneRegionMode: ppiflowBackboneRegionMode,
        ppiflowMaturationRegionMode: ppiflowMaturationRegionMode,
        selectedInputDir: selectedInputDir,
        selectedInputIsSequenceConditioned: selectedInputIsSequenceConditioned,
    ]
}

include { RFANTIBODY } from '../modules/rfantibody'
include { ANTIFOLD } from '../modules/antifold'
include { PrepFAMPNN ; RunFAMPNN ; FilterFAMPNN } from '../modules/fampnn'
include { RunCaliby ; FilterCaliby } from '../modules/caliby'
include { PrepMPNN ; RunMPNN as ProteinMPNNSeq } from '../modules/proteinmpnn'
include { ANTIBERTY_SCORE ; ANTIBERTY_FILTER_STRUCTURES } from '../modules/antiberty'
include { THERMOMPNN } from '../modules/thermompnn'
include { MergeComplex ; AF2_BACKPROP } from '../modules/af2_backprop'
include { IGGM_AFFINITY_MATURATION } from '../modules/iggm'
include { PrepBoltz ; PrepBoltzWithMSA ; RunBoltz } from '../modules/boltz'
include { BoltzFromSequence } from '../modules/structure_prediction'
include { ANARCII } from '../modules/utils/anarci'
include { PredictTargetComplex } from '../modules/predict_target_complex'
include { CanonicalFrustraMPNNV2 } from '../modules/frustrampnn'
include { BatchBoltzValidation ; BatchProtenixValidation ; BatchESMFold2Validation } from '../modules/antibody_batch'
include { PrepareAntibodyFrustraMPNNCandidate ; PublishAntibodyFrustraMPNNCandidate ; AggregateAndReportAntibodyFrustraMPNN ; ReportAntibodyFrustraMPNNNotRequested } from '../modules/antibody_frustrampnn_parent'
include { FinalizeSequentialValidationOutputs ; FinalizeTerminalAntibodyOutputs } from '../modules/antibody_output_finalization'
include { AntibodyOpenMMRefinement } from '../modules/antibody_openmm_refinement'


process SpawnRFantibodyJobs {
    label 'process_low'
    
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    
    input:
    path target_pdb
    val epitope_residues
    val framework_type
    val total_designs
    val designs_per_job
    val parent_job_id
    val batch_name
    
    output:
    path "spawn_rfa_result.json", emit: result
    
    script:
    def customLoopSpec = params.get('rfantibody_design_loops_custom')
    def loopLengthSpec = params.get('rfantibody_loop_length_ranges')
    def params_json = groovy.json.JsonOutput.toJson([
        rfantibody_diffusion_steps: params.rfantibody_diffusion_steps ?: 50,
        rfantibody_noise_scale_ca: params.rfantibody_noise_scale_ca ?: 1.0,
        rfantibody_noise_scale_frame: params.rfantibody_noise_scale_frame ?: 1.0,
        rfantibody_guide_scale: params.rfantibody_guide_scale ?: 10,
        rfantibody_ckpt_override: params.rfantibody_ckpt_override,
        rfantibody_debug_repo_overlay: params.rfantibody_debug_repo_overlay ?: false,
        antibody_design_loops: customLoopSpec ?: (params.antibody_design_loops ?: ''),
        rfantibody_loop_length_ranges: loopLengthSpec,
        antibody_chains: params.antibody_chains ?: 'H,L',
        pinned_gpus: params.pinned_gpus
    ])
    def frameworkArg = params.framework_pdb ? "--framework_pdb \"${params.framework_pdb}\" \\\n        " : ""
    """
    python3 ${params.code_root}/scripts/spawn_rfantibody_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --total_designs ${total_designs} \\
        --designs_per_job ${designs_per_job} \\
        --target_pdb "\$(readlink -f ${target_pdb})" \\
        --epitope_residues "${epitope_residues}" \\
        --framework_type "${framework_type}" \\
        ${frameworkArg}\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_rfa_result.json \\
        2>&1 | tee spawn_rfa.log
    """
}

process NormalizeTargetPDB {
    label 'process_low'

    publishDir "${params.out_dir}/input", mode: 'copy', pattern: "normalized_target.pdb"

    input:
    tuple val(meta), path(target_pdb)

    output:
    tuple val(meta), path("normalized_target.pdb"), emit: normalized

    script:
    def chainArg = params.antigen_chains ? "--chains \"${params.antigen_chains}\" \\\n        " : ""
    def modelArg = params.target_model_number ? "--model-number ${params.target_model_number} \\\n        " : ""
    def firstModelArg = params.target_model_number ? "" : "--first-model-only \\\n        "
    """
    python3 ${params.code_root}/scripts/normalize_target_pdb.py \\
        --input "\$(readlink -f ${target_pdb})" \\
        --output normalized_target.pdb \\
        ${firstModelArg}\
        ${modelArg}\
        ${chainArg}\
        2>&1 | tee normalize_target.log
    """
}

process StageRFantibodyBackbones {
    label 'process_low'

    publishDir "${params.out_dir}/collected/rfantibody_raw", mode: 'copy', pattern: "staged_output/*.pdb", saveAs: { fn -> fn.replace('staged_output/', '') }
    publishDir "${params.out_dir}/collected/rfantibody_raw", mode: 'copy', pattern: "staged_output/*.trb", saveAs: { fn -> fn.replace('staged_output/', '') }

    input:
    path pdb_files

    output:
    path "staged_output", emit: dir
    path "staged_output/*.pdb", emit: pdbs, optional: true
    path "staged_output/*.trb", emit: trbs, optional: true
    path "rfantibody_stage_summary.json", emit: summary

    script:
    """
    set -euo pipefail
    mkdir -p staged_output
    count=0
    for pdb in ${pdb_files}; do
        [ -f "\$pdb" ] || continue
        base="\$(basename "\$pdb")"
        dest="staged_output/\$base"
        if [ -e "\$dest" ]; then
            dest="staged_output/\${count}_\$base"
        fi
        cp "\$pdb" "\$dest"
        trb="\${pdb%.pdb}.trb"
        if [ -f "\$trb" ]; then
            trb_base="\$(basename "\$trb")"
            trb_dest="staged_output/\$trb_base"
            if [ -e "\$trb_dest" ]; then
                trb_dest="staged_output/\${count}_\$trb_base"
            fi
            cp "\$trb" "\$trb_dest"
        fi
        count=\$((count + 1))
    done
    cat > rfantibody_stage_summary.json <<EOF
{
  "total_designs": \$count
}
EOF
    """
}

process ScreenRFantibodyBackbones {
    label 'process_low'

    publishDir "${params.out_dir}/run/rfantibody_screen", mode: 'copy', pattern: '*.log'
    publishDir "${params.out_dir}/run/rfantibody_screen", mode: 'copy', pattern: 'screening_summary.json'
    publishDir "${params.out_dir}/collected/rfantibody_filtered", mode: 'copy', pattern: 'screened_output/*.pdb', saveAs: { fn -> fn.replace('screened_output/', '') }
    publishDir "${params.out_dir}/collected/rfantibody_filtered", mode: 'copy', pattern: 'screened_output/*.trb', saveAs: { fn -> fn.replace('screened_output/', '') }
    publishDir "${params.out_dir}/collected/rfantibody_filtered", mode: 'copy', pattern: 'screened_output/*.json', saveAs: { fn -> fn.replace('screened_output/', '') }
    publishDir "${params.out_dir}/collected/rfantibody_filtered", mode: 'copy', pattern: 'screened_output/*.csv', saveAs: { fn -> fn.replace('screened_output/', '') }

    input:
    path staged_dir
    val epitope_residues
    val antibody_chains
    val target_chain
    path reference_target_pdb

    output:
    path "screened_output", emit: dir
    path "screened_output/*.pdb", emit: pdbs, optional: true
    path "screened_output/*.trb", emit: trbs, optional: true
    path "screened_output/*.json", emit: jsons, optional: true
    path "screened_output/*.csv", emit: csvs, optional: true
    path "screening_summary.json", emit: summary
    path "screen_rfantibody_${task.index}.log", emit: log

    script:
    def minContactsArg = params.rfantibody_min_epitope_contacts != null ? "--min-epitope-contacts ${params.rfantibody_min_epitope_contacts}" : ""
    def maxDistanceArg = params.rfantibody_max_epitope_distance != null ? "--max-epitope-distance ${params.rfantibody_max_epitope_distance}" : ""
    def contactCutoffArg = params.rfantibody_contact_distance_threshold != null ? "--contact-distance-threshold ${params.rfantibody_contact_distance_threshold}" : ""
    def minTargetContactsArg = params.rfantibody_min_target_contacts != null ? "--min-target-contacts ${params.rfantibody_min_target_contacts}" : ""
    def maxTargetDistanceArg = params.rfantibody_max_target_distance != null ? "--max-target-distance ${params.rfantibody_max_target_distance}" : ""
    def maxEpitopeCentroidArg = params.rfantibody_max_epitope_centroid_distance != null ? "--max-epitope-centroid-distance ${params.rfantibody_max_epitope_centroid_distance}" : ""
    def targetContactCutoffArg = params.rfantibody_target_contact_distance_threshold != null ? "--target-contact-distance-threshold ${params.rfantibody_target_contact_distance_threshold}" : ""
    def screenReferenceScopeArg = params.rfantibody_screen_reference_scope ? "--screen-reference-scope \"${params.rfantibody_screen_reference_scope}\"" : ""
    def targetChainArg = target_chain ? "--target-chain \"${target_chain}\"" : ""
    """
    python3 ${params.code_root}/scripts/screen_rfantibody_backbones.py \\
        --pdb-dir "\$(readlink -f ${staged_dir})" \\
        --output-dir screened_output \\
        --summary-json screening_summary.json \\
        --epitope-residues "${epitope_residues ?: ''}" \\
        --antibody-chains "${antibody_chains ?: ''}" \\
        --reference-target-pdb "\$(readlink -f ${reference_target_pdb})" \\
        ${targetChainArg} \\
        ${minContactsArg} \\
        ${maxDistanceArg} \\
        ${contactCutoffArg} \\
        ${minTargetContactsArg} \\
        ${maxTargetDistanceArg} \\
        ${maxEpitopeCentroidArg} \\
        ${targetContactCutoffArg} \\
        ${screenReferenceScopeArg} \\
        2>&1 | tee screen_rfantibody_${task.index}.log
    """
}

process CheckRFantibodyYield {
    label 'process_low'

    input:
    val candidate_count

    output:
    path "rfantibody_yield_guard.ok", emit: ok

    script:
    def reportJson = groovy.json.JsonOutput.prettyPrint(
        groovy.json.JsonOutput.toJson([
            status: "completed_zero_yield",
            reason: "No RFantibody backbones survived coarse screening or backbone generation failed upstream",
            min_epitope_contacts: params.rfantibody_min_epitope_contacts,
            max_epitope_distance: params.rfantibody_max_epitope_distance,
            min_target_contacts: params.rfantibody_min_target_contacts,
            max_target_distance: params.rfantibody_max_target_distance,
            max_epitope_centroid_distance: params.rfantibody_max_epitope_centroid_distance,
            recommendation: "Inspect RFantibody review artifacts, relax the coarse screen, or pause after RFantibody to review backbones manually before FAMPNN."
        ])
    )
    """
    set -euo pipefail
    if [ "${candidate_count}" -le 0 ]; then
        mkdir -p "${params.out_dir}"
        cat > "${params.out_dir}/rfantibody_zero_yield_report.json" <<'JSON'
${reportJson}
JSON
        echo "ZERO-YIELD: no RFantibody backbones passed coarse screening" >&2
        exit 1
    fi
    touch rfantibody_yield_guard.ok
    """
}

process CheckZeroYield {
    label 'process_low'

    input:
    val candidate_count

    output:
    path "zero_yield_guard.ok", emit: ok

    script:
    def structureValidator = (params.structure_validator ?: 'boltz2').toString().toLowerCase()
    def validationLabel = structureValidator == 'protenix' ? 'Protenix' : 'Boltz2'
    def reportJson = groovy.json.JsonOutput.prettyPrint(
        groovy.json.JsonOutput.toJson([
            status: "completed_zero_yield",
            reason: "No sequences survived upstream filtering or upstream child jobs failed before ${validationLabel} validation",
            structure_validator: structureValidator,
            fampnn_psce_threshold: params.fampnn_psce_threshold ?: "default",
            fampnn_temperature: params.fampnn_temperature ?: "default",
            recommendation: "Check RFantibody/FAMPNN child logs, confirm target antigen preprocessing, or relax FAMPNN filtering"
        ])
    )
    """
    set -euo pipefail
    if [ "${candidate_count}" -le 0 ]; then
        mkdir -p "${params.out_dir}"
        cat > "${params.out_dir}/zero_yield_report.json" <<'JSON'
${reportJson}
JSON
        echo "ZERO-YIELD: no designs reached ${validationLabel} validation" >&2
        exit 1
    fi
    touch zero_yield_guard.ok
    """
}

process CheckFrustraYield {
    label 'process_low'

    input:
    val candidate_count

    output:
    path "frustrampnn_yield_guard.ok", emit: ok

    script:
    def reportJson = groovy.json.JsonOutput.prettyPrint(
        groovy.json.JsonOutput.toJson([
            status: "completed_zero_yield",
            stage: "frustrampnn",
            reason: "No final antibody candidates reached required FrustraMPNN analysis after upstream workflow completion",
            recommendation: "Inspect the terminal validation/maturation child outputs and resolve any upstream failure before retrying required FrustraMPNN analysis"
        ])
    )
    """
    set -euo pipefail
    if [ "${candidate_count}" -le 0 ]; then
        mkdir -p "${params.out_dir}"
        cat > "${params.out_dir}/frustrampnn_zero_yield_report.json" <<'JSON'
${reportJson}
JSON
        echo "ZERO-YIELD: no final candidates reached required FrustraMPNN" >&2
        exit 1
    fi
    touch frustrampnn_yield_guard.ok
    """
}

process CheckPPIFlowYield {
    label 'process_low'

    input:
    val candidate_count
    val stage_name

    output:
    path "ppiflow_yield_guard.ok", emit: ok

    script:
    def stageLabel = (
        stage_name == 'backbone_refine'
            ? 'PPIFlow backbone refinement'
            : stage_name == 'maturation'
                ? 'PPIFlow maturation'
                : 'PPIFlow refinement'
    )
    def reportJson = groovy.json.JsonOutput.prettyPrint(
        groovy.json.JsonOutput.toJson([
            status: "completed_zero_yield",
            stage: stage_name,
            reason: "No structures survived ${stageLabel}; all candidate inputs were skipped or child jobs emitted no refined PDBs",
            ppiflow_require_anchors: params.ppiflow_require_anchors != null ? params.ppiflow_require_anchors : true,
            maturation_anchor_threshold: paramValueOrDefault(params, 'maturation_anchor_threshold', -5.0),
            maturation_anchor_distance_cutoff: paramValueOrDefault(params, 'maturation_anchor_distance_cutoff', 12.0),
            recommendation: "Relax the anchor threshold, disable strict anchor requirement, narrow the loop scope, or inspect the published *_anchors.json files for skipped inputs."
        ])
    )
    """
    set -euo pipefail
    if [ "${candidate_count}" -le 0 ]; then
        mkdir -p "${params.out_dir}"
        cat > "${params.out_dir}/${stage_name}_zero_yield_report.json" <<'JSON'
${reportJson}
JSON
        echo "ZERO-YIELD: no structures survived ${stageLabel}" >&2
        exit 1
    fi
    touch ppiflow_yield_guard.ok
    """
}

process SpawnFAMPNNJobs {
    label 'process_low'
    
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    
    input:
    path pdb_dir
    val seqs_per_design
    val pdbs_per_job
    val parent_job_id
    val batch_name
    
    output:
    path "spawn_fampnn_result.json", emit: result
    
    script:
    def params_json = groovy.json.JsonOutput.toJson([
        fampnn_checkpoint: params.fampnn_checkpoint,
        fampnn_checkpoint_path: params.fampnn_checkpoint_path,
        fampnn_temperature: params.fampnn_temperature ?: 0.0001,
        fampnn_num_steps: params.fampnn_num_steps ?: 500,
        fampnn_psce_threshold: params.fampnn_psce_threshold ?: 0.15,
        fampnn_mutation_top_n: params.fampnn_mutation_top_n,
        fampnn_mutation_min_log_odds_delta: params.fampnn_mutation_min_log_odds_delta,
        fampnn_constraint_mode: params.fampnn_constraint_mode,
        rfantibody_design_loops_custom: params.get('rfantibody_design_loops_custom'),
        rfantibody_loop_length_ranges: params.get('rfantibody_loop_length_ranges'),
        pinned_gpus: params.pinned_gpus
    ])
    """
    python3 ${params.code_root}/scripts/spawn_fampnn_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir "${pdb_dir}" \\
        --pdbs_per_job ${pdbs_per_job} \\
        --seqs_per_design ${seqs_per_design} \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_fampnn_result.json \\
        2>&1 | tee spawn_fampnn.log
    """
}

process WaitForChildren {
    label 'process_low'
    
    input:
    val parent_job_id
    val stage_name
    val poll_interval_seconds
    val batch_name
    
    output:
    path "child_outputs.json", emit: child_outputs
    
    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output child_outputs.json
    """
}

process CollectChildOutputs {
    label 'process_low'
    
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.pdb"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.trb"
    publishDir "${params.out_dir}/collected/${stage_name}/traj", mode: 'copy', pattern: "traj/*.pdb", saveAs: { fn -> fn.replace('traj/', '') }
    
    input:
    path child_outputs_json
    val stage_name
    
    output:
    path "*.pdb", emit: pdbs, optional: true
    path "*.trb", emit: trbs, optional: true
    path "traj/*.pdb", emit: trajs, optional: true
    path "collection_manifest.json", emit: manifest
    
    script:
    """
    #!/usr/bin/env python3
    import json
    import shutil
    from pathlib import Path
    
    with open("${child_outputs_json}") as f:
        data = json.load(f)
    
    output_dirs = data.get("child_output_dirs", [])
    collected = []
    collected_trbs = []
    collected_trajs = []
    traj_dir = Path("traj")
    traj_dir.mkdir(exist_ok=True)
    
    for job_idx, output_dir in enumerate(output_dirs):
        dir_path = Path(output_dir)
        if not dir_path.exists():
            print(f"Warning: Output dir not found: {output_dir}")
            continue
        
        # Look for PDBs in standard locations
        for subdir in ["pdb_files", "run/rfantibody/output", "run/rfantibody", "run/fampnn/results", ""]:
            search_path = dir_path / subdir if subdir else dir_path
            if not search_path.exists():
                continue
            for pdb in search_path.glob("*.pdb"):
                # Add job index prefix to avoid filename collisions between child jobs
                dest = Path(f"job{job_idx}_{pdb.name}")
                if not dest.exists():
                    shutil.copy(pdb, dest)
                    collected.append(str(dest))
                    print(f"Collected: {pdb} -> {dest}")
                trb = pdb.with_suffix(".trb")
                if trb.exists():
                    trb_dest = Path(f"job{job_idx}_{trb.name}")
                    if not trb_dest.exists():
                        shutil.copy(trb, trb_dest)
                        collected_trbs.append(str(trb_dest))
                        print(f"Collected: {trb} -> {trb_dest}")
            traj_search = search_path / "traj"
            if traj_search.exists():
                for traj in traj_search.glob("*.pdb"):
                    traj_dest = traj_dir / f"job{job_idx}_{traj.name}"
                    if not traj_dest.exists():
                        shutil.copy(traj, traj_dest)
                        collected_trajs.append(str(traj_dest))
                        print(f"Collected trajectory: {traj} -> {traj_dest}")
    
    manifest = {
        "stage": "${stage_name}",
        "source_dirs": output_dirs,
        "collected_pdbs": collected,
        "collected_trbs": collected_trbs,
        "collected_trajectories": collected_trajs,
        "count": len(collected)
    }
    
    with open("collection_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    print(f"Collected {len(collected)} PDBs from {len(output_dirs)} child jobs")
    """
}

process WaitForFAMPNNChildren {
    label 'process_low'
    
    input:
    val parent_job_id
    val stage_name
    val poll_interval_seconds
    val batch_name
    
    output:
    path "child_outputs.json", emit: child_outputs
    
    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output child_outputs.json
    """
}

process CollectFAMPNNOutputs {
    label 'process_low'
    
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "job*.pdb"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "job*.json"
    
    input:
    path child_outputs_json
    val stage_name
    
    output:
    tuple path("job*.pdb"), path("job*.json"), emit: outputs
    path "collection_manifest.json", emit: manifest
    
    script:
    """
    #!/usr/bin/env python3
    import json
    import shutil
    from pathlib import Path
    
    with open("${child_outputs_json}") as f:
        data = json.load(f)
    
    output_dirs = data.get("child_output_dirs", [])
    collected_pdbs = []
    collected_jsons = []
    
    def candidate_output_dirs(raw_output_dir):
        raw = str(raw_output_dir)
        candidates = [Path(raw)]
        if raw.startswith("/var/lib/biomodstack/"):
            candidates.append(Path("/mnt/BioModStack") / raw.removeprefix("/var/lib/biomodstack/"))
        if raw.startswith("/mnt/BioModStack/"):
            candidates.append(Path("/var/lib/biomodstack") / raw.removeprefix("/mnt/BioModStack/"))
        seen = set()
        ordered = []
        for candidate in candidates:
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                ordered.append(candidate)
        return ordered

    for job_idx, output_dir in enumerate(output_dirs):
        dir_candidates = candidate_output_dirs(output_dir)
        dir_path = next((candidate for candidate in dir_candidates if candidate.exists()), None)
        if dir_path is None:
            print(f"Warning: Output dir not found: {output_dir} (checked: {[str(c) for c in dir_candidates]})")
            continue
        
        # Look for PDBs and JSONs in FAMPNN output locations
        for subdir in ["pdb_files", "collected/fampnn_filtered", "run/fampnn/results", "fampnn_output/samples", "results", ""]:
            search_path = dir_path / subdir if subdir else dir_path
            if not search_path.exists():
                continue
            
            # Collect PDBs
            for pdb in search_path.glob("*.pdb"):
                dest = Path(f"job{job_idx}_{pdb.name}")
                if not dest.exists():
                    shutil.copy(pdb, dest)
                    collected_pdbs.append(str(dest))
                    print(f"Collected PDB: {pdb} -> {dest}")
            
            # Collect JSONs (analysis results)
            for json_file in search_path.glob("*.json"):
                dest = Path(f"job{job_idx}_{json_file.name}")
                if not dest.exists():
                    shutil.copy(json_file, dest)
                    collected_jsons.append(str(dest))
    
    manifest = {
        "stage": "${stage_name}",
        "source_dirs": output_dirs,
        "collected_pdbs": collected_pdbs,
        "collected_jsons": collected_jsons,
        "pdb_count": len(collected_pdbs),
        "json_count": len(collected_jsons)
    }
    
    with open("collection_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    print(f"Collected {len(collected_pdbs)} PDBs and {len(collected_jsons)} JSONs from {len(output_dirs)} FAMPNN child jobs")
    """
}

process StageMaturationInputs {
    label 'process_low'

    publishDir "${params.out_dir}/ppiflow/input_pdbs", mode: 'copy', pattern: "*.pdb"

    input:
    path pdbs

    output:
    path "input_pdbs", emit: pdb_dir

    script:
    """
    mkdir -p input_pdbs
    cp ${pdbs} input_pdbs/ 2>/dev/null || true
    """
}

process SpawnMaturationJobs {
    label 'process_low'

    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"

    input:
    path pdb_dir
    val designs_per_job
    val parent_job_id
    val batch_name
    val stage_name
    val ppiflow_region_mode_input
    val ppiflow_selected_loops_input

    output:
    path "spawn_maturation_result.json", emit: result

    script:
    def stageRegionMode = ppiflow_region_mode_input ?: 'selected_cdrs'
    def payloadBackboneRegionMode = stage_name == 'backbone_refine'
        ? stageRegionMode
        : paramValueOrDefault(params, 'ppiflow_backbone_region_mode', 'selected_cdrs')
    def payloadMaturationRegionMode = stage_name == 'backbone_refine'
        ? paramValueOrDefault(params, 'ppiflow_maturation_region_mode', 'selected_cdrs')
        : stageRegionMode
    def selectedLoopsList = ppiflow_selected_loops_input
        ? ppiflow_selected_loops_input.toString().split(',').collect { it.toString().trim().toUpperCase() }.findAll { it }
        : null
    def selectedLoopScope = [
        region_mode: stageRegionMode,
        ppiflow_region_mode: stageRegionMode,
        ppiflow_backbone_region_mode: payloadBackboneRegionMode,
        ppiflow_maturation_region_mode: payloadMaturationRegionMode,
    ]
    if (selectedLoopsList) {
        selectedLoopScope.selected_loops = selectedLoopsList
        selectedLoopScope.ppiflow_selected_loops = selectedLoopsList
    }
    def params_json = groovy.json.JsonOutput.toJson([
        framework_type: params.framework_type,
        framework_pdb: params.framework_pdb,
        antibody_chains: params.antibody_chains,
        antigen_chains: params.antigen_chains,
        epitope_residues: params.epitope_residues ?: "",
        ppiflow_start_t: paramValueOrDefault(params, 'ppiflow_start_t', 0.8),
        ppiflow_samples_per_target: paramValueOrDefault(params, 'ppiflow_samples_per_target', 1),
        ppiflow_retry_limit: paramValueOrDefault(params, 'ppiflow_retry_limit', 10),
        ppiflow_config: params.ppiflow_config,
        ppiflow_checkpoint: params.ppiflow_checkpoint,
        ppiflow_checkpoint_path: params.ppiflow_checkpoint_path,
        ppiflow_weights_dir: params.ppiflow_weights_dir,
        ppiflow_rotamer_enrichment_enabled: params.ppiflow_rotamer_enrichment_enabled != null ? params.ppiflow_rotamer_enrichment_enabled : true,
        ppiflow_require_anchors: params.ppiflow_require_anchors != null ? params.ppiflow_require_anchors : true,
        ppiflow_rotamer_shell_distance: paramValueOrDefault(params, 'ppiflow_rotamer_shell_distance', paramValueOrDefault(params, 'ppiflow_rotamer_shell_cutoff', 20.0)),
        ppiflow_rotamer_shell_cutoff: paramValueOrDefault(params, 'ppiflow_rotamer_shell_distance', paramValueOrDefault(params, 'ppiflow_rotamer_shell_cutoff', 20.0)),
        ppiflow_relax_antibody_backbone_shell: paramValueOrDefault(params, 'ppiflow_relax_antibody_backbone_shell', false),
        ppiflow_objective_mode: paramValueOrDefault(params, 'ppiflow_objective_mode', null),
        ppiflow_objective_threshold: paramValueOrDefault(params, 'ppiflow_objective_threshold', null),
        ppiflow_antigen_chain: params.ppiflow_antigen_chain,
        ppiflow_heavy_chain: params.ppiflow_heavy_chain,
        ppiflow_light_chain: params.ppiflow_light_chain,
        maturation_anchor_threshold: paramValueOrDefault(params, 'maturation_anchor_threshold', -5.0),
        maturation_anchor_distance_cutoff: paramValueOrDefault(params, 'maturation_anchor_distance_cutoff', 12.0),
        maturation_min_improvement: paramValueOrDefault(params, 'maturation_min_improvement', -1.0),
        maturation_filter_percentile: params.maturation_filter_percentile,
        maturation_redesign_temp: params.maturation_redesign_temp,
        maturation_redesign_steps: params.maturation_redesign_steps,
        maturation_design_mode: params.maturation_design_mode,
        maturation_redesign_enabled: params.maturation_redesign_enabled,
        maturation_redesign_top_n: params.maturation_redesign_top_n,
        ppiflow_region_mode: stageRegionMode,
        ppiflow_backbone_region_mode: payloadBackboneRegionMode,
        ppiflow_maturation_region_mode: payloadMaturationRegionMode,
        ppiflow_selected_loops: ppiflow_selected_loops_input,
        selected_loop_scope: selectedLoopScope,
        cdr_positions_by_loop: params.get('cdr_positions_by_loop'),
        manual_cdr_definitions: params.get('manual_cdr_definitions'),
        stage_family: 'ppiflow',
        stage_mode: stage_name,
        ppiflow_stage_mode: stage_name,
        ppiflow_mode: stage_name == 'backbone_refine' ? 'backbone_refine' : 'maturation',
        seq_design_fampnn: true,
        seq_design_antifold: false,
        seq_design_proteinmpnn: false,
        run_anarcii_post: false,
        anarcii_execution_mode: params.anarcii_execution_mode ?: 'auto',
        anarcii_gpu_id: params.anarcii_gpu_id,
        anarcii_batch_size: params.anarcii_batch_size,
        anarcii_cpu_threads: params.anarcii_cpu_threads,
        fampnn_checkpoint: params.fampnn_checkpoint,
        fampnn_checkpoint_path: params.fampnn_checkpoint_path,
        fampnn_temperature: params.fampnn_temperature,
        fampnn_num_steps: params.fampnn_num_steps,
        fampnn_psce_threshold: params.fampnn_psce_threshold,
        fampnn_exclude_cys: params.fampnn_exclude_cys,
        fampnn_repack_last: params.fampnn_repack_last,
        fampnn_seq_only: params.fampnn_seq_only,
        fampnn_extra_config: params.fampnn_extra_config,
        thermompnn_max_ddg: params.thermompnn_max_ddg,
        structure_validator: params.structure_validator,
        rfantibody_design_loops_custom: params.get('rfantibody_design_loops_custom'),
        rfantibody_loop_length_ranges: params.get('rfantibody_loop_length_ranges'),
        pinned_gpus: params.pinned_gpus
    ])
    """
    python3 ${params.code_root}/scripts/spawn_maturation_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir "${pdb_dir}" \\
        --designs_per_job ${designs_per_job} \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --stage "${stage_name}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_maturation_result.json \\
        2>&1 | tee spawn_maturation.log
    """
}

process WaitForMaturationChildren {
    label 'process_low'

    input:
    val parent_job_id
    val stage_name
    val poll_interval_seconds
    val batch_name

    output:
    path "child_outputs.json", emit: child_outputs

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output child_outputs.json
    """
}

process CollectMaturationOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.pdb"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.txt"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.csv"

    input:
    path child_outputs_json
    val stage_name

    output:
    path "*.pdb", emit: pdbs, optional: true
    path "*.json", emit: jsons, optional: true
    path "*.txt", emit: txts, optional: true
    path "*.csv", emit: csvs, optional: true
    path "collection_manifest.json", emit: manifest

    script:
    """
    python3 ${params.code_root}/scripts/collect_maturation_outputs.py \\
        --child_outputs_json "${child_outputs_json}" \\
        --stage_name "${stage_name}" \\
        --manifest collection_manifest.json
    """
}

process StageValidatedMaturationInputs {
    label 'process_low'

    publishDir "${params.out_dir}/ppiflow/validated_input_pdbs", mode: 'copy', pattern: "*.pdb"

    input:
    path pdbs

    output:
    path "input_pdbs", emit: pdb_dir

    script:
    """
    mkdir -p input_pdbs
    cp ${pdbs} input_pdbs/ 2>/dev/null || true
    """
}

process SpawnValidatedMaturationJobs {
    label 'process_low'

    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"

    input:
    path pdb_dir
    val designs_per_job
    val parent_job_id
    val batch_name
    val stage_name
    val ppiflow_region_mode_input
    val ppiflow_selected_loops_input

    output:
    path "spawn_validated_maturation_result.json", emit: result

    script:
    def stageRegionMode = ppiflow_region_mode_input ?: 'selected_cdrs'
    def payloadBackboneRegionMode = stage_name == 'backbone_refine'
        ? stageRegionMode
        : paramValueOrDefault(params, 'ppiflow_backbone_region_mode', 'selected_cdrs')
    def payloadMaturationRegionMode = stage_name == 'backbone_refine'
        ? paramValueOrDefault(params, 'ppiflow_maturation_region_mode', 'selected_cdrs')
        : stageRegionMode
    def selectedLoopsList = ppiflow_selected_loops_input
        ? ppiflow_selected_loops_input.toString().split(',').collect { it.toString().trim().toUpperCase() }.findAll { it }
        : null
    def selectedLoopScope = [
        region_mode: stageRegionMode,
        ppiflow_region_mode: stageRegionMode,
        ppiflow_backbone_region_mode: payloadBackboneRegionMode,
        ppiflow_maturation_region_mode: payloadMaturationRegionMode,
    ]
    if (selectedLoopsList) {
        selectedLoopScope.selected_loops = selectedLoopsList
        selectedLoopScope.ppiflow_selected_loops = selectedLoopsList
    }
    def params_json = groovy.json.JsonOutput.toJson([
        framework_type: params.framework_type,
        framework_pdb: params.framework_pdb,
        antibody_chains: params.antibody_chains,
        antigen_chains: params.antigen_chains,
        epitope_residues: params.epitope_residues ?: "",
        ppiflow_start_t: paramValueOrDefault(params, 'ppiflow_start_t', 0.8),
        ppiflow_samples_per_target: paramValueOrDefault(params, 'ppiflow_samples_per_target', 1),
        ppiflow_retry_limit: paramValueOrDefault(params, 'ppiflow_retry_limit', 10),
        ppiflow_config: params.ppiflow_config,
        ppiflow_checkpoint: params.ppiflow_checkpoint,
        ppiflow_checkpoint_path: params.ppiflow_checkpoint_path,
        ppiflow_weights_dir: params.ppiflow_weights_dir,
        ppiflow_rotamer_enrichment_enabled: params.ppiflow_rotamer_enrichment_enabled != null ? params.ppiflow_rotamer_enrichment_enabled : true,
        ppiflow_require_anchors: params.ppiflow_require_anchors != null ? params.ppiflow_require_anchors : true,
        ppiflow_rotamer_shell_distance: paramValueOrDefault(params, 'ppiflow_rotamer_shell_distance', paramValueOrDefault(params, 'ppiflow_rotamer_shell_cutoff', 20.0)),
        ppiflow_rotamer_shell_cutoff: paramValueOrDefault(params, 'ppiflow_rotamer_shell_distance', paramValueOrDefault(params, 'ppiflow_rotamer_shell_cutoff', 20.0)),
        ppiflow_relax_antibody_backbone_shell: paramValueOrDefault(params, 'ppiflow_relax_antibody_backbone_shell', false),
        ppiflow_objective_mode: paramValueOrDefault(params, 'ppiflow_objective_mode', null),
        ppiflow_objective_threshold: paramValueOrDefault(params, 'ppiflow_objective_threshold', null),
        ppiflow_antigen_chain: params.ppiflow_antigen_chain,
        ppiflow_heavy_chain: params.ppiflow_heavy_chain,
        ppiflow_light_chain: params.ppiflow_light_chain,
        maturation_anchor_threshold: paramValueOrDefault(params, 'maturation_anchor_threshold', -5.0),
        maturation_anchor_distance_cutoff: paramValueOrDefault(params, 'maturation_anchor_distance_cutoff', 12.0),
        maturation_min_improvement: paramValueOrDefault(params, 'maturation_min_improvement', -1.0),
        maturation_filter_percentile: params.maturation_filter_percentile,
        maturation_redesign_temp: params.maturation_redesign_temp,
        maturation_redesign_steps: params.maturation_redesign_steps,
        maturation_design_mode: params.maturation_design_mode,
        maturation_redesign_enabled: params.maturation_redesign_enabled,
        maturation_redesign_top_n: params.maturation_redesign_top_n,
        ppiflow_region_mode: stageRegionMode,
        ppiflow_backbone_region_mode: payloadBackboneRegionMode,
        ppiflow_maturation_region_mode: payloadMaturationRegionMode,
        ppiflow_selected_loops: ppiflow_selected_loops_input,
        selected_loop_scope: selectedLoopScope,
        cdr_positions_by_loop: params.get('cdr_positions_by_loop'),
        manual_cdr_definitions: params.get('manual_cdr_definitions'),
        stage_family: 'ppiflow',
        stage_mode: stage_name,
        ppiflow_stage_mode: stage_name,
        ppiflow_mode: stage_name == 'backbone_refine' ? 'backbone_refine' : 'maturation',
        seq_design_fampnn: true,
        seq_design_antifold: false,
        seq_design_proteinmpnn: false,
        run_anarcii_post: false,
        anarcii_execution_mode: params.anarcii_execution_mode ?: 'auto',
        anarcii_gpu_id: params.anarcii_gpu_id,
        anarcii_batch_size: params.anarcii_batch_size,
        anarcii_cpu_threads: params.anarcii_cpu_threads,
        fampnn_checkpoint: params.fampnn_checkpoint,
        fampnn_checkpoint_path: params.fampnn_checkpoint_path,
        fampnn_temperature: params.fampnn_temperature,
        fampnn_num_steps: params.fampnn_num_steps,
        fampnn_psce_threshold: params.fampnn_psce_threshold,
        fampnn_exclude_cys: params.fampnn_exclude_cys,
        fampnn_repack_last: params.fampnn_repack_last,
        fampnn_seq_only: params.fampnn_seq_only,
        fampnn_extra_config: params.fampnn_extra_config,
        structure_validator: params.structure_validator,
        rfantibody_design_loops_custom: params.get('rfantibody_design_loops_custom'),
        rfantibody_loop_length_ranges: params.get('rfantibody_loop_length_ranges'),
        pinned_gpus: params.pinned_gpus
    ])
    """
    python3 ${params.code_root}/scripts/spawn_maturation_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir "${pdb_dir}" \\
        --designs_per_job ${designs_per_job} \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --stage "${stage_name}" \\
        --params_json '${params_json}' \\
        --api_url "${params.api_url}" \\
        --output spawn_validated_maturation_result.json \\
        2>&1 | tee spawn_validated_maturation.log
    """
}

process WaitForValidatedMaturationChildren {
    label 'process_low'

    input:
    val parent_job_id
    val stage_name
    val poll_interval_seconds
    val batch_name

    output:
    path "child_outputs.json", emit: child_outputs

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${stage_name}" \\
        --poll_interval ${poll_interval_seconds} \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output child_outputs.json
    """
}

process CollectValidatedMaturationOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.pdb"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.txt"
    publishDir "${params.out_dir}/collected/${stage_name}", mode: 'copy', pattern: "*.csv"

    input:
    path child_outputs_json
    val stage_name

    output:
    path "*.pdb", emit: pdbs, optional: true
    path "*.json", emit: jsons, optional: true
    path "*.txt", emit: txts, optional: true
    path "*.csv", emit: csvs, optional: true
    path "collection_manifest.json", emit: manifest

    script:
    """
    python3 ${params.code_root}/scripts/collect_maturation_outputs.py \\
        --child_outputs_json "${child_outputs_json}" \\
        --stage_name "${stage_name}" \\
        --manifest collection_manifest.json
    """
}

process StageStructureValidationArtifacts {
    label 'process_low'

    publishDir "${params.out_dir}/collected/structure_validation", mode: 'copy', pattern: "validation_artifacts/*"

    input:
    path pdb_list_file

    output:
    path "validation_artifacts", emit: dir

    script:
    """
    set -euo pipefail
    shopt -s nullglob
    mkdir -p validation_artifacts
    cp "${pdb_list_file}" pdbs.list

    copy_if_present() {
        local src="\$1"
        [ -f "\$src" ] || return 0
        cp -f "\$src" "validation_artifacts/\$(basename "\$src")" 2>/dev/null || true
    }

    while IFS= read -r pdb; do
        [ -n "\$pdb" ] || continue
        [ -f "\$pdb" ] || continue
        base="\$(basename "\$pdb")"
        stem="\${base%.*}"
        src_dir="\$(dirname "\$pdb")"
        cp -f "\$pdb" "validation_artifacts/\$base"

        copy_if_present "\${pdb%.*}.json"
        copy_if_present "\${pdb%.*}.cif"
        copy_if_present "\${pdb%.*}.npz"
        copy_if_present "\${pdb%.*}.pae.npz"
        copy_if_present "\$src_dir/\${stem}_full_data.json"
        copy_if_present "\$src_dir/full_data_\${stem}.json"

        if [[ "\$stem" =~ ^(.+)_sample_([0-9]+)\$ ]]; then
            prefix="\${BASH_REMATCH[1]}"
            sample_rank="\${BASH_REMATCH[2]}"
            copy_if_present "\$src_dir/\${prefix}_summary_confidence_sample_\${sample_rank}.json"
            copy_if_present "\$src_dir/\${prefix}_full_data_sample_\${sample_rank}.json"
        fi
    done < pdbs.list

    # Keep this gate-artifact staging step best-effort; the canonical outputs
    # already live under ${params.out_dir}/pdb_files/validated_designs.
    true
    """
}

process OpenInteractiveGate {
    label 'process_low'

    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: "*.json"

    input:
    val job_id
    val stage_name
    val gate_trigger
    val candidate_dir
    val raw_dir
    val filtered_dir
    val framework_type
    val antibody_chains
    val structure_validator

    output:
    path "gate_${stage_name}.json", emit: report

    script:
    def filteredArg = filtered_dir ? "--filtered_dir \"${filtered_dir}\"" : ""
    def rawArg = raw_dir ? "--raw_dir \"${raw_dir}\"" : ""
    """
    echo "Gate trigger ready: ${gate_trigger}" >&2
    python3 ${params.code_root}/scripts/open_stage_gate.py \\
        --job_id "${job_id}" \\
        --stage "${stage_name}" \\
        --candidate_dir "${candidate_dir}" \\
        ${rawArg} \\
        ${filteredArg} \\
        --framework_type "${framework_type ?: ''}" \\
        --antibody_chains "${antibody_chains ?: ''}" \\
        --structure_validator "${structure_validator ?: ''}" \\
        --api_url "${params.api_url}" \\
        --output "gate_${stage_name}.json"
    """
}

process OpenInteractivePayloadGate {
    label 'process_low'

    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: "*.json"

    input:
    val job_id
    val stage_name
    path payload_json

    output:
    path "gate_${stage_name}.json", emit: report

    script:
    """
    python3 ${params.code_root}/scripts/open_stage_gate.py \\
        --job_id "${job_id}" \\
        --stage "${stage_name}" \\
        --candidate_dir "" \\
        --payload_json "${payload_json}" \\
        --api_url "${params.api_url}" \\
        --output "gate_${stage_name}.json"
    """
}

process CheckProtenixMsaPreflight {
    label 'process_low'

    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: "protenix_msa_preflight.json"

    input:
    path pdbs

    output:
    path "protenix_msa_preflight.json", emit: report

    script:
    def msa_preferred_gpu_csv = normalizeGpuCsvValue(params.msa_preferred_gpus)
    def msa_excluded_gpu_csv = normalizeGpuCsvValue(params.msa_excluded_gpus)
    def msa_cpu_only_flag = (params.msa_use_gpu == false || params.msa_use_gpu == 'false') ? '--cpu-only' : ''
    """
    python3 ${params.code_root}/scripts/prep_protenix_batch.py \\
        --pdb_files ${pdbs} \\
        --out_json input.json \\
        --seeds "${params.protenix_seeds ?: '42'}"

    python3 ${params.code_root}/scripts/check_protenix_msa_preflight.py \\
        --input_json input.json \\
        --output protenix_msa_preflight.json \\
        --backend "${params.protenix_msa_backend ?: 'auto'}" \\
        --db-path "${params.msa_local_db}" \\
        --cache-dir "${params.msa_cache_dir}" \\
        ${msa_cpu_only_flag} \\
        --gpu-mode "${params.msa_gpu_mode ?: 'auto'}" \\
        --gpu-threshold ${params.msa_gpu_threshold ?: 80} \\
        ${msa_preferred_gpu_csv ? '--preferred-gpus "' + msa_preferred_gpu_csv + '"' : ''} \\
        ${msa_excluded_gpu_csv ? '--excluded-gpus "' + msa_excluded_gpu_csv + '"' : ''} \\
        --gpu-server-mode "${params.msa_gpu_server_mode ?: 'persistent'}" \\
        --gpu-server-wait-timeout ${params.msa_gpu_server_wait_timeout ?: 120} \\
        --gpu-server-db-load-mode ${params.msa_gpu_server_db_load_mode ?: 2} \\
        --gpu-server-startup-wait ${params.msa_gpu_server_startup_wait ?: 5.0} \\
        --small-max-tasks ${params.protenix_msa_small_max_tasks ?: 1} \\
        --small-max-protein-chains ${params.protenix_msa_small_max_protein_chains ?: 4} \\
        --small-max-total-residues ${params.protenix_msa_small_max_total_residues ?: 1500}
    """
}

process TriggerANARCIIAnnotationPostFAMPNNGate {
    label 'process_low'

    input:
    val job_id
    val include_children

    output:
    path "anarcii_trigger.log", emit: log

    script:
    """
    python3 ${params.code_root}/scripts/trigger_anarcii_annotation.py \\
        --job_id "${job_id}" \\
        --include_children "${include_children}" \\
        --api_url "${params.api_url}" \\
        2>&1 | tee anarcii_trigger.log
    """
}

process TriggerANARCIIAnnotationPostValidationGate {
    label 'process_low'

    input:
    val job_id
    val include_children

    output:
    path "anarcii_trigger.log", emit: log

    script:
    """
    python3 ${params.code_root}/scripts/trigger_anarcii_annotation.py \\
        --job_id "${job_id}" \\
        --include_children "${include_children}" \\
        --api_url "${params.api_url}" \\
        2>&1 | tee anarcii_trigger.log
    """
}

process TriggerANARCIIAnnotationFinal {
    label 'process_low'

    input:
    val job_id
    val include_children

    output:
    path "anarcii_trigger.log", emit: log

    script:
    """
    python3 ${params.code_root}/scripts/trigger_anarcii_annotation.py \\
        --job_id "${job_id}" \\
        --include_children "${include_children}" \\
        --api_url "${params.api_url}" \\
        2>&1 | tee anarcii_trigger.log
    """
}

process SpawnChildJobs {
    label 'process_low'
    
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/spawn", mode: 'copy', pattern: "*.json"
    
    input:
    path pdbs
    path msa_file
    val parent_job_id
    val batch_name
    val child_params_json
    val seqs_per_validation_job
    
    output:
    path "spawn_result.json", emit: result
    path "spawn.log", emit: log
    
    script:
    """
    #!/bin/bash
    set -euo pipefail
    
    # Create directory for PDBs and copy them
    mkdir -p pdb_input
    for f in *.pdb; do
        if [ -f "\$f" ]; then
            cp "\$f" pdb_input/
        fi
    done
    
    PDB_COUNT=\$(ls pdb_input/*.pdb 2>/dev/null | wc -l || echo 0)
    echo "Found \$PDB_COUNT PDB files to spawn as child jobs" | tee spawn.log
    
    if [ "\$PDB_COUNT" -eq 0 ]; then
        echo '{"spawned_jobs": 0, "status": "no_pdbs_found", "error": null}' > spawn_result.json
        echo "WARNING: No PDB files found to spawn" | tee -a spawn.log
        exit 0
    fi
    
    # Run the spawn script
    # Resolve absolute path of MSA file (staged by Nextflow as symlink)
    MSA_ABS_PATH=\$(readlink -f "${msa_file}" 2>/dev/null || realpath "${msa_file}" 2>/dev/null || echo "${msa_file}")
    echo "Resolved MSA path: \$MSA_ABS_PATH" | tee -a spawn.log
    
    # Persist MSA to parent output directory for reliability
    # (Nextflow work dirs may be cleaned before children run)
    mkdir -p "${params.out_dir}/msa"
    cp "\$MSA_ABS_PATH" "${params.out_dir}/msa/" 2>/dev/null || true
    MSA_PERSIST_PATH="${params.out_dir}/msa/\$(basename \$MSA_ABS_PATH)"
    echo "Persisted MSA to: \$MSA_PERSIST_PATH" | tee -a spawn.log
    
    # Pass ALL quality settings to child jobs
    echo "Forwarding quality settings to child jobs" | tee -a spawn.log
    
    python3 ${params.code_root}/scripts/spawn_antibody_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --pdb_dir pdb_input \\
        --batch_name "${batch_name}" \\
        --display_prefix "${params.job_name ?: 'Antibody'}" \\
        --msa_path "\$MSA_PERSIST_PATH" \\
        --params_json '${child_params_json}' \\
        --seqs_per_validation_job ${seqs_per_validation_job} \\
        --api_url "${params.api_url}" \\
        2>&1 | tee -a spawn.log
    
    SPAWN_EXIT=\${PIPESTATUS[0]}
    
    if [ "\$SPAWN_EXIT" -eq 0 ]; then
        CREATED_CHILDREN=\$(awk 'index(\$0, "[SPAWN] Created ") == 1 {count++} END {print count+0}' spawn.log)
        echo '{"spawned_jobs": '\$CREATED_CHILDREN', "status": "complete", "error": null}' > spawn_result.json
    else
        echo '{"spawned_jobs": 0, "status": "failed", "error": "spawn script exited with '\$SPAWN_EXIT'"}' > spawn_result.json
    fi
    
    echo "Spawn process complete" | tee -a spawn.log
    """
}

process WaitAndAggregateChildResults {
    label 'process_low'
    
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.json"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.cif"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.npz"
    publishDir "${params.out_dir}/pdb_files/aligned_error", mode: 'copy', pattern: "validated_designs/aligned_error/*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}", mode: 'copy', pattern: "aggregation_report.json"
    
    input:
    val parent_job_id
    val batch_name
    val expected_child_count
    val child_stage
    
    output:
    path "validated_designs/*.pdb", emit: pdbs, optional: true
    path "validated_designs/*.json", emit: scores, optional: true
    path "validated_designs/*.npz", emit: aligned_error, optional: true
    path "validated_designs/aligned_error/*.json", emit: aligned_error_json, optional: true
    path "aggregation_report.json", emit: report
    
    script:
    """
    #!/bin/bash
    set -euo pipefail
    
    echo "Waiting for ${expected_child_count} child validation jobs to complete..."
    
    mkdir -p validated_designs validated_designs/aligned_error intermediates/boltz intermediates/scores
    declare -A COPIED_BASENAMES

    choose_dest_name() {
        local base_dir="${'$'}1"
        local child_idx="${'$'}2"
        local filename="${'$'}3"
        local stem="\${filename%.*}"
        local ext=""
        if [ "\$stem" != "\$filename" ]; then
            ext=".\${filename##*.}"
        fi
        local candidate="\${base_dir}/\$filename"
        if [ ! -e "\$candidate" ]; then
            printf '%s\n' "\$candidate"
            return
        fi
        candidate="\${base_dir}/\${child_idx}_\$filename"
        if [ ! -e "\$candidate" ]; then
            printf '%s\n' "\$candidate"
            return
        fi
        local counter=2
        while true; do
            candidate="\${base_dir}/\${child_idx}_\${stem}_\${counter}\${ext}"
            if [ ! -e "\$candidate" ]; then
                printf '%s\n' "\$candidate"
                return
            fi
            counter=\$((counter + 1))
        done
    }
    
    # Wait for all children using the wait script
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "${child_stage}" \\
        --batch_name "${batch_name}" \\
        --output wait_result.json \\
        --api_url "${params.api_url}" \\
        2>&1 | tee wait.log
    
    # Parse wait result into a file so paths with spaces survive shell handling
    python3 -c "
import json
with open('wait_result.json') as f:
    data = json.load(f)
    for d in data.get('child_output_dirs', []):
        print(d)
" > child_dirs.txt

    mapfile -t CHILD_DIRS < child_dirs.txt
    
    echo "Collecting validated designs from child jobs..."
    
    TOTAL_PDBS=0
    TOTAL_CHILDREN=0
    
    for child_dir in "\${CHILD_DIRS[@]}"; do
        if [ -d "\$child_dir" ]; then
            TOTAL_CHILDREN=\$((TOTAL_CHILDREN + 1))
            child_idx="\$TOTAL_CHILDREN"
            
            # Search multiple possible locations where validator outputs may be published
            for subdir in "pdb_files/predictions" "pdb_files/aligned_error" "pdb_files" "run/boltz/predictions" "run/boltz" "run/protenix/predictions/aligned_error" "run/protenix/predictions" "run/protenix" ""; do
                search_path="\$child_dir/\$subdir"
                if [ -d "\$search_path" ]; then
                    while IFS= read -r -d '' artifact_path; do
                        basename=\$(basename "\$artifact_path")
                        if [ -n "\${COPIED_BASENAMES[\$basename]:-}" ]; then
                            continue
                        fi
                        dest_dir="validated_designs"
                        if [[ "\$artifact_path" == */aligned_error/* ]] && [[ "\$artifact_path" == *.json ]]; then
                            dest_dir="validated_designs/aligned_error"
                        fi
                        dest_path=\$(choose_dest_name "\$dest_dir" "\$child_idx" "\$basename")
                        cp "\$artifact_path" "\$dest_path" 2>/dev/null || true
                        COPIED_BASENAMES[\$basename]=1
                        case "\$artifact_path" in
                            *.pdb)
                                TOTAL_PDBS=\$((TOTAL_PDBS + 1))
                                ;;
                        esac
                    done < <(find "\$search_path" -maxdepth 1 -type f \\( -name '*.pdb' -o -name '*.cif' -o -name '*.json' -o -name '*.npz' \\) -print0)
                fi
            done
        fi
    done

    if [ "${expected_child_count}" -gt 0 ] && [ "\$TOTAL_CHILDREN" -lt "${expected_child_count}" ]; then
        echo "Warning: expected ${expected_child_count} child jobs but found \$TOTAL_CHILDREN" | tee -a wait.log
    fi
    
    echo "Collected \$TOTAL_PDBS validated PDBs from \$TOTAL_CHILDREN child jobs"
    
    # Create aggregation report
    cat > aggregation_report.json << EOF
{
    "parent_job_id": "${parent_job_id}",
    "batch_name": "${batch_name}",
    "children_processed": \$TOTAL_CHILDREN,
    "total_validated_designs": \$TOTAL_PDBS,
    "output_path": "${params.out_dir}/pdb_files",
    "status": "complete"
}
EOF

    # Trigger result ingestion for parent job (updates database)
    if [ \$TOTAL_PDBS -gt 0 ]; then
        mkdir -p "${params.out_dir}/pdb_files/validated_designs"
        while IFS= read -r -d '' staged_artifact; do
            rel_path="\${staged_artifact#validated_designs/}"
            dest_dir="${params.out_dir}/pdb_files/validated_designs/\$(dirname "\$rel_path")"
            mkdir -p "\$dest_dir"
            cp -f "\$staged_artifact" "\$dest_dir/"
        done < <(find validated_designs -type f -print0)
        cp -f aggregation_report.json "${params.out_dir}/aggregation_report.json"
        echo "Triggering result ingestion for parent job..."
        python3 ${params.code_root}/scripts/result_ingester.py \\
            --job_id "${parent_job_id}" \\
            --results_dir "${params.out_dir}" \\
            --api_url "${params.api_url}" \\
            2>&1 | tee ingest.log || echo "Warning: Ingestion had issues (non-fatal)"
    fi
    
    echo "Aggregation complete: \$TOTAL_PDBS designs ready for analytics"
    """
}

workflow ANTIBODY_DENOVO {
take:
target_pdb_ch // Channel: [meta, target_pdb]
epitope_residues // Value: epitope residues string (e.g., "A45,A46,A52")
framework_pdb_ch // Channel: [meta, framework_pdb] (optional)

main:
def workflowContext = initializeAntibodyDenovoParams(params)
def ppiflowBackboneLoopScope = workflowContext.ppiflowBackboneLoopScope
def ppiflowMaturationLoopScope = workflowContext.ppiflowMaturationLoopScope
def ppiflowBackboneRegionMode = workflowContext.ppiflowBackboneRegionMode
def ppiflowMaturationRegionMode = workflowContext.ppiflowMaturationRegionMode
def selectedInputDir = workflowContext.selectedInputDir
def selectedInputIsSequenceConditioned = workflowContext.selectedInputIsSequenceConditioned

if (params.run_affinity_maturation == true && params.run_frustrampnn == true) {
    error('antibody_denovo:frustrampnn_stale_post_iggm_structure: IgGM changes sequence, but no producer-bound post-IgGM structure revalidation is wired')
}


log.info("Step 1: Generating CDR backbones with RFantibody...")

def framework_path = params.framework_pdb ? file(params.framework_pdb) : file("${params.code_root}/lib/NO_FRAMEWORK")
framework_for_rfantibody = framework_pdb_ch
    .map { meta, pdb -> pdb }
    .ifEmpty(framework_path)

if (params.framework_type == 'nanobody' &&
    (!params.antibody_chains || params.antibody_chains.toString().trim() == 'H,L')) {
    params.antibody_chains = 'H'
    log.info("  Nanobody mode detected; defaulting antibody_chains to H for maturation/design stages")
}

def available_gpus = []
if (params.pinned_gpus) {
    available_gpus = params.pinned_gpus.toString().split(',').collect { it.trim().toInteger() }
} else if (params.gpu_id != null) {
    available_gpus = [params.gpu_id.toInteger()]
} else {
    available_gpus = [0] // Default to GPU 0
}

def total_designs = params.rfantibody_num_designs ?: 10
def num_gpus = available_gpus.size()
def designs_per_gpu = (total_designs / num_gpus).intValue()
def remainder = total_designs % num_gpus
def designs_per_job = params.designs_per_job ?: 5
def planned_child_jobs = Math.ceil(total_designs / designs_per_job.toDouble()).intValue()
def orchestrator_batch_name = params.batch_name
    ?: (params.job_id
        ? "${params.job_name ?: 'antibody_batch'}_${params.job_id}"
        : "${params.job_name ?: 'antibody_batch'}_${workflow.runName}")

def skip_rfantibody = params.skip_rfantibody == true || selectedInputDir != null
def skip_rfantibody_input_dir = selectedInputDir

if (skip_rfantibody && skip_rfantibody_input_dir) {
    log.info("  SKIP: Loading pre-existing backbone PDBs from ${skip_rfantibody_input_dir}")

    backbone_designs = Channel.fromPath("${skip_rfantibody_input_dir}/*.pdb")
        .collect()
        .map { pdbs ->
            log.info("  Loaded ${pdbs.size()} backbone PDBs")
            def meta = [id: params.name ?: "antibody"]
            [meta, pdbs]
        }
} else if (skip_rfantibody) {
    error("skip_rfantibody=true but no selected_input_dir-compatible directory was provided")
} else {
    def use_orchestrator = params.parallel_mode == 'full_orchestrator'

if (use_orchestrator) {
    log.info("  Orchestrator mode: Spawning ${planned_child_jobs} child job(s)")

    SpawnRFantibodyJobs(
        target_pdb_ch.map { meta, pdb -> pdb }.first(),
        epitope_residues ?: "",
        params.framework_type ?: "standard-fv",
        total_designs,
        designs_per_job,
        params.job_id ?: "unknown",
        orchestrator_batch_name
    )

    wait_trigger = SpawnRFantibodyJobs.out.result.map { it -> params.job_id ?: "unknown" }
    batch_name = orchestrator_batch_name
    WaitForChildren(
        wait_trigger,
        "rfantibody",
        30,  // poll_interval_seconds
        batch_name
    )

    CollectChildOutputs(
        WaitForChildren.out.child_outputs,
        "rfantibody"
    )

    CollectChildOutputs.out.pdbs.subscribe { pdbs ->
        try {
            def file_list = pdbs instanceof List ? pdbs : [pdbs]
            def count = file_list.size()
            log.info("  RFantibody via orchestrator: Collected ${count} PDBs from child jobs")
            def report_files = count > 50 ? file_list[0..49] : file_list
            def args = [params.job_id, "rfantibody", "complete"] + report_files.collect { it.toString() }
            def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
            proc.waitFor()
        } catch (Exception e) {
            println "Warning: Failed to report stage rfantibody: ${e.message}"
        }
    }

    backbone_designs = CollectChildOutputs.out.pdbs
        .flatten()
        .collect()
        .map { pdbs ->
        def meta = [id: params.name ?: "antibody"]
        [meta, pdbs]
    }

} else {
    log.info("  Multi-GPU mode: Splitting ${total_designs} designs across ${num_gpus} GPU(s): ${available_gpus}")

    rfantibody_parallel_inputs = Channel.from(available_gpus).map { gpu_id ->
        def idx = available_gpus.indexOf(gpu_id)
        def designs_for_this_gpu = designs_per_gpu + (idx < remainder ? 1 : 0)
        log.info("    GPU ${gpu_id}: ${designs_for_this_gpu} designs")
        [gpu_id, designs_for_this_gpu]
    }

    rfantibody_input = target_pdb_ch.combine(rfantibody_parallel_inputs).map { meta, pdb, gpu_id, designs_count ->
        def hotspots = epitope_residues ?: ""
        def split_meta = [id: "${meta.id}_gpu${gpu_id}"]
        [split_meta, pdb, hotspots, gpu_id, designs_count]
    }

    RFANTIBODY(rfantibody_input, framework_for_rfantibody)

    RFANTIBODY.out.designs.subscribe { meta, files ->
        try {
            def file_list = files instanceof List ? files : [files]
            def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
            def args = [params.job_id, "rfantibody", "complete"] + report_files.collect { it.toString() }
            def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
            proc.waitFor()
        } catch (Exception e) {
            println "Warning: Failed to report stage rfantibody: ${e.message}"
        }
    }

    backbone_designs = RFANTIBODY.out.designs.map { meta, files ->
        def base_id = meta.id.replaceAll(/_gpu\d+$/, '')
        def unified_meta = [id: base_id]
        [unified_meta, files]
    }
} // End of else block (standard mode)
} // End of skip_rfantibody else block

def interactiveGateEnabled = params.interactive_gating == true || params.interactive_swa == true
def rfantibodyRawDir = params.out_dir ? "${params.out_dir}/collected/rfantibody_raw" : null
def rfantibodyFilteredDir = params.out_dir ? "${params.out_dir}/collected/rfantibody_filtered" : null
def rfantibodyScreenEnabled = params.enable_rfantibody_filter == true
def shouldPauseAfterRFantibody = !params.skip_rfantibody && interactiveGateEnabled &&
    (params.interactive_gate_stage ?: 'post_fampnn') == 'post_rfantibody' &&
    params.interactive_gate_continue != true
def shouldScreenRFantibody = !selectedInputIsSequenceConditioned && (
    shouldPauseAfterRFantibody ||
    rfantibodyScreenEnabled ||
    params.rfantibody_min_epitope_contacts != null ||
    params.rfantibody_max_epitope_distance != null ||
    params.rfantibody_min_target_contacts != null ||
    params.rfantibody_max_target_distance != null ||
    params.rfantibody_max_epitope_centroid_distance != null
)

staged_rfantibody_pdbs = backbone_designs
    .map { meta, files -> files }
    .flatten()
    .collect()

StageRFantibodyBackbones(staged_rfantibody_pdbs)

if (shouldScreenRFantibody) {
    ScreenRFantibodyBackbones(
        StageRFantibodyBackbones.out.dir,
        epitope_residues ?: "",
        params.antibody_chains ?: "",
        params.antigen_chains ?: "",
        target_pdb_ch.map { meta, pdb -> pdb }.first()
    )
    rfantibody_ready_dir = ScreenRFantibodyBackbones.out.dir
    rfantibody_candidate_count = ScreenRFantibodyBackbones.out.summary.map { summary_file ->
        def data = new groovy.json.JsonSlurper().parse(summary_file)
        (data.passed_designs ?: 0) as Integer
    }
    rfantibodyCandidateDir = rfantibodyFilteredDir ?: rfantibodyRawDir
} else {
    rfantibody_ready_dir = StageRFantibodyBackbones.out.dir
    rfantibody_candidate_count = StageRFantibodyBackbones.out.summary.map { summary_file ->
        def data = new groovy.json.JsonSlurper().parse(summary_file)
        (data.total_designs ?: 0) as Integer
    }
    rfantibodyCandidateDir = rfantibodyRawDir
}

reviewed_backbone_designs = rfantibody_ready_dir.map { dir ->
    def pdbs = dir.toFile().listFiles()?.findAll { it.name.toLowerCase().endsWith('.pdb') }?.sort { it.name }?.collect { file(it.toString()) } ?: []
    def meta = [id: params.name ?: "antibody"]
    [meta, pdbs]
}

if (shouldPauseAfterRFantibody) {
    log.info("Interactive SWA gate: pausing after RFantibody backbone generation at ${rfantibodyCandidateDir}")
    OpenInteractiveGate(
        params.job_id ?: "unknown",
        "post_rfantibody",
        rfantibody_candidate_count,
        rfantibodyCandidateDir,
        rfantibodyRawDir ?: "",
        shouldScreenRFantibody ? (rfantibodyFilteredDir ?: "") : "",
        params.framework_type ?: "standard-fv",
        params.antibody_chains ?: "",
        params.structure_validator ?: "boltz2"
    )
    final_designs = Channel.empty()
    immunogenicity_scores = Channel.empty()
    stability_scores_early = Channel.empty()
    mutations = Channel.empty()
    backbone_designs = reviewed_backbone_designs
} else {
    if (params.skip_rfantibody && !shouldScreenRFantibody) {
        backbone_designs = reviewed_backbone_designs
    } else {
        CheckRFantibodyYield(rfantibody_candidate_count)
        backbone_designs = reviewed_backbone_designs
            .combine(CheckRFantibodyYield.out.ok)
            .map { meta, pdbs, _guard -> [meta, pdbs] }
    }

    def run_ppiflow_backbone_refine = (params.run_ppiflow_backbone_refine == true) ||
        (params.ppiflow_stage != null && params.ppiflow_stage.toString().toLowerCase() in ['post_rfantibody', 'backbone_refine', 'ppiflow_backbone_refine'])

    if (run_ppiflow_backbone_refine) {
        log.info("Step 1.5: Running PPIFlow backbone refinement on RFantibody outputs...")
        log.info("  Spawning backbone-refine child jobs (${params.maturation_designs_per_job ?: 4} PDBs per job)")
        def backboneRefineRegionMode = ppiflowBackboneRegionMode
        def backboneRefineSelectedLoops = backboneRefineRegionMode == 'selected_cdrs' ? ppiflowBackboneLoopScope : null
        log.info("  Backbone refinement region mode: ${backboneRefineRegionMode}")
        if (backboneRefineSelectedLoops) {
            log.info("  Backbone refinement loop scope: ${backboneRefineSelectedLoops}")
        }

        backbone_refine_inputs = backbone_designs
            .map { meta, pdbs -> pdbs }
            .flatten()
            .collect()

        StageMaturationInputs(backbone_refine_inputs)

        SpawnMaturationJobs(
            StageMaturationInputs.out.pdb_dir,
            params.maturation_designs_per_job ?: 4,
            params.job_id ?: "unknown",
            orchestrator_batch_name,
            "backbone_refine",
            backboneRefineRegionMode,
            backboneRefineSelectedLoops ?: ""
        )

        backbone_refine_wait_trigger = SpawnMaturationJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }
        backbone_refine_batch_name = orchestrator_batch_name

        WaitForMaturationChildren(
            backbone_refine_wait_trigger,
            "backbone_refine",
            30,
            backbone_refine_batch_name
        )

        CollectMaturationOutputs(
            WaitForMaturationChildren.out.child_outputs,
            "backbone_refine"
        )

        CollectMaturationOutputs.out.pdbs.subscribe { pdbs ->
            try {
                def file_list = pdbs instanceof List ? pdbs : [pdbs]
                def count = file_list.size()
                log.info("  PPIFlow backbone refinement: Collected ${count} PDBs from child jobs")
                def report_files = count > 50 ? file_list[0..49] : file_list
                def args = [params.job_id, "backbone_refine", "complete"] + report_files.collect { it.toString() }
                def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage backbone_refine: ${e.message}"
            }
        }

        ppiflow_backbone_candidate_count = CollectMaturationOutputs.out.manifest.map { manifest_json ->
            try {
                def parsed = new groovy.json.JsonSlurper().parse(new File(manifest_json.toString()))
                return (parsed.count_pdbs ?: parsed.count ?: 0) as int
            } catch (Exception e) {
                return 0
            }
        }
        CheckPPIFlowYield(ppiflow_backbone_candidate_count, "backbone_refine")

        backbone_designs = CollectMaturationOutputs.out.pdbs
            .map { pdbs ->
            def meta = [id: "ppiflow_backbone_refine"]
            [meta, pdbs]
        }
    }

    log.info("Step 2: Designing CDR sequences...")

    def run_fampnn = (params.seq_design_fampnn != null) ? params.seq_design_fampnn : true
    def run_antifold = (params.seq_design_antifold != null) ? params.seq_design_antifold : true
    def run_proteinmpnn = (params.seq_design_proteinmpnn != null) ? params.seq_design_proteinmpnn : true
    def run_caliby = params.seq_design_caliby == true
    def selectedInputStageFamily = (params.selected_input_stage_family ?: params.source_stage_family ?: '').toString().trim().toLowerCase()
    def sequenceDesignResumeSource = params.interactive_gate_continue == true || params.resume_job_id != null
    def fampnnUsesPreCollectedInputs = sequenceDesignResumeSource &&
        selectedInputIsSequenceConditioned &&
        selectedInputDir &&
        (!selectedInputStageFamily || selectedInputStageFamily == 'fampnn')
    def calibyUsesPreCollectedInputs = sequenceDesignResumeSource &&
        selectedInputIsSequenceConditioned &&
        selectedInputDir &&
        selectedInputStageFamily == 'caliby'

    fampnn_seqs = Channel.empty()
    caliby_seqs = Channel.empty()
    antifold_seqs = Channel.empty()
    proteinmpnn_seqs = Channel.empty()
    def fampnnRawDir = params.out_dir ? "${params.out_dir}/collected/fampnn" : null
    def fampnnFilteredDir = params.out_dir ? "${params.out_dir}/collected/fampnn_filtered" : null
    def fampnnCandidateDir = (fampnnUsesPreCollectedInputs && selectedInputDir) ? selectedInputDir.toString() : null
    def calibyRawDir = params.out_dir ? "${params.out_dir}/collected/caliby_raw" : null
    def calibyFilteredDir = params.out_dir ? "${params.out_dir}/collected/caliby" : null
    def calibyCandidateDir = (selectedInputIsSequenceConditioned && selectedInputDir && selectedInputStageFamily == 'caliby') ? selectedInputDir.toString() : null

    if (!run_fampnn && selectedInputIsSequenceConditioned && selectedInputDir && (!selectedInputStageFamily || selectedInputStageFamily == 'fampnn')) {
        log.info("  Sequence design skipped: Using pre-collected PDBs from ${selectedInputDir}")

        pre_collected_pdbs = Channel.fromPath("${selectedInputDir}/*.pdb")
            .collect()

        pre_collected_pdbs.subscribe { pdbs ->
            log.info("  Sequence design skipped: Loaded ${pdbs.size()} input PDBs")
        }

        fampnn_seqs = pre_collected_pdbs.map { pdbs ->
            def meta = [id: "selected_designs"]
            [meta, pdbs]
        }
        fampnnCandidateDir = selectedInputDir.toString()
    }

    if (run_fampnn) {
    if (fampnnUsesPreCollectedInputs && selectedInputDir) {
        log.info("  FAMPNN: Using pre-collected PDBs from ${selectedInputDir}")

        pre_collected_pdbs = Channel.fromPath("${selectedInputDir}/*.pdb")
            .collect()

        pre_collected_pdbs.subscribe { pdbs ->
            log.info("  FAMPNN: Loaded ${pdbs.size()} pre-collected PDBs")
        }

        fampnn_seqs = pre_collected_pdbs.map { pdbs ->
            def meta = [id: "fampnn_designs"]
            [meta, pdbs]
        }
        fampnnCandidateDir = selectedInputDir.toString()


    } else {
        log.info("  Running FAMPNN via GPU Orchestrator...")
        log.info("  Spawning child jobs (${params.pdbs_per_job ?: 5} PDBs per job, ${params.seqs_per_design ?: 20} seqs/design)")

        all_backbone_pdbs = backbone_designs
            .map { meta, files -> files }
            .flatten()
            .collect()

        fampnn_prep_input = all_backbone_pdbs.map { pdbs ->
            [pdbs, file("${params.code_root}/lib/empty-meta.jsonl")]
        }
        PrepFAMPNN(fampnn_prep_input)

        fampnn_pdb_dir = PrepFAMPNN.out.pdbs.collect().map { files ->
            files[0].parent.toString()
        }

        SpawnFAMPNNJobs(
            fampnn_pdb_dir,
            params.seqs_per_design ?: 20,
            params.pdbs_per_job ?: 5,
            params.job_id ?: "unknown",
            orchestrator_batch_name
        )

        fampnn_wait_trigger = SpawnFAMPNNJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }
        fampnn_batch_name = orchestrator_batch_name

        WaitForFAMPNNChildren(
            fampnn_wait_trigger,
            "fampnn",
            30,  // poll_interval
            fampnn_batch_name
        )

        CollectFAMPNNOutputs(
            WaitForFAMPNNChildren.out.child_outputs,
            "fampnn"
        )

        CollectFAMPNNOutputs.out.outputs.subscribe { items ->
            try {
                def (pdbs, jsons) = items
                def file_list = pdbs instanceof List ? pdbs : [pdbs]
                def count = file_list.size()
                log.info("  FAMPNN via orchestrator: Collected ${count} PDBs from child jobs")
                def report_files = count > 50 ? file_list[0..49] : file_list
                def args = [params.job_id, "fampnn", "complete"] + report_files.collect { it.toString() }
                def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage fampnn: ${e.message}"
            }
        }

        def filterEnabled = params.enable_fampnn_filter != false &&
                           (params.fampnn_max_psce != null || params.fampnn_max_residue_psce != null)

        if (filterEnabled) {
            def filterDesc = []
            if (params.fampnn_max_psce != null) filterDesc << "max avg PSCE: ${params.fampnn_max_psce}"
            if (params.fampnn_max_residue_psce != null) filterDesc << "max residue PSCE: ${params.fampnn_max_residue_psce}"
            log.info("  Filtering FAMPNN designs (${filterDesc.join(', ')})...")

            FilterFAMPNN(CollectFAMPNNOutputs.out.outputs)

            FilterFAMPNN.out.pdbs.subscribe { pdbs ->
                def count = pdbs instanceof List ? pdbs.size() : 1
                log.info("  FilterFAMPNN: ${count} designs passed filter")
            }

            fampnn_seqs = FilterFAMPNN.out.pdbs.map { pdbs ->
                def meta = [id: "fampnn_designs"]
                [meta, pdbs]
            }
            fampnnCandidateDir = fampnnFilteredDir ?: fampnnRawDir
        } else {
            log.info("  FAMPNN filtering disabled (enable with fampnn_max_psce or fampnn_max_residue_psce)")
            fampnn_seqs = CollectFAMPNNOutputs.out.outputs.map { pdbs, jsons ->
                def meta = [id: "fampnn_designs"]
                [meta, pdbs]
            }
            fampnnCandidateDir = fampnnRawDir
        }
    } // End of else block (standard FAMPNN mode)
}

    if (!run_caliby && selectedInputIsSequenceConditioned && selectedInputDir && selectedInputStageFamily == 'caliby') {
        log.info("  Caliby sequence design skipped: Using pre-collected PDBs from ${selectedInputDir}")

        pre_collected_caliby_pdbs = Channel.fromPath("${selectedInputDir}/*.pdb")
            .collect()

        pre_collected_caliby_pdbs.subscribe { pdbs ->
            log.info("  Caliby: Loaded ${pdbs.size()} pre-collected PDBs")
        }

        caliby_seqs = pre_collected_caliby_pdbs.map { pdbs ->
            def meta = [id: "caliby_designs"]
            [meta, pdbs]
        }
    }

    if (run_caliby) {
        if (calibyUsesPreCollectedInputs && selectedInputDir) {
            log.info("  Caliby: Using pre-collected PDBs from ${selectedInputDir}")

            pre_collected_caliby_pdbs = Channel.fromPath("${selectedInputDir}/*.pdb")
                .collect()

            pre_collected_caliby_pdbs.subscribe { pdbs ->
                log.info("  Caliby: Loaded ${pdbs.size()} pre-collected PDBs")
            }

            caliby_seqs = pre_collected_caliby_pdbs.map { pdbs ->
                def meta = [id: "caliby_designs"]
                [meta, pdbs]
            }
            calibyCandidateDir = selectedInputDir.toString()
        } else {
            log.info("  Running Caliby experimental sequence design...")
            RunCaliby(backbone_designs)
            RunCaliby.out.pdbs_jsons.subscribe { items ->
                try {
                    def (pdbs, jsons) = items
                    def file_list = pdbs instanceof List ? pdbs : [pdbs]
                    def count = file_list.size()
                    log.info("  Caliby: Produced ${count} designed structures")
                    def report_files = count > 50 ? file_list[0..49] : file_list
                    def args = [params.job_id, "caliby", "complete"] + report_files.collect { it.toString() }
                    def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage caliby: ${e.message}"
                }
            }

            def calibyFilterEnabled = params.enable_caliby_filter != false &&
                (params.caliby_max_potts_energy != null || params.caliby_min_sc_plddt != null || params.caliby_max_sc_rmsd != null)

            if (calibyFilterEnabled) {
                def filterDesc = []
                if (params.caliby_max_potts_energy != null) filterDesc << "max Potts energy: ${params.caliby_max_potts_energy}"
                if (params.caliby_min_sc_plddt != null) filterDesc << "min self-consistency pLDDT: ${params.caliby_min_sc_plddt}"
                if (params.caliby_max_sc_rmsd != null) filterDesc << "max self-consistency RMSD: ${params.caliby_max_sc_rmsd}"
                log.info("  Filtering Caliby designs (${filterDesc.join(', ')})...")

                FilterCaliby(RunCaliby.out.pdbs_jsons)

                FilterCaliby.out.pdbs.subscribe { pdbs ->
                    def count = pdbs instanceof List ? pdbs.size() : 1
                    log.info("  FilterCaliby: ${count} designs passed filter")
                }

                caliby_seqs = FilterCaliby.out.pdbs.map { pdbs ->
                    def meta = [id: "caliby_designs"]
                    [meta, pdbs]
                }
                calibyCandidateDir = calibyFilteredDir ?: calibyRawDir
            } else {
                log.info("  Caliby filtering disabled (enable with caliby_max_potts_energy, caliby_min_sc_plddt, or caliby_max_sc_rmsd)")
                caliby_seqs = RunCaliby.out.pdbs_jsons.map { pdbs, jsons ->
                    def meta = [id: "caliby_designs"]
                    [meta, pdbs]
                }
                calibyCandidateDir = calibyRawDir
            }
        }
    }

def shouldPauseAfterFampnn = interactiveGateEnabled &&
    (params.interactive_gate_stage ?: 'post_fampnn') == 'post_fampnn' &&
    params.interactive_gate_continue != true &&
    run_fampnn &&
    fampnnCandidateDir
def shouldPauseAfterCaliby = interactiveGateEnabled &&
    (params.interactive_gate_stage ?: 'post_fampnn') == 'post_caliby' &&
    params.interactive_gate_continue != true &&
    run_caliby &&
    calibyCandidateDir
def fampnn_gate_trigger = fampnn_seqs.map { meta, pdbs ->
    (pdbs instanceof Collection ? pdbs.size() : 1) as Integer
}
def caliby_gate_trigger = caliby_seqs.map { meta, pdbs ->
    (pdbs instanceof Collection ? pdbs.size() : 1) as Integer
}
def usingExistingCalibySequenceSource = !run_fampnn && !run_caliby && !run_antifold && !run_proteinmpnn &&
    selectedInputIsSequenceConditioned && selectedInputDir && selectedInputStageFamily == 'caliby'
def primarySequenceDesigns = (run_caliby || usingExistingCalibySequenceSource) ? caliby_seqs : fampnn_seqs
def primarySequenceCandidateDir = (run_caliby || usingExistingCalibySequenceSource) ? calibyCandidateDir : fampnnCandidateDir
def primarySequenceDesignerLabel = (run_caliby || usingExistingCalibySequenceSource) ? 'Caliby' : 'FAMPNN'

if (shouldPauseAfterFampnn || shouldPauseAfterCaliby) {
    def gateStageName = shouldPauseAfterCaliby ? "post_caliby" : "post_fampnn"
    def gateTrigger = shouldPauseAfterCaliby ? caliby_gate_trigger : fampnn_gate_trigger
    def gateCandidateDir = shouldPauseAfterCaliby ? calibyCandidateDir : fampnnCandidateDir
    def gateRawDir = shouldPauseAfterCaliby ? calibyRawDir : fampnnRawDir
    def gateFilteredDir = shouldPauseAfterCaliby ? (calibyFilteredDir ?: "") : (fampnnFilteredDir ?: "")
    log.info("Interactive SWA gate: pausing after ${shouldPauseAfterCaliby ? 'Caliby' : 'FAMPNN'} candidate collection at ${gateCandidateDir}")
    OpenInteractiveGate(
        params.job_id ?: "unknown",
        gateStageName,
        gateTrigger,
        gateCandidateDir,
        gateRawDir ?: "",
        gateFilteredDir,
        params.framework_type ?: "standard-fv",
        params.antibody_chains ?: "",
        params.structure_validator ?: "boltz2"
    )
    validated_structures = Channel.empty()
    stability_scores_early = Channel.empty()
} else {
    maturation_seqs = Channel.empty()
    def run_ppiflow_maturation = params.run_ppiflow_maturation != null ? params.run_ppiflow_maturation : params.run_maturation
    if (run_ppiflow_maturation == true) {
        def hasPrimarySequenceInputs = primarySequenceCandidateDir != null
        if (!hasPrimarySequenceInputs) {
            log.warn("PPIFlow maturation requested but no sequence-designed inputs are available; skipping maturation.")
            maturation_seqs = primarySequenceDesigns
        } else {
            log.info("Step 2.4: Running PPIFlow maturation on ${primarySequenceDesignerLabel} outputs...")
            log.info("  Spawning maturation child jobs (${params.maturation_designs_per_job ?: 4} PDBs per job)")
            def maturationRegionMode = ppiflowMaturationRegionMode
            def maturationSelectedLoops = maturationRegionMode == 'selected_cdrs' ? ppiflowMaturationLoopScope : null
            log.info("  PPIFlow maturation region mode: ${maturationRegionMode}")
                if (maturationSelectedLoops) {
                    log.info("  PPIFlow maturation loop scope: ${maturationSelectedLoops}")
                }

                maturation_inputs = primarySequenceDesigns
                    .map { meta, pdbs -> pdbs }
                    .flatten()
                    .collect()

                StageMaturationInputs(maturation_inputs)

                SpawnMaturationJobs(
                    StageMaturationInputs.out.pdb_dir,
                    params.maturation_designs_per_job ?: 4,
                    params.job_id ?: "unknown",
                    orchestrator_batch_name,
                    "maturation",
                    maturationRegionMode,
                    maturationSelectedLoops ?: ""
                )

                maturation_wait_trigger = SpawnMaturationJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }
                maturation_batch_name = orchestrator_batch_name

                WaitForMaturationChildren(
                    maturation_wait_trigger,
                    "maturation",
                    30,
                    maturation_batch_name
                )

                CollectMaturationOutputs(
                    WaitForMaturationChildren.out.child_outputs,
                    "maturation"
                )

                CollectMaturationOutputs.out.pdbs.subscribe { pdbs ->
                    try {
                        def file_list = pdbs instanceof List ? pdbs : [pdbs]
                        def count = file_list.size()
                        log.info("  PPIFlow maturation: Collected ${count} PDBs from child jobs")
                        def report_files = count > 50 ? file_list[0..49] : file_list
                        def args = [params.job_id, "maturation", "complete"] + report_files.collect { it.toString() }
                        def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                        proc.waitFor()
                    } catch (Exception e) {
                        println "Warning: Failed to report stage maturation: ${e.message}"
                    }
                }

                ppiflow_maturation_candidate_count = CollectMaturationOutputs.out.manifest.map { manifest_json ->
                    try {
                        def parsed = new groovy.json.JsonSlurper().parse(new File(manifest_json.toString()))
                        return (parsed.count_pdbs ?: parsed.count ?: 0) as int
                    } catch (Exception e) {
                        return 0
                    }
                }
                CheckPPIFlowYield(ppiflow_maturation_candidate_count, "maturation")

                maturation_seqs = CollectMaturationOutputs.out.pdbs
                    .map { pdbs ->
                    def meta = [id: "ppiflow_maturation"]
                    [meta, pdbs]
                }
            }
        } else {
            maturation_seqs = primarySequenceDesigns
        }

        if (run_antifold) {
            log.info("  Running AntiFold...")
            ANARCII(backbone_designs)
            ANTIFOLD(ANARCII.out.pdb_imgt)
            antifold_seqs = ANTIFOLD.out.sequences
        }

        if (run_proteinmpnn) {
            log.info("  Running ProteinMPNN...")
            mpnn_prep_input = backbone_designs.map { meta, pdbs ->
                 [pdbs, file("${params.code_root}/lib/empty-meta.jsonl")]
            }
            PrepMPNN(mpnn_prep_input)
            ProteinMPNNSeq(PrepMPNN.out.pdbs)
            proteinmpnn_seqs = ProteinMPNNSeq.out.pdbs_jsons.map { pdbs, jsons ->
                def meta = [id: "proteinmpnn_designs"]
                [meta, pdbs]
            }
        }

        pdb_designs = maturation_seqs.mix(proteinmpnn_seqs)
        if (!run_fampnn && !run_antifold && !run_proteinmpnn && !run_caliby) {
            log.info("  No sequence-design branch selected; carrying backbone-stage PDBs forward for downstream refinement/validation.")
            pdb_designs = backbone_designs
        }
        sequence_only_designs = Channel.empty()
        if (run_antifold) {
            if (params.exploration_mode == true) {
                log.warn("AntiFold emits FASTA only. Exploration-mode Boltz children accept PDBs only, so AntiFold candidates are skipped until serial refinement.")
            } else {
                sequence_only_designs = antifold_seqs.flatMap { meta, fasta ->
                    parseFastaRecords(fasta).collect { record ->
                        tuple(record.sequence, record.id ?: "${meta.id}_antifold")
                    }
                }
            }
        }

        pdb_designs_for_boltz = pdb_designs
        if (params.run_thermompnn == true) {
            log.info("Step 2.5: Scoring sequence stability with ThermoMPNN...")

            thermompnn_input = pdb_designs.flatMap { meta, pdbs ->
                def pdb_list = pdbs instanceof List ? pdbs : [pdbs]
                pdb_list.collect { pdb ->
                    def design_meta = [id: pdb.baseName]
                    [design_meta, pdb]
                }
            }

            THERMOMPNN(thermompnn_input)

            THERMOMPNN.out.stability.subscribe { meta, csv ->
                try {
                    def args = [params.job_id, "thermompnn", "complete", csv.toString()]
                    def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage thermompnn: ${e.message}"
                }
            }

            def thermompnn_with_pdb = THERMOMPNN.out.stability
                .join(thermompnn_input.map { meta, pdb -> tuple(meta, pdb) })
                .map { meta, csv, pdb ->
                    tuple(meta, pdb, csv)
                }

            if (params.thermompnn_max_ddg != null) {
                log.info("  Filtering by ThermoMPNN ddG <= ${params.thermompnn_max_ddg}...")

                stable_pdb_designs = thermompnn_with_pdb.filter { meta, pdb, csv ->
                    try {
                        def lines = csv.text.split('\n')
                        if (lines.size() > 1) {
                            def ddg = lines[1].split(',')[1]?.trim()
                            if (ddg && ddg != 'N/A' && ddg != 'ERROR') {
                                return Float.parseFloat(ddg) <= params.thermompnn_max_ddg
                            }
                        }
                    } catch (Exception e) {
                        log.warn("Could not parse ThermoMPNN output for ${meta.id}: ${e.message}")
                    }
                    return true
                }

                pdb_designs_for_boltz = stable_pdb_designs
                    .map { meta, pdb, csv -> pdb }
                    .collect()
                    .map { pdbs ->
                        def meta = [id: "thermompnn_filtered"]
                        [meta, pdbs]
                    }
            }

            stability_scores_early = THERMOMPNN.out.stability
        } else {
            log.info("ThermoMPNN stability scoring disabled (enable with run_thermompnn=true)")
            stability_scores_early = Channel.empty()
        }

        if (params.run_af2_backprop == true) {
            log.info("Step 2.6: Refining CDR sequences with AF2 Backprop...")

            af2_merge_input = pdb_designs_for_boltz
                .flatMap { meta, pdbs ->
                    def pdb_list = pdbs instanceof List ? pdbs : [pdbs]
                    pdb_list.collect { pdb ->
                        def design_meta = [id: pdb.baseName]
                        [design_meta, pdb]
                    }
                }
                .combine(target_pdb_ch.first().map { meta, pdb -> pdb })
                .map { meta, antibody_pdb, target_pdb ->
                    [meta, antibody_pdb, target_pdb]
                }

            MergeComplex(af2_merge_input)
            AF2_BACKPROP(MergeComplex.out.complex)

            AF2_BACKPROP.out.refined.subscribe { meta, pdb ->
                try {
                    def args = [params.job_id, "af2_backprop", "complete", pdb.toString()]
                    def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage af2_backprop: ${e.message}"
                }
            }

            pdb_designs_for_boltz = AF2_BACKPROP.out.refined
                .map { meta, pdb -> pdb }
                .collect()
                .map { pdbs ->
                    def meta = [id: "af2_refined"]
                    [meta, pdbs]
                }
        }

        def structure_validator = (params.structure_validator ?: 'boltz2').toString().toLowerCase()
        if (!(structure_validator in ['boltz2', 'protenix', 'esmfold2'])) {
            log.warn("Unknown structure_validator '${structure_validator}', defaulting to boltz2")
            structure_validator = 'boltz2'
        }
        def validation_stage_name = "structure_validation"
        def validation_label = structure_validator == 'protenix' ? 'Protenix' : (structure_validator == 'esmfold2' ? 'ESMFold2' : 'Boltz2')

        log.info("Step 3: Validating structures with ${validation_label}...")

        if (params.run_structure_validation != false) {
            pdb_design_sequences = pdb_designs_for_boltz
                .flatMap { meta, files ->
                    def pdbs = files instanceof List ? files : [files]
                    pdbs.collect { pdb ->
                        def sequence = extractSequenceFromPDB(pdb)
                        tuple(sequence, pdb.baseName, pdb)
                    }
                }

            def supportsSequenceOnlyBoltzValidation = structure_validator == 'boltz2' && params.exploration_mode != true
            if (run_antifold && !supportsSequenceOnlyBoltzValidation) {
                log.warn("AntiFold emits FASTA only. ${validation_label} validation currently runs on PDB-backed candidates only, so AntiFold sequence-only designs are skipped.")
            } else if (run_antifold && supportsSequenceOnlyBoltzValidation) {
                log.info("  AntiFold sequence-only candidates will use MSA-free Boltz validation to avoid shared-MSA identity drift.")
            }

            design_sequences = supportsSequenceOnlyBoltzValidation
                ? pdb_design_sequences.mix(sequence_only_designs.map { sequence, name -> tuple(sequence, name, null) })
                : pdb_design_sequences

            design_sequence_count = design_sequences.count()
            CheckZeroYield(design_sequence_count)
            design_sequences = design_sequences
                .combine(CheckZeroYield.out.ok)
                .map { sequence, name, pdb, _guard -> tuple(sequence, name, pdb) }

            def msa_file_ch = Channel.value(file("${params.code_root}/lib/NO_MSA"))
            if (structure_validator == 'protenix') {
                log.info("Protenix validation uses its built-in MSA/update pipeline; skipping parent GenerateLocalMSA step.")
            } else if (structure_validator == 'esmfold2') {
                log.info("ESMFold2 validation is MSA-free; skipping parent GenerateLocalMSA step.")
            } else {
                log.info("Boltz validation is running without a shared representative MSA so each candidate is validated without cross-sequence MSA contamination.")
            }

            def protenix_use_msa = (params.protenix_use_msa == true || params.protenix_use_msa == 'true' || params.protenix_use_msa == null)
            def validationBatchPlan = resolveValidationBatchPlanValue(params, structure_validator, protenix_use_msa)
            def effectiveValidationBatchSize = validationBatchPlan[1] as int
            if (validationBatchPlan[2]) {
                log.info("  ${validationBatchPlan[2]}")
            }
            def shouldPreflightProtenixMsa = structure_validator == 'protenix' &&
                protenix_use_msa &&
                !(params.protenix_allow_cpu_msa_fallback == true || params.protenix_allow_cpu_msa_fallback == 'true')
            def protenixMsaReadySignal = Channel.value(true)

            if (shouldPreflightProtenixMsa) {
                protenix_msa_preflight_inputs = pdb_design_sequences
                    .map { sequence, name, pdb -> pdb }
                    .collect()
                    .map { pdbs -> pdbs.take(effectiveValidationBatchSize) }

                CheckProtenixMsaPreflight(protenix_msa_preflight_inputs)

                protenixMsaReadySignal = CheckProtenixMsaPreflight.out.report
                    .map { report_file ->
                        def data = new groovy.json.JsonSlurper().parse(report_file)
                        ((data.allow_validation ?: false) as Boolean) ? true : null
                    }
                    .filter { it != null }

                protenix_msa_gate_payload = CheckProtenixMsaPreflight.out.report
                    .map { report_file ->
                        def data = new groovy.json.JsonSlurper().parse(report_file)
                        ((data.allow_validation ?: false) as Boolean) ? null : report_file
                    }
                    .filter { it != null }

                OpenInteractivePayloadGate(
                    params.job_id ?: "unknown",
                    "pre_protenix_msa",
                    protenix_msa_gate_payload
                )
            }

            if (params.exploration_mode == true) {
                log.info("Exploration Mode: Spawning child jobs for parallel GPU processing...")

                collected_pdbs = pdb_designs_for_boltz
                    .flatMap { meta, files ->
                        def fileList = files instanceof List ? files : [files]
                        return fileList
                    }
                    .collect()

                msa_for_spawn = msa_file_ch
                spawn_validation_inputs = collected_pdbs
                    .combine(msa_for_spawn)
                    .map { payload -> tuple(payload[0..-2], payload[-1]) }
                    .combine(protenixMsaReadySignal)
                    .map { payload -> tuple(payload[0], payload[1]) }

                def parent_id = params.job_id ?: "unknown_${System.currentTimeMillis()}"
                def batch = orchestrator_batch_name

                def child_params = groovy.json.JsonOutput.toJson([
                    structure_validator: structure_validator,
                    boltz_sampling_steps: params.boltz_sampling_steps ?: 200,
                    boltz_recycling_steps: params.boltz_recycling_steps ?: 3,
                    boltz_num_samples: params.boltz_num_samples ?: 1,
                    boltz_use_potentials: params.boltz_use_potentials ?: false,
                    boltz_use_msa: false,
                    boltz_step_scale: params.boltz_step_scale,
                    protenix_model_weights: params.protenix_model_weights,
                    protenix_seeds: params.protenix_seeds,
                    protenix_n_sample: params.protenix_n_sample,
                    protenix_n_step: params.protenix_n_step,
                    protenix_n_cycle: params.protenix_n_cycle,
                    protenix_use_msa: params.protenix_use_msa,
                    protenix_msa_backend: params.protenix_msa_backend,
                    protenix_use_template: params.protenix_use_template,
                    protenix_enable_cache: params.protenix_enable_cache,
                    protenix_enable_fusion: params.protenix_enable_fusion,
                    target_pdb: params.target_pdb,
                    target_model_number: params.target_model_number,
                    antibody_chains: params.antibody_chains,
                    antigen_chains: params.antigen_chains,
                    target_chains: params.antigen_chains,
                    protenix_binder_source_chains: params.protenix_binder_source_chains ?: params.antibody_chains,
                    protenix_auto_oom_retry: params.protenix_auto_oom_retry,
                    protenix_oom_retry_attempts: params.protenix_oom_retry_attempts,
                    msa_preset: params.msa_preset,
                    msa_use_gpu: params.msa_use_gpu,
                    msa_local_db: params.msa_local_db,
                    msa_cache_dir: params.msa_cache_dir,
                    msa_threads: params.msa_threads,
                    colabfold_api_host: params.colabfold_api_host,
                    msa_gpu_mode: params.msa_gpu_mode,
                    msa_gpu_threshold: params.msa_gpu_threshold,
                    msa_preferred_gpus: params.msa_preferred_gpus,
                    msa_excluded_gpus: params.msa_excluded_gpus,
                    msa_gpu_server_mode: params.msa_gpu_server_mode,
                    msa_gpu_server_wait_timeout: params.msa_gpu_server_wait_timeout,
                    msa_gpu_server_db_load_mode: params.msa_gpu_server_db_load_mode,
                    msa_gpu_server_startup_wait: params.msa_gpu_server_startup_wait,
                    protenix_allow_cpu_msa_fallback: params.protenix_allow_cpu_msa_fallback,
                    protenix_local_msa_timeout_seconds: params.protenix_local_msa_timeout_seconds,
                    protenix_msa_max_seqs_per_validation_job: params.protenix_msa_max_seqs_per_validation_job,
                    protenix_local_msa_max_seqs_per_validation_job: params.protenix_local_msa_max_seqs_per_validation_job,
                    run_thermompnn: params.run_thermompnn ?: false,
                    thermompnn_max_ddg: params.thermompnn_max_ddg,
                    run_immunogenicity_scoring: params.run_immunogenicity_scoring ?: false,
                    pinned_gpus: params.pinned_gpus,
                    fampnn_max_psce: params.fampnn_max_psce,
                    fampnn_max_residue_psce: params.fampnn_max_residue_psce
                ])

                SpawnChildJobs(
                    spawn_validation_inputs.map { pdbs, msa_file -> pdbs },
                    spawn_validation_inputs.map { pdbs, msa_file -> msa_file },
                    parent_id,
                    batch,
                    child_params,
                    effectiveValidationBatchSize
                )

                spawn_child_count = SpawnChildJobs.out.result
                    .map { result_file ->
                        try {
                            def result = new groovy.json.JsonSlurper().parse(result_file)
                            log.info("Spawned ${result.spawned_jobs} child validation jobs")
                            return result.spawned_jobs ?: 0
                        } catch (Exception e) {
                            log.warn("Failed to parse spawn result: ${e.message}")
                            return 0
                        }
                    }

                WaitAndAggregateChildResults(
                    parent_id,
                    batch,
                    spawn_child_count,
                    validation_stage_name
                )

                WaitAndAggregateChildResults.out.report.subscribe { report_file ->
                    try {
                        def report = new groovy.json.JsonSlurper().parse(report_file)
                        log.info("Aggregation complete: ${report.total_validated_designs} validated designs collected")
                    } catch (Exception e) {
                        log.warn("Failed to parse aggregation report: ${e.message}")
                    }
                }

                validated_structures = WaitAndAggregateChildResults.out.pdbs
                    .flatten()
                    .map { pdb ->
                        def name = pdb.baseName.replace('_model_0', '').replace('_boltzpred', '')
                        def meta = [id: name]
                        [meta, pdb]
                    }
            } else {
                log.info("Refinement Mode: Running ${validation_label} validation sequentially...")

                if (structure_validator == 'protenix') {
                    collected_validation_pdbs = pdb_design_sequences
                        .map { sequence, name, pdb -> pdb }
                        .buffer(size: effectiveValidationBatchSize, remainder: true)

                    msa_for_validation = msa_file_ch
                    ready_validation_inputs = collected_validation_pdbs
                        .combine(msa_for_validation)
                        .map { payload -> tuple(payload[0..-2], payload[-1]) }
                        .combine(protenixMsaReadySignal)
                        .map { payload -> tuple(payload[0], payload[1]) }

                    BatchProtenixValidation(
                        ready_validation_inputs.map { pdbs, msa_file -> pdbs },
                        ready_validation_inputs.map { pdbs, msa_file -> msa_file }
                    )

                    sequential_validation_manifest = BatchProtenixValidation.out.pdbs.collect()
                        .combine(BatchProtenixValidation.out.cifs.collect().ifEmpty([]))
                        .combine(BatchProtenixValidation.out.scores.collect().ifEmpty([]))
                        .combine(BatchProtenixValidation.out.aligned_error.collect().ifEmpty([]))
                        .map { pdbs, cifs, scores, aligned_error ->
                            buildValidationArtifactManifestJson([
                                pdbs: pdbs,
                                cifs: cifs,
                                scores: scores,
                                aligned_error: aligned_error,
                            ])
                        }
                        .filter { manifest_json ->
                            def data = new groovy.json.JsonSlurper().parseText(manifest_json)
                            (data.pdbs ?: []).size() > 0
                        }

                    sequential_validation_manifest_file = sequential_validation_manifest
                        .collectFile(name: 'validation_artifacts.json', newLine: false) { manifest_json -> manifest_json }

                    FinalizeSequentialValidationOutputs(sequential_validation_manifest_file)
                } else if (structure_validator == 'esmfold2') {
                    esmfold2_validation_pdbs = pdb_design_sequences
                        .map { sequence, name, pdb -> pdb }
                        .buffer(size: effectiveValidationBatchSize, remainder: true)

                    BatchESMFold2Validation(
                        esmfold2_validation_pdbs,
                        msa_file_ch
                    )

                    sequential_validation_manifest = BatchESMFold2Validation.out.pdbs.collect()
                        .combine(BatchESMFold2Validation.out.cifs.collect().ifEmpty([]))
                        .combine(BatchESMFold2Validation.out.metrics.collect().ifEmpty([]))
                        .map { pdbs, cifs, scores ->
                            buildValidationArtifactManifestJson([
                                pdbs: pdbs,
                                cifs: cifs,
                                scores: scores,
                                aligned_error: [],
                            ])
                        }
                        .filter { manifest_json ->
                            def data = new groovy.json.JsonSlurper().parseText(manifest_json)
                            (data.pdbs ?: []).size() > 0
                        }

                    sequential_validation_manifest_file = sequential_validation_manifest
                        .collectFile(name: 'validation_artifacts.json', newLine: false) { manifest_json -> manifest_json }

                    FinalizeSequentialValidationOutputs(sequential_validation_manifest_file)
                } else {
                    params.boltz_use_msa = false

                    pdb_backed_validation_designs = design_sequences
                        .filter { sequence, name, pdb -> pdb != null }
                    sequence_only_validation_designs = design_sequences
                        .filter { sequence, name, pdb -> pdb == null }
                        .map { sequence, name, pdb -> tuple(sequence, name) }

                    def boltz_validation_pdbs = Channel.empty()
                    def boltz_validation_scores = Channel.empty()
                    def boltz_validation_aligned_error = Channel.empty()

                    pdb_validation_batches = pdb_backed_validation_designs
                        .map { sequence, name, pdb -> pdb }
                        .buffer(size: effectiveValidationBatchSize, remainder: true)

                    BatchBoltzValidation(
                        pdb_validation_batches,
                        msa_file_ch
                    )
                    AlignBoltzValidation(
                        BatchBoltzValidation.out.raw_pdbs.flatten().collect(),
                        BatchBoltzValidation.out.raw_scores.flatten().collect(),
                        BatchBoltzValidation.out.raw_aligned_error.flatten().collect(),
                        BatchBoltzValidation.out.original_designs.flatten().collect()
                    )
                    boltz_validation_pdbs = boltz_validation_pdbs.mix(AlignBoltzValidation.out.pdbs)
                    boltz_validation_scores = boltz_validation_scores.mix(AlignBoltzValidation.out.scores)
                    boltz_validation_aligned_error = boltz_validation_aligned_error.mix(AlignBoltzValidation.out.aligned_error)

                    if (run_antifold) {
                        BoltzFromSequence(sequence_only_validation_designs)
                        boltz_validation_pdbs = boltz_validation_pdbs.mix(BoltzFromSequence.out.pdbs)
                        boltz_validation_scores = boltz_validation_scores.mix(BoltzFromSequence.out.jsons)
                    }

                    sequential_validation_manifest = boltz_validation_pdbs.collect()
                        .combine(boltz_validation_scores.collect().ifEmpty([]))
                        .combine(boltz_validation_aligned_error.collect().ifEmpty([]))
                        .map { pdbs, scores, aligned_error ->
                            buildValidationArtifactManifestJson([
                                pdbs: pdbs,
                                cifs: [],
                                scores: scores,
                                aligned_error: aligned_error,
                            ])
                        }
                        .filter { manifest_json ->
                            def data = new groovy.json.JsonSlurper().parseText(manifest_json)
                            (data.pdbs ?: []).size() > 0
                        }

                    sequential_validation_manifest_file = sequential_validation_manifest
                        .collectFile(name: 'validation_artifacts.json', newLine: false) { manifest_json -> manifest_json }

                    FinalizeSequentialValidationOutputs(sequential_validation_manifest_file)
                }

                validated_structures = FinalizeSequentialValidationOutputs.out.pdbs
                    .flatten()
                    .map { pdb ->
                        def name = pdb.baseName.replace('_model_0', '').replace('_boltzpred', '')
                        def meta = [id: name]
                        [meta, pdb]
                    }
            }
        } else {
            if (run_antifold) {
                log.warn("Structure validation disabled; AntiFold sequence-only outputs are omitted from downstream structure-based stages.")
            }
            validated_structures = pdb_designs_for_boltz
        }

        def shouldPauseAfterStructureValidation = interactiveGateEnabled &&
            (params.interactive_gate_stage ?: 'post_fampnn') == 'post_structure_validation' &&
            params.interactive_gate_continue != true &&
            params.run_structure_validation != false

        if (shouldPauseAfterStructureValidation) {
            staged_validation_pdbs = validated_structures
                .map { meta, pdb -> pdb }
                .collect()
                .filter { pdbs -> pdbs && pdbs.size() > 0 }

            staged_validation_pdb_list_file = staged_validation_pdbs
                .map { pdbs -> pdbs.collect { it.toString() }.join('\n') + '\n' }
                .collectFile(name: 'staged_validation_pdbs.list', newLine: false)

            StageStructureValidationArtifacts(staged_validation_pdb_list_file)

            validation_gate_candidate_count = staged_validation_pdbs
                .map { pdbs -> pdbs.size() as Integer }
            validation_gate_candidate_dir = StageStructureValidationArtifacts.out.dir
                .map { _dir -> "${params.out_dir}/collected/structure_validation" }
        }

        if (shouldPauseAfterStructureValidation) {
            log.info("Interactive SWA gate: pausing after ${validation_label} structure validation")
            OpenInteractiveGate(
                params.job_id ?: "unknown",
                "post_structure_validation",
                validation_gate_candidate_count,
                validation_gate_candidate_dir,
                "",
                "",
                params.framework_type ?: "standard-fv",
                params.antibody_chains ?: "",
                structure_validator
            )
            validated_structures = Channel.empty()
        } else if (params.run_post_validation_maturation == true) {
            log.info("Step 3.25: Running PPIFlow maturation on ${validation_label}-validated structures...")
            log.info("  Spawning post-validation maturation child jobs (${params.maturation_designs_per_job ?: 4} PDBs per job)")
            def postValidationRegionMode = ppiflowMaturationRegionMode
            def postValidationSelectedLoops = postValidationRegionMode == 'selected_cdrs' ? ppiflowMaturationLoopScope : null
            log.info("  Post-validation maturation region mode: ${postValidationRegionMode}")
            if (postValidationSelectedLoops) {
                log.info("  Post-validation maturation loop scope: ${postValidationSelectedLoops}")
            }

            validated_maturation_inputs = validated_structures
                .map { meta, pdb -> pdb }
                .collect()
                .filter { pdbs -> pdbs && pdbs.size() > 0 }

            StageValidatedMaturationInputs(validated_maturation_inputs)

            SpawnValidatedMaturationJobs(
                StageValidatedMaturationInputs.out.pdb_dir,
                params.maturation_designs_per_job ?: 4,
                params.job_id ?: "unknown",
                "${orchestrator_batch_name}_post_validation",
                "maturation_post_validation",
                postValidationRegionMode,
                postValidationSelectedLoops ?: ""
            )

            validated_maturation_wait_trigger = SpawnValidatedMaturationJobs.out.result.map { _spawn_result -> params.job_id ?: "unknown" }

            WaitForValidatedMaturationChildren(
                validated_maturation_wait_trigger,
                "maturation_post_validation",
                30,
                "${orchestrator_batch_name}_post_validation"
            )

            CollectValidatedMaturationOutputs(
                WaitForValidatedMaturationChildren.out.child_outputs,
                "maturation_post_validation"
            )

            CollectValidatedMaturationOutputs.out.pdbs.subscribe { pdbs ->
                try {
                    def file_list = pdbs instanceof List ? pdbs : [pdbs]
                    def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
                    def args = [params.job_id, "maturation_post_validation", "complete"] + report_files.collect { it.toString() }
                    def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                    proc.waitFor()
                } catch (Exception e) {
                    println "Warning: Failed to report stage maturation_post_validation: ${e.message}"
                }
            }

            validated_structures = CollectValidatedMaturationOutputs.out.pdbs
                .flatten()
                .map { pdb ->
                    def meta = [id: pdb.baseName]
                    [meta, pdb]
                }
        }
    }


    if (params.openmm_enabled == true) {
        AntibodyOpenMMRefinement(validated_structures)
        refined_structures = AntibodyOpenMMRefinement.out.refined_structures
    } else {
        refined_structures = validated_structures
    }

    log.info("Step 4: Scoring immunogenicity with AntiBERTy...")

    if (params.run_immunogenicity_scoring != false) {
        antiberty_input = refined_structures.map { meta, pdb ->
            [meta, pdb]
        }

        ANTIBERTY_SCORE(antiberty_input)
        immunogenicity_scores = ANTIBERTY_SCORE.out.scores

        if (params.filter_immunogenic != false) {
            antiberty_filter_input = ANTIBERTY_SCORE.out.scores.join(refined_structures)
            ANTIBERTY_FILTER_STRUCTURES(antiberty_filter_input)
            filtered_structures = ANTIBERTY_FILTER_STRUCTURES.out.filtered_pdb
        }
        else {
            filtered_structures = refined_structures
        }
    }
    else {
        filtered_structures = refined_structures
        immunogenicity_scores = Channel.empty()
    }

    stable_designs = filtered_structures

    if (params.run_affinity_maturation == true) {
        log.info("Step 6: Running affinity maturation with IgGM...")

        maturation_input = stable_designs
            .combine(target_pdb_ch.first())
            .map { meta, design_pdb, target_meta, target_pdb ->
                [meta, design_pdb, target_pdb]
            }

        IGGM_AFFINITY_MATURATION(maturation_input)
        matured_designs = IGGM_AFFINITY_MATURATION.out.matured_designs
        mutations = IGGM_AFFINITY_MATURATION.out.mutations

        if (params.run_structure_validation != false) {
            log.warn("IgGM affinity maturation completed, but a full post-IgGM Boltz revalidation loop is not yet wired in this workflow.")
        }
        final_designs = matured_designs
    }
    else {
        final_designs = stable_designs
        mutations = Channel.empty()
    }

    def terminalStage = params.openmm_enabled == true
        ? 'openmm_relaxation'
        : params.run_post_validation_maturation == true
            ? 'maturation_post_validation'
            : params.run_structure_validation != false
                ? "structure_validation_${(params.structure_validator ?: 'boltz2').toString().toLowerCase()}"
                : params.run_ppiflow_maturation == true || params.run_maturation == true
                    ? 'ppiflow_maturation'
                    : 'sequence_design_terminal'
    def terminalMethod = params.openmm_enabled == true
        ? 'openmm'
        : params.run_post_validation_maturation == true || params.run_ppiflow_maturation == true || params.run_maturation == true
            ? 'ppiflow'
            : params.run_structure_validation != false
                ? (params.structure_validator ?: 'boltz2').toString().toLowerCase()
                : params.seq_design_caliby == true
                    ? 'caliby'
                    : 'fampnn'

    if (params.run_frustrampnn == true) {
        def requiredness = params.frustrampnn_requiredness ?: 'required'
        if (requiredness != 'required') {
            error('antibody_denovo:frustrampnn_requiredness_must_be_required')
        }
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
        def settingsWithOrigin = new TreeMap<String, Object>()
        settingsWithOrigin.putAll(rawSettings)
        settingsWithOrigin['settings_value_origin'] = settingsValueOrigin
        def settingsSha256 = sha256Hex(canonicalJsonBytes(settingsWithOrigin))
        log.info("Step 4.x: Running canonical FrustraMPNN on final antibody candidates...")
        typed_terminal_candidates = final_designs.flatMap { candidate_meta, structure_or_structures ->
            def structures = structure_or_structures instanceof Collection
                ? new ArrayList(structure_or_structures as Collection)
                : [structure_or_structures]
            if (structures.size() != 1) {
                error('antibody_denovo:ambiguous_terminal_candidate_metadata: every final structure requires its own producer metadata tuple')
            }
            def preparedCandidate = antibodyTerminalCandidate(candidate_meta, structures[0], terminalStage, terminalMethod)
            [tuple(preparedCandidate[0], preparedCandidate[1], settingsBase64, settingsSha256, settingsValueOrigin)]
        }
        def frustrampnn_candidate_count = typed_terminal_candidates.map { _candidate -> 1 }.count()
        CheckFrustraYield(frustrampnn_candidate_count)

        PrepareAntibodyFrustraMPNNCandidate(typed_terminal_candidates)
        CanonicalFrustraMPNNV2(PrepareAntibodyFrustraMPNNCandidate.out.prepared)
        PublishAntibodyFrustraMPNNCandidate(CanonicalFrustraMPNNV2.out.result)
        frustrampnn_results = PublishAntibodyFrustraMPNNCandidate.out.published
        AggregateAndReportAntibodyFrustraMPNN(
            PublishAntibodyFrustraMPNNCandidate.out.published
                .map { result_meta, result_manifest, marker -> marker }
                .collect()
        )
    } else {
        ReportAntibodyFrustraMPNNNotRequested(Channel.value('not_requested'))
        frustrampnn_results = ReportAntibodyFrustraMPNNNotRequested.out.result
    }

    }

    emit:
    designs = final_designs // Final antibody designs
    frustrampnn_results = frustrampnn_results // Typed canonical component result/status stream
    immunogenicity = immunogenicity_scores // AntiBERTy PLL scores
    stability = stability_scores_early // ThermoMPNN ddG scores
    mutations = mutations // IgGM suggested mutations
    backbones = backbone_designs // RFantibody backbones after optional coarse screening/review staging
}

workflow {
    
    if (params.target_pdb) {
        target_pdb = file(params.target_pdb)
        if (!target_pdb.exists()) {
            error("Target PDB not found: ${params.target_pdb}")
        }
        meta = [id: params.run_id ?: target_pdb.baseName]
        target_ch = Channel.of([meta, target_pdb])
    }
    else if (params.target_protein_seq) {
        log.info("No target_pdb provided - will predict target structure from sequence")
        
        meta = [id: params.run_id ?: 'target_complex']
        def protein_seq = params.target_protein_seq
        def dna_seq = params.target_dna_seq ?: null
        
        if (dna_seq) {
            log.info("DNA sequence provided - will predict protein-DNA complex")
        }
        
        complex_input = Channel.of([meta, protein_seq, dna_seq])
        
        PredictTargetComplex(complex_input)
        
        target_ch = PredictTargetComplex.out.complex
    }
    else {
        error("Please provide either --target_pdb (antigen structure) or --target_protein_seq (sequence to predict)")
    }

    epitope = params.epitope_residues ?: ""

    framework_ch = params.framework_pdb
        ? Channel.of([meta, file(params.framework_pdb)])
        : Channel.empty()

    NormalizeTargetPDB(target_ch)
    normalized_target_ch = NormalizeTargetPDB.out.normalized

    ANTIBODY_DENOVO(normalized_target_ch, epitope, framework_ch)

    ANTIBODY_DENOVO.out.designs
        .map { designMeta, pdb -> pdb }
        .flatten()
        .collectFile(name: 'final_designs.txt', storeDir: params.out_dir) { it.name + '\n' }

    if (params.run_structure_validation == false) {
        terminal_pdb_list_file = ANTIBODY_DENOVO.out.designs
            .map { designMeta, pdb -> pdb }
            .flatten()
            .map { pdb -> "${pdb}\n" }
            .ifEmpty('')
            .collectFile(name: 'terminal_pdbs.list', newLine: false)

        FinalizeTerminalAntibodyOutputs(
            terminal_pdb_list_file
        )
    }
}
