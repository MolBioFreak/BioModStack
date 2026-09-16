// Owning-stage accounting only. Does not select/finalize result candidates.
process PublishRFFilterStage {
    label 'process_low'
    publishDir "${params.out_dir}/run/filter_stages", mode: 'copy', pattern: '*.json'

    input:
    val stage_owner
    val stage_id
    val role
    val expected_tasks
    path task_receipts
    path terminal_manifests

    output:
    path "${stage_id}.json", emit: stage

    script:
    def receipts = task_receipts.collect { "--receipt '${it}'" }.join(' ')
    def terminals = terminal_manifests.collect { "--terminal-manifest '${it}'" }.join(' ')
    """
    set -euo pipefail
    python3 '${params.code_root}/scripts/collect_rf_filter_stage.py' \
      --job-id '${params.job_id}' --owner '${stage_owner}' --stage-id '${stage_id}' \
      --role '${role}' --expected-tasks '${expected_tasks}' ${receipts} ${terminals} \
      --output '${stage_id}.json'
    """
}
