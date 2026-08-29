nextflow.enable.dsl = 2

process StageFrustraMPNNParentCandidate {
    tag "frustrampnn-parent-source:${candidate_meta.candidate_id}"
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    tuple val(candidate_meta), path(terminal_structure)

    output:
    path 'candidate_*', emit: candidate

    script:
    def metadataBase64 = groovy.json.JsonOutput.toJson(candidate_meta).getBytes('UTF-8').encodeBase64().toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/stage_frustrampnn_parent_candidate.py' \
      --source '${terminal_structure}' \
      --metadata-base64 '${metadataBase64}'
    """
}

process SpawnWaitFrustraMPNNParentChildren {
    tag "frustrampnn-parent-fanout:${parent_workflow_id}:${parent_job_id}"
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    val parent_job_id
    val parent_workflow_id
    val settings_json
    val settings_value_origin
    path candidate_dirs

    output:
    path 'frustrampnn_parent_terminal_v1.json', emit: receipt
    path 'frustrampnn_child_bundles', emit: bundles

    script:
    def candidateArgs = candidate_dirs.collect { candidate -> "--candidate-dir '${candidate}'" }.join(' \\\n       ')
    def settingsBase64 = settings_json.getBytes('UTF-8').encodeBase64().toString()
    """
    set -euo pipefail
    printf '%s' '${settingsBase64}' | base64 --decode > frustrampnn_settings_v2.json
    '${params.api_python}' '${params.code_root}/scripts/run_frustrampnn_parent_fanout.py' \
      --parent-job-id '${parent_job_id}' \
      --parent-workflow-id '${parent_workflow_id}' \
      --settings-json-file frustrampnn_settings_v2.json \
      --settings-value-origin '${settings_value_origin}' \
      ${candidateArgs} \
      --output-receipt frustrampnn_parent_terminal_v1.json \
      --output-bundles frustrampnn_child_bundles
    """
}

process ReportFrustraMPNNParentChildrenComplete {
    tag "frustrampnn-parent-report:${parent_workflow_id}:${parent_job_id}"
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0

    input:
    val parent_job_id
    val parent_workflow_id
    path terminal_receipt

    output:
    path 'frustrampnn_children_complete.reported', emit: marker

    script:
    """
    set -euo pipefail
    destination='${params.out_dir}/frustrampnn/parent_fanout/${parent_workflow_id}_terminal_v1.json'
    mkdir -p "\$(dirname "\${destination}")"
    test ! -e "\${destination}"
    cp -L '${terminal_receipt}' "\${destination}"
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' --job-root-relative \
      '${parent_job_id}' frustrampnn complete \
      'frustrampnn/parent_fanout/${parent_workflow_id}_terminal_v1.json'
    : > frustrampnn_children_complete.reported
    """
}

workflow SchedulerFrustraMPNNParentFanout {
    take:
    candidates
    parent_job_id
    parent_workflow_id
    settings_json
    settings_value_origin

    main:
    StageFrustraMPNNParentCandidate(candidates)
    staged_candidates = StageFrustraMPNNParentCandidate.out.candidate.collect()
    SpawnWaitFrustraMPNNParentChildren(
        parent_job_id,
        parent_workflow_id,
        settings_json,
        settings_value_origin,
        staged_candidates,
    )
    ReportFrustraMPNNParentChildrenComplete(
        parent_job_id,
        parent_workflow_id,
        SpawnWaitFrustraMPNNParentChildren.out.receipt,
    )
    result_bundles = SpawnWaitFrustraMPNNParentChildren.out.bundles.flatMap { bundle_root ->
        bundle_root.toFile().listFiles().sort { left, right -> left.name <=> right.name }
    }

    emit:
    receipt = SpawnWaitFrustraMPNNParentChildren.out.receipt
    result_bundles = result_bundles
    completion = ReportFrustraMPNNParentChildrenComplete.out.marker
}
