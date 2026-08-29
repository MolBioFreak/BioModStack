#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.util.Arrays

params.frustrampnn_settings_value_origin = params.frustrampnn_settings_value_origin ?: null

def FRUSTRAMPNN_SETTINGS_MAX_BYTES = 64 * 1024

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
        throw new IllegalArgumentException('frustrampnn_settings protein selectors must be arrays')
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
    if (value instanceof Collection) return value.collect { item -> canonicalJsonValue(item) }
    if (value == null || value instanceof CharSequence || value instanceof Boolean) return value
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

params.sequence_batch_json_path = params.sequence_batch_json_path ?: null
params.complex_batch_dir = params.complex_batch_dir ?: null
params.target_geometry_mode = params.target_geometry_mode ?: null
params.boltz_target_geometry_mode = params.boltz_target_geometry_mode ?: null
params.protenix_target_geometry_mode = params.protenix_target_geometry_mode ?: null
params.target_template_threshold_angstrom = params.target_template_threshold_angstrom ?: 2.0
params.strict_target_rmsd = params.strict_target_rmsd ?: null

include { RFDiffusionWorkflow } from './rfdiffusion.nf'
include { FilterRFD ; RunRFDiffusion } from '../modules/rfdiffusion.nf'
include { PrepRFD3Input ; RunRFD3 ; FilterRFD3 } from '../modules/rfd3.nf'
include { RunRF3 ; FilterRF3 } from '../modules/rf3.nf'
include { PrepFAMPNN ; FilterFAMPNN ; RunFAMPNN } from '../modules/fampnn.nf'
include { FilterMPNN ; PrepMPNN ; RunMPNN } from '../modules/proteinmpnn.nf'
include { AlignAF2 ; FilterAF2 ; RunAF2 } from '../modules/af2.nf'
include { AnalyseBestDesigns } from '../modules/analysis.nf'
include { PublishResults } from '../modules/publish.nf'
include { AlignBoltz ; FilterBoltz ; PrepBoltz ; RunBoltz } from '../modules/boltz.nf'
include { PrepBoltzGenInput ; RunBoltzGen ; FilterBoltzGen ; SpawnBoltzGenJobs ; WaitForBoltzGenChildren ; CollectBoltzGenOutputs ; AggregateBoltzGenResults } from '../modules/boltzgen.nf'
include { CombineMetadata } from '../modules/combine_metadata.nf'
include { Compress as CompressRFD } from '../modules/compress'
include { Compress as CompressMPNN } from '../modules/compress'
include { Compress as CompressFAMPNN } from '../modules/compress'
include { Compress as CompressAF2 } from '../modules/compress'
include { Compress as CompressBoltz } from '../modules/compress'
include { MergeUncroppedTarget } from '../modules/merge_uncropped_target.nf'
include { BoltzFromSequence } from '../modules/structure_prediction.nf'
include { RF3FromSequence } from '../modules/structure_prediction.nf'
include { structure_prediction_wf } from '../modules/structure_prediction.nf'
include { OpenMMRelaxation ; OpenMMScore } from '../modules/openmm.nf'
include { SchedulerFrustraMPNNParentFanout } from '../modules/frustrampnn_parent_fanout.nf'

def proteinDesignSha256(rawPath) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    rawPath.toFile().withInputStream { stream ->
        stream.eachByte(1024 * 1024) { buffer, count ->
            digest.update(buffer, 0, count as int)
        }
    }
    return digest.digest().encodeHex().toString()
}

def proteinDesignCandidateId(parentJobId, producerStage, producerCandidateKey) {
    def domain = [
        'bms.frustrampnn.parent-candidate.v1',
        parentJobId?.toString()?.trim(),
        'protein_design',
        producerStage?.toString()?.trim(),
        producerCandidateKey?.toString()?.trim(),
    ]
    if (domain.tail().any { !it || it.contains('\u0000') }) {
        throw new IllegalArgumentException('protein_design candidate identity fields must be non-empty and NUL-free')
    }
    def digest = java.security.MessageDigest.getInstance('SHA-256')
        .digest(domain.join('\u0000').getBytes('UTF-8')).encodeHex().toString()[0..<32]
    return "${digest[0..<8]}-${digest[8..<12]}-${digest[12..<16]}-${digest[16..<20]}-${digest[20..<32]}"
}

def invalidSequenceBatchIdentity(message) {
    throw new IllegalArgumentException("protein_design:invalid_sequence_batch_identity:${message}")
}

def sequenceSubmissionMetadata(submissionId, submissionName, sequence, fold = null, rank = null) {
    def authority = submissionId?.toString()?.trim()
    def displayName = submissionName?.toString()?.trim()
    def sequenceValue = sequence?.toString()?.trim()
    if (!(authority ==~ /[A-Za-z0-9][A-Za-z0-9._-]*/)) {
        invalidSequenceBatchIdentity('entry ID must be non-empty and safe')
    }
    if (!displayName || !sequenceValue) {
        invalidSequenceBatchIdentity('entry name and sequence must be non-empty')
    }
    if (rank != null && (!(rank instanceof Integer) || (rank as int) < 0)) {
        invalidSequenceBatchIdentity('entry rank must be null or a non-negative integer')
    }
    if (fold != null && !(fold instanceof Integer) && !(fold instanceof CharSequence)) {
        invalidSequenceBatchIdentity('entry fold must be null, an integer, or a string')
    }
    return [
        producer_artifact_id: authority,
        producer_artifact_key: authority,
        producer_sample: authority,
        producer_sequence: sequenceValue,
        producer_fold: fold,
        producer_rank: rank,
        producer_submission_id: authority,
        producer_submission_name: displayName,
        original_submission_identity: [id: authority, name: displayName],
    ]
}

def normalizeProteinDesignSequenceBatchEntries(rawEntries) {
    if (!(rawEntries instanceof Collection) || rawEntries.isEmpty()) {
        invalidSequenceBatchIdentity('batch must contain at least one entry')
    }
    def normalized = rawEntries.collect { rawEntry ->
        if (!(rawEntry instanceof Map)) {
            invalidSequenceBatchIdentity('every batch entry must be an object')
        }
        def explicitIdentityFields = ['producer_artifact_id', 'entry_id', 'id'].findAll {
            rawEntry.containsKey(it)
        }
        def explicitValues = explicitIdentityFields.collect {
            rawEntry[it] == null ? '' : rawEntry[it].toString().trim()
        }
        if (explicitIdentityFields && (explicitValues.any { !it } || explicitValues.toSet().size() != 1)) {
            invalidSequenceBatchIdentity('explicit entry IDs must be non-empty and agree')
        }
        def legacyName = rawEntry.name == null ? '' : rawEntry.name.toString().trim()
        def authority = explicitValues ? explicitValues[0] : legacyName
        def sequence = rawEntry.sequence == null ? '' : rawEntry.sequence.toString().trim()
        def displayName = legacyName ?: authority
        def metadata = sequenceSubmissionMetadata(
            authority,
            displayName,
            sequence,
            rawEntry.containsKey('fold') ? rawEntry.fold : null,
            rawEntry.containsKey('rank') ? rawEntry.rank : null,
        )
        tuple(metadata, sequence, authority)
    }
    def artifactIds = normalized.collect { it[0].producer_artifact_id }
    def artifactKeys = normalized.collect { it[0].producer_artifact_key }
    if (artifactIds.toSet().size() != artifactIds.size() || artifactKeys.toSet().size() != artifactKeys.size()) {
        invalidSequenceBatchIdentity('entry IDs must be unique within the batch')
    }
    return normalized
}

def proteinDesignTerminalCandidate(rawPath, branch, method, producer = [:]) {
    def branchAuthorities = [
        early_sequence_prediction: [producer_branch: 'early_sequence_prediction'],
        rfd3_only: [producer_branch: 'rfd3_only'],
        af2_terminal: [producer_branch: 'af2_terminal'],
        boltz_terminal: [producer_branch: 'boltz_terminal'],
        rf3_terminal: [producer_branch: 'rf3_terminal'],
        protenix_terminal: [producer_branch: 'protenix_terminal'],
        boltzgen_direct: [producer_branch: 'boltzgen_direct'],
        boltzgen_child: [producer_branch: 'boltzgen_child'],
        skip_rfd: [producer_branch: 'skip_rfd'],
        skip_rfd_seq: [producer_branch: 'skip_rfd_seq'],
        analysis_import: [producer_branch: 'analysis_import'],
    ]
    if (!branchAuthorities.containsKey(branch)) {
        throw new IllegalArgumentException("unknown protein_design terminal branch: ${branch}")
    }
    def artifactDigest = proteinDesignSha256(rawPath)
    def outputKey = producer.producer_output_key?.toString() ?: "${branch}/${rawPath.getName()}"
    def sample = producer.containsKey('producer_sample') ? producer.producer_sample : null
    def rank = producer.containsKey('producer_rank') ? producer.producer_rank : null
    def identityDomain = JsonOutput.toJson([
        producer_method: method,
        producer_output_key: outputKey.replaceFirst(/(?i)\.(?:pdb|cif|mmcif)$/, ''),
        producer_rank: rank,
        producer_sample: sample,
    ])
    def identityDigest = java.security.MessageDigest.getInstance('SHA-256')
        .digest(identityDomain.getBytes('UTF-8')).encodeHex().toString()
    def sourceFormat = rawPath.getName().toLowerCase().endsWith('.pdb') ? 'pdb' : 'mmcif'
    def producerKey = "frustrampnn/sources/${method}/${identityDigest}/${artifactDigest}.normalized.pdb"
    def producerStage = "protein_design:${branch}"
    def parentJobId = params.job_id?.toString()
    return tuple([
        candidate_id: proteinDesignCandidateId(parentJobId, producerStage, producerKey),
        parent_job_id: parentJobId,
        parent_workflow_id: 'protein_design',
        producer_stage: producerStage,
        producer_branch: branchAuthorities[branch].producer_branch,
        producer_candidate_key: producerKey,
        producer_method: method,
        producer_sample: sample,
        producer_rank: rank,
        producer_output_key: outputKey,
        producer_identity_sha256: identityDigest,
        producer_artifact_sha256: artifactDigest,
        source_format: sourceFormat,
        requiredness: 'required',
        checkpoint_id: 'megascale.ckpt',
        child_job_id: producer.child_job_id ?: null,
    ], rawPath)
}

process BindProteinDesignTerminalMetadata {
    label 'pyrosetta_tools'
    tag "protein-design-metadata:${candidate_meta.candidate_id}"
    stageInMode 'copy'

    input:
    tuple val(candidate_meta), path(terminal_structure)

    output:
    path "bound_${candidate_meta.candidate_id}.jsonl", topic: metadata_ch_fold_seq
    path "terminal_${candidate_meta.candidate_id}.json", emit: manifest
    path "analysis_${candidate_meta.candidate_id}.log", emit: log

    script:
    def terminalManifest = [
        schema_name: 'protein_design_terminal_candidate',
        schema_version: 1,
        candidate_id: candidate_meta.candidate_id,
        parent_job_id: candidate_meta.parent_job_id,
        parent_workflow_id: candidate_meta.parent_workflow_id,
        producer_stage: candidate_meta.producer_stage,
        producer_candidate_key: candidate_meta.producer_candidate_key,
        producer_method: candidate_meta.producer_method,
        producer_sample: candidate_meta.producer_sample,
        producer_rank: candidate_meta.producer_rank,
        producer_output_key: candidate_meta.producer_output_key,
        producer_identity_sha256: candidate_meta.producer_identity_sha256,
        producer_artifact_sha256: candidate_meta.producer_artifact_sha256,
        source_format: candidate_meta.source_format,
    ]
    def manifestBase64 = JsonOutput.toJson(terminalManifest).getBytes('UTF-8').encodeBase64().toString()
    def candidateId = candidate_meta.candidate_id.toString()
    """
    set -euo pipefail
    python3 -u /scripts/analyse_best_designs.py \
      --pdb_dir ./ --output ordinary_${candidateId}.jsonl --verbose --num_processes 1 \
      2>&1 | tee analysis_${candidateId}.log
    '${params.api_python}' '${params.code_root}/scripts/project_protein_design_metadata.py' bind-jsonl \
      --metadata-jsonl ordinary_${candidateId}.jsonl \
      --manifest-metadata-base64 '${manifestBase64}' \
      --output-jsonl bound_${candidateId}.jsonl \
      --terminal-manifest terminal_${candidateId}.json
    """
}

process ProjectProteinDesignMetadata {
    label 'CPU'
    stageInMode 'copy'

    input:
    path combined_metadata
    path terminal_manifests

    output:
    path 'all_designs.csv', emit: csv

    script:
    def manifestArguments = terminal_manifests.collect { "--terminal-manifest '${it}'" }.join(' ')
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/project_protein_design_metadata.py' project-csv \
      --metadata-csv '${combined_metadata}' \
      ${manifestArguments} \
      --output all_designs.csv
    """
}

process StageProteinDesignPublishStructure {
    label 'CPU'
    tag "protein-design-publish:${candidate_meta.candidate_id}"
    stageInMode 'copy'

    input:
    tuple val(candidate_meta), path(terminal_structure)

    output:
    path "candidate_${candidate_meta.candidate_id}.*", emit: structure

    script:
    def extension = candidate_meta.source_format == 'mmcif' ? 'cif' : 'pdb'
    def outputName = "candidate_${candidate_meta.candidate_id}.${extension}"
    """
    set -euo pipefail
    cp -- '${terminal_structure}' '${outputName}'
    """
}

process PrepareProteinDesignFrustraMPNNCandidate {
    tag "frustrampnn-protein-design:${candidate_meta.producer_branch}:${candidate_meta.producer_identity_sha256}"
    stageInMode 'copy'
    publishDir { "${params.out_dir}/${new File(candidate_meta.producer_candidate_key.toString()).parent}" },
        mode: 'copy', pattern: 'canonical_source.pdb', saveAs: { new File(candidate_meta.producer_candidate_key.toString()).name }

    input:
    tuple val(candidate_meta), path(terminal_structure), val(settings_base64), \
        val(settings_sha256), val(settings_value_origin)

    output:
    tuple path('workflow_component_request_v3.json'), path('canonical_source.pdb'), \
        path('frustrampnn_structure_map_v1.json'), emit: prepared

    script:
    def requestMetadata = candidate_meta.subMap([
        'parent_job_id', 'parent_workflow_id', 'producer_stage', 'producer_candidate_key',
        'requiredness', 'producer_method', 'producer_sample',
        'producer_rank', 'producer_output_key', 'producer_identity_sha256',
        'producer_artifact_sha256', 'source_format', 'candidate_id'
    ])
    def metadataBase64 = JsonOutput.toJson(requestMetadata).getBytes('UTF-8').encodeBase64().toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_frustrampnn_candidate.py' \
      --source '${terminal_structure}' --output-pdb canonical_source.pdb \
      --request workflow_component_request_v3.json --metadata-base64 '${metadataBase64}' \
      --request-version 3 --structure-map frustrampnn_structure_map_v1.json \
      --settings-base64 '${settings_base64}' --settings-sha256 '${settings_sha256}' \
      --settings-value-origin '${settings_value_origin}'
    """
}

process ReportProteinDesignFrustraMPNNNotRequested {
    label 'CPU'
    publishDir "${params.out_dir}/frustrampnn", mode: 'copy', pattern: 'protein_design_frustrampnn_terminal_manifest.json'
    input:
    val trigger
    output:
    path 'protein_design_frustrampnn_terminal_manifest.json'
    path 'frustrampnn_not_requested.reported'
    script:
    def payload = JsonOutput.toJson([
        schema_name: 'protein_design_frustrampnn_terminal_manifest',
        schema_version: 1,
        parent_job_id: params.job_id.toString(),
        parent_workflow_id: 'protein_design',
        status: 'not_requested',
        requiredness: 'not_requested',
        candidate_count: 0,
        candidates: [],
        reported_outputs: [],
    ])
    """
    set -euo pipefail
    printf '%s\n' '${payload}' > protein_design_frustrampnn_terminal_manifest.json
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' \
      '${params.job_id}' frustrampnn not_requested
    : > frustrampnn_not_requested.reported
    """
}

process PublishProteinDesignFrustraMPNNCandidate {
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

process ReportProteinDesignFrustraMPNNComplete {
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
    print(payload['result']); print(payload['manifest']); print(payload['source'])
PY
    )
    test \"\${#outputs[@]}\" -gt 0
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' --job-root-relative \
      '${params.job_id}' frustrampnn complete \"\${outputs[@]}\"
    : > frustrampnn_complete.reported
    """
}






workflow PROTEIN_DESIGN {
    try {
        nextflow.preview.topic = true
    }
    catch (Exception e) {
    }

    def outputDirectory = params.out_dir

    if (params.run_rfd_only && (params.skip_rfd_seq || params.skip_rfd_seq_pred)) {
        error("Cannot use --run_rfd_only with skip flags --skip_rfd_seq or --skip_rfd_seq_pred. These options are contradictory.")
    }
    if (params.run_rfd_only && params.skip_rfd) {
        error("Cannot use --run_rfd_only with --skip_rfd. These options are contradictory.")
    }

    def num_batches = Math.min(params.gpus, params.rfd_num_designs).intValue()
    def batch_size = Math.ceil(params.rfd_num_designs / num_batches).intValue()
    def num_designs = num_batches * batch_size

    println("Pipeline Mode: ${params.rfd_mode}")
    println("Number of RFdiffusion designs: ${num_designs}")
    println("Number of sequences for each design: ${params.seqs_per_design}")
    println("Output Directory: ${outputDirectory}")


    def configDir = file("${outputDirectory}/configs")
    configDir.mkdirs()
    workflow.configFiles.each { configFile ->
        configFile.copyTo("${configDir}/${configFile.getName()}")
    }

    def inputsDir = file("${outputDirectory}/inputs")
    inputsDir.mkdirs()

    def migratedDirectModes = [
        'antibody_child': 'workflows/antibody_child.nf',

        'fampnn_child': 'workflows/fampnn_child.nf',
        'maturation_child': 'workflows/maturation_child.nf',
        'ppiflow_generator': 'workflows/ppiflow_generator_design.nf',
        'rfantibody_backbone': 'workflows/rfantibody_backbone.nf',
    ]
    if (params.rfd_mode in migratedDirectModes.keySet()) {
        error("${params.rfd_mode} is isolated from the core protein-design entrypoint; launch ${migratedDirectModes[params.rfd_mode]} directly")
    }
    if (params.complex_json_path) {
        error("complex_json_path jobs are isolated from the core protein-design entrypoint; launch workflows/complex_prediction.nf directly")
    }
    if (params.unidock_ligand_smiles || params.unidock_ntp_type || params.run_docking) {
        error("standalone docking is isolated from the core protein-design entrypoint; launch workflows/docking.nf directly")
    }














    if (params.sequence_input || params.sequence_batch_json_path) {
        def numParallelJobs = params.num_parallel_jobs ?: 1
        println("Running sequence-based structure prediction")
        if (params.sequence_batch_json_path) {
            println("* Batch manifest: ${params.sequence_batch_json_path}")
        } else {
            println("* Sequence: ${params.sequence_input.take(50)}...")
        }
        println("* Predictor: ${params.pred_method ?: 'boltz'}")
        println("* Parallel jobs: ${numParallelJobs}")

        def seq_name = params.sequence_name ?: 'predicted'
        def parallel_jobs_ch

        if (params.sequence_batch_json_path) {
            def batchEntries = parseJsonFile(params.sequence_batch_json_path) as List
            def normalizedBatchEntries = normalizeProteinDesignSequenceBatchEntries(batchEntries)
            println("* Batch sequences: ${normalizedBatchEntries.size()}")
            parallel_jobs_ch = Channel.fromList(normalizedBatchEntries)
        } else {
            def job_indices = Channel.from(0..<numParallelJobs)

            parallel_jobs_ch = job_indices.map { idx ->
                def jobName = numParallelJobs > 1 ? "${seq_name}_job${idx}" : seq_name
                def metadata = sequenceSubmissionMetadata(
                    jobName,
                    seq_name,
                    params.sequence_input,
                    numParallelJobs > 1 ? idx : null,
                    null,
                )
                metadata.producer_submission_id = seq_name
                metadata.original_submission_identity = [id: seq_name, name: seq_name]
                tuple(metadata, params.sequence_input, jobName)
            }
        }

        structure_prediction_wf(parallel_jobs_ch)
        terminal_designs = structure_prediction_wf.out.canonical_structures.map { producer_meta, predicted ->
            def method = producer_meta.producer_method?.toString() ?: (params.pred_method ?: 'boltz').toString()
            def producer = new LinkedHashMap(producer_meta as Map)
            producer.producer_output_key = producer_meta.producer_output_key?.toString() ?:
                producer_meta.producer_artifact_key?.toString()
            proteinDesignTerminalCandidate(predicted, 'early_sequence_prediction', method, producer)
        }.ifEmpty { error('protein_design:no_candidates') }
        final_pdbs = terminal_designs.map { candidate_meta, structure -> structure }.collect()
    }
    else {


    if (!params.skip_rfd & !params.skip_rfd_seq & !params.skip_rfd_seq_pred & params.diffusion_method != 'boltzgen') {
        if (!params.rfd_num_designs) {
            error("Please provide the number of designs for RFdiffusion to generate")
        }

        if (params.diffusion_method == "rfd3") {
            println("Using RFdiffusion3 (Foundry) for structure generation")

            def inputFiles = collectInputFiles(params)
            inputFiles.each { inputFile ->
                "rsync -r ${inputFile} ${inputsDir}/.".execute()
            }

            def rfd3_input_ch = Channel.of(
                [
                    params.rfd_mode,
                    params.rfd_contigs ?: '[100-100]',
                    params.rfd_input_pdb ? file(params.rfd_input_pdb) : file("${params.code_root}/lib/NO_FILE"),
                    params.rfd_hotspots ?: '',
                    params.rfd_num_designs,
                    0,
                ]
            )

            PrepRFD3Input(rfd3_input_ch)

            RunRFD3(PrepRFD3Input.out.input_json)

            RunRFD3.out.structures_metadata.set { rfd_pdbs_jsons }

            rebatchTuples(rfd_pdbs_jsons, 200)
                .set { rfd_tuples }

            FilterRFD3(rfd_tuples)

            if (params.run_rfd_only) {
                terminal_designs = FilterRFD3.out.structures_metadata
                    .flatMap { pdbs, sidecars ->
                        def structures = pdbs instanceof Collection ? pdbs : [pdbs]
                        structures.collect { pdb ->
                            proteinDesignTerminalCandidate(pdb, 'rfd3_only', 'rfd3')
                        }
                    }
                    .ifEmpty { error('protein_design:no_candidates') }
                final_pdbs = terminal_designs.map { candidate_meta, structure -> structure }.collect()
            }
            else {
                FilterRFD3.out.structures_metadata.set { filt_rfd_pdbs_jsons }
            }
        }
        else {
            error("diffusion_method='rfd' has been retired from tracked BioModStack repo state. Use diffusion_method='rfd3' instead.")
        }
    }
    else if (params.diffusion_method == "boltzgen") {

        println("Using BoltzGen for all-atom binder generation")

        def boltzgenNumDesigns = params.get('boltzgen_num_designs') ?: 10
        def boltzgenDesignsPerJob = params.get('boltzgen_designs_per_job') ?: 100
        def boltzgenParallelMode = params.get('boltzgen_parallel_mode') == true

        PrepBoltzGenInput(
            params.boltzgen_ligand_smiles ?: '',
            params.boltzgen_ntp_type ?: '',
            params.boltzgen_scaffold_length,
            boltzgenNumDesigns,
            params.boltzgen_binding_site_residues ?: '',
            params.boltzgen_catalytic_site ?: false,
            params.boltzgen_protein_sequence ?: '',
            params.boltzgen_dna_template_seq ?: '',
            params.boltzgen_dna_primer_seq ?: '',
            params.boltzgen_secondary_structure ?: '',
            params.boltzgen_protocol ?: 'protein-anything',
            params.boltzgen_covalent_bonds ?: '',
            params.boltzgen_nanobody_framework ?: '',
            params.boltzgen_cdr_h1_length ?: '5-8',
            params.boltzgen_cdr_h2_length ?: '6-10',
            params.boltzgen_cdr_h3_length ?: '12-18',
            params.boltzgen_input_pdb ? file(params.boltzgen_input_pdb) : file("${params.code_root}/lib/NO_INPUT_PDB"),
            params.boltzgen_ligand_pdb ? file(params.boltzgen_ligand_pdb) : file("${params.code_root}/lib/NO_LIGAND_PDB"),
            params.boltzgen_dna_structure ? file(params.boltzgen_dna_structure) : file("${params.code_root}/lib/NO_DNA_STRUCT"),
            params.boltzgen_target_pdb_path ? file(params.boltzgen_target_pdb_path) : file("${params.code_root}/lib/NO_TARGET_PDB"),
        )

        def parallel_mode_set = params.containsKey('parallel_mode')
        def use_orchestrator = parallel_mode_set
            ? (params.parallel_mode == 'full_orchestrator')
            : boltzgenParallelMode
        if (use_orchestrator) {
            println("BoltzGen PARALLEL MODE: Spawning ${Math.ceil(boltzgenNumDesigns / boltzgenDesignsPerJob)} child jobs")

            def target_pdb = params.boltzgen_target_pdb_path ? file(params.boltzgen_target_pdb_path) : file("${params.code_root}/lib/NO_TARGET_PDB")

            SpawnBoltzGenJobs(
                params.job_id ?: 'unknown',
                boltzgenNumDesigns,
                boltzgenDesignsPerJob,
                PrepBoltzGenInput.out.yaml,
                target_pdb,
                params.boltzgen_mode ?: 'nanobody_binder',
                params.name ?: 'boltzgen_campaign',
            )

            WaitForBoltzGenChildren(
                params.job_id ?: 'unknown',
                SpawnBoltzGenJobs.out.result,
                params.name ?: 'boltzgen_campaign',
            )

            CollectBoltzGenOutputs(WaitForBoltzGenChildren.out.result)

            AggregateBoltzGenResults(
                params.job_id ?: 'unknown',
                CollectBoltzGenOutputs.out.pdbs.collect(),
                CollectBoltzGenOutputs.out.jsons.collect(),
                CollectBoltzGenOutputs.out.manifest,
            )

            rfd_tuples = Channel.empty()
            filt_rfd_pdbs_jsons = Channel.empty()
            filt_seq_pdbs = Channel.empty()
            analysis_input_pdbs = CollectBoltzGenOutputs.out.pdbs.flatten()
        }
        else {
            RunBoltzGen(PrepBoltzGenInput.out.yaml)

            FilterBoltzGen(RunBoltzGen.out.pdbs, RunBoltzGen.out.jsons)

            FilterBoltzGen.out.pdbs
                .flatten()
                .collect()
                .set { filt_seq_pdbs }

            FilterBoltzGen.out.pdbs
                .flatten()
                .collect()
                .set { analysis_input_pdbs }

            rfd_tuples = Channel.empty()
            filt_rfd_pdbs_jsons = Channel.empty()
            seq_tuple = Channel.empty()
        }
    }
    else if (params.skip_rfd & !params.skip_rfd_seq & !params.skip_rfd_seq_pred) {
        println("Skipping RFDiffusion stage as skip_rfd=true.")
        println("Running Sequence Design, Prediction, and Analysis stages only.")
        println("Looking for PDBs and JSONs in: ${params.skip_input_dir}")
        if (!file(params.skip_input_dir).exists()) {
            throw new FileNotFoundException("Skip input file directory not found at: ${params.skip_input_dir}. Please ensure the path is correct.")
        }
        def previous_pdbs = file("${params.skip_input_dir}").listFiles().findAll { it.name.endsWith('.pdb') }
        def previous_jsons = file("${params.skip_input_dir}").listFiles().findAll { it.name.endsWith('.json') }
        if (previous_pdbs.isEmpty()) {
            throw new FileNotFoundException("No PDB files found in directory: ${params.skip_input_dir}. Please provide PDB files to proceed with the workflow.")
        }
        if (previous_jsons.isEmpty()) {
            throw new FileNotFoundException("No JSON files found in directory: ${params.skip_input_dir}. Please provide JSON files to proceed with the workflow.")
        }
        println("Found ${previous_pdbs.size()} PDB files")
        println("Found ${previous_jsons.size()} JSON files\n")

        previous_pdbs.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }
        previous_jsons.each { jsonFile ->
            jsonFile.copyTo("${inputsDir}/${jsonFile.getName()}")
        }

        Channel
            .of([previous_pdbs, previous_jsons])
            .set { rfd_pdbs_jsons }
        rebatchTuples(rfd_pdbs_jsons, 200)
            .set { filt_rfd_pdbs_jsons }
    }
    else {
        println("Skipping RFDiffusion stage as skip_rfd_seq=true or skip_rfd_seq_pred=true.")
    }
    if (params.diffusion_method == 'boltzgen') {
        println("Skipping Sequence Design stage for BoltzGen diffusion output.")
    }
    else if (!params.skip_rfd_seq & !params.skip_rfd_seq_pred & !params.run_rfd_only) {
        if (params.seq_method == "mpnn") {
            PrepMPNN(filt_rfd_pdbs_jsons)

            if (params.mpnn_relax_max_cycles > 0) {
                PrepMPNN.out.pdbs
                    .collect()
                    .flatten()
                    .buffer(size: 2, remainder: true)
                    .set { seq_input_pdbs }
            }
            else {
                PrepMPNN.out.pdbs
                    .collect()
                    .flatten()
                    .buffer(size: 10, remainder: true)
                    .set { seq_input_pdbs }
            }

            RunMPNN(seq_input_pdbs)

            CompressMPNN("mpnn", RunMPNN.out.pdbs_jsons.flatten().collect())

            rebatchTuples(RunMPNN.out.pdbs_jsons, 200)
                .set { seq_tuple }

            FilterMPNN(seq_tuple)
            FilterMPNN.out.pdbs
                .flatten()
                .collect()
                .set { filt_seq_pdbs }
        }
        else if (params.seq_method == "fampnn") {
            rebatchTuples(filt_rfd_pdbs_jsons, 10)
                .set { fampnn_prep_input_tuple }

            PrepFAMPNN(fampnn_prep_input_tuple)
            PrepFAMPNN.out.csv
                .collectFile(name: 'merged_results.csv', keepHeader: true)
                .set { mega_csv }

            PrepFAMPNN.out.pdbs
                .collect()
                .map { allPdbs -> partitionGpuBatches(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { fampnn_pdbs }

            def default_gpu = params.pinned_gpus ? params.pinned_gpus.toString().split(',')[0].trim().toInteger() : (params.gpu_id ?: 0)
            fampnn_pdbs
                .combine(mega_csv)
                .map { batch_id, pdbs, csv -> [batch_id, pdbs, csv, default_gpu] }
                .set { fampnn_input }

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                RunFAMPNN(fampnn_input, 'A')
            }
            else {
                RunFAMPNN(fampnn_input, 'all_chains')
            }

            CompressFAMPNN("fampnn", RunFAMPNN.out.pdbs_jsons.flatten().collect())

            rebatchTuples(RunFAMPNN.out.pdbs_jsons, 200)
                .set { seq_tuple }

            FilterFAMPNN(seq_tuple)
            FilterFAMPNN.out.pdbs
                .flatten()
                .collect()
                .set { filt_seq_pdbs }
        }
        else {
            error("Not a valid sequence assignment method")
        }
    }
    else if (!params.skip_rfd_seq_pred & !params.run_rfd_only) {
        println("Skipping Sequence Design stage as skip_rfd_seq=true.")
        println("Running Prediction and Analysis stages only.")
        println("Looking for PDBs in: ${params.skip_input_dir}")

        def inputPath = file(params.skip_input_dir)
        if (!inputPath.exists()) {
            throw new FileNotFoundException("Skip input path not found at: ${params.skip_input_dir}. Please ensure the path is correct.")
        }

        def pdbs_for_pred = []
        if (inputPath.isFile()) {
            if (inputPath.name.endsWith('.pdb')) {
                pdbs_for_pred = [inputPath]
                println("Using single PDB file: ${inputPath.name}")
            }
            else {
                throw new FileNotFoundException("Input file must be a .pdb file: ${params.skip_input_dir}")
            }
        }
        else {
            pdbs_for_pred = inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
            if (pdbs_for_pred.isEmpty()) {
                throw new FileNotFoundException("No PDB files found in directory: ${params.skip_input_dir}. Please provide PDB files to proceed with the workflow.")
            }
            println("Found ${pdbs_for_pred.size()} PDB files in directory")
        }

        pdbs_for_pred.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }

        Channel
            .of(pdbs_for_pred)
            .set { filt_seq_pdbs }
    }
    else if (params.skip_rfd_seq_pred) {
        println("Skipping Sequence Design stage as skip_rfd_seq_pred=true.")
    }
    else {
        println("Skipping Sequence Design stage as run_rfd_only=true.")
    }
    if (!params.skip_rfd_seq_pred && !params.run_rfd_only && !params.skip_pred && params.diffusion_method != 'boltzgen') {
        if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
            if (params.uncropped_target_pdb) {
                def uncroppedPDBfile = file(params.uncropped_target_pdb)
                if (!uncroppedPDBfile.exists()) {
                    throw new FileNotFoundException("Uncropped target PDB file not found at path: ${params.uncropped_target_pdb}. Please ensure the file exists and the path is correct.")
                }
                MergeUncroppedTarget(filt_seq_pdbs, uncroppedPDBfile).set { pred_input_pdbs }
            }
            else {
                filt_seq_pdbs.set { pred_input_pdbs }
            }
        }
        else {
            filt_seq_pdbs.set { pred_input_pdbs }
        }
        if (params.pred_method == "af2") {

            pred_input_pdbs
                .collect()
                .map { allPdbs -> partitionGpuBatchesByNumRes(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { pred_input_tuple }

            RunAF2(pred_input_tuple)

            CompressAF2("af2", RunAF2.out.pdbs_jsons.flatten().collect())

            rebatchTuples(RunAF2.out.pdbs_jsons, 200)
                .set { af2_tuple }

            FilterAF2(af2_tuple)

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                AlignAF2(FilterAF2.out.pdbs.flatten().collect(), pred_input_pdbs.flatten().last())
                AlignAF2.out.pdbs
                    .flatten()
                    .collect()
                    .set { analysis_input_pdbs }
            }
            else {
                FilterAF2.out.pdbs
                    .flatten()
                    .collect()
                    .set { analysis_input_pdbs }
            }
        }
        else if (params.pred_method == "boltz") {
            PrepBoltz(pred_input_pdbs)

            PrepBoltz.out.yamls
                .collect()
                .map { allPdbs -> partitionGpuBatches(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { pred_input_tuple }

            RunBoltz(pred_input_tuple)

            rebatchTuples(RunBoltz.out.pdbs_jsons, 200)
                .set { boltz_tuple }

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                AlignBoltz(boltz_tuple, filt_seq_pdbs, 'binder')
            }
            else {
                AlignBoltz(boltz_tuple, filt_seq_pdbs, 'monomer')
            }
            CompressBoltz("boltz", AlignBoltz.out.pdbs_jsons.flatten().collect())

            FilterBoltz(AlignBoltz.out.pdbs_jsons)
            FilterBoltz.out.pdbs
                .flatten()
                .collect()
                .set { analysis_input_pdbs }
        }
        else if (params.pred_method == "rf3") {
            println("Using RosettaFold3 (Foundry) for structure prediction")

            pred_input_pdbs
                .collect()
                .map { allPdbs -> partitionGpuBatches(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { pred_input_tuple }

            RunRF3(pred_input_tuple)

            rebatchTuples(RunRF3.out.structures_metadata, 200)
                .set { rf3_tuple }

            FilterRF3(rf3_tuple)
            FilterRF3.out.structures
                .flatten()
                .collect()
                .set { analysis_input_pdbs }
        }
        else {
            error("Not a valid structure prediction method. Choose from: af2, boltz, rf3, protenix")
        }
    }
    else if (!params.run_rfd_only && params.diffusion_method != 'boltzgen') {
        println("Skipping Structure Prediction stage as skip_rfd_seq_pred=true.")
        println("Running Analysis Stage only")
        def skipInputDir = params.get('skip_input_dir')
        if (!skipInputDir) {
            throw new IllegalArgumentException("skip_input_dir is required when skip_rfd_seq_pred=true and analysis-only mode is selected")
        }
        println("Looking for PDBs in: ${skipInputDir}")
        def inputPath = file(skipInputDir)
        if (!inputPath.exists()) {
            throw new FileNotFoundException("Skip input file path not found at: ${skipInputDir}. Please ensure the path is correct.")
        }

        def pdbs_for_analysis = []
        if (inputPath.isFile()) {
            if (inputPath.name.endsWith('.pdb')) {
                pdbs_for_analysis = [inputPath]
                println("Using single PDB file: ${inputPath.name}")
            }
            else {
                throw new IllegalArgumentException("Input file must be a .pdb file: ${skipInputDir}")
            }
        }
        else {
            pdbs_for_analysis = inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
            if (pdbs_for_analysis.isEmpty()) {
                throw new FileNotFoundException("No PDB files found in directory: ${skipInputDir}. Please provide PDB files to proceed with the workflow.")
            }
            println("Found ${pdbs_for_analysis.size()} PDB files")
        }

        pdbs_for_analysis.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }

        Channel
            .of(pdbs_for_analysis)
            .set { analysis_input_pdbs }
    }
    else if (params.diffusion_method == 'boltzgen') {
        println("Skipping Structure Prediction stage for BoltzGen diffusion output.")
    }
    else {
        println("Skipping Structure Prediction stage as run_rfd_only=true.")
    }

    if (!params.run_rfd_only) {
        def terminalBranch = (
            params.diffusion_method == 'boltzgen'
                ? (((params.containsKey('parallel_mode') && params.parallel_mode == 'full_orchestrator') || params.boltzgen_parallel_mode == true)
                    ? 'boltzgen_child' : 'boltzgen_direct')
                : (params.skip_rfd_seq_pred == true || params.skip_pred == true)
                    ? 'analysis_import'
                    : params.skip_rfd_seq == true
                        ? 'skip_rfd_seq'
                        : params.skip_rfd == true
                            ? 'skip_rfd'
                            : params.pred_method == 'af2'
                                ? 'af2_terminal'
                                : params.pred_method == 'boltz'
                                    ? 'boltz_terminal'
                                    : params.pred_method == 'rf3'
                                        ? 'rf3_terminal'
                                        : 'protenix_terminal'
        )
        def terminalMethod = params.diffusion_method == 'boltzgen'
            ? 'boltzgen'
            : (terminalBranch == 'analysis_import' ? 'import' : params.pred_method.toString())
        terminal_designs = analysis_input_pdbs
            .flatten()
            .map { pdb -> proteinDesignTerminalCandidate(pdb, terminalBranch, terminalMethod) }
            .ifEmpty { error('protein_design:no_candidates') }
        def projected_terminal_pdbs = terminal_designs.map { candidate_meta, structure -> structure }
        final_pdbs = projected_terminal_pdbs.collect()
    }
    else {
        println("Skipping Analysis stage as run_rfd_only=true.")
    }
    }

    // Bind canonical identity to each candidate's ordinary metadata while the
    // scheduler still owns the typed tuple. This projection is required even
    // when FrustraMPNN itself is disabled so ordinary Design ingestion keeps
    // the same deterministic identity contract.
    BindProteinDesignTerminalMetadata(terminal_designs)
    StageProteinDesignPublishStructure(terminal_designs)
    StageProteinDesignPublishStructure.out.structure.collect().set { published_final_structures }

    channel.topic('metadata_ch_fold')
        .flatten()
        .collectFile(name: "metadata_fold.jsonl", newLine: true)
        .ifEmpty { file("${params.code_root}/lib/empty-meta-fold.jsonl") }
        .set { metadata_fold }
    channel.topic('metadata_ch_fold_seq')
        .flatten()
        .collectFile(name: "metadata_fold_seq.jsonl", newLine: true)
        .ifEmpty { file("${params.code_root}/lib/empty-meta-seq.jsonl") }
        .set { metadata_fold_seq }

    CombineMetadata(metadata_fold, metadata_fold_seq).csv.set { all_designs_metadata }
    BindProteinDesignTerminalMetadata.out.manifest.collect().set { terminal_candidate_manifests }
    ProjectProteinDesignMetadata(all_designs_metadata, terminal_candidate_manifests)
    projected_design_metadata = ProjectProteinDesignMetadata.out.csv

    if (params.sequence_input || params.sequence_batch_json_path) {
        rfd_count = 0
        filter_rfd_count = 0
        seq_count = 0
        filter_seq_count = 0
        countPdbFiles(final_pdbs).set { filter_pred_count }
    }
    else if (params.run_rfd_only) {
        countPdbFiles(rfd_tuples).set { rfd_count }
        countPdbFiles(final_pdbs).set { filter_rfd_count }
        seq_count = 0
        filter_seq_count = 0
        filter_pred_count = 0
    }
    else if (params.skip_rfd_seq_pred) {
        rfd_count = 0
        filter_rfd_count = 0
        seq_count = 0
        filter_seq_count = 0
        filter_pred_count = 0
    }
    else if (params.skip_rfd_seq) {
        rfd_count = 0
        filter_rfd_count = 0
        seq_count = 0
        filter_seq_count = 0
        countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }
    else if (params.skip_rfd) {
        rfd_count = 0
        filter_rfd_count = 0
        countPdbFiles(seq_tuple).set { seq_count }
        countPdbFiles(filt_seq_pdbs).set { filter_seq_count }
        countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }
    else {
        countPdbFiles(rfd_tuples).set { rfd_count }
        countPdbFiles(filt_rfd_pdbs_jsons).set { filter_rfd_count }
        countPdbFiles(seq_tuple).set { seq_count }
        countPdbFiles(filt_seq_pdbs).set { filter_seq_count }
        countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }

    PublishResults(
        published_final_structures,
        projected_design_metadata,
        rfd_count,
        filter_rfd_count,
        seq_count,
        filter_seq_count,
        filter_pred_count,
    )
    final_structures = final_pdbs

    def frustrampnnRequiredness = params.frustrampnn_requiredness ?: 'required'
    if (frustrampnnRequiredness != 'required') {
        error('frustrampnn_requiredness must be required')
    }
    if (params.run_frustrampnn == true) {
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
        SchedulerFrustraMPNNParentFanout(
            terminal_designs,
            Channel.value(params.job_id.toString()),
            Channel.value('protein_design'),
            Channel.value(params.frustrampnn_settings.toString()),
            Channel.value(settingsValueOrigin),
        )
        frustrampnn_results = SchedulerFrustraMPNNParentFanout.out.receipt
    }
    else {
        ReportProteinDesignFrustraMPNNNotRequested(Channel.value('not_requested'))
        frustrampnn_results = Channel.empty()
    }

    workflow.onComplete {
        def logFile = file('.nextflow.log')
        def outputDir = file(outputDirectory)
        if (logFile.exists()) {
            logFile.copyTo(outputDir.resolve('nextflow.log'))
        }
    }

    emit:
    final_structures
    terminal_designs
    frustrampnn_results
}

def collectInputFiles(params) {
    def inputs = []

    if (params.rfd_mode in [
        'binder_denovo',
        'binder_foldcond',
        'binder_motifscaff',
        'binder_partialdiff',
        'monomer_motifscaff',
        'monomer_partialdiff',
    ]) {
        if (params.rfd_input_pdb) {
            inputs << file(params.rfd_input_pdb)
        }
    }

    if (params.rfd_mode in ['binder_foldcond', 'monomer_foldcond']) {
        if (params.rfd_scaffold_dir) {
            inputs << file(params.rfd_scaffold_dir)
        }
    }
    if (params.rfd_mode == 'binder_foldcond') {
        if (params.rfd_target_ss) {
            inputs << file(params.rfd_target_ss)
        }
        if (params.rfd_target_adj) {
            inputs << file(params.rfd_target_adj)
        }
    }

    return inputs
}

def parseJsonFile(rawPath) {
    return new groovy.json.JsonSlurper().parse(file(rawPath))
}

def rebatchTuples(inputChannel, batchSize = 50) {
    return inputChannel
        .transpose()
        .buffer(size: batchSize, remainder: true)
        .map { pairs ->
            def firstElements = pairs.collect { pair -> pair[0] }
            def secondElements = pairs.collect { pair -> pair[1] }
            [firstElements, secondElements]
        }
}

def partitionGpuBatches(allPdbs, gpus) {
    def totalSize = allPdbs.size()
    if (totalSize == 0) {
        return []
    }
    def batchCount = Math.max(1, Math.min((gpus ?: 1) as int, totalSize))
    def batchSize = (totalSize / batchCount).doubleValue()
    def index = 0
    def batches = allPdbs.collect { pdb ->
        def position = index
        index = index + 1
        def batchId = (position / batchSize).intValue()
        [batchId, pdb]
    }
    return batches
}

def countResidues(pdbFile) {
    def residueSet = new HashSet()

    pdbFile.eachLine { line ->
        if (line.startsWith("ATOM  ") || line.startsWith("HETATM")) {
            if (line.length() >= 26) {
                def chainId = line.substring(21, 22)
                def residueNumber = line.substring(22, 26).trim()
                def residueName = line.substring(17, 20).trim()
                residueSet.add("${chainId}_${residueNumber}_${residueName}")
            }
        }
    }

    return residueSet.size()
}

def partitionGpuBatchesByNumRes(allPdbs, gpus) {
    def sortedPdbs = allPdbs.sort { pdb -> countResidues(pdb) }
    return partitionGpuBatches(sortedPdbs, gpus)
}

def countPdbFiles(inputChannel) {
    return inputChannel
        .flatten()
        .collect()
        .map { files ->
            files.findAll { fileObj ->
                def name = fileObj.toString()
                name.endsWith('.pdb') || name.endsWith('.cif.gz') || name.endsWith('.cif')
            }.size()
        }
        .ifEmpty(0)
}

// Direct entry point for the legacy/core protein-design workflow.
workflow {
    PROTEIN_DESIGN()
}
