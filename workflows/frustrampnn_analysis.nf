nextflow.enable.dsl = 2

import groovy.json.JsonOutput
import groovy.json.JsonSlurper

include { CanonicalFrustraMPNNV2 } from '../modules/frustrampnn'

process PreparePersistedFrustraMPNNCandidate {
    tag 'frustrampnn-prepare-persisted'
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    tuple val(record_base64), path(request_snapshot), path(source_snapshot), path(structure_map_snapshot)

    output:
    tuple path('workflow_component_request_v3.json'), path('canonical_source.pdb'), \
        path('frustrampnn_structure_map_v1.json'), emit: prepared

    script:
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_persisted_frustrampnn_candidate.py' \
      --record-base64 '${record_base64}' \
      --request '${request_snapshot}' \
      --source '${source_snapshot}' \
      --structure-map '${structure_map_snapshot}' \
      --output-request .prepared_workflow_component_request_v3.json \
      --output-source .prepared_canonical_source.pdb \
      --output-structure-map .prepared_frustrampnn_structure_map_v1.json
    mv .prepared_workflow_component_request_v3.json workflow_component_request_v3.json
    mv .prepared_canonical_source.pdb canonical_source.pdb
    mv .prepared_frustrampnn_structure_map_v1.json frustrampnn_structure_map_v1.json
    """
}

process PublishPersistedFrustraMPNNChildBundle {
    tag "frustrampnn-publish:${result_meta.invocation_id}"
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    tuple val(result_meta), path(candidate_bundle), path(result_manifest)

    output:
    path 'published_*.json', emit: marker

    script:
    def candidateId = result_meta.candidate_id.toString()
    if (!(candidateId ==~ /[A-Za-z0-9][A-Za-z0-9._-]{0,127}/)) {
        error('canonical FrustraMPNN emitted an unsafe candidate identity')
    }
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/publish_frustrampnn_bundle.py' \
      --source-bundle '${candidate_bundle}' \
      --allowed-root '${params.out_dir}' \
      --destination '${params.out_dir}/frustrampnn/results/${candidateId}' \
      --marker 'published_${candidateId}.json'
    """
}

process RunPersistedFrustraMPNNGroupedBatch {
    tag 'frustrampnn-grouped-predict-batch'
    label 'frustrampnn_gpu'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    val batch_manifest_path

    output:
    path 'grouped_results', emit: results

    script:
    def assigned_gpu = params.frustrampnn_physical_gpu_id?.toString()
    if (!(assigned_gpu ==~ /(?:0|[1-9][0-9]*)/)) {
        error('grouped FrustraMPNN requires explicit scheduler-assigned physical GPU ID')
    }
    def apptainer_bin = params.get('apptainer_bin') ?: 'apptainer'
    """
    set -euo pipefail
    export CUDA_VISIBLE_DEVICES='${assigned_gpu}'
    '${params.api_python}' '${params.code_root}/scripts/run_frustrampnn_grouped_batch.py' \
      --batch-manifest '${batch_manifest_path}' \
      --job-root '${params.out_dir}' \
      --container '${params.container_dir}/frustrampnn.sif' \
      --apptainer '${apptainer_bin}' \
      --physical-gpu-id '${assigned_gpu}' > grouped_batch_terminal.json
    test -d grouped_results
    """
}

process PublishPersistedFrustraMPNNGroupedBundles {
    tag 'frustrampnn-grouped-publish'
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    path grouped_results

    output:
    path 'published_*.json', emit: marker

    script:
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/publish_frustrampnn_grouped_results.py' \
      --grouped-root '${grouped_results}' \
      --job-root '${params.out_dir}' \
      --marker-root .
    """
}

process ReportPersistedFrustraMPNNComplete {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

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
      --status-output frustrampnn_stage_status \
      published_*.json)
    test \"\${#outputs[@]}\" -gt 0
    status=\$(<frustrampnn_stage_status)
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' --job-root-relative \
      '${params.job_id}' frustrampnn \"\${status}\" \"\${outputs[@]}\"
    : > frustrampnn_complete.reported
    test \"\${status}\" = complete
    """
}

workflow {
    if (!params.job_id) {
        throw new IllegalArgumentException('job_id is required')
    }
    if (!params.frustrampnn_batch_manifest_path) {
        throw new IllegalArgumentException('frustrampnn_batch_manifest_path is required')
    }
    def manifestPath = file(params.frustrampnn_batch_manifest_path)
    def batch = new JsonSlurper().parse(manifestPath)
    def batchKeys = [
        'schema_name', 'schema_version', 'execution_owner_job_id',
        'batching_enabled', 'structures_per_job', 'settings_sha256',
        'expected_cardinality', 'records'
    ] as Set
    if (
        !(batch instanceof Map) ||
        (batch.keySet() as Set) != batchKeys ||
        batch.schema_name != 'bms_frustrampnn_scheduler_batch' ||
        batch.schema_version != 3 ||
        batch.execution_owner_job_id?.toString() != params.job_id.toString() ||
        !(batch.batching_enabled instanceof Boolean) ||
        !(batch.structures_per_job instanceof Integer) ||
        batch.structures_per_job < 1 || batch.structures_per_job > 250 ||
        !(batch.settings_sha256 ==~ /[a-f0-9]{64}/) ||
        !(batch.records instanceof List) ||
        batch.records.isEmpty() ||
        batch.expected_cardinality != batch.records.size() ||
        batch.records.size() > batch.structures_per_job ||
        (!batch.batching_enabled && batch.records.size() != 1)
    ) {
        throw new IllegalArgumentException('invalid persisted FrustraMPNN scheduler batch manifest')
    }
    def authorityRoot = manifestPath.parent.parent
    def recordKeys = [
        'record_schema_name', 'record_schema_version', 'ordinal', 'candidate_id',
        'invocation_id', 'request_relative_path', 'request_sha256',
        'request_size_bytes', 'source_relative_path', 'source_sha256',
        'source_size_bytes', 'structure_map_relative_path',
        'structure_map_sha256', 'structure_map_size_bytes'
    ] as Set
    def persisted = batch.records.collect { record ->
        if (
            !(record instanceof Map) ||
            (record.keySet() as Set) != recordKeys ||
            record.record_schema_name != 'bms_frustrampnn_scheduler_record' ||
            record.record_schema_version != 2
        ) {
            throw new IllegalArgumentException('invalid persisted FrustraMPNN v2 scheduler record')
        }
        def requestRelative = record.request_relative_path?.toString()
        def sourceRelative = record.source_relative_path?.toString()
        def structureMapRelative = record.structure_map_relative_path?.toString()
        if (!(requestRelative ==~ /inputs\/requests\/[A-Za-z0-9._-]+\/workflow_component_request_v3\.json/) ||
            !(sourceRelative ==~ /inputs\/sources\/[A-Za-z0-9._-]+\/canonical_source\.pdb/) ||
            !(structureMapRelative ==~ /inputs\/maps\/[A-Za-z0-9._-]+\/frustrampnn_structure_map_v1\.json/)) {
            throw new IllegalArgumentException('invalid persisted FrustraMPNN snapshot path')
        }
        def encoded = JsonOutput.toJson(record).getBytes('UTF-8').encodeBase64().toString()
        tuple(
            encoded,
            file(authorityRoot.resolve(requestRelative)),
            file(authorityRoot.resolve(sourceRelative)),
            file(authorityRoot.resolve(structureMapRelative))
        )
    }
    if (batch.batching_enabled && batch.records.size() > 1) {
        RunPersistedFrustraMPNNGroupedBatch(manifestPath.toString())
        PublishPersistedFrustraMPNNGroupedBundles(RunPersistedFrustraMPNNGroupedBatch.out.results)
        publicationMarkers = PublishPersistedFrustraMPNNGroupedBundles.out.marker
    } else {
        preparedInputs = Channel.fromList(persisted)
        PreparePersistedFrustraMPNNCandidate(preparedInputs)
        CanonicalFrustraMPNNV2(PreparePersistedFrustraMPNNCandidate.out.prepared)
        PublishPersistedFrustraMPNNChildBundle(CanonicalFrustraMPNNV2.out.result)
        publicationMarkers = PublishPersistedFrustraMPNNChildBundle.out.marker
    }
    ReportPersistedFrustraMPNNComplete(publicationMarkers.flatten().collect())
}
