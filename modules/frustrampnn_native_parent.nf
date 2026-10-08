nextflow.enable.dsl = 2

// Identical local/worker DAG. Placement changes bindings, never grouping or science.
process PrepareNativeParentFrustraMPNN {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    input:
    tuple val(meta), path(source)
    val settings
    val origin
    output:
    path 'prepared', emit: prepared
    script:
    def metadata64 = groovy.json.JsonOutput.toJson(meta).bytes.encodeBase64().toString()
    def settings64 = settings.bytes.encodeBase64().toString()
    """
    '${params.api_python}' '${params.code_root}/scripts/native_frustrampnn_parent.py' prepare \
      --source '${source}' --metadata-base64 '${metadata64}' --settings-base64 '${settings64}' --origin '${origin}'
    """
}

process PlanNativeParentFrustraMPNN {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    input:
    path candidates, stageAs: 'candidate??/*'
    output:
    path 'groups/group_*', emit: groups
    path 'groups/grouping_plan_v1.json', emit: plan
    script:
    def args = (candidates instanceof List ? candidates : [candidates]).collect { "--candidate '${it}'" }.join(' ')
    def attempt = params.get('component_attempt_id') ?: System.getenv('BMS_REMOTE_ATTEMPT_ID') ?: params.job_id
    """
    '${params.api_python}' '${params.code_root}/scripts/native_frustrampnn_parent.py' plan ${args} --attempt '${attempt}'
    """
}

process RunNativeParentFrustraMPNN {
    label 'frustrampnn_gpu'
    publishDir "${params.out_dir}/frustrampnn/component_runtime/groups/${group.name}", mode: 'copy', pattern: 'group_receipts'
    // One assigned parent GPU, one owner at a time, including singleton remainders.
    maxForks 1
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0
    input:
    path group
    output:
    path 'grouped_results', emit: bundles
    path 'group_receipts', emit: receipts
    script:
    def gpu = params.frustrampnn_physical_gpu_id?.toString()
    if (!(gpu ==~ /(?:0|[1-9][0-9]*)/)) error('Native FrustraMPNN requires scheduler assigned GPU')
    def apptainer = params.get('apptainer_bin') ?: 'apptainer'
    def attempt = params.get('component_attempt_id') ?: System.getenv('BMS_REMOTE_ATTEMPT_ID') ?: params.job_id
    """
    set -euo pipefail
    export CUDA_VISIBLE_DEVICES='${gpu}'
    candidates=()
    for candidate in '${group}'/candidate_*; do candidates+=(--candidate "\$candidate"); done
    '${params.api_python}' '${params.code_root}/scripts/native_frustrampnn_parent.py' run "\${candidates[@]}" \
      --container '${params.container_dir}/frustrampnn.sif' --gpu '${gpu}' --apptainer '${apptainer}' \
      --diagnostic-root '${params.out_dir}/frustrampnn/component_runtime/failed/${attempt}/${group.name}'
    """
}

process SealNativeParentFrustraMPNN {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    publishDir "${params.out_dir}/frustrampnn/component_runtime", mode: 'copy', pattern: 'joined/*json', saveAs: { name -> new File(name).name }
    publishDir "${params.out_dir}/frustrampnn/component_runtime", mode: 'copy', pattern: 'joined/components.sqlite', saveAs: { name -> new File(name).name }
    input:
    path candidates, stageAs: 'candidate??/*'
    path results, stageAs: 'result??/*'
    path group_receipts, stageAs: 'receipt??/*'
    output:
    path 'joined/terminal.json', emit: receipt
    path 'joined/*json', emit: evidence
    path 'joined/components.sqlite', emit: ledger
    path 'joined/bundles', emit: bundles
    script:
    def candidateArgs = (candidates instanceof List ? candidates : [candidates]).collect { "--candidate '${it}'" }.join(' ')
    def resultArgs = (results instanceof List ? results : [results]).collect { "--bundle '${it}'" }.join(' ')
    def attempt = params.get('component_attempt_id') ?: System.getenv('BMS_REMOTE_ATTEMPT_ID') ?: params.job_id
    """
    '${params.api_python}' '${params.code_root}/scripts/native_frustrampnn_parent.py' seal \
      ${candidateArgs} ${resultArgs} --publish-root '${params.out_dir}' --attempt '${attempt}'
    """
}

workflow NativePreparedFrustraMPNNParent {
    take:
    prepared
    main:
    candidates = prepared.toList()
    PlanNativeParentFrustraMPNN(candidates)
    RunNativeParentFrustraMPNN(PlanNativeParentFrustraMPNN.out.groups.flatten())
    bundles = RunNativeParentFrustraMPNN.out.bundles.flatMap { root ->
        root.toFile().listFiles().sort { a, b -> a.name <=> b.name }.collect { file(it.toPath()) }
    }.toList()
    SealNativeParentFrustraMPNN(candidates, bundles, RunNativeParentFrustraMPNN.out.receipts.toList())
    emit:
    receipt = SealNativeParentFrustraMPNN.out.receipt
    result_bundles = SealNativeParentFrustraMPNN.out.bundles.flatMap { root ->
        root.toFile().listFiles().sort { a, b -> a.name <=> b.name }.collect { file(it.toPath()) }
    }
}

workflow NativeFrustraMPNNParentFanout {
    take:
    candidates
    parent_job_id
    parent_workflow_id
    settings_json
    settings_value_origin
    main:
    boundCandidates = candidates.combine(parent_job_id).combine(parent_workflow_id).map { meta, source, parent, workflow ->
        if (meta.parent_job_id != parent || meta.parent_workflow_id != workflow) error('Native parent identity conflict')
        tuple(meta, source)
    }
    PrepareNativeParentFrustraMPNN(boundCandidates, settings_json, settings_value_origin)
    NativePreparedFrustraMPNNParent(PrepareNativeParentFrustraMPNN.out.prepared)
    emit:
    receipt = NativePreparedFrustraMPNNParent.out.receipt
    result_bundles = NativePreparedFrustraMPNNParent.out.result_bundles
}
