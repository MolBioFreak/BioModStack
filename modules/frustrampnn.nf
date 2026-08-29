nextflow.enable.dsl = 2

process CanonicalFrustraMPNNTask {
    tag "frustrampnn:${component_request_meta.parent_job_id}:${component_request_meta.candidate_id}"
    label 'frustrampnn_gpu'
    errorStrategy 'terminate'
    maxRetries 0
    stageInMode 'copy'


    input:
    tuple val(component_request_meta), path(source_structure)

    output:
    // Emit the terminal result envelope itself. Parent workflows must consume
    // this status authority rather than treating request metadata as a result.
    tuple path('candidate_bundle/workflow_component_result_v1.json'), \
        path('candidate_bundle'), \
        path('candidate_bundle/frustrampnn_result_manifest_v1.json'), emit: result

    script:
    def requiredIdentityFields = [
        'component_id', 'component_contract_version', 'invocation_id',
        'parent_job_id', 'parent_workflow_id', 'candidate_id', 'requiredness'
    ]
    if (!(component_request_meta instanceof Map) ||
        requiredIdentityFields.any { field -> !component_request_meta[field] }) {
        throw new IllegalArgumentException(
            'CanonicalFrustraMPNN requires typed parent/candidate/invocation metadata'
        )
    }
    if (component_request_meta.component_id != 'frustrampnn') {
        throw new IllegalArgumentException('CanonicalFrustraMPNN component_id must be frustrampnn')
    }

    def assigned_gpu = params.frustrampnn_physical_gpu_id?.toString()
    if (!(assigned_gpu ==~ /(?:0|[1-9][0-9]*)/)) {
        throw new IllegalArgumentException(
            'CanonicalFrustraMPNN requires explicit scheduler-assigned frustrampnn_physical_gpu_id'
        )
    }
    def request_base64 = groovy.json.JsonOutput.toJson(component_request_meta)
        .getBytes('UTF-8').encodeBase64().toString()
    def apptainer_bin = params.get('apptainer_bin') ?: 'apptainer'

    """
    set -euo pipefail
    export CUDA_VISIBLE_DEVICES='${assigned_gpu}'
    '${params.api_python}' '${params.code_root}/scripts/run_frustrampnn_component.py' \
      --request-base64 '${request_base64}' \
      --structure '${source_structure}' \
      --container '${params.container_dir}/frustrampnn.sif' \
      --apptainer '${apptainer_bin}' \
      --physical-gpu-id '${assigned_gpu}' \
      --output-dir candidate_bundle
    """

    stub:
    def stub_assigned_gpu = params.frustrampnn_physical_gpu_id?.toString()
    if (!(stub_assigned_gpu ==~ /(?:0|[1-9][0-9]*)/)) {
        throw new IllegalArgumentException(
            'CanonicalFrustraMPNN requires explicit scheduler-assigned frustrampnn_physical_gpu_id'
        )
    }
    def stub_result = groovy.json.JsonOutput.toJson(component_request_meta + [status: 'succeeded'])
    """
    mkdir -p candidate_bundle
    printf '%s\n' '${stub_result}' > candidate_bundle/workflow_component_result_v1.json
    printf '{}\n' > candidate_bundle/frustrampnn_result_manifest_v1.json
    """
}

workflow CanonicalFrustraMPNN {
    take:
    requests

    main:
    normalized_requests = requests.map { request_or_meta, source_structure ->
        def component_request_meta = request_or_meta instanceof Map
            ? request_or_meta
            : new groovy.json.JsonSlurper().parse(request_or_meta)
        tuple(component_request_meta, source_structure)
    }
    CanonicalFrustraMPNNTask(normalized_requests)
    terminal_results = CanonicalFrustraMPNNTask.out.result.map {
        result_path, candidate_bundle, result_manifest ->
        def component_result_meta = new groovy.json.JsonSlurper().parse(result_path)
        tuple(component_result_meta, candidate_bundle, result_manifest)
    }

    emit:
    result = terminal_results
}

process CanonicalFrustraMPNNV2Task {
    tag 'frustrampnn:v2'
    label 'frustrampnn_gpu'
    errorStrategy 'terminate'
    maxRetries 0
    stageInMode 'copy'

    input:
    tuple path(component_request), path(source_structure), path(structure_map)

    output:
    tuple path('candidate_bundle/workflow_component_result_v3.json'), \
        path('candidate_bundle'), \
        path('candidate_bundle/frustrampnn_result_manifest_v3.json'), emit: result

    script:
    def assigned_gpu = params.frustrampnn_physical_gpu_id?.toString()
    if (!(assigned_gpu ==~ /(?:0|[1-9][0-9]*)/)) {
        throw new IllegalArgumentException(
            'CanonicalFrustraMPNNV2 requires explicit scheduler-assigned frustrampnn_physical_gpu_id'
        )
    }
    def apptainer_bin = params.get('apptainer_bin') ?: 'apptainer'
    """
    set -euo pipefail
    export CUDA_VISIBLE_DEVICES='${assigned_gpu}'
    '${params.api_python}' '${params.code_root}/scripts/run_frustrampnn_component.py' \
      --request '${component_request}' \
      --structure '${source_structure}' \
      --structure-map '${structure_map}' \
      --container '${params.container_dir}/frustrampnn.sif' \
      --apptainer '${apptainer_bin}' \
      --physical-gpu-id '${assigned_gpu}' \
      --output-dir candidate_bundle
    """

    stub:
    def stub_assigned_gpu = params.frustrampnn_physical_gpu_id?.toString()
    if (!(stub_assigned_gpu ==~ /(?:0|[1-9][0-9]*)/)) {
        throw new IllegalArgumentException(
            'CanonicalFrustraMPNNV2 requires explicit scheduler-assigned frustrampnn_physical_gpu_id'
        )
    }
    """
    mkdir -p candidate_bundle
    '${params.api_python}' - '${component_request}' <<'PY'
import json
import pathlib
import sys

request = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
result = {
    'candidate_id': request['candidate_id'],
    'invocation_id': request['invocation_id'],
    'status': 'succeeded',
}
pathlib.Path('candidate_bundle/workflow_component_result_v3.json').write_text(
    json.dumps(result, sort_keys=True, separators=(',', ':')) + '\\n',
    encoding='utf-8',
)
pathlib.Path('candidate_bundle/frustrampnn_result_manifest_v3.json').write_text(
    json.dumps({'candidate_id': request['candidate_id']}, sort_keys=True, separators=(',', ':')) + '\\n',
    encoding='utf-8',
)
PY
    """
}

workflow CanonicalFrustraMPNNV2 {
    take:
    requests

    main:
    CanonicalFrustraMPNNV2Task(requests)
    terminal_results = CanonicalFrustraMPNNV2Task.out.result.map {
        result_path, candidate_bundle, result_manifest ->
        def component_result_meta = new groovy.json.JsonSlurper().parse(result_path)
        tuple(component_result_meta, candidate_bundle, result_manifest)
    }

    emit:
    result = terminal_results
}
