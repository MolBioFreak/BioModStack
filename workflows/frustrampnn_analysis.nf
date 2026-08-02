nextflow.enable.dsl = 2

import groovy.json.JsonOutput
import groovy.json.JsonSlurper

include { CanonicalFrustraMPNN } from '../modules/frustrampnn'

process PreparePersistedFrustraMPNNCandidate {
    tag 'frustrampnn-prepare-persisted'
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    tuple val(record_base64), path(request_snapshot), path(source_snapshot)

    output:
    tuple path('workflow_component_request_v1.json'), path('canonical_source.pdb'), emit: prepared

    script:
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_persisted_frustrampnn_candidate.py' \
      --record-base64 '${record_base64}' \
      --request '${request_snapshot}' \
      --source '${source_snapshot}' \
      --output-request workflow_component_request_v1.json \
      --output-source canonical_source.pdb
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

process ReportPersistedFrustraMPNNComplete {
    label 'CPU'
    errorStrategy 'terminate'
    maxRetries 0

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

workflow {
    if (!params.job_id) {
        throw new IllegalArgumentException('job_id is required')
    }
    if (!params.frustrampnn_batch_manifest_path) {
        throw new IllegalArgumentException('frustrampnn_batch_manifest_path is required')
    }
    def manifestPath = file(params.frustrampnn_batch_manifest_path)
    def batch = new JsonSlurper().parse(manifestPath)
    if (
        batch.schema_name != 'bms_frustrampnn_scheduler_batch' ||
        batch.schema_version != 1 ||
        batch.execution_owner_job_id?.toString() != params.job_id.toString() ||
        !(batch.records instanceof List) ||
        batch.records.isEmpty()
    ) {
        throw new IllegalArgumentException('invalid persisted FrustraMPNN scheduler batch manifest')
    }
    def authorityRoot = manifestPath.parent.parent
    def persisted = batch.records.collect { record ->
        def requestRelative = record.request_relative_path?.toString()
        def sourceRelative = record.source_relative_path?.toString()
        if (!(requestRelative ==~ /inputs\/requests\/[A-Za-z0-9._-]+/) ||
            !(sourceRelative ==~ /inputs\/sources\/[A-Za-z0-9._-]+/)) {
            throw new IllegalArgumentException('invalid persisted FrustraMPNN snapshot path')
        }
        def encoded = JsonOutput.toJson(record).getBytes('UTF-8').encodeBase64().toString()
        tuple(encoded, file(authorityRoot.resolve(requestRelative)), file(authorityRoot.resolve(sourceRelative)))
    }
    preparedInputs = Channel.fromList(persisted)
    PreparePersistedFrustraMPNNCandidate(preparedInputs)
    CanonicalFrustraMPNN(PreparePersistedFrustraMPNNCandidate.out.prepared)
    PublishPersistedFrustraMPNNChildBundle(CanonicalFrustraMPNN.out.result)
    ReportPersistedFrustraMPNNComplete(PublishPersistedFrustraMPNNChildBundle.out.marker.collect())
}
