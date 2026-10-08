// Shared local/worker durable gate. Outputs are sealed from DAG task paths,
// never from asynchronously populated publishDir destinations.
process OpenInteractiveGate {
    label 'process_low'
    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: 'gate_*.json'

    input:
    val stage_name
    val candidate_files
    val review_files
    val review_metadata

    output:
    path "gate_${stage_name}.json", emit: report

    script:
    if (!params.checkpoint_context) {
        error('Interactive checkpoint requires scheduler-owned checkpoint_context')
    }
    def flattenPaths = { paths ->
        (paths instanceof Collection ? paths.flatten() : [paths])
            .findAll { it != null }.collect { it.toString() }.unique()
    }
    def request = [
        root: "${params.out_dir}/checkpoints/${stage_name}",
        context: params.checkpoint_context.toString(), stage: stage_name,
        candidates: flattenPaths(candidate_files), review: flattenPaths(review_files),
        metadata: review_metadata,
    ]
    def requestJson = groovy.json.JsonOutput.toJson(request)
    """
    set -euo pipefail
    cat > checkpoint_request.json <<'BMS_CHECKPOINT_REQUEST'
${requestJson}
BMS_CHECKPOINT_REQUEST
    python3 '${params.code_root}/scripts/interactive_checkpoint.py' seal \\
        --request checkpoint_request.json --output 'gate_${stage_name}.json'
    """
}
